"""Run presets: named, versioned harness / sampling / budget configurations.

A preset is a reusable label for a piece of agent-run configuration:

* ``harness``  -- ``{"harness": <name>, "options": {<non-default options>}}``
* ``sampling`` -- ``{"llm_params": {...}, "sampling_policy"?: ...}``: model-
  agnostic sampling settings applied identically to every model of a batch
  (per-model support shows up in the run preview, never in the preset).
  Routing (``provider``) and output formatting (``stop``,
  ``response_format``) are not sampling properties and are rejected.
* ``budget``   -- a sparse subset of the request / wall-clock / token / USD
  limits and the network policy (``{}`` = the platform defaults).

Rules that keep presets honest provenance:

* Configs are normalized before hashing (defaults dropped, validated), so
  equal behaviour means equal ``content_hash``.
* Content is immutable per version: a content edit creates version+1, a
  name/description edit does not. Slugs are global per kind (any owner) and
  ``*-default`` slugs are reserved for system presets.
* Jobs copy a preset's values into their own fields at creation
  (``llm_params``, ``agent_config``, ``harness_options``); the snapshot's
  ``agent`` / ``gateway`` / ``harness`` sections stay the authoritative
  record of what ran, and preset hashes are labels only.

System presets are seeded from ``data/presets/*.yaml``; the default path
never depends on that seeding (see :func:`ensure_system_presets`).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.db import DEFAULT_DB_ALIAS
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Max
from django.db.models import Q

from backend.generation.services.agent.config import BUDGET_KEYS
from backend.generation.services.agent.config import SAMPLING_POLICIES
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.harnesses import HARNESSES
from backend.generation.services.agent.harnesses import default_harness_name
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.versioning import canonical_json

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import RunPreset

logger = logging.getLogger(__name__)

KINDS = ("harness", "sampling", "budget")
PRESETS_DIR = Path(__file__).resolve().parents[1] / "data" / "presets"
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
RESERVED_SLUG_SUFFIX = "-default"
SAMPLING_REJECTED_KEYS = {
    "provider": "provider routing is not a sampling setting (set it as an explicit per-run override)",
    "stop": "stop sequences do not apply to agent runs",
    "response_format": "response_format does not apply to agent runs",
}
SYSTEM_DEFAULT_SLUGS = {"sampling": "provider-default", "budget": "platform-default"}
MAX_NAME_LENGTH = 200  # RunPreset.name max_length


class PresetError(Exception):
    """A preset request the API refuses (``status`` is the HTTP status)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ── hashing ───────────────────────────────────────────────────────────


def config_hash(kind: str, config: dict[str, Any]) -> str:
    """``content_hash`` of a *normalized* config."""
    return hashlib.sha256(canonical_json({"kind": kind, "config": config}).encode("utf-8")).hexdigest()


# ── validation ────────────────────────────────────────────────────────


def _normalize_harness(config: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    unknown = sorted(set(config) - {"harness", "options"})
    if unknown:
        errors.append(f"Unknown harness preset keys: {', '.join(unknown)} (allowed: harness, options)")
    name = config.get("harness")
    if name not in HARNESSES:
        errors.append(f"harness must be one of {', '.join(HARNESSES)}")
        return {}
    try:
        options = HARNESSES[name].normalize_options(config.get("options") or {})
    except ValueError as exc:
        errors.append(str(exc))
        return {}
    return {"harness": name, "options": options}


def _normalize_sampling(config: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    unknown = sorted(set(config) - {"llm_params", "sampling_policy"})
    if unknown:
        errors.append(f"Unknown sampling preset keys: {', '.join(unknown)} (allowed: llm_params, sampling_policy)")
    raw = config.get("llm_params")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        errors.append("llm_params must be an object")
        raw = {}
    for key, why in SAMPLING_REJECTED_KEYS.items():
        if raw.get(key) is not None:
            errors.append(f"{key}: {why}")
    llm_params: dict[str, Any] = {}
    try:
        llm_params = validate_llm_params({k: v for k, v in raw.items() if k not in SAMPLING_REJECTED_KEYS})
    except ValueError as exc:
        errors.append(str(exc))
    normalized: dict[str, Any] = {"llm_params": llm_params}
    policy = config.get("sampling_policy")
    if policy is not None:
        if policy not in SAMPLING_POLICIES:
            errors.append(f"sampling_policy must be one of {', '.join(SAMPLING_POLICIES)}")
        else:
            normalized["sampling_policy"] = policy
    return normalized


def _normalize_budget(config: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    unknown = sorted(set(config) - set(BUDGET_KEYS))
    if unknown:
        errors.append(
            f"Unknown budget preset keys: {', '.join(unknown)} (allowed: {', '.join(BUDGET_KEYS)}; "
            "the harness and sampling policy belong to harness and sampling presets)",
        )
    try:
        return validate_agent_config({k: config[k] for k in BUDGET_KEYS if k in config})
    except ValueError as exc:
        errors.append(str(exc))
        return {}


_NORMALIZERS = {"harness": _normalize_harness, "sampling": _normalize_sampling, "budget": _normalize_budget}


def check_config(kind: str, config: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """``(normalized config, [])`` or ``(None, [problems])``; never raises."""
    if kind not in KINDS:
        return None, [f"kind must be one of {', '.join(KINDS)}"]
    if not isinstance(config, dict):
        return None, ["config must be an object"]
    errors: list[str] = []
    normalized = _NORMALIZERS[kind](config, errors)
    return (None, errors) if errors else (normalized, [])


def normalize_config(kind: str, config: Any) -> dict[str, Any]:
    """Normalized config for *kind*; ``ValueError`` listing every problem."""
    normalized, errors = check_config(kind, config)
    if errors or normalized is None:
        raise ValueError("; ".join(errors))
    return normalized


def saving_errors(kind: str, config: dict[str, Any]) -> list[str]:
    """Rules for user-saved presets on top of validity (a normalized *config*)."""
    if kind == "harness" and not config.get("options"):
        name = config.get("harness", "")
        return [f"Every option is at its default; use the system preset {harness_default_slug(name)}"]
    return []


def validate_slug(slug: str) -> list[str]:
    if not SLUG_PATTERN.match(slug or ""):
        return ["slug must be 2-81 characters: lowercase letters, digits and dashes, starting with a letter or digit"]
    return []


def harness_default_slug(harness_name: str) -> str:
    return f"{harness_name}{RESERVED_SLUG_SUFFIX}"


def default_slug(kind: str, harness_name: str | None = None) -> str:
    if kind == "harness":
        return harness_default_slug(harness_name or default_harness_name())
    return SYSTEM_DEFAULT_SLUGS[kind]


def reserved_slugs(kind: str) -> set[str]:
    """Slugs user presets of *kind* may not take: every system preset file's and default slug."""
    slugs = {entry.slug for entry in load_system_preset_files() if entry.kind == kind}
    if kind == "harness":
        slugs |= {harness_default_slug(name) for name in HARNESSES}
    else:
        slugs.add(SYSTEM_DEFAULT_SLUGS[kind])
    return slugs


def preset_config_for_use(preset: RunPreset, kind: str) -> dict[str, Any]:
    """Re-validated config of a stored preset about to be applied (``ValueError`` if unusable).

    Stored presets are validated on write, but a harness release can drop
    or change an option afterwards; such a preset must fail loudly here,
    never at snapshot time.
    """
    if preset.kind != kind:
        msg = f"Preset {preset.slug} is a {preset.kind} preset, not a {kind} preset"
        raise ValueError(msg)
    try:
        return normalize_config(kind, preset.config)
    except ValueError as exc:
        msg = f"Preset {preset.kind}/{preset.slug} v{preset.version} is no longer valid: {exc}"
        raise ValueError(msg) from exc


# ── reading ───────────────────────────────────────────────────────────


def visible_q(user: AbstractUser | None) -> Q:
    if user is not None and getattr(user, "is_authenticated", False):
        return Q(is_system=True) | Q(created_by=user)
    return Q(is_system=True)


def is_default_preset(preset: RunPreset) -> bool:
    """System preset that is the default for its kind (harness: per harness name)."""
    if not preset.is_system:
        return False
    if preset.kind == "harness":
        return preset.slug == harness_default_slug(str((preset.config or {}).get("harness", "")))
    return preset.slug == SYSTEM_DEFAULT_SLUGS.get(preset.kind)


def latest_version_numbers(presets: list[RunPreset]) -> dict[tuple[str, str], int]:
    keys = {(p.kind, p.slug) for p in presets}
    if not keys:
        return {}
    from backend.generation.models import RunPreset

    query = Q()
    for kind, slug in keys:
        query |= Q(kind=kind, slug=slug)
    rows = RunPreset.objects.filter(query).values("kind", "slug").annotate(latest=Max("version"))
    return {(r["kind"], r["slug"]): r["latest"] for r in rows}


def preset_payload(preset: RunPreset, *, latest_version: int | None = None) -> dict[str, Any]:
    """The API's RunPreset JSON."""
    if latest_version is None:
        latest_version = latest_version_numbers([preset]).get((preset.kind, preset.slug), preset.version)
    return {
        "id": preset.id,
        "kind": preset.kind,
        "slug": preset.slug,
        "version": preset.version,
        "name": preset.name,
        "description": preset.description,
        "config": preset.config,
        "content_hash": preset.content_hash,
        "is_system": preset.is_system,
        "is_archived": preset.is_archived,
        "is_default": is_default_preset(preset),
        "latest_version": latest_version,
        "created_at": preset.created_at,
        "updated_at": preset.updated_at,
    }


def provenance(preset: RunPreset | None) -> dict[str, Any] | None:
    """What a job snapshot records about a preset it used (a label, not the config)."""
    if preset is None:
        return None
    return {
        "id": preset.id,
        "slug": preset.slug,
        "version": preset.version,
        "content_hash": preset.content_hash,
        "name": preset.name,
    }


def _list_order(preset: RunPreset) -> tuple:
    overall_default = (
        preset.kind == "harness" and preset.is_system and preset.slug == harness_default_slug(default_harness_name())
    )
    rank = 0 if overall_default else 1 if is_default_preset(preset) else 2 if preset.is_system else 3
    return (KINDS.index(preset.kind), rank, preset.name.lower(), preset.slug)


def list_presets(
    user: AbstractUser | None,
    *,
    kind: str | None = None,
    include_archived: bool = False,
) -> list[RunPreset]:
    """Latest version per (kind, slug) of the presets *user* can see: defaults, system, own."""
    from backend.generation.models import RunPreset

    ensure_system_presets()
    qs = RunPreset.objects.filter(visible_q(user))
    if kind:
        qs = qs.filter(kind=kind)
    latest: dict[tuple[str, str], RunPreset] = {}
    for preset in qs.order_by("kind", "slug", "-version"):
        latest.setdefault((preset.kind, preset.slug), preset)
    presets = [p for p in latest.values() if include_archived or not p.is_archived]
    return sorted(presets, key=_list_order)


def get_visible_preset(user: AbstractUser | None, preset_id: int) -> RunPreset:
    """Any version (archived included) of a preset *user* can see; ``RunPreset.DoesNotExist`` otherwise."""
    from backend.generation.models import RunPreset

    return RunPreset.objects.filter(visible_q(user)).get(id=preset_id)


def preset_versions(user: AbstractUser | None, kind: str, slug: str) -> list[RunPreset]:
    from backend.generation.models import RunPreset

    return list(RunPreset.objects.filter(visible_q(user), kind=kind, slug=slug).order_by("-version"))


# ── writing ───────────────────────────────────────────────────────────


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise PresetError(400, "name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise PresetError(400, f"name must be at most {MAX_NAME_LENGTH} characters")
    return name


def _validated_for_saving(kind: str, config: Any) -> dict[str, Any]:
    normalized, errors = check_config(kind, config)
    if errors or normalized is None:
        raise PresetError(400, "; ".join(errors))
    problems = saving_errors(kind, normalized)
    if problems:
        raise PresetError(400, "; ".join(problems))
    return normalized


def slug_problem(kind: str, slug: str) -> str | None:
    """Why *slug* cannot name a new user preset of *kind* (None if it can).

    Collisions are checked against every preset of the kind, whoever owns
    it, and the message never says whose it is.
    """
    from backend.generation.models import RunPreset

    errors = validate_slug(slug)
    if errors:
        return errors[0]
    if slug.endswith(RESERVED_SLUG_SUFFIX) or slug in reserved_slugs(kind):
        return f"slug {slug!r} is reserved for system presets"
    if RunPreset.objects.filter(kind=kind, slug=slug).exists():
        return "slug already in use"
    return None


def create_preset(
    user: AbstractUser,
    *,
    kind: str,
    slug: str,
    name: str,
    description: str = "",
    config: Any,
) -> RunPreset:
    """Version 1 of a new user preset (``PresetError`` when refused)."""
    from backend.generation.models import RunPreset

    if kind not in KINDS:
        raise PresetError(400, f"kind must be one of {', '.join(KINDS)}")
    slug = (slug or "").strip()
    name = _clean_name(name)
    normalized = _validated_for_saving(kind, config)
    problem = slug_problem(kind, slug)
    if problem:
        raise PresetError(400, problem)
    try:
        with transaction.atomic():
            return RunPreset.objects.create(
                kind=kind,
                slug=slug,
                version=1,
                name=name,
                description=description or "",
                config=normalized,
                content_hash=config_hash(kind, normalized),
                is_system=False,
                created_by=user,
            )
    except IntegrityError as exc:  # a concurrent create took the slug
        raise PresetError(400, "slug already in use") from exc


def _owned_latest(user: AbstractUser, kind: str, slug: str) -> RunPreset:
    from backend.generation.models import RunPreset

    if kind not in KINDS:
        raise PresetError(404, "Preset not found")
    latest = RunPreset.objects.filter(kind=kind, slug=slug).order_by("-version").first()
    if latest is None:
        raise PresetError(404, "Preset not found")
    if latest.is_system:
        raise PresetError(403, "System presets cannot be changed")
    if latest.created_by_id != getattr(user, "id", None):
        raise PresetError(403, "Only the preset's owner can change it")
    return latest


def update_preset(
    user: AbstractUser,
    *,
    kind: str,
    slug: str,
    name: str,
    description: str = "",
    config: Any = None,
) -> RunPreset:
    """A content change creates version+1; a name/description change updates the latest version.

    ``config=None`` keeps the latest version's config.
    """
    from backend.generation.models import RunPreset

    latest = _owned_latest(user, kind, slug)
    if latest.is_archived:
        raise PresetError(400, "Archived presets cannot be changed")
    name = _clean_name(name)
    normalized = _validated_for_saving(kind, latest.config if config is None else config)
    digest = config_hash(kind, normalized)
    if digest == latest.content_hash:
        latest.name = name
        latest.description = description or ""
        latest.save(update_fields=["name", "description", "updated_at"])
        return latest
    try:
        with transaction.atomic():
            return RunPreset.objects.create(
                kind=kind,
                slug=slug,
                version=latest.version + 1,
                name=name,
                description=description or "",
                config=normalized,
                content_hash=digest,
                is_system=False,
                created_by=user,
            )
    except IntegrityError as exc:
        raise PresetError(409, "The preset changed concurrently; reload and try again") from exc


def archive_preset(user: AbstractUser, *, kind: str, slug: str) -> int:
    """Archive every version (jobs keep referencing them); returns the number of rows."""
    from backend.generation.models import RunPreset

    _owned_latest(user, kind, slug)
    return RunPreset.objects.filter(kind=kind, slug=slug).update(is_archived=True)


# ── system presets ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SystemPresetFile:
    kind: str
    slug: str
    name: str
    description: str
    config: dict[str, Any]
    path: Path


def load_system_preset_files() -> list[SystemPresetFile]:
    """Every ``data/presets/*.yaml`` entry (unreadable files are logged and skipped)."""
    entries: list[SystemPresetFile] = []
    if not PRESETS_DIR.is_dir():
        return entries
    for path in sorted(PRESETS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            logger.exception("Failed to read preset file %s", path)
            continue
        if not isinstance(data, dict):
            logger.error("Preset file %s is not a mapping", path)
            continue
        entries.append(
            SystemPresetFile(
                kind=str(data.get("kind", "")),
                slug=str(data.get("slug", "")),
                name=str(data.get("name") or data.get("slug", "")),
                description=str(data.get("description") or ""),
                config=data.get("config") if isinstance(data.get("config"), dict) else {},
                path=path,
            ),
        )
    return entries


def _builtin_default_presets() -> list[SystemPresetFile]:
    """The default preset of every kind/harness as code knows it (fallback when no file provides it)."""
    entries = [
        SystemPresetFile(
            kind="harness",
            slug=harness_default_slug(name),
            name=f"{harness.label} (default)",
            description=f"{harness.label} with its pinned default configuration.",
            config={"harness": name, "options": {}},
            path=Path(),
        )
        for name, harness in HARNESSES.items()
    ]
    entries.append(
        SystemPresetFile(
            kind="sampling",
            slug=SYSTEM_DEFAULT_SLUGS["sampling"],
            name="Provider defaults",
            description="No sampling parameters are set; every model samples at its provider's defaults.",
            config={"llm_params": {}},
            path=Path(),
        ),
    )
    entries.append(
        SystemPresetFile(
            kind="budget",
            slug=SYSTEM_DEFAULT_SLUGS["budget"],
            name="Platform defaults",
            description="The platform's default limits and network policy.",
            config={},
            path=Path(),
        ),
    )
    return entries


def _upsert_system_preset(entry: SystemPresetFile, *, using: str, log=None) -> tuple[int, int]:
    """Create v1 / version+1 of a system preset when its content changed (never in place)."""
    from backend.generation.models import RunPreset

    if entry.kind not in KINDS or validate_slug(entry.slug):
        logger.error("System preset %s has an invalid kind or slug", entry.path or entry.slug)
        return 0, 0
    try:
        normalized = normalize_config(entry.kind, entry.config)
    except ValueError:
        logger.exception("System preset %s/%s is invalid", entry.kind, entry.slug)
        return 0, 0
    rows = RunPreset.objects.using(using).filter(kind=entry.kind, slug=entry.slug)
    if rows.filter(is_system=False).exists():
        logger.error("System preset %s/%s skipped: a user preset already uses the slug", entry.kind, entry.slug)
        if log:
            log(f"  Skipped system preset {entry.kind}/{entry.slug}: slug taken by a user preset")
        return 0, 0
    digest = config_hash(entry.kind, normalized)
    latest = rows.order_by("-version").first()
    if latest is not None and latest.content_hash == digest:
        if (latest.name, latest.description) == (entry.name, entry.description):
            return 0, 0
        # Name/description are metadata, not content: no new version.
        latest.name = entry.name
        latest.description = entry.description
        latest.save(using=using, update_fields=["name", "description", "updated_at"])
        return 0, 1
    RunPreset.objects.using(using).create(
        kind=entry.kind,
        slug=entry.slug,
        version=(latest.version + 1) if latest else 1,
        name=entry.name,
        description=entry.description,
        config=normalized,
        content_hash=digest,
        is_system=True,
    )
    if log:
        log(f"  System preset {entry.kind}/{entry.slug} v{(latest.version + 1) if latest else 1}")
    return 1, 0


def seed_presets(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Seed ``data/presets/*.yaml``: idempotent by content hash, changed content = new version."""
    created = updated = 0
    for entry in load_system_preset_files():
        try:
            with transaction.atomic(using=using):
                c, u = _upsert_system_preset(entry, using=using, log=log)
        except IntegrityError:
            logger.exception("System preset %s/%s could not be seeded", entry.kind, entry.slug)
            continue
        created += c
        updated += u
    return created, updated


def _default_keys() -> set[tuple[str, str]]:
    keys = {("harness", harness_default_slug(name)) for name in HARNESSES}
    keys |= set(SYSTEM_DEFAULT_SLUGS.items())
    return keys


def ensure_system_presets() -> None:
    """Make sure every default preset exists (request-time guard; never raises).

    Seeding runs after every migrate, but the default run path must not
    depend on it having succeeded: missing defaults are seeded from the
    files, and anything still missing is created from code. If a user row
    blocks a default slug, callers fall back to the built-in default config
    without provenance (:func:`system_default_preset` returns None).
    """
    from backend.generation.models import RunPreset

    wanted = _default_keys()

    def missing() -> set[tuple[str, str]]:
        have = set(
            RunPreset.objects.filter(is_system=True, slug__in={slug for _k, slug in wanted}).values_list(
                "kind",
                "slug",
            ),
        )
        return wanted - have

    try:
        # A savepoint, so a failure here never poisons the caller's transaction.
        with transaction.atomic():
            if not missing():
                return
            seed_presets()
            still = missing()
            for entry in _builtin_default_presets():
                if (entry.kind, entry.slug) not in still:
                    continue
                try:
                    with transaction.atomic():
                        _upsert_system_preset(entry, using=DEFAULT_DB_ALIAS)
                except IntegrityError:
                    logger.warning("Default preset %s/%s was created concurrently", entry.kind, entry.slug)
    except Exception:
        # The callers fall back to built-in defaults; a broken preset table
        # must not take the run path down with it.
        logger.exception("Could not ensure the system run presets")


def system_default_preset(kind: str, harness_name: str | None = None) -> RunPreset | None:
    """Latest version of the system default preset (``None`` if it cannot exist, see above)."""
    from backend.generation.models import RunPreset

    ensure_system_presets()
    return (
        RunPreset.objects.filter(kind=kind, slug=default_slug(kind, harness_name), is_system=True)
        .order_by("-version")
        .first()
    )


def builtin_default_config(kind: str, harness_name: str | None = None) -> dict[str, Any]:
    """The configuration a default preset stands for, without needing its row."""
    if kind == "harness":
        return {"harness": harness_name or default_harness_name(), "options": {}}
    if kind == "sampling":
        return {"llm_params": {}}
    return {}
