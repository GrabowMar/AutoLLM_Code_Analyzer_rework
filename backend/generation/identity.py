"""Identity and hashing for v3 sample generation (spec section 3.1).

This module is the **only** place where identity keys are computed. Everything
else (``study/plan.py``, ``agentic/manifest.py``, slot targets, library
versions) calls the functions below and copies their results; nothing
re-implements a hash.

``H(x)`` is ``sha256(canonical_json(x))`` as lowercase hex. ``canonical_json``
pre-normalizes its input recursively:

* every ``str`` (dict keys included) is NFC-normalized and ``"\\r\\n"``/``"\\r"``
  become ``"\\n"``;
* floats must be finite (NaN and infinities are rejected);
* tuples become lists; dict keys must be ``str``; ``None`` is kept;
* anything else that is not a JSON scalar, list or dict (sets, bytes,
  ``Decimal``, datetimes, pydantic models, ...) is rejected, so callers dump
  their objects explicitly (``model_dump(mode="json")``) before hashing.

Changing any algorithm or constant here changes every pinned golden hash and
requires a protocol bump (``app@2``, ``slot@2``) plus a changelog line in
``docs/STUDIES.md#identity-changelog``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields
from typing import Any
from typing import Final

__all__ = [
    "KIND_PROTOCOL",
    "PROTOCOLS",
    "RUN_KINDS",
    "SLOT_POLICY_KEYS",
    "TREATMENT_KEYS",
    "CanonicalJSONError",
    "H",
    "IdentityInputs",
    "IdentityKeys",
    "bytes_sha",
    "canonical_json",
    "compute_keys",
    "condition_hash",
    "crn_seed",
    "design_hash",
    "detector_set_hash",
    "library_content_hash",
    "normalize_text",
    "pinned_ref",
    "plan_hash",
    "prereg_hash",
    "prompt_hash",
    "target_hash",
    "task_prose_hashes",
    "task_structured_hash",
    "text_sha",
    "tree_hash",
]

SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

PROTOCOLS: Final = ("app@1", "slot@1", "ref@1")
RUN_KINDS: Final = ("app_agentic", "slot_single_shot", "slot_agent", "reference")
KIND_PROTOCOL: Final[Mapping[str, str]] = {
    "app_agentic": "app@1",
    "slot_single_shot": "slot@1",
    "slot_agent": "slot@1",
    "reference": "ref@1",
}

# The exact key set of ``IdentityInputs.treatment`` (section 3.1, treatment_hash row).
# ``protocol`` and ``executor`` are added by ``compute_keys`` from ``IdentityInputs``.
TREATMENT_KEYS: Final = (
    "stack",
    "starter",
    "style",
    "auth_mode",
    "guidance",
    "prompt_set",
    "harness_preset",
    "gateway",
    "allowed_bases_sha",
    "slot_policy",
)
GATEWAY_KEYS: Final = ("front_protocol", "protocol_mode")
SLOT_POLICY_KEYS: Final = (
    "hole_mode",
    "keep_docstring",
    "context_policy_hash",
    "resolved_context_sha",
    "include_tests",
    "edit_policy",
)
TASK_KEYS: Final = ("structured_hash", "variant", "prose_sha")
TARGET_KEYS: Final = ("target_hash",)
MODEL_KEYS: Final = ("slug", "endpoint_kind", "pinned_digest")
IMAGE_KEYS: Final = ("harness", "gateway", "starter_base")
# Kinds that run with the per-run gateway sidecar (and therefore pin its image digest).
_SIDECAR_KINDS: Final = frozenset({"app_agentic", "slot_agent"})

_SEED_MODULUS: Final = 2**31
_SEED_HEX_DIGITS: Final = 12
_PINNED_HASH_CHARS: Final = 12


class CanonicalJSONError(ValueError):
    """Raised when an object cannot be canonically serialized (non-finite float, bad key, unsupported type)."""


# ---------------------------------------------------------------------------
# Text and JSON canonicalization
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """NFC-normalize ``text`` and convert ``\\r\\n`` / ``\\r`` line endings to ``\\n``."""
    if not isinstance(text, str):
        msg = f"normalize_text expects str, got {type(text).__name__}"
        raise TypeError(msg)
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _normalize(obj: Any, path: str) -> Any:
    # bool before int: bool is an int subclass and must stay a JSON boolean.
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        return normalize_text(str(obj))
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            msg = f"non-finite float at {path or '$'}: {obj!r}"
            raise CanonicalJSONError(msg)
        return float(obj)
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                msg = f"dict key at {path or '$'} must be str, got {type(key).__name__}: {key!r}"
                raise CanonicalJSONError(msg)
            norm_key = normalize_text(str(key))
            if norm_key in out:
                msg = f"dict keys at {path or '$'} collide after normalization: {key!r}"
                raise CanonicalJSONError(msg)
            out[norm_key] = _normalize(value, f"{path}.{norm_key}")
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(obj)]
    msg = f"unsupported type at {path or '$'}: {type(obj).__name__} (dump it to JSON types before hashing)"
    raise CanonicalJSONError(msg)


def canonical_json(obj: Any) -> bytes:
    """Return the canonical UTF-8 JSON encoding of ``obj`` (sorted keys, no whitespace, normalized text)."""
    normalized = _normalize(obj, "")
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def H(obj: Any) -> str:  # noqa: N802 - the spec's name for sha256(canonical_json(x))
    """``sha256(canonical_json(obj))`` as lowercase hex."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def text_sha(text: str) -> str:
    """sha256 of the NFC/LF-normalized UTF-8 text (no JSON quoting)."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def bytes_sha(data: bytes) -> str:
    """sha256 of raw bytes."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        msg = f"bytes_sha expects bytes, got {type(data).__name__}"
        raise TypeError(msg)
    return hashlib.sha256(data).hexdigest()


def _require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.match(value):
        msg = f"{name} must be a lowercase hex sha256, got {value!r}"
        raise ValueError(msg)
    return value


def _normalize_posix_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        msg = f"tree path must be a non-empty str, got {path!r}"
        raise ValueError(msg)
    norm = normalize_text(path)
    if "\\" in norm or norm.startswith("/") or "\x00" in norm:
        msg = f"tree path must be a relative POSIX path: {path!r}"
        raise ValueError(msg)
    if any(part in {"", ".", ".."} for part in norm.split("/")):
        msg = f"tree path has an empty, '.' or '..' segment: {path!r}"
        raise ValueError(msg)
    return norm


def tree_hash(entries: Iterable[tuple[str, int, str]]) -> str:
    """Hash a file tree: ``H(sorted([[path, mode & 0o111 != 0, blob_sha], ...]))``.

    Only the executable bit of ``mode`` is significant. Paths are relative POSIX
    paths, NFC-normalized; duplicates are rejected.
    """
    rows: list[list[Any]] = []
    seen: set[str] = set()
    for entry in entries:
        path, mode, blob_sha = entry
        norm = _normalize_posix_path(path)
        if norm in seen:
            msg = f"duplicate tree path: {norm!r}"
            raise ValueError(msg)
        seen.add(norm)
        if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0:
            msg = f"tree mode must be a non-negative int, got {mode!r} for {norm!r}"
            raise ValueError(msg)
        rows.append([norm, (mode & 0o111) != 0, _require_sha(blob_sha, f"blob sha of {norm!r}")])
    rows.sort()
    return H(rows)


def pinned_ref(slug: str, version: int, content_hash: str) -> str:
    """``f"{slug}@{version}#{content_hash[:12]}"``."""
    if not slug or "@" in slug or "#" in slug:
        msg = f"invalid slug for a ref: {slug!r}"
        raise ValueError(msg)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        msg = f"version must be a positive int, got {version!r}"
        raise ValueError(msg)
    _require_sha(content_hash, "content_hash")
    return f"{slug}@{version}#{content_hash[:_PINNED_HASH_CHARS]}"


# ---------------------------------------------------------------------------
# Library and plan-level hashes
# ---------------------------------------------------------------------------


def library_content_hash(kind: str, schema_version: int, content: Mapping[str, Any]) -> str:
    """``H({"kind", "schema", "content"})``. Slug, name, version, ids and owner are never hashed."""
    return H({"kind": kind, "schema": schema_version, "content": content})


def task_structured_hash(content: Mapping[str, Any]) -> str:
    """``TaskSpecVersion.structured_hash``: ``H(content minus "prose")``."""
    return H({key: value for key, value in content.items() if key != "prose"})


def task_prose_hashes(content: Mapping[str, Any]) -> dict[str, str]:
    """``TaskSpecVersion.prose_hashes``: ``{variant: text_sha(prose)}``."""
    prose = content.get("prose") or {}
    return {variant: text_sha(text) for variant, text in sorted(prose.items())}


def condition_hash(levels: Mapping[str, str | bool | int | None]) -> str:
    """Condition identity over every treatment factor level (prose variant included, units excluded).

    The spec's "sorted condition level pinned refs" is hashed as sorted
    ``[factor_key, level]`` pairs, because enum levels (``stub``, ``true``) are
    not self-describing without their factor key.
    """
    return H(sorted([[key, level] for key, level in levels.items()], key=lambda pair: pair[0]))


def design_hash(design: Mapping[str, Any]) -> str:
    """``H(Design.model_dump(mode="json"))``; pass the dumped dict."""
    return H(design)


def plan_hash(
    *,
    design_hash: str,
    conditions: Sequence[str],
    units: Sequence[str],
    cells: Iterable[Sequence[Any]],
    prompt_hashes: Mapping[str, str],
) -> str:
    """``H({"design", "conditions", "units", "cells", "prompt_hashes"})`` (section 3.1).

    ``conditions`` are condition hashes in condition-index order and ``units``
    unit keys in unit-index order (cells refer to both by index). ``cells`` are
    ``[c_idx, u_idx, repeat, stage, seed, sequence]`` rows; they are sorted so
    the hash does not depend on iteration order.
    """
    rows = [list(cell) for cell in cells]
    for row in rows:
        if len(row) != 6:
            msg = f"plan cell rows are [c_idx, u_idx, repeat, stage, seed, sequence], got {row!r}"
            raise ValueError(msg)
    rows.sort(key=lambda row: (row[0], row[1], row[2], str(row[3])))
    return H(
        {
            "design": _require_sha(design_hash, "design_hash"),
            "conditions": list(conditions),
            "units": list(units),
            "cells": rows,
            "prompt_hashes": dict(prompt_hashes),
        },
    )


def prereg_hash(
    *,
    plan_hash: str,
    analysis_plan: Mapping[str, Any] | None,
    stop_rules: Any,
    pilot_gates: Any,
    evaluation: Mapping[str, Any],
) -> str:
    """``H({"plan", "analysis_plan", "stop_rules", "pilot_gates", "evaluation"})`` (section 3.1)."""
    return H(
        {
            "plan": _require_sha(plan_hash, "plan_hash"),
            "analysis_plan": analysis_plan,
            "stop_rules": stop_rules,
            "pilot_gates": pilot_gates,
            "evaluation": evaluation,
        },
    )


def target_hash(
    *,
    parent_tree: str,
    address: Mapping[str, Any],
    original_sha: str,
    allowlist: Iterable[str],
    interface: Mapping[str, Any],
) -> str:
    """Slot target identity. Hole mode and context policy are condition-level and NOT part of it."""
    return H(
        {
            "parent_tree": _require_sha(parent_tree, "parent_tree"),
            "address": dict(address),
            "original": _require_sha(original_sha, "original_sha"),
            "allowlist": sorted(allowlist),
            "interface": dict(interface),
        },
    )


DETECTOR_SET_HASH_VERSION: Final = 1


def _validate_stimuli(stimuli: Iterable[Any]) -> list[tuple[str, str]]:
    """``(role, text_sha)`` pairs: non-empty, unique roles and lowercase hex shas (ValueError otherwise)."""
    pairs: list[tuple[str, str]] = []
    roles: set[str] = set()
    for item in stimuli:
        if len(item) != 2:
            msg = f"stimuli entries are (role, text_sha), got {item!r}"
            raise ValueError(msg)
        role, sha = item
        if not isinstance(role, str) or not role:
            msg = f"stimulus role must be a non-empty str, got {role!r}"
            raise ValueError(msg)
        if role in roles:
            msg = f"duplicate stimulus role {role!r}"
            raise ValueError(msg)
        roles.add(role)
        _require_sha(sha, f"stimulus {role!r} text_sha")
        pairs.append((role, sha))
    return pairs


def _prompt_hash(pairs: Iterable[tuple[str, str]]) -> str:
    return H(sorted([[role, sha] for role, sha in pairs]))


def prompt_hash(stimuli: Iterable[tuple[str, str]]) -> str:
    """``prompt_hash = H(sorted([[role, text_sha(rendered)], ...]))`` (section 3.1), the one implementation.

    ``stimuli`` are ``(role, text_sha)`` pairs, one per role; ``compute_keys`` uses the same function, so a
    preview (``prompts.engine.preview_prompt_hash``) always equals the key of a run with those stimuli.
    """
    return _prompt_hash(_validate_stimuli(stimuli))


def detector_set_hash(detectors: Iterable[tuple[str, str]]) -> str:
    """``PatternRun.detector_set_hash``: ``H({"version": 1, "detectors": sorted([[slug@version, content_hash]])})``.

    ``detectors`` are ``(ref, full content_hash)`` pairs; a pinned ref (``slug@v#hash12``) is reduced to
    ``slug@version``. Order-independent, duplicates collapse, and the hash commits to every detector's
    full content hash. This is the value ``analysis_plan.patterns.detector_set_hash`` must hold for a
    planned PatternRun (section 7.9 step 9).
    """
    pairs: set[tuple[str, str]] = set()
    for ref, content_hash in detectors:
        base = str(ref).split("#", 1)[0]
        slug, sep, version = base.partition("@")
        if not slug or not sep or not version.isdigit() or int(version) < 1:
            msg = f"detector ref must be slug@version, got {ref!r}"
            raise ValueError(msg)
        pairs.add((base, _require_sha(content_hash, f"content hash of {base}")))
    return H({"version": DETECTOR_SET_HASH_VERSION, "detectors": [list(pair) for pair in sorted(pairs)]})


def crn_seed(base_seed: int, unit_key: str, repeat_index: int) -> int:
    """CRN seed (section 9.4): ``int(H({"base", "unit", "repeat"})[:12], 16) % 2**31``.

    Every condition (prose variant included) gets the same seed for one (unit, repeat).
    """
    digest = H({"base": base_seed, "unit": unit_key, "repeat": repeat_index})
    return int(digest[:_SEED_HEX_DIGITS], 16) % _SEED_MODULUS


# ---------------------------------------------------------------------------
# Run identity keys
# ---------------------------------------------------------------------------


def _require_exact_keys(value: Any, keys: Sequence[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        msg = f"{name} must be a dict with keys {sorted(keys)}, got {type(value).__name__}"
        raise TypeError(msg)
    missing = set(keys) - set(value)
    extra = set(value) - set(keys)
    if missing or extra:
        msg = f"{name} keys mismatch: missing={sorted(missing)} unexpected={sorted(extra)}"
        raise ValueError(msg)
    return value


def _require_optional_str(value: Any, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        msg = f"{name} must be a non-empty str or None, got {value!r}"
        raise ValueError(msg)


@dataclass(frozen=True)
class IdentityInputs:
    """Everything the run identity keys depend on; built by ``study/plan.py`` from a ResolvedCell.

    * ``task`` (app): ``{"structured_hash", "variant", "prose_sha"}``; ``target`` (slot): ``{"target_hash"}``.
      Reference runs (``ref@1``) carry exactly one of the two.
    * ``treatment``: exactly :data:`TREATMENT_KEYS`, already pinned (content hashes, enum values, ``None``).
    * ``stimuli``: ``[(role, text_sha(rendered))]``, one per role.
    * ``model``: ``{"slug", "endpoint_kind", "pinned_digest"}``.
    * ``images``: ``{"harness": digest | None, "gateway": digest, "starter_base": [digests]}``;
      ``gateway`` may be ``None`` only for kinds without a gateway sidecar (``slot_single_shot``, ``reference``).
    """

    protocol: str
    kind: str
    task: Mapping[str, Any] | None
    target: Mapping[str, Any] | None
    treatment: Mapping[str, Any]
    stimuli: Sequence[tuple[str, str]]
    model: Mapping[str, Any]
    sampling_hash: str
    policy_treatment_view: Mapping[str, Any]
    images: Mapping[str, Any]
    seed: int

    def __post_init__(self) -> None:
        self._validate_kind()
        self._validate_unit()
        self._validate_treatment()
        self._validate_rest()

    def _validate_kind(self) -> None:
        if self.protocol not in PROTOCOLS:
            msg = f"unknown protocol {self.protocol!r}; expected one of {PROTOCOLS}"
            raise ValueError(msg)
        if self.kind not in KIND_PROTOCOL:
            msg = f"unknown run kind {self.kind!r}; expected one of {RUN_KINDS}"
            raise ValueError(msg)
        if KIND_PROTOCOL[self.kind] != self.protocol:
            msg = f"kind {self.kind!r} runs under protocol {KIND_PROTOCOL[self.kind]!r}, not {self.protocol!r}"
            raise ValueError(msg)

    def _validate_unit(self) -> None:
        if self.task is not None:
            task = _require_exact_keys(self.task, TASK_KEYS, "IdentityInputs.task")
            _require_sha(task["structured_hash"], "task.structured_hash")
            _require_sha(task["prose_sha"], "task.prose_sha")
            if not isinstance(task["variant"], str) or not task["variant"]:
                msg = f"task.variant must be a non-empty str, got {task['variant']!r}"
                raise ValueError(msg)
        if self.target is not None:
            target = _require_exact_keys(self.target, TARGET_KEYS, "IdentityInputs.target")
            _require_sha(target["target_hash"], "target.target_hash")
        has_task, has_target = self.task is not None, self.target is not None
        if self.protocol == "app@1" and not (has_task and not has_target):
            msg = "protocol app@1 requires task and no target"
            raise ValueError(msg)
        if self.protocol == "slot@1" and not (has_target and not has_task):
            msg = "protocol slot@1 requires target and no task"
            raise ValueError(msg)
        if self.protocol == "ref@1" and has_task == has_target:
            msg = "protocol ref@1 requires exactly one of task or target"
            raise ValueError(msg)

    def _validate_treatment(self) -> None:
        treatment = _require_exact_keys(self.treatment, TREATMENT_KEYS, "IdentityInputs.treatment")
        for key in ("stack", "starter", "style", "guidance", "prompt_set", "harness_preset", "allowed_bases_sha"):
            value = treatment[key]
            if value is not None:
                _require_sha(value, f"treatment.{key}")
        _require_optional_str(treatment["auth_mode"], "treatment.auth_mode")
        if treatment["gateway"] is not None:
            gateway = _require_exact_keys(treatment["gateway"], GATEWAY_KEYS, "treatment.gateway")
            for key in GATEWAY_KEYS:
                _require_optional_str(gateway[key], f"treatment.gateway.{key}")
        slot_policy = treatment["slot_policy"]
        if slot_policy is not None:
            _require_exact_keys(slot_policy, SLOT_POLICY_KEYS, "treatment.slot_policy")
        if self.protocol == "slot@1" and slot_policy is None:
            msg = "protocol slot@1 requires treatment.slot_policy"
            raise ValueError(msg)
        if self.protocol == "app@1" and slot_policy is not None:
            msg = "protocol app@1 must not carry treatment.slot_policy"
            raise ValueError(msg)

    def _validate_rest(self) -> None:
        _validate_stimuli(self.stimuli)
        model = _require_exact_keys(self.model, MODEL_KEYS, "IdentityInputs.model")
        if not isinstance(model["slug"], str) or not model["slug"]:
            msg = f"model.slug must be a non-empty str, got {model['slug']!r}"
            raise ValueError(msg)
        _require_optional_str(model["endpoint_kind"], "model.endpoint_kind")
        _require_optional_str(model["pinned_digest"], "model.pinned_digest")
        _require_sha(self.sampling_hash, "sampling_hash")
        if not isinstance(self.policy_treatment_view, Mapping):
            msg = "policy_treatment_view must be a dict"
            raise TypeError(msg)
        images = _require_exact_keys(self.images, IMAGE_KEYS, "IdentityInputs.images")
        _require_optional_str(images["harness"], "images.harness")
        _require_optional_str(images["gateway"], "images.gateway")
        if images["gateway"] is None and self.kind in _SIDECAR_KINDS:
            # Single-shot slot calls apply the gateway policy in-process (section 7.6): no gateway image.
            msg = f"images.gateway is required for kind {self.kind!r}"
            raise ValueError(msg)
        if not isinstance(images["starter_base"], (list, tuple)) or not all(
            isinstance(digest, str) and digest for digest in images["starter_base"]
        ):
            msg = f"images.starter_base must be a list of digest strs, got {images['starter_base']!r}"
            raise ValueError(msg)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            msg = f"seed must be a non-negative int, got {self.seed!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class IdentityKeys:
    """The six run identity keys; frozen in the RunManifest and copied to indexed GenerationRun columns."""

    task_hash: str
    treatment_hash: str
    prompt_hash: str
    generation_policy_hash: str
    config_fingerprint: str
    run_fingerprint: str

    def as_dict(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def compute_keys(inputs: IdentityInputs) -> IdentityKeys:
    """Compute every run identity key from ``inputs`` (pure; the only implementation of section 3.1)."""
    if inputs.target is not None:
        task_hash = H({"protocol": inputs.protocol, "target": inputs.target["target_hash"]})
    else:
        task = inputs.task or {}
        task_hash = H(
            {
                "protocol": inputs.protocol,
                "task": task["structured_hash"],
                "variant": task["variant"],
                "prose": task["prose_sha"],
            },
        )
    treatment_hash = H({"protocol": inputs.protocol, "executor": inputs.kind, **dict(inputs.treatment)})
    prompt_hash = _prompt_hash(inputs.stimuli)
    generation_policy_hash = H(inputs.policy_treatment_view)
    model = inputs.model
    images = inputs.images
    config_fingerprint = H(
        {
            "protocol": inputs.protocol,
            "task_hash": task_hash,
            "treatment_hash": treatment_hash,
            "prompt_hash": prompt_hash,
            "model": {
                "slug": model["slug"],
                "endpoint_kind": model["endpoint_kind"],
                "pinned_digest": model["pinned_digest"],
            },
            "sampling": inputs.sampling_hash,
            "policy": generation_policy_hash,
            "images": {
                "harness": images["harness"],
                "gateway": images["gateway"],
                "starter_base": list(images["starter_base"]),
            },
        },
    )
    run_fingerprint = H({"config": config_fingerprint, "seed": inputs.seed})
    return IdentityKeys(
        task_hash=task_hash,
        treatment_hash=treatment_hash,
        prompt_hash=prompt_hash,
        generation_policy_hash=generation_policy_hash,
        config_fingerprint=config_fingerprint,
        run_fingerprint=run_fingerprint,
    )
