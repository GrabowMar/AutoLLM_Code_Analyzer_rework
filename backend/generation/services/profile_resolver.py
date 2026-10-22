"""Resolve generation profiles into immutable job snapshots for scaffolding runs."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile
from backend.generation.services.llm_params import SAMPLING_KEYS
from backend.generation.services.llm_params import merge_llm_params
from backend.runtime.services.scaffolding import resolve_stack_slug
from backend.runtime.services.scaffolding import stack_snapshot_entry

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import GenerationJob
    from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

BUNDLE_SCHEMA_VERSION = 3
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BLOCKS_DIR = DATA_DIR / "blocks"
CATALOG_PATH = BLOCKS_DIR / "catalog.yaml"

_APPEND_TO_SYSTEM = frozenset(
    {
        ContentBlock.BlockType.PROMPT_TONE,
        ContentBlock.BlockType.PROMPT_RULES,
        ContentBlock.BlockType.SCAFFOLD_HINT,
    },
)


DEFAULT_PROFILE_SLUG = "system-scaffolding-standard"

# Sampling params that are real numbers. A layer may hold an equal int (a
# preset or profile stored ``temperature: 0``; inline API values arrive as
# floats), and the fingerprints hash plain JSON, where 0 and 0.0 differ.
FLOAT_PARAM_KEYS = (
    "temperature",
    "top_p",
    "min_p",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
)


def coerce_float_params(params: dict[str, Any]) -> dict[str, Any]:
    """*params* with every float-kind sampling value as a float (in place; returned for chaining).

    Why here and not by canonicalizing the fingerprint input (integral
    floats -> ints, as ``canonical_json`` does for preset hashes and method
    keys): default runs carry integral floats (``agent.max_cost_usd`` 5.0),
    so canonicalizing would change every default run's ``run_fingerprint``
    and stop it pooling with the runs before it. Coercing the values to
    their declared type where they enter a snapshot leaves defaults
    byte-identical (their values are floats already) while a preset's
    ``temperature: 0`` and an inline ``0.0`` freeze, and fingerprint, alike.
    """
    for key in FLOAT_PARAM_KEYS:
        value = params.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            params[key] = float(value)
    return params


def load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.is_file():
        return {}
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}


def default_profile_slug() -> str:
    """The global default profile: ``catalog.yaml: default_bundle_slug``."""
    return str(load_catalog().get("default_bundle_slug") or DEFAULT_PROFILE_SLUG)


def app_requirement_to_dict(app_req: AppRequirementTemplate) -> dict[str, Any]:
    return {
        "id": app_req.id,
        "slug": app_req.slug,
        "name": app_req.name,
        "category": app_req.category,
        "description": app_req.description,
        "backend_requirements": app_req.backend_requirements or [],
        "frontend_requirements": app_req.frontend_requirements or [],
        "admin_requirements": app_req.admin_requirements or [],
        "api_endpoints": app_req.api_endpoints or [],
        "data_model": app_req.data_model or {},
        "admin_api_endpoints": app_req.admin_api_endpoints or [],
    }


def get_content_block(
    slug: str,
    version: int,
    user: AbstractUser | None = None,
) -> ContentBlock:
    """Load a block visible to *user* (system blocks or user-owned)."""
    qs = ContentBlock.objects.filter(slug=slug, version=version)
    if user and getattr(user, "is_authenticated", False):
        block = qs.filter(Q(is_system=True) | Q(created_by=user)).first()
    else:
        block = qs.filter(is_system=True).first()
    if not block:
        raise ContentBlock.DoesNotExist(
            ContentBlock,
            {"slug": slug, "version": version},
        )
    return block


def resolve_block_refs(
    block_refs: list[dict[str, Any]],
    user: AbstractUser | None = None,
) -> list[dict[str, Any]]:
    """Resolve references to full block records with ``resolved_content``."""
    resolved: list[dict[str, Any]] = []
    for ref in block_refs:
        slug = ref["slug"]
        version = int(ref.get("version", 1))
        block = get_content_block(slug, version, user)
        resolved.append(
            {
                "type": ref.get("type", block.block_type),
                "slug": slug,
                "version": version,
                "name": block.name,
                "metadata": block.metadata or {},
                "resolved_content": block.content,
            },
        )
    return resolved


def assemble_prompt_templates(resolved_blocks: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Build raw system/user template strings per stage from resolved blocks."""
    templates: dict[str, dict[str, str]] = {
        "backend": {"system": "", "user": ""},
        "frontend": {"system": "", "user": ""},
    }
    system_addons: dict[str, list[str]] = {"backend": [], "frontend": []}

    for block in resolved_blocks:
        btype = block["type"]
        content = (block.get("resolved_content") or "").strip()
        if not content:
            continue
        meta = block.get("metadata") or {}
        if btype == ContentBlock.BlockType.PROMPT_STAGE:
            stage = meta.get("stage") or "backend"
            role = meta.get("role") or "system"
            if stage in templates and role in templates[stage]:
                templates[stage][role] = (
                    f"{templates[stage][role]}\n\n{content}".strip() if templates[stage][role] else content
                )
        elif btype in _APPEND_TO_SYSTEM:
            target_stage = meta.get("stage")
            stages = [target_stage] if target_stage in templates else list(templates)
            for stage in stages:
                system_addons[stage].append(content)

    for stage in ("backend", "frontend"):
        if system_addons[stage]:
            extra = "\n\n".join(system_addons[stage])
            base = templates[stage]["system"]
            templates[stage]["system"] = f"{base}\n\n{extra}".strip() if base else extra

    return templates


def bundle_slug_for_app(app_slug: str) -> str:
    """Convention: ``content_recipe_list`` → ``app-content-recipe-list``."""
    return f"app-{app_slug.replace('_', '-')}"


def _visible_profiles(user: AbstractUser | None):
    """Non-archived profiles visible to *user*, latest version of each slug first."""
    qs = GenerationProfile.objects.filter(is_archived=False).order_by("slug", "-version")
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)


def get_profile_for_app(
    app_req: AppRequirementTemplate,
    user: AbstractUser | None = None,
    scaffolding_slug: str | None = None,
) -> GenerationProfile | None:
    """Latest version of the per-app pilot profile if seeded, else system default."""
    slug = bundle_slug_for_app(app_req.slug)
    qs = _visible_profiles(user).filter(slug=slug)
    if scaffolding_slug:
        profile = qs.filter(scaffolding_slug=scaffolding_slug).first()
        if profile:
            return profile
        return get_default_profile(scaffolding_slug=scaffolding_slug, user=user)

    return qs.first() or get_default_profile(user=user)


def suggest_profile_for_app(
    app_req: AppRequirementTemplate,
    user: AbstractUser | None = None,
    scaffolding_slug: str | None = None,
) -> tuple[GenerationProfile | None, str]:
    """Like :func:`get_profile_for_app`, but also says *why* that profile won.

    Provenance values: ``app-pilot`` (seeded per-app profile), ``stack-default``
    (default/system profile matching the stack), ``system-default`` (global
    default), ``catalog`` (no DB profile at all — bare catalog block refs).
    """
    slug = bundle_slug_for_app(app_req.slug)
    qs = _visible_profiles(user).filter(slug=slug)
    pilot = qs.filter(scaffolding_slug=scaffolding_slug).first() if scaffolding_slug else qs.first()
    if pilot:
        return pilot, "app-pilot"

    default = get_default_profile(scaffolding_slug=scaffolding_slug, user=user)
    if default is None:
        return None, "catalog"
    if scaffolding_slug and default.scaffolding_slug == scaffolding_slug:
        return default, "stack-default"
    return default, "system-default"


def _own_stack_default(qs, scaffolding_slug: str, user: AbstractUser | None) -> GenerationProfile | None:
    """The user's own profile whose latest version is the default for *scaffolding_slug*.

    Only latest versions count, whatever stack they target: unticking
    "default" in a new version, or moving the profile to another stack,
    must not let an older version keep winning for the old stack.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    latest: dict[str, GenerationProfile] = {}
    for profile in qs.filter(is_system=False, created_by=user):
        latest.setdefault(profile.slug, profile)  # qs is ordered slug, -version
    return next(
        (p for p in latest.values() if p.is_default and p.scaffolding_slug == scaffolding_slug),
        None,
    )


def get_default_profile(
    scaffolding_slug: str | None = None,
    user: AbstractUser | None = None,
) -> GenerationProfile | None:
    """Latest version of the applicable default profile.

    With a stack: the user's own default for it (``is_default`` means
    "default for its stack"; a user who marks a profile default for a stack
    means it to win over the shipped one), else the system default for it,
    else a system profile for it. Otherwise -- and as the last resort -- the
    global default named by ``catalog.yaml: default_bundle_slug``; every
    stack has a default, so ordering by slug must never pick the global one.
    """
    qs = _visible_profiles(user)
    if scaffolding_slug:
        own_default = _own_stack_default(qs, scaffolding_slug, user)
        if own_default:
            return own_default
        matching_default = qs.filter(
            scaffolding_slug=scaffolding_slug,
            is_default=True,
            is_system=True,
        ).first()
        if matching_default:
            return matching_default
        matching_system = qs.filter(
            scaffolding_slug=scaffolding_slug,
            slug__startswith="system-",
        ).first()
        if matching_system:
            return matching_system

    return (
        qs.filter(slug=default_profile_slug(), is_system=True).first()
        or qs.filter(is_default=True, is_system=True, scaffolding_slug=settings.GENERATION_DEFAULT_STACK).first()
        or qs.filter(is_default=True, is_system=True).first()
    )


def resolve_profile_for_job(
    *,
    profile: GenerationProfile | None,
    scaffolding_slug: str,
    user: AbstractUser | None,
) -> tuple[list[dict[str, Any]], str, str, int, dict[str, Any]]:
    """Return (block_refs, scaffolding_slug, bundle_slug, bundle_version, llm_config)."""
    if profile:
        return (
            list(profile.block_refs or []),
            profile.scaffolding_slug,
            profile.slug,
            profile.version,
            dict(profile.llm_config or {}),
        )

    if profile := get_default_profile(scaffolding_slug=scaffolding_slug, user=user):
        return (
            list(profile.block_refs or []),
            profile.scaffolding_slug,
            profile.slug,
            profile.version,
            dict(profile.llm_config or {}),
        )

    return catalog_default_block_refs(), scaffolding_slug, "catalog", 1, {}


def catalog_default_block_refs() -> list[dict[str, Any]]:
    """Block refs of the catalog's default profile, before any profile row exists (first boot).

    Pinned to the latest system block versions like the seeder pins them;
    an unseeded block keeps version 1 so resolution fails loudly on it.
    """
    from backend.generation.seeding import latest_system_block_refs

    catalog = load_catalog()
    default_slug = default_profile_slug()
    entry = next(
        (p for p in catalog.get("profiles", catalog.get("bundles", [])) if p.get("slug") == default_slug),
        {},
    )
    refs: list[dict[str, Any]] = []
    for ref in entry.get("block_refs", []):
        try:
            refs.extend(latest_system_block_refs([ref]))
        except LookupError:
            refs.append({"type": ref.get("type", ""), "slug": ref.get("slug", ""), "version": 1})
    return refs


def build_resolved_snapshot(
    *,
    app_requirement: AppRequirementTemplate,
    profile: GenerationProfile | None,
    scaffolding_slug: str,
    model: LLMModel | None,
    temperature: float,
    max_tokens: int,
    user: AbstractUser | None,
    experiment_seed: int | None = None,
    top_p: float | None = None,
    llm_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the full immutable snapshot dict for ``GenerationJob.resolved_bundle``.

    The llm section layers sampling params bottom-up: legacy job columns
    (temperature/max_tokens/top_p) → profile ``llm_config`` → per-run
    ``llm_overrides`` (which already include any experiment/condition layers,
    collapsed at job creation). Model pricing provenance is attached on top
    under non-sampling keys.
    """
    block_refs, bundle_scaffold_slug, resolved_bundle_slug, resolved_bundle_version, profile_llm = (
        resolve_profile_for_job(
            profile=profile,
            scaffolding_slug=scaffolding_slug,
            user=user,
        )
    )
    resolved_blocks = resolve_block_refs(block_refs, user)
    prompt_templates = assemble_prompt_templates(resolved_blocks)

    app_dict = app_requirement_to_dict(app_requirement)
    bundle_slug = profile.slug if profile else resolved_bundle_slug

    # Reproducibility seed for an experiment run, not a crypto value.
    seed = experiment_seed if experiment_seed is not None else random.randint(0, 2_147_483_647)  # noqa: S311

    base_params: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens}
    if top_p is not None:
        base_params["top_p"] = top_p
    llm_section = coerce_float_params(merge_llm_params(base_params, profile_llm, llm_overrides))
    _attach_model_provenance(llm_section, model)

    snapshot: dict[str, Any] = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_slug": bundle_slug,
        "bundle_version": resolved_bundle_version,
        "scaffolding_slug": bundle_scaffold_slug or scaffolding_slug,
        "llm": llm_section,
        "llm_overrides": dict(llm_overrides or {}),
        "seed": seed,
        "blocks": resolved_blocks,
        "app_requirement": app_dict,
        "prompt_templates": prompt_templates,
        "prompts": {},
    }
    # Pin the exact skeleton the job will be provisioned with (None only on
    # a first boot where the Stack table has not been seeded yet).
    stack_entry = stack_snapshot_entry(snapshot["scaffolding_slug"])
    if stack_entry:
        snapshot["stack"] = stack_entry
    snapshot["prompt_hash"] = _snapshot_prompt_hash(snapshot)
    snapshot["run_fingerprint"] = run_fingerprint(snapshot)
    return snapshot


def _attach_model_provenance(llm_section: dict[str, Any], model: LLMModel | None) -> None:
    """Freeze the model's pricing/context values into the llm section.

    LLMModel rows mutate in place on catalog sync, so the values in effect at
    job creation must be captured here. ("provider" is reserved for OpenRouter
    routing prefs since snapshot schema v3 — provenance uses model_provider.)
    """
    if not model:
        return
    llm_section["model_id"] = model.id
    llm_section["model_slug"] = model.model_id
    llm_section["model_provider"] = model.provider
    llm_section["context_window"] = model.context_window
    llm_section["max_output_tokens"] = model.max_output_tokens
    llm_section["input_price_per_token"] = model.input_price_per_token
    llm_section["output_price_per_token"] = model.output_price_per_token
    llm_section["pricing_snapshot_at"] = timezone.now().isoformat()


def build_custom_snapshot(
    *,
    model: LLMModel | None,
    temperature: float,
    max_tokens: int,
    llm_overrides: dict[str, Any] | None = None,
    sampling_preset: Any = None,
) -> dict[str, Any]:
    """Minimal ``resolved_bundle`` for custom-mode jobs: just the frozen llm section.

    Custom jobs have no blocks or app spec, but freezing the merged sampling
    params here lets execution read one path for every mode.
    """
    llm_section = coerce_float_params(
        merge_llm_params({"temperature": temperature, "max_tokens": max_tokens}, llm_overrides),
    )
    _attach_model_provenance(llm_section, model)
    snapshot = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "llm": llm_section,
        "llm_overrides": dict(llm_overrides or {}),
    }
    _attach_preset_provenance(snapshot, sampling_preset)
    return snapshot


def _attach_preset_provenance(snapshot: dict[str, Any], sampling_preset: Any) -> None:
    """Record the sampling preset a template-mode job used (a label; its values are in ``llm``).

    Only when there is one, so snapshots of preset-less jobs keep their shape.
    """
    if sampling_preset is not None:
        from backend.generation.services.presets import provenance

        snapshot["presets"] = {"sampling": provenance(sampling_preset)}


def attach_rendered_backend_prompts(
    snapshot: dict[str, Any],
    renderer: Any,
) -> dict[str, Any]:
    """Pre-render backend prompts into snapshot (called at job create)."""
    from backend.generation.services.prompt_renderer import PromptRenderer

    r = renderer if renderer is not None else PromptRenderer()
    messages = r.render_messages_from_snapshot(snapshot, stage="backend")
    snapshot = dict(snapshot)
    snapshot["prompts"] = dict(snapshot.get("prompts") or {})
    snapshot["prompts"]["backend"] = {
        "system": messages[0]["content"],
        "user": messages[1]["content"],
    }
    snapshot["prompt_hash"] = _snapshot_prompt_hash(snapshot)
    snapshot["run_fingerprint"] = run_fingerprint(snapshot)
    return snapshot


def snapshot_for_scaffolding_job(job: GenerationJob) -> dict[str, Any]:
    """Create and return the resolved snapshot for a scaffolding job (caller saves on job)."""
    if not job.app_requirement:
        msg = "Scaffolding job requires app_requirement"
        raise ValueError(msg)

    scaffolding_slug = resolve_stack_slug(job)

    snapshot = build_resolved_snapshot(
        app_requirement=job.app_requirement,
        profile=job.profile,
        scaffolding_slug=scaffolding_slug,
        model=job.model,
        temperature=job.temperature,
        max_tokens=job.max_tokens,
        user=job.created_by,
        experiment_seed=job.experiment_seed,
        top_p=job.top_p,
        llm_overrides=job.llm_params or {},
    )
    from backend.generation.services.prompt_renderer import PromptRenderer

    snapshot = attach_rendered_backend_prompts(snapshot, PromptRenderer())
    _attach_preset_provenance(snapshot, job.sampling_preset if job.sampling_preset_id else None)
    return snapshot


def apply_snapshot_to_job(job: GenerationJob, *, save: bool = True) -> GenerationJob:
    """Build snapshot; set seed, ``resolved_bundle``, and slicing keys on job."""
    snapshot = snapshot_for_scaffolding_job(job)
    job.experiment_seed = snapshot.get("seed")
    job.resolved_bundle = snapshot
    job.prompt_hash = snapshot.get("prompt_hash") or ""
    job.bundle_key = bundle_key_from_snapshot(snapshot)
    if save:
        job.save(
            update_fields=[
                "experiment_seed",
                "resolved_bundle",
                "prompt_hash",
                "bundle_key",
                "updated_at",
            ],
        )
    return job


def bundle_key_from_snapshot(snapshot: dict[str, Any]) -> str:
    """Denormalized ``slug@version`` slicing key for a job snapshot."""
    slug = snapshot.get("bundle_slug") or ""
    if not slug:
        return ""
    return f"{slug}@{snapshot.get('bundle_version') or 1}"


def _snapshot_prompt_hash(snapshot: dict[str, Any]) -> str:
    """Stable hash of prompt templates + app requirement.

    Deliberately excludes seed and llm config so jobs generated from the same
    prompt material share a hash — this is the grouping key for comparing
    results across models, seeds, and repeats. Per-run identity (including
    seed/llm) lives in :func:`run_fingerprint`.
    """
    payload = {
        "prompt_templates": snapshot.get("prompt_templates"),
        "app_requirement": snapshot.get("app_requirement"),
        "blocks": [{"slug": b["slug"], "version": b["version"]} for b in snapshot.get("blocks", [])],
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def run_fingerprint(snapshot: dict[str, Any]) -> str:
    """Hash of the complete run configuration (prompts + seed + llm params).

    Two jobs with equal fingerprints were configured identically — an exact
    replay should reproduce the run up to provider nondeterminism.
    """
    payload = {
        "prompt_hash": snapshot.get("prompt_hash"),
        "seed": snapshot.get("seed"),
        "llm": {
            k: v
            for k, v in (snapshot.get("llm") or {}).items()
            # pricing/context are provenance, not run configuration
            if k in ("model_slug", *SAMPLING_KEYS)
        },
        "scaffolding_slug": snapshot.get("scaffolding_slug"),
    }
    # Skeleton content identity: two runs on different stack versions are
    # not exact replays of each other.
    stack = snapshot.get("stack") or {}
    if stack.get("content_hash"):
        payload["stack_content_hash"] = stack["content_hash"]
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def derive_experiment_seed(base_seed: int, *parts: Any) -> int:
    """Deterministic per-run seed from an experiment base seed and cell identity.

    Same (base_seed, condition, app, repeat) always yields the same seed, so
    re-launching an experiment reproduces the exact seed matrix.
    """
    encoded = json.dumps([base_seed, *[str(p) for p in parts]])
    digest = hashlib.sha256(encoded.encode()).digest()
    return int.from_bytes(digest[:4], "big") % 2_147_483_647


def experiment_cell_seed(
    base_seed: int,
    *,
    agent: bool,
    condition_id: int | None,
    app_id: int,
    repeat_index: int,
) -> int:
    """The seed of one experiment cell, as an experiment launch derives it.

    Agent mode uses common random numbers: every condition of an (app,
    repeat) block shares one seed, so paired comparisons differ only in the
    condition. Scaffolding keeps its historical per-condition derivation so
    old launches stay reproducible.
    """
    parts = (app_id, repeat_index) if agent else (condition_id, app_id, repeat_index)
    return derive_experiment_seed(base_seed, *parts)


def batch_cell_seed(base_seed: int, app_slug: str | None, trial: int) -> int:
    """The seed of one (task, trial) cell of an ad-hoc agent batch, shared by every model and harness preset."""
    return derive_experiment_seed(base_seed, app_slug or "task", trial)


def scaffolding_slug_from_manifest(job: GenerationJob) -> str:
    """Canonical stack slug for a job (manifest-aware)."""
    return resolve_stack_slug(job)
