"""Profiles and frozen plans (owner: executor stage).

Profile config (validated, stored on AnalysisProfile.config)::

    {
      "tools":    [{"slug": "ruff", "required": true, "config": {...}}, ...],
      "reviewer": {"enabled": bool, "required": bool, "engine": "single_shot"|"agent",
                   "harness": null|"opencode"|"mini-swe-agent"|"aider", "harness_options": {},
                   "model": "deepseek/deepseek-v4.1-flash", "samples": 3, "temperature": 0.2,
                   "reasoning_effort": null|"none"|"minimal"|"low"|"medium"|"high"|"max",
                   "max_output_tokens": 32000, "provider": null|{...routing...},
                   "prompt_version": "review-v1", "schema_version": "findings-v1",
                   "include_origins": ["authored", "added", "modified"],
                   "include_kinds": ["source", "config", "manifest", "env"],
                   "chunk_char_budget": 300000, "seed_base": 1,
                   "budget": {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900}},
      "scope":    {"measured_origins": ["authored", "added", "modified"], "measured_kinds": ["source"],
                   "line_attribution": true, "exclude_globs": [...], "exclude_kinds": [...],
                   "max_file_bytes": 524288},
      "outcomes": {"severity_weights": {...}, "count_style": false, "count_metric_findings": false,
                   "min_sloc_for_density": 50, "max_unparseable_share": 0.1}
    }

Reviewer keys are validated by ``reviewer.config`` (its docstring has the
details): ``reasoning_effort`` is OpenRouter's vocabulary (null = the
model's own default) and is checked against the model's supported efforts
at freeze time; ``max_output_tokens`` bounds reasoning plus answer (32k by
default: reasoning models truncate at 16k); ``seed_base`` seeds the
per-replicate file order. Scope defaults are ``sample.DEFAULT_SCOPE``:
counted findings and measured SLOC cover included files whose origin is in
``measured_origins`` and whose kind is in ``measured_kinds`` (default
``["source"]``; a study may add e.g. ``config`` / ``env`` to count
model-written configuration).

A frozen plan (AnalysisRun.plan / AnalysisStudy.plan) resolves everything a
run depends on — tool versions + spec/config hashes, analyzer image tag, the
reviewer's resolved model (canonical slug, pricing snapshot, capabilities),
prompt/schema hashes, harness version, mapping hashes and engine stage
versions — so two runs are comparable iff their plan_hash is equal.

``plan_hash`` covers the analysis procedure only. It leaves out provenance
that does not change what is measured: the profile the plan came from, the
freeze timestamp / image id (``provenance``), diagnostics (``notes``), where a
tool version was read from, a tool's display name, the tools' display order
(execution order does not change any result: every tool scans the same
read-only sample) and the reviewer model's pricing snapshot (cost, not
findings). An identical config frozen from two profiles is comparable.

A frozen plan only means something if runs execute the procedure it froze:
:func:`procedure_drift` compares a plan's tool ``spec_hash`` es, engine stage
versions and mapping hashes with the running code, and the executor refuses
to measure under a plan whose procedure the running code no longer
implements (the static counterpart of the reviewer's prompt/schema check).

When the AI reviewer is enabled, it must be shown every file the scope
measures (``reviewer.include_origins`` ⊇ ``scope.measured_origins``,
``reviewer.include_kinds`` ⊇ ``scope.measured_kinds``): the AI consensus
density divides by the measured SLOC, so code the reviewer never saw would
sit in its denominator only (contract rule 3).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import UTC
from datetime import datetime
from typing import Any

from django.db import IntegrityError
from django.db import transaction
from django.utils.text import slugify

from backend.analysis.engine import catalog
from backend.analysis.engine import normalize
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine import types
from backend.analysis.engine.catalog import AI_TOOL_SLUG
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.reviewer import config as reviewer_config
from backend.analysis.engine.reviewer import prompt as reviewer_prompt

logger = logging.getLogger(__name__)

PLAN_SCHEMA_VERSION = 1
DEFAULT_PROFILE_SLUG = "research-default"

PROFILE_KEYS = ("tools", "reviewer", "scope", "outcomes")
DEFAULT_OUTCOMES: dict[str, Any] = {
    # Only feed the secondary "weighted" score; densities count issues, not weights.
    "severity_weights": {"critical": 10.0, "high": 5.0, "medium": 2.0, "low": 1.0, "info": 0.0},
    # Style findings and metric findings (complexity ranks, clones) are not
    # counted as issues unless a profile opts in (normalize decides `counted`).
    "count_style": False,
    "count_metric_findings": False,
    "min_sloc_for_density": 50,
    # Static outcomes are NA when more than this share of the measured SLOC is
    # in files a static tool could not parse (measurement.DEFAULT_OUTCOME_CONFIG
    # has the same default): frozen so the data-quality rule is part of the plan.
    "max_unparseable_share": 0.1,
}
FILE_KINDS = ("source", "config", "manifest", "lockfile", "doc", "asset", "build", "env", "other")
MAX_FILE_BYTES_RANGE = (1024, 16 * 1024 * 1024)
MAX_MIN_SLOC = 1_000_000

# Keys of a frozen plan that are provenance or presentation, not procedure (see module docstring).
_UNHASHED_PLAN_KEYS = ("profile", "notes", "provenance")
_UNHASHED_TOOL_KEYS = ("version_source", "name")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


# --------------------------------------------------------------------------
# Profile config
# --------------------------------------------------------------------------


def default_profile_config() -> dict[str, Any]:
    """The research default: every default-enabled static tool, the default reviewer, default scope/outcomes."""
    return {
        "tools": [
            {"slug": spec.slug, "required": spec.default_required, "config": copy.deepcopy(spec.default_config)}
            for spec in catalog.static_specs()
            if spec.default_enabled
        ],
        "reviewer": reviewer_config.default_reviewer_config(),
        "scope": copy.deepcopy(sample_mod.DEFAULT_SCOPE),
        "outcomes": copy.deepcopy(DEFAULT_OUTCOMES),
    }


def validate_profile_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize + validate (raises catalog.ConfigError with dotted field paths).

    Every problem is collected before raising so an editor can mark all
    fields at once. Tools are returned in catalog display order with their
    effective (defaults-merged) config, so the same selection always hashes
    the same regardless of the order it was submitted in.
    """
    if not isinstance(config, dict):
        raise ConfigError({"config": "must be an object"})
    errors: dict[str, str] = {}
    for key in config:
        if key not in PROFILE_KEYS:
            errors[str(key)] = "unknown field"
    tools = _validate_tools(config.get("tools", []), errors)
    reviewer = _validate_reviewer(config.get("reviewer"), errors)
    scope = _validate_scope(config.get("scope"), errors)
    outcomes = _validate_outcomes(config.get("outcomes"), errors)
    if not tools and not (reviewer or {}).get("enabled"):
        errors.setdefault("tools", "select at least one tool or enable the AI reviewer")
    _check_review_scope(reviewer, scope, errors)
    if errors:
        raise ConfigError(errors)
    return {"tools": tools, "reviewer": reviewer, "scope": scope, "outcomes": outcomes}


def _validate_tools(raw: Any, errors: dict[str, str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors["tools"] = "must be a list"
        return []
    specs = catalog.load_specs()
    seen: set[str] = set()
    tools: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for i, item in enumerate(raw):
        entry = {"slug": item} if isinstance(item, str) else item
        path = f"tools.{i}"
        if not isinstance(entry, dict):
            errors[path] = "must be an object with a slug"
            continue
        unknown = set(entry) - {"slug", "required", "config", "enabled"}
        if unknown:
            errors[f"{path}.{sorted(unknown)[0]}"] = "unknown field"
            continue
        slug = entry.get("slug")
        spec = specs.get(slug) if isinstance(slug, str) else None
        if spec is None:
            errors[f"{path}.slug"] = f"unknown tool {slug!r}"
            continue
        if spec.kind == "ai" or slug == AI_TOOL_SLUG:
            errors[f"{path}.slug"] = "the AI reviewer is configured under 'reviewer', not as a tool"
            continue
        if slug in seen:
            errors[f"{path}.slug"] = f"duplicate tool {slug!r}"
            continue
        seen.add(slug)
        enabled = entry.get("enabled", True)
        required = entry.get("required", spec.default_required)
        if not isinstance(enabled, bool):
            errors[f"{path}.enabled"] = "must be a boolean"
            continue
        if not isinstance(required, bool):
            errors[f"{path}.required"] = "must be a boolean"
            continue
        tool_config = entry.get("config") or {}
        if not isinstance(tool_config, dict):
            errors[f"{path}.config"] = "must be an object"
            continue
        try:
            effective = catalog.validate_tool_config(slug, tool_config)
        except ConfigError as exc:
            for field, message in exc.field_errors.items():
                field_path = str(field).removeprefix("config.")
                errors[f"{path}.config.{field_path}" if field_path else f"{path}.config"] = message
            continue
        if enabled:
            tools.append(((spec.display_order, slug), {"slug": slug, "required": required, "config": effective}))
    return [tool for _key, tool in sorted(tools, key=lambda item: item[0])]


def _validate_reviewer(raw: Any, errors: dict[str, str]) -> dict[str, Any]:
    if raw is not None and not isinstance(raw, dict):
        errors["reviewer"] = "must be an object"
        return {"enabled": False, "required": False}
    if raw is not None and raw.get("enabled") is False:
        # A disabled reviewer never runs: keep its settings when they are
        # valid (so re-enabling restores them) but never reject the profile
        # over them (e.g. a model that left the catalog).
        try:
            normalized = reviewer_config.validate_reviewer_config(raw)
        except ConfigError:
            return {"enabled": False, "required": False}
        return {**normalized, "enabled": False, "required": False}
    try:
        return reviewer_config.validate_reviewer_config(raw)
    except ConfigError as exc:
        for field, message in exc.field_errors.items():
            key = str(field)
            errors[key if key.startswith("reviewer") else f"reviewer.{key}"] = message
        return {"enabled": False, "required": False}


def _check_review_scope(reviewer: dict[str, Any], scope: dict[str, Any], errors: dict[str, str]) -> None:
    """An enabled reviewer must be shown every measured file (see module docstring).

    ``ai_consensus_per_kloc`` divides the counted consensus findings by the
    measured SLOC; measured code the reviewer is never shown could only ever
    add to the denominator, deflating the density by an app-dependent factor.
    """
    if not (reviewer or {}).get("enabled"):
        return
    checks = (
        ("origin", "measured_origins", "include_origins", reviewer_prompt.ORIGIN_CHOICES),
        ("kind", "measured_kinds", "include_kinds", reviewer_prompt.KIND_CHOICES),
    )
    for noun, measured_key, shown_key, choices in checks:
        measured = scope.get(measured_key)
        shown = reviewer.get(shown_key)
        if not isinstance(measured, list) or not isinstance(shown, list):
            continue
        unshowable = [value for value in measured if value not in choices]
        missing = [value for value in measured if value in choices and value not in shown]
        if unshowable:
            errors.setdefault(
                f"scope.{measured_key}",
                f"{unshowable[0]!r} files cannot be shown to the AI reviewer, so they cannot be measured while it "
                "is enabled (its consensus density divides by the measured lines)",
            )
        if missing:
            errors.setdefault(
                f"reviewer.{shown_key}",
                f"must include every measured {noun} (scope.{measured_key}); missing "
                f"{', '.join(repr(value) for value in missing)}: the AI consensus density divides by the measured "
                "lines, so the reviewer must be shown all of them",
            )


def _validate_scope(raw: Any, errors: dict[str, str]) -> dict[str, Any]:
    from backend.analysis.models import FileOrigin

    defaults = sample_mod.DEFAULT_SCOPE
    if raw is None:
        return copy.deepcopy(defaults)
    if not isinstance(raw, dict):
        errors["scope"] = "must be an object"
        return copy.deepcopy(defaults)
    scope = copy.deepcopy(defaults)
    for key, value in raw.items():
        path = f"scope.{key}"
        if key not in defaults:
            errors[path] = "unknown field"
            continue
        default = defaults[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                errors[path] = "must be a boolean"
                continue
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                errors[path] = "must be an integer"
                continue
        elif isinstance(default, list):
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                errors[path] = "must be a list of non-empty strings"
                continue
            value = list(dict.fromkeys(v.strip() for v in value))  # noqa: PLW2901 - normalized in place
        scope[key] = value
    origins = scope.get("measured_origins")
    if isinstance(origins, list):
        bad = [o for o in origins if o not in FileOrigin.values]
        if bad:
            errors["scope.measured_origins"] = f"unknown origin {bad[0]!r}"
        elif not origins:
            errors["scope.measured_origins"] = "at least one origin must be measured"
    kinds = scope.get("exclude_kinds")
    if isinstance(kinds, list):
        bad = [k for k in kinds if k not in FILE_KINDS]
        if bad:
            errors["scope.exclude_kinds"] = f"unknown file kind {bad[0]!r}"
        elif "source" in kinds:
            errors["scope.exclude_kinds"] = "source files cannot be excluded by kind"
    measured_kinds = scope.get("measured_kinds")
    if isinstance(measured_kinds, list):
        bad = [k for k in measured_kinds if k not in FILE_KINDS]
        excluded = [k for k in measured_kinds if isinstance(kinds, list) and k in kinds]
        if bad:
            errors["scope.measured_kinds"] = f"unknown file kind {bad[0]!r}"
        elif not measured_kinds:
            errors["scope.measured_kinds"] = "at least one file kind must be measured"
        elif excluded:
            # Excluded files never reach the tools, so they could never be measured.
            errors["scope.measured_kinds"] = f"{excluded[0]!r} files are excluded (scope.exclude_kinds)"
    max_bytes = scope.get("max_file_bytes")
    if isinstance(max_bytes, int) and not MAX_FILE_BYTES_RANGE[0] <= max_bytes <= MAX_FILE_BYTES_RANGE[1]:
        errors["scope.max_file_bytes"] = f"must be between {MAX_FILE_BYTES_RANGE[0]} and {MAX_FILE_BYTES_RANGE[1]}"
    return scope


def _validate_outcomes(raw: Any, errors: dict[str, str]) -> dict[str, Any]:
    outcomes = copy.deepcopy(DEFAULT_OUTCOMES)
    if raw is None:
        return outcomes
    if not isinstance(raw, dict):
        errors["outcomes"] = "must be an object"
        return outcomes
    for key, value in raw.items():
        path = f"outcomes.{key}"
        if key == "severity_weights":
            if not isinstance(value, dict):
                errors[path] = "must be an object"
                continue
            weights = dict(outcomes["severity_weights"])
            for severity, weight in value.items():
                if severity not in types.SEVERITIES:
                    errors[f"{path}.{severity}"] = "unknown severity"
                elif isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
                    errors[f"{path}.{severity}"] = "must be a number >= 0"
                else:
                    weights[severity] = float(weight)
            outcomes["severity_weights"] = weights
        elif key in ("count_style", "count_metric_findings"):
            if not isinstance(value, bool):
                errors[path] = "must be a boolean"
            else:
                outcomes[key] = value
        elif key == "max_unparseable_share":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                errors[path] = "must be a number between 0 and 1"
            else:
                outcomes[key] = float(value)
        elif key == "min_sloc_for_density":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_MIN_SLOC:
                errors[path] = f"must be an integer between 0 and {MAX_MIN_SLOC}"
            else:
                outcomes[key] = value
        else:
            errors[path] = "unknown field"
    return outcomes


def profile_content_hash(config: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of a (normalized) profile config."""
    return _sha256(config)


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


def _unique_slug(base: str) -> str:
    from backend.analysis.models import AnalysisProfile

    root = (slugify(base) or "profile")[:90]
    slug = root
    n = 2
    while AnalysisProfile.objects.filter(slug=slug).exists():
        slug = f"{root}-{n}"
        n += 1
    return slug


def save_profile(
    *,
    user,
    name: str,
    description: str,
    config: dict[str, Any],
    slug: str | None = None,
    base=None,
    is_system: bool = False,
):
    """Create a profile, or version+1 of *base* when the content changed.

    Content is immutable per version (runs and studies reference the row as
    provenance): saving *base* with identical normalized content only updates
    its name/description and returns it unchanged in version; different
    content creates a new row ``(slug, latest version + 1)``.
    """
    from backend.analysis.models import AnalysisProfile

    normalized = validate_profile_config(config)
    content_hash = profile_content_hash(normalized)
    name = (name or "").strip()[:200] or (base.name if base is not None else "Profile")
    description = description or ""
    if base is not None and base.content_hash == content_hash:
        updates = {"name": name, "description": description}
        changed = [field for field, value in updates.items() if getattr(base, field) != value]
        if changed:
            for field in changed:
                setattr(base, field, updates[field])
            base.save(update_fields=[*changed, "updated_at"])
        return base
    for _attempt in range(3):
        try:
            with transaction.atomic():
                if base is not None:
                    profile_slug = base.slug
                    latest = (
                        AnalysisProfile.objects.select_for_update()
                        .filter(slug=profile_slug)
                        .order_by("-version")
                        .first()
                    )
                    version = (latest.version if latest else base.version) + 1
                else:
                    profile_slug = _unique_slug(slug or name)
                    version = 1
                return AnalysisProfile.objects.create(
                    slug=profile_slug,
                    version=version,
                    name=name,
                    description=description,
                    config=normalized,
                    content_hash=content_hash,
                    is_system=is_system,
                    created_by=user,
                )
        except IntegrityError:
            # A concurrent save took this (slug, version); recompute and retry.
            logger.info("Profile slug/version race on %s; retrying", slug or name)
    msg = "Could not allocate a profile version (concurrent edits); try again"
    raise ConfigError({"slug": msg})


# --------------------------------------------------------------------------
# Frozen plans
# --------------------------------------------------------------------------


def _image_identity(notes: list[dict[str, str]]) -> tuple[str, str, dict[str, Any] | None]:
    """(tag, image id, manifest) of the analyzer image; id/manifest empty when not built / unreachable."""
    try:
        tag = sandbox.image_tag()
    except sandbox.SandboxError as exc:
        notes.append({"code": "image_definition_missing", "detail": str(exc)})
        return "", "", None
    try:
        _tag, image_id = sandbox.ensure_image(expect_tag=tag, build=False)
        manifest = sandbox.image_manifest(image_id)
    except sandbox.SandboxError as exc:
        notes.append({"code": "image_unavailable", "detail": str(exc)})
        return tag, "", None
    return tag, image_id, manifest


def freeze_plan(
    *,
    profile=None,
    config: dict[str, Any] | None = None,
    user: Any = reviewer_config.ANY_USER,
) -> dict[str, Any]:
    """Resolve a profile (or raw config) into a frozen plan.

    With *user*, a local reviewer model must be one that user can see (an
    endpoint shared with them); without it the model is looked up unscoped.

    Tool versions come from the analyzer image manifest when the image is
    built; otherwise from the spec, marked ``version_source: "spec"`` with a
    ``version_unverified`` note (the executor records the real version on
    each ToolResult and flags any difference). Raises ConfigError when the
    profile/config is (no longer) valid against the catalog.
    """
    raw = profile.config if profile is not None else (config if config is not None else default_profile_config())
    cfg = validate_profile_config(raw)
    notes: list[dict[str, str]] = []
    tag, image_id, manifest = _image_identity(notes) if cfg["tools"] else ("", "", None)
    tools = []
    unverified = []
    for entry in cfg["tools"]:
        spec = catalog.get_spec(entry["slug"])
        version = sandbox.tool_version(manifest, spec.slug) if manifest else ""
        source = "image_manifest"
        if not version:
            version = str(spec.raw.get("version") or "")
            source = "spec"
            unverified.append(spec.slug)
        tools.append(
            {
                "slug": spec.slug,
                "name": spec.name,
                "kind": spec.kind,
                "category": spec.category,
                "required": entry["required"],
                "config": entry["config"],
                "config_hash": catalog.config_hash(spec.slug, entry["config"], version),
                "spec_hash": spec.spec_hash,
                "version": version,
                "version_source": source,
                "languages": list(spec.languages),
            },
        )
    if unverified:
        notes.append(
            {
                "code": "version_unverified",
                "detail": "Versions not read from a built analyzer image (spec versions used): "
                + ", ".join(unverified),
            },
        )
    reviewer = None
    frozen_reviewer = (
        reviewer_config.freeze_reviewer(cfg["reviewer"], user=user) if cfg["reviewer"].get("enabled") else None
    )
    if frozen_reviewer:
        reviewer = dict(frozen_reviewer)
        reviewer["enabled"] = True
        reviewer["required"] = bool(cfg["reviewer"].get("required", reviewer.get("required", False)))
        reviewer.setdefault("tool_slug", AI_TOOL_SLUG)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "profile": (
            {
                "id": str(profile.pk),
                "slug": profile.slug,
                "version": profile.version,
                "content_hash": profile.content_hash,
            }
            if profile is not None
            else None
        ),
        "tools": tools,
        "reviewer": reviewer,
        "scope": cfg["scope"],
        "outcomes": cfg["outcomes"],
        "analyzer_image": {"tag": tag},
        "engine_versions": engine_versions(),
        "mapping_hashes": normalize.mapping_hashes(),
        "provenance": {
            "frozen_at": datetime.now(UTC).isoformat(),
            "image_id": image_id,
            "image_manifest": sandbox.manifest_digest(manifest),
        },
        "notes": notes,
    }


def engine_versions() -> dict[str, str]:
    """The engine stage versions of the running code (frozen into every plan).

    A stage that binds its version at import (dedup keys, the measurement's
    ``version`` stamp) is read from that binding: it is what the stage uses.
    """
    from backend.analysis.engine import dedup
    from backend.analysis.engine import measurement

    return {
        "sample_resolver": types.SAMPLE_RESOLVER_VERSION,
        "sloc": types.SLOC_VERSION,
        "normalizer": types.NORMALIZER_VERSION,
        "dedup": str(getattr(dedup, "DEDUP_VERSION", types.DEDUP_VERSION)),
        "measurement": str(getattr(measurement, "MEASUREMENT_VERSION", types.MEASUREMENT_VERSION)),
    }


def procedure_drift(plan: dict[str, Any]) -> dict[str, Any]:
    """How the running code differs from the procedure *plan* froze.

    Returns ``{"tools": {slug: reason}, "engine": [reason, ...]}``, both
    empty when the code implements the frozen procedure exactly:

    * ``tools``: a static tool whose catalog spec (adapter version, resources,
      languages, file kinds, config schema/defaults, ...) no longer has the
      frozen ``spec_hash``, or that left the catalog. Only that tool is affected.
    * ``engine``: an engine stage version (sample resolver, SLOC, normalizer,
      dedup, measurement) or a mapping table that differs. Every finding and
      the measurement pass through those, so the whole run is affected.

    An adapter edit that does not bump ``adapter_version`` cannot be seen
    here; that is what the version is for (see ``catalog``).
    """
    tools: dict[str, str] = {}
    for entry in plan.get("tools") or []:
        if not isinstance(entry, dict) or not entry.get("slug"):
            continue
        slug = str(entry["slug"])
        try:
            spec = catalog.get_spec(slug)
        except KeyError:
            tools[slug] = f"the tool {slug!r} is no longer in the catalog"
            continue
        frozen = str(entry.get("spec_hash") or "")
        if frozen != spec.spec_hash:
            tools[slug] = (
                f"the {slug} spec changed since the plan was frozen (spec_hash {frozen[:12] or 'none'} -> "
                f"{spec.spec_hash[:12]}, adapter_version {spec.adapter_version})"
            )
    engine: list[str] = []
    frozen_versions = plan.get("engine_versions") if isinstance(plan.get("engine_versions"), dict) else {}
    for stage, current in engine_versions().items():
        planned = frozen_versions.get(stage)
        if planned != current:
            engine.append(f"{stage} {planned or 'unrecorded'} -> {current}")
    frozen_maps = plan.get("mapping_hashes") if isinstance(plan.get("mapping_hashes"), dict) else {}
    current_maps = normalize.mapping_hashes()
    for name in sorted(set(frozen_maps) | set(current_maps)):
        planned, current = frozen_maps.get(name), current_maps.get(name)
        if planned != current:
            engine.append(f"mapping table {name} {str(planned or 'unrecorded')[:12]} -> {str(current or 'gone')[:12]}")
    return {"tools": tools, "engine": engine}


def comparable_content(plan: dict[str, Any]) -> dict[str, Any]:
    """The part of a plan that decides comparability (what :func:`plan_hash` hashes)."""
    content = {k: copy.deepcopy(v) for k, v in plan.items() if k not in _UNHASHED_PLAN_KEYS}
    tools = [
        {k: v for k, v in tool.items() if k not in _UNHASHED_TOOL_KEYS} if isinstance(tool, dict) else tool
        for tool in content.get("tools") or []
    ]
    # Order is presentation (catalog display order), not procedure; slugs are unique in a plan.
    content["tools"] = sorted(tools, key=lambda tool: str(tool.get("slug") if isinstance(tool, dict) else tool))
    reviewer = content.get("reviewer")
    if isinstance(reviewer, dict) and isinstance(reviewer.get("model"), dict):
        reviewer["model"] = {k: v for k, v in reviewer["model"].items() if k != "pricing"}
    return content


def plan_hash(plan: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of the plan's comparability-relevant content."""
    return _sha256(comparable_content(plan))


def check_frozen(plan: Any) -> None:
    """Raise ValueError unless *plan* looks like a frozen plan this engine can execute."""
    if not isinstance(plan, dict):
        msg = "The plan must be an object (a frozen plan from plan.freeze_plan)"
        raise TypeError(msg)
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        msg = f"Unsupported plan schema_version {plan.get('schema_version')!r} (expected {PLAN_SCHEMA_VERSION})"
        raise ValueError(msg)
    tools = plan.get("tools")
    if not isinstance(tools, list) or not all(isinstance(t, dict) and t.get("slug") for t in tools):
        msg = "The plan's tools must be a list of frozen tool entries"
        raise ValueError(msg)
    if len({t["slug"] for t in tools}) != len(tools):
        msg = "The plan lists a tool twice"
        raise ValueError(msg)
    reviewer = plan.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, dict):
        msg = "The plan's reviewer must be an object or null"
        raise ValueError(msg)
    if not tools and not (reviewer or {}).get("enabled"):
        msg = "The plan runs no tool and no AI reviewer"
        raise ValueError(msg)
    if (reviewer or {}).get("enabled") and (reviewer.get("tool_slug") or AI_TOOL_SLUG) in {t["slug"] for t in tools}:
        msg = "The plan's reviewer slug collides with a tool slug"
        raise ValueError(msg)
    if not isinstance(plan.get("scope"), dict):
        msg = "The plan has no scope"
        raise TypeError(msg)
