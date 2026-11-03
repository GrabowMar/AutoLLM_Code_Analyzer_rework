"""Dockerfile templates, base image digest pinning and the free-choice allowlist (spec sections 6.10, 11.4).

* ``data/starters/base_images.yaml`` pins every base image a starter template may use (``base_images()``).
  Templates write ``{{ base.python }}``, which renders ``python:3.13-slim@sha256:...``.
* ``data/starters/allowed_bases.yaml`` (or ``settings.SAMPLE_BUILD_ALLOWED_BASES``) is the free-choice
  allowlist. Its digest-pinned refs (``ref@sha256:...``, ``allowed_bases_record()["refs"]``) are rendered
  into the free-choice brief (fragment ``run-contract-free@1``) and frozen in ``RunManifest.allowed_bases``;
  ``allowed_bases_record()["sha"]`` is the ``allowed_bases_sha`` of ``treatment_hash``.
* A model may still write a tag-only ``FROM python:3.13-slim``. The build never re-resolves the tag: the
  check returns ``BaseCheck.pins`` (``{image:tag: ref@digest}``), which the build passes to BuildKit as
  named contexts (``--build-context python:3.13-slim=docker-image://python:3.13-slim@sha256:...``,
  ``build_context_args``), so every build and re-evaluation uses the digest frozen at generation time.
* ``parse_from_lines`` / ``check_bases`` read the ``FROM`` lines of a Dockerfile as written. The platform
  never rewrites a model's Dockerfile: a ``FROM`` outside the allowlist is a measured instruction-following
  failure (outcome ``build_fail``, reason ``base_not_allowed``), not something to repair.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Final

import yaml

from backend.generation import identity

__all__ = [
    "BASE_IMAGES_PATH",
    "DEFAULT_ALLOWED_BASES_PATH",
    "STARTERS_ROOT",
    "AllowedBase",
    "BaseCheck",
    "BaseImageSpec",
    "FromLine",
    "allowed_bases",
    "allowed_bases_from_refs",
    "allowed_bases_path",
    "allowed_bases_record",
    "base_images",
    "build_context_args",
    "check_bases",
    "lock_tools",
    "normalize_image",
    "parse_from_lines",
    "pinned_bases",
]

STARTERS_ROOT: Final = Path(__file__).resolve().parent.parent / "data" / "starters"
BASE_IMAGES_PATH: Final = STARTERS_ROOT / "base_images.yaml"
DEFAULT_ALLOWED_BASES_PATH: Final = STARTERS_ROOT / "allowed_bases.yaml"

_DIGEST_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCKER_HUB_PREFIXES: Final = ("docker.io/", "index.docker.io/", "registry-1.docker.io/")
_AS_ARGS: Final = 3  # FROM <image> AS <name>


# =====================================================================================
# Base images and the allowlist
# =====================================================================================


@dataclass(frozen=True)
class BaseImageSpec:
    """One pinned base image: ``ref`` (``image:tag``) and its ``digest`` (``sha256:<64 hex>``)."""

    key: str
    ref: str
    digest: str

    @property
    def pinned(self) -> str:
        """``<ref>@<digest>``: what a template's ``FROM`` renders and what ``StarterContent.base_images`` keeps."""
        return f"{self.ref}@{self.digest}"


@dataclass(frozen=True)
class AllowedBase:
    """One allowlisted free-choice base image."""

    ref: str
    digest: str

    @property
    def pinned(self) -> str:
        return f"{self.ref}@{self.digest}"


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        msg = f"{path} must hold a mapping"
        raise TypeError(msg)
    return data


def _checked_digest(digest: Any, where: str) -> str:
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        msg = f"{where}: digest must be sha256:<64 hex>, got {digest!r}"
        raise ValueError(msg)
    return digest


def base_images(path: Path | None = None) -> dict[str, BaseImageSpec]:
    """The pinned base images by template key (``python``, ``node``, ...)."""
    path = path or BASE_IMAGES_PATH
    images = _load_yaml(path).get("images") or {}
    out: dict[str, BaseImageSpec] = {}
    for key, row in images.items():
        ref = normalize_image(str(row["ref"]))[0]
        out[str(key)] = BaseImageSpec(str(key), ref, _checked_digest(row.get("digest"), f"{path.name}:{key}"))
    return out


def lock_tools(path: Path | None = None) -> dict[str, str]:
    """Tool versions the lock step installs inside a base image (``tools`` in base_images.yaml)."""
    return {str(k): str(v) for k, v in (_load_yaml(path or BASE_IMAGES_PATH).get("tools") or {}).items()}


def allowed_bases_path() -> Path:
    """The allowlist file: ``settings.SAMPLE_BUILD_ALLOWED_BASES`` when set (read at call time), else the default."""
    from django.conf import settings

    configured = getattr(settings, "SAMPLE_BUILD_ALLOWED_BASES", "") or ""
    return Path(configured) if configured else DEFAULT_ALLOWED_BASES_PATH


def allowed_bases(path: Path | None = None) -> list[AllowedBase]:
    """The free-choice allowlist, in file order (the order the brief renders)."""
    path = path or allowed_bases_path()
    rows = _load_yaml(path).get("bases") or []
    out: list[AllowedBase] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        ref = normalize_image(str(row["ref"]))[0]
        if ref in seen:
            msg = f"{path.name}: duplicate allowed base {ref}"
            raise ValueError(msg)
        seen.add(ref)
        out.append(AllowedBase(ref, _checked_digest(row.get("digest"), f"{path.name}[{index}]")))
    if not out:
        msg = f"{path} lists no allowed base images"
        raise ValueError(msg)
    return out


def _allowed_sha(bases: list[AllowedBase]) -> str:
    return identity.H([{"ref": base.ref, "digest": base.digest} for base in bases])


def allowed_bases_record(path: Path | None = None) -> dict[str, Any]:
    """``{"sha", "refs", "tags"}``; ``sha`` + ``refs`` are ``RunManifest.allowed_bases``.

    ``refs`` are the **digest-pinned** references (``python:3.13-slim@sha256:...``) in file order: they are
    rendered into the brief and frozen in the manifest, so a re-evaluation builds against the same digests
    (``allowed_bases_from_refs``). ``tags`` are the bare ``image:tag`` names (display only). The sha commits
    to every ref **and** its digest, in the rendered order: a digest bump of an allowed base is a different
    treatment, like a change of the list.
    """
    bases = allowed_bases(path)
    return {
        "sha": _allowed_sha(bases),
        "refs": [base.pinned for base in bases],
        "tags": [base.ref for base in bases],
    }


def allowed_bases_from_refs(refs: list[str], *, sha: str | None = None) -> list[AllowedBase]:
    """The allowlist frozen in a manifest (``RunManifest.allowed_bases.refs``, digest-pinned refs).

    Raises ``ValueError`` when a ref carries no digest (a manifest written before refs were pinned cannot be
    rebuilt without re-resolving a tag) or when ``sha`` is given and does not match the refs.
    """
    out: list[AllowedBase] = []
    for index, raw in enumerate(refs):
        ref, digest = normalize_image(str(raw))
        out.append(AllowedBase(ref, _checked_digest(digest, f"allowed_bases.refs[{index}]")))
    if not out:
        msg = "the frozen allowlist is empty"
        raise ValueError(msg)
    if sha is not None and _allowed_sha(out) != sha:
        msg = "the frozen allowlist refs do not match its sha"
        raise ValueError(msg)
    return out


# =====================================================================================
# FROM lines
# =====================================================================================


def normalize_image(image: str) -> tuple[str, str | None]:
    """``(image:tag, digest | None)`` with Docker Hub prefixes and ``library/`` removed and ``latest`` filled.

    ``docker.io/library/python:3.13-slim@sha256:ab..`` -> ``("python:3.13-slim", "sha256:ab..")``.
    """
    name, _, digest = image.strip().partition("@")
    for prefix in _DOCKER_HUB_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    name = name.removeprefix("library/")
    if ":" not in name.rsplit("/", 1)[-1]:
        name = f"{name}:latest"
    return name, (digest or None)


@dataclass(frozen=True)
class FromLine:
    """One ``FROM`` instruction of a Dockerfile."""

    line: int  # 1-based line of the instruction
    image: str  # as written (the --platform flag left out)
    stage: str | None  # the ``AS <name>`` alias
    ref: str | None  # normalized ``image:tag``; None for a stage reference or an unresolvable image
    digest: str | None  # ``sha256:...`` when the FROM pins one
    is_stage_ref: bool = False  # FROM <earlier stage>
    unresolvable: bool = False  # uses a build ARG (``$VAR``) or names no image: it cannot be checked statically


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Dockerfile logical lines (continuations joined, comments dropped) with their first line number."""
    out: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = 0
    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        stripped = raw.strip()
        if stripped.startswith("#") or (not buffer and not stripped):
            continue  # comments (also inside a continuation) and blank lines
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer.append(stripped[:-1].strip())
            continue
        buffer.append(stripped)
        out.append((start, " ".join(part for part in buffer if part)))
        buffer = []
    if buffer:
        out.append((start, " ".join(part for part in buffer if part)))
    return out


def parse_from_lines(text: str) -> list[FromLine]:
    """Every ``FROM`` of ``text`` in order; earlier ``AS`` names are recognised as stage references."""
    stages: set[str] = set()
    out: list[FromLine] = []
    for number, logical in _logical_lines(text):
        parts = logical.split()
        if not parts or parts[0].upper() != "FROM":
            continue
        args = [part for part in parts[1:] if not part.startswith("--")]
        if not args:
            out.append(FromLine(number, "", None, None, None, unresolvable=True))
            continue
        image = args[0]
        stage = args[2] if len(args) >= _AS_ARGS and args[1].upper() == "AS" else None
        if image.lower() in stages:
            out.append(FromLine(number, image, stage, None, None, is_stage_ref=True))
        elif "$" in image:
            out.append(FromLine(number, image, stage, None, None, unresolvable=True))
        else:
            ref, digest = normalize_image(image)
            out.append(FromLine(number, image, stage, ref, digest))
        if stage:
            stages.add(stage.lower())
    return out


def pinned_bases(text: str) -> list[dict[str, str]]:
    """``[{"ref", "digest"}]`` for ``StarterContent.base_images``: every external FROM, deduplicated, in order.

    ``digest`` is the full pinned reference (``ref@sha256:...``). Raises ``ValueError`` for a FROM without a
    digest: starter templates are always digest-pinned.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in parse_from_lines(text):
        if line.is_stage_ref:
            continue
        if line.ref is None or line.digest is None:
            msg = f"line {line.line}: FROM {line.image!r} is not digest-pinned"
            raise ValueError(msg)
        pinned = f"{line.ref}@{line.digest}"
        if pinned not in seen:
            seen.add(pinned)
            out.append({"ref": line.ref, "digest": pinned})
    return out


@dataclass
class BaseCheck:
    """The allowlist verdict for one Dockerfile (recorded in ``RunEvaluation.stages.build.detail``)."""

    ok: bool
    bases: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    allowed_bases_sha: str | None = None
    # {familiar image:tag of a tag-only FROM: allowlisted ref@digest}: the BuildKit named contexts that pin it.
    # BuildKit looks a FROM up by its familiar name (docker.io/library/ removed), which is ``FromLine.ref``.
    pins: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_bases(text: str, allowed: list[AllowedBase] | None = None) -> BaseCheck:
    """Check every ``FROM`` of ``text`` against the allowlist.

    A FROM passes when it refers to an earlier stage, or when its ``image:tag`` is allowlisted and it pins
    either no digest or exactly the allowlisted one. Each external FROM is recorded with the digest the
    allowlist expects (``expected_digest``). A tag-only FROM is pinned at build time, never re-resolved:
    ``pins[image] = ref@digest`` (see ``build_context_args``). A Dockerfile without any FROM is a violation
    too (nothing can be built from it).
    """
    allowed = allowed if allowed is not None else allowed_bases()
    by_ref = {base.ref: base for base in allowed}
    check = BaseCheck(ok=True)
    lines = parse_from_lines(text)
    if not lines:
        check.ok = False
        check.violations.append({"line": 0, "image": "", "reason": "no_from"})
    for line in lines:
        if line.is_stage_ref:
            check.bases.append({"line": line.line, "image": line.image, "stage_ref": True, "allowed": True})
            continue
        if line.unresolvable or line.ref is None:
            check.ok = False
            check.violations.append({"line": line.line, "image": line.image, "reason": "unresolvable"})
            continue
        base = by_ref.get(line.ref)
        record: dict[str, Any] = {
            "line": line.line,
            "image": line.image,
            "ref": line.ref,
            "digest": line.digest,
            "expected_digest": base.digest if base else None,
            "allowed": False,
        }
        if base is None:
            check.violations.append({**record, "reason": "not_allowlisted"})
        elif line.digest is not None and line.digest != base.digest:
            check.violations.append({**record, "reason": "digest_mismatch"})
        else:
            record["allowed"] = True
            record["pinned"] = base.pinned
            if line.digest is None:
                check.pins[line.ref] = base.pinned
        check.bases.append(record)
        check.ok = check.ok and bool(record["allowed"])
    return check


def build_context_args(pins: dict[str, str]) -> list[str]:
    """``docker buildx build`` arguments that pin tag-only FROMs: ``--build-context <image>=docker-image://<ref@digest>``.

    BuildKit replaces every ``FROM <image>`` with the named context, so the tag is never resolved against the
    registry (the Dockerfile itself is not rewritten).
    """
    args: list[str] = []
    for image, pinned in sorted(pins.items()):
        args += ["--build-context", f"{image}=docker-image://{pinned}"]
    return args
