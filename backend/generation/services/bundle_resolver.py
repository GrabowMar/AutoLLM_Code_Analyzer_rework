"""Resolve template bundles into immutable job snapshots for scaffolding runs."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.db.models import Q
from django.utils import timezone

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import TemplateBundle
from backend.runtime.services.scaffolding import resolve_stack_slug

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import GenerationJob
    from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

BUNDLE_SCHEMA_VERSION = 2
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


def load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.is_file():
        return {}
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}


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


def _visible_bundles(user: AbstractUser | None):
    """Non-archived bundles visible to *user*, latest version of each slug first."""
    qs = TemplateBundle.objects.filter(is_archived=False).order_by("slug", "-version")
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)


def get_bundle_for_app(
    app_req: AppRequirementTemplate,
    user: AbstractUser | None = None,
    scaffolding_slug: str | None = None,
) -> TemplateBundle | None:
    """Latest version of the per-app pilot bundle if seeded, else system default."""
    slug = bundle_slug_for_app(app_req.slug)
    qs = _visible_bundles(user).filter(slug=slug)
    if scaffolding_slug:
        bundle = qs.filter(scaffolding_slug=scaffolding_slug).first()
        if bundle:
            return bundle
        return get_default_bundle(scaffolding_slug=scaffolding_slug, user=user)

    return qs.first() or get_default_bundle(user=user)


def get_default_bundle(
    scaffolding_slug: str | None = None,
    user: AbstractUser | None = None,
) -> TemplateBundle | None:
    """Latest version of the applicable default bundle."""
    qs = _visible_bundles(user)
    if scaffolding_slug:
        matching_default = qs.filter(
            scaffolding_slug=scaffolding_slug,
            is_default=True,
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
        qs.filter(is_default=True, is_system=True).first()
        or qs.filter(
            slug="system-scaffolding-standard",
            is_system=True,
        ).first()
    )


def resolve_bundle_for_job(
    *,
    template_bundle: TemplateBundle | None,
    scaffolding_slug: str,
    user: AbstractUser | None,
) -> tuple[list[dict[str, Any]], str, str, int]:
    """Return (block_refs, scaffolding_slug, bundle_slug, bundle_version) for snapshot building."""
    if template_bundle:
        return (
            list(template_bundle.block_refs or []),
            template_bundle.scaffolding_slug,
            template_bundle.slug,
            template_bundle.version,
        )

    if bundle := get_default_bundle(scaffolding_slug=scaffolding_slug, user=user):
        return list(bundle.block_refs or []), bundle.scaffolding_slug, bundle.slug, bundle.version

    catalog = load_catalog()
    refs = catalog.get("default_block_refs", [])
    return refs, scaffolding_slug, "catalog", 1


def build_resolved_bundle(
    *,
    app_requirement: AppRequirementTemplate,
    template_bundle: TemplateBundle | None,
    scaffolding_slug: str,
    model: LLMModel | None,
    temperature: float,
    max_tokens: int,
    user: AbstractUser | None,
    experiment_seed: int | None = None,
    legacy_backend_system: str | None = None,
    legacy_frontend_system: str | None = None,
) -> dict[str, Any]:
    """Build the full immutable snapshot dict for ``GenerationJob.resolved_bundle``."""
    block_refs, bundle_scaffold_slug, resolved_bundle_slug, resolved_bundle_version = resolve_bundle_for_job(
        template_bundle=template_bundle,
        scaffolding_slug=scaffolding_slug,
        user=user,
    )
    resolved_blocks = resolve_block_refs(block_refs, user)
    prompt_templates = assemble_prompt_templates(resolved_blocks)

    if legacy_backend_system and not prompt_templates["backend"]["system"]:
        prompt_templates["backend"]["system"] = legacy_backend_system
    if legacy_frontend_system and not prompt_templates["frontend"]["system"]:
        prompt_templates["frontend"]["system"] = legacy_frontend_system

    app_dict = app_requirement_to_dict(app_requirement)
    bundle_slug = template_bundle.slug if template_bundle else resolved_bundle_slug

    # Reproducibility seed for an experiment run, not a crypto value.
    seed = experiment_seed if experiment_seed is not None else random.randint(0, 2_147_483_647)  # noqa: S311

    llm_section: dict[str, Any] = {
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if model:
        llm_section["model_id"] = model.id
        llm_section["model_slug"] = model.model_id
        # Pricing/context provenance: LLMModel rows mutate in place on catalog
        # sync, so the values in effect at job creation must be frozen here.
        llm_section["provider"] = model.provider
        llm_section["context_window"] = model.context_window
        llm_section["max_output_tokens"] = model.max_output_tokens
        llm_section["input_price_per_token"] = model.input_price_per_token
        llm_section["output_price_per_token"] = model.output_price_per_token
        llm_section["pricing_snapshot_at"] = timezone.now().isoformat()

    snapshot: dict[str, Any] = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_slug": bundle_slug,
        "bundle_version": resolved_bundle_version,
        "scaffolding_slug": bundle_scaffold_slug or scaffolding_slug,
        "llm": llm_section,
        "seed": seed,
        "blocks": resolved_blocks,
        "app_requirement": app_dict,
        "prompt_templates": prompt_templates,
        "prompts": {},
    }
    snapshot["prompt_hash"] = _snapshot_prompt_hash(snapshot)
    snapshot["run_fingerprint"] = run_fingerprint(snapshot)
    return snapshot


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
    """Create and return resolved bundle for a scaffolding job (caller saves on job)."""
    if not job.app_requirement:
        msg = "Scaffolding job requires app_requirement"
        raise ValueError(msg)

    scaffolding_slug = resolve_stack_slug(job)
    legacy_backend = job.backend_prompt_template.content if job.backend_prompt_template else None
    legacy_frontend = job.frontend_prompt_template.content if job.frontend_prompt_template else None

    snapshot = build_resolved_bundle(
        app_requirement=job.app_requirement,
        template_bundle=job.template_bundle,
        scaffolding_slug=scaffolding_slug,
        model=job.model,
        temperature=job.temperature,
        max_tokens=job.max_tokens,
        user=job.created_by,
        experiment_seed=job.experiment_seed,
        legacy_backend_system=legacy_backend,
        legacy_frontend_system=legacy_frontend,
    )
    from backend.generation.services.prompt_renderer import PromptRenderer

    return attach_rendered_backend_prompts(snapshot, PromptRenderer())


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
            if k in ("model_slug", "temperature", "max_tokens", "top_p")
        },
        "scaffolding_slug": snapshot.get("scaffolding_slug"),
    }
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


def scaffolding_slug_from_manifest(job: GenerationJob) -> str:
    """Canonical stack slug for a job (manifest-aware)."""
    return resolve_stack_slug(job)
