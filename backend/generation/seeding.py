"""Seed generation templates from ``backend/generation/data/`` after every migrate.

Mirrors :mod:`backend.analysis.seeding`: idempotent, content-hash aware, and
never raises out of the ``post_migrate`` signal (see ``apps.py``).

Content blocks, template bundles, and app requirements are versioned by
content hash — see :mod:`backend.generation.services.versioning`. A source
file's ``version:`` key (where present) is not authoritative; the seeder
decides the next version by comparing content hashes against what's already
in the database, so a job's ``resolved_bundle`` snapshot always keeps
resolving to the exact content it captured even after the source file moves
on.

Note: under pytest's ``--reuse-db`` the post_migrate signal only fires when
the test database is (re)created, so data/ changes require ``--create-db``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from django.db import DEFAULT_DB_ALIAS

from backend.generation.services.versioning import content_hash

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
REQUIREMENTS_DIR = DATA_DIR / "requirements"
MANIFESTS_DIR = REQUIREMENTS_DIR / "manifests"
PROMPTS_DIR = DATA_DIR / "prompts"
BLOCKS_DIR = DATA_DIR / "blocks"
CATALOG_PATH = BLOCKS_DIR / "catalog.yaml"

SCAFFOLDING_SEEDS = [
    {
        "slug": "flask-react",
        "name": "Flask + React",
        "description": (
            "Single-container scaffolding: Flask 3.x serves both the REST API "
            "and a pre-built React 18 SPA (Vite + Tailwind)."
        ),
        "tech_stack": {
            "frontend": "React 18 + Vite + Tailwind CSS",
            "backend": "Flask 3.x + SQLAlchemy + JWT",
            "database": "SQLite",
            "runtime": "Single Docker container",
        },
        "substitution_vars": ["{{PROJECT_NAME}}", "{{app_port|8000}}"],
        "is_default": True,
    },
    {
        "slug": "fastapi-vue",
        "name": "FastAPI + Vue",
        "description": (
            "Single-container FastAPI backend with a Vue 3 SPA. "
            "Useful for comparing Python API generation against the Flask stack."
        ),
        "tech_stack": {
            "frontend": "Vue 3 + Vite",
            "backend": "FastAPI + SQLAlchemy + JWT",
            "database": "SQLite",
            "runtime": "Single Docker container",
        },
        "substitution_vars": ["{{PROJECT_NAME}}", "{{app_port|8000}}"],
        "is_default": False,
    },
    {
        "slug": "fastapi-react",
        "name": "FastAPI + React",
        "description": (
            "Single-container FastAPI backend paired with a React 18 SPA "
            "for stack-comparison and prompt-evaluation runs."
        ),
        "tech_stack": {
            "frontend": "React 18 + Vite + Tailwind CSS",
            "backend": "FastAPI + SQLAlchemy + JWT",
            "database": "SQLite",
            "runtime": "Single Docker container",
        },
        "substitution_vars": ["{{PROJECT_NAME}}", "{{app_port|8000}}"],
        "is_default": False,
    },
]

# Legacy PromptTemplate rows, kept only for admin-UI compatibility until that
# model is removed; not content-hash versioned.
PROMPT_TEMPLATE_SEEDS = [
    {
        "slug": "v2-backend-system",
        "name": "Backend System Prompt v2",
        "stage": "backend",
        "role": "system",
        "description": "Default system prompt for Flask backend generation",
        "path": "v2/backend/system.md.jinja2",
        "is_default": True,
    },
    {
        "slug": "v2-backend-user",
        "name": "Backend User Prompt v2",
        "stage": "backend",
        "role": "user",
        "description": "Default user prompt for Flask backend generation",
        "path": "v2/backend/user.md.jinja2",
        "is_default": True,
    },
    {
        "slug": "v2-frontend-system",
        "name": "Frontend System Prompt v2",
        "stage": "frontend",
        "role": "system",
        "description": "Default system prompt for React frontend generation",
        "path": "v2/frontend/system.md.jinja2",
        "is_default": True,
    },
    {
        "slug": "v2-frontend-user",
        "name": "Frontend User Prompt v2",
        "stage": "frontend",
        "role": "user",
        "description": "Default user prompt for React frontend generation",
        "path": "v2/frontend/user.md.jinja2",
        "is_default": True,
    },
    {
        "slug": "fastapi-backend-system-v1",
        "name": "FastAPI Backend System Prompt v1",
        "stage": "backend",
        "role": "system",
        "description": "FastAPI-oriented system prompt for sample generation",
        "path": "fastapi/backend/system.md.jinja2",
        "is_default": False,
    },
    {
        "slug": "fastapi-backend-user-v1",
        "name": "FastAPI Backend User Prompt v1",
        "stage": "backend",
        "role": "user",
        "description": "FastAPI-oriented user prompt for sample generation",
        "path": "fastapi/backend/user.md.jinja2",
        "is_default": False,
    },
    {
        "slug": "vue-frontend-system-v1",
        "name": "Vue Frontend System Prompt v1",
        "stage": "frontend",
        "role": "system",
        "description": "Vue-oriented system prompt for sample generation",
        "path": "vue/frontend/system.md.jinja2",
        "is_default": False,
    },
    {
        "slug": "vue-frontend-user-v1",
        "name": "Vue Frontend User Prompt v1",
        "stage": "frontend",
        "role": "user",
        "description": "Vue-oriented user prompt for sample generation",
        "path": "vue/frontend/user.md.jinja2",
        "is_default": False,
    },
]

# ContentBlock(block_type=PROMPT_STAGE) rows assembled from the same prompt
# files, keyed by a stable slug independent of PromptTemplate's.
PROMPT_STAGE_SEEDS = [
    {"slug": "base-backend-system", "stage": "backend", "role": "system", "path": "v2/backend/system.md.jinja2"},
    {"slug": "base-backend-user", "stage": "backend", "role": "user", "path": "v2/backend/user.md.jinja2"},
    {"slug": "base-frontend-system", "stage": "frontend", "role": "system", "path": "v2/frontend/system.md.jinja2"},
    {"slug": "base-frontend-user", "stage": "frontend", "role": "user", "path": "v2/frontend/user.md.jinja2"},
    {
        "slug": "fastapi-backend-system",
        "stage": "backend",
        "role": "system",
        "path": "fastapi/backend/system.md.jinja2",
    },
    {"slug": "fastapi-backend-user", "stage": "backend", "role": "user", "path": "fastapi/backend/user.md.jinja2"},
    {"slug": "vue-frontend-system", "stage": "frontend", "role": "system", "path": "vue/frontend/system.md.jinja2"},
    {"slug": "vue-frontend-user", "stage": "frontend", "role": "user", "path": "vue/frontend/user.md.jinja2"},
]


def _read_prompt_file(relative_path: str) -> str:
    path = PROMPTS_DIR / relative_path
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Prompt not found: %s", path)
    return ""


def seed_scaffolding(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Upsert the (soon-removed) ``ScaffoldingTemplate`` catalog rows. Not versioned."""
    from backend.generation.models import ScaffoldingTemplate

    created = updated = 0
    for seed in SCAFFOLDING_SEEDS:
        obj, was_created = ScaffoldingTemplate.objects.using(using).update_or_create(
            slug=seed["slug"],
            defaults={
                "name": seed["name"],
                "description": seed["description"],
                "tech_stack": seed["tech_stack"],
                "substitution_vars": seed["substitution_vars"],
                "is_default": seed["is_default"],
            },
        )
        created += was_created
        updated += not was_created
        if log:
            log(f"  {'Created' if was_created else 'Updated'} scaffolding: {obj.name}")
    return created, updated


def _requirement_hash_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": data.get("category", ""),
        "description": data.get("description", ""),
        "backend_requirements": data.get("backend_requirements", []),
        "frontend_requirements": data.get("frontend_requirements", []),
        "admin_requirements": data.get("admin_requirements", []),
        "api_endpoints": data.get("api_endpoints", []),
        "data_model": data.get("data_model", {}),
        "admin_api_endpoints": data.get("admin_api_endpoints", []),
        "difficulty": data.get("difficulty", ""),
    }


def seed_requirements(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Upsert ``AppRequirementTemplate`` rows from ``data/requirements/*.json``.

    One mutable row per slug; ``version`` bumps when the spec content changes
    so a snapshot's embedded ``app_requirement.version`` stays meaningful.
    """
    from backend.generation.models import AppRequirementTemplate

    if not REQUIREMENTS_DIR.exists():
        if log:
            log(f"  Requirements directory not found: {REQUIREMENTS_DIR}")
        return 0, 0

    created = updated = 0
    for json_path in sorted(REQUIREMENTS_DIR.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to read %s", json_path)
            continue

        slug = data.get("slug", json_path.stem)
        new_hash = content_hash(_requirement_hash_payload(data))
        existing = AppRequirementTemplate.objects.using(using).filter(slug=slug).first()

        prior_version = existing.version if existing else 1
        version = prior_version
        content_changed = bool(existing and existing.content_hash and existing.content_hash != new_hash)
        if content_changed:
            version = existing.version + 1

        obj, was_created = AppRequirementTemplate.objects.using(using).update_or_create(
            slug=slug,
            defaults={
                "name": data.get("name", json_path.stem),
                "category": data.get("category", ""),
                "description": data.get("description", ""),
                "backend_requirements": data.get("backend_requirements", []),
                "frontend_requirements": data.get("frontend_requirements", []),
                "admin_requirements": data.get("admin_requirements", []),
                "api_endpoints": data.get("api_endpoints", []),
                "data_model": data.get("data_model", {}),
                "admin_api_endpoints": data.get("admin_api_endpoints", []),
                "difficulty": data.get("difficulty", ""),
                "spec_schema_version": data.get("spec_schema_version", 1),
                "content_hash": new_hash,
                "version": version,
                "is_default": True,
            },
        )
        created += was_created
        updated += not was_created
        if log:
            bumped = f" (bumped to v{version})" if content_changed else ""
            log(f"  {'Created' if was_created else 'Updated'} requirement: {obj.name}{bumped}")

    return created, updated


def _prompt_stage_seeds() -> list[dict[str, Any]]:
    return [{**seed, "content": _read_prompt_file(seed["path"])} for seed in PROMPT_STAGE_SEEDS]


def _yaml_block_seeds() -> list[dict[str, Any]]:
    seeds = []
    for yaml_path in sorted(BLOCKS_DIR.rglob("*.yaml")):
        if yaml_path.name == "catalog.yaml":
            continue
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            logger.exception("Failed to read block %s", yaml_path)
            continue
        if not data.get("slug"):
            continue
        seeds.append(
            {
                "slug": data["slug"],
                "block_type": data.get("block_type", "prompt_tone"),
                "name": data.get("name", data["slug"]),
                "description": data.get("description", ""),
                "content": data.get("content", ""),
                "metadata": data.get("metadata") or {},
            },
        )
    return seeds


def _upsert_content_block_version(
    ContentBlockModel: Any,  # noqa: N803
    using: str,
    seed: dict[str, Any],
    *,
    default_block_type: str,
    log,
) -> tuple[int, int]:
    """Create a new version if content changed; update cosmetic fields in place otherwise."""
    slug = seed["slug"]
    block_type = seed.get("block_type", default_block_type)
    metadata = seed.get("metadata") or {}
    new_hash = content_hash({"block_type": block_type, "content": seed["content"], "metadata": metadata})

    latest = ContentBlockModel.objects.using(using).filter(slug=slug, is_system=True).order_by("-version").first()

    if latest and latest.content_hash == new_hash:
        changed = latest.name != seed["name"] or latest.description != seed.get("description", "")
        if changed:
            latest.name = seed["name"]
            latest.description = seed.get("description", "")
            latest.save(update_fields=["name", "description", "updated_at"])
        if log:
            log(f"  Up to date block: {slug} v{latest.version}")
        return 0, int(changed)

    next_version = (latest.version + 1) if latest else 1
    ContentBlockModel.objects.using(using).create(
        block_type=block_type,
        slug=slug,
        version=next_version,
        name=seed["name"],
        description=seed.get("description", ""),
        content=seed["content"],
        metadata=metadata,
        content_hash=new_hash,
        is_system=True,
    )
    action = "Created" if latest is None else f"Bumped to v{next_version}"
    if log:
        log(f"  {action} block: {slug}")
    return int(latest is None), int(latest is not None)


def seed_content_blocks(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Version-aware upsert of ``ContentBlock`` rows from prompt files + block YAMLs."""
    from backend.generation.models import ContentBlock

    created = updated = 0
    for seed in _prompt_stage_seeds():
        seed_with_meta = {**seed, "metadata": {"stage": seed["stage"], "role": seed["role"]}}
        c, u = _upsert_content_block_version(
            ContentBlock,
            using,
            {
                "slug": seed_with_meta["slug"],
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "name": seed_with_meta["slug"].replace("-", " ").title(),
                "description": f"{seed_with_meta['stage']} {seed_with_meta['role']} prompt",
                "content": seed_with_meta["content"],
                "metadata": seed_with_meta["metadata"],
            },
            default_block_type=ContentBlock.BlockType.PROMPT_STAGE,
            log=log,
        )
        created += c
        updated += u

    for seed in _yaml_block_seeds():
        c, u = _upsert_content_block_version(
            ContentBlock,
            using,
            seed,
            default_block_type="prompt_tone",
            log=log,
        )
        created += c
        updated += u

    return created, updated


def _load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.is_file():
        return {}
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}


def _upsert_bundle_version(
    TemplateBundleModel: Any,  # noqa: N803
    using: str,
    *,
    slug: str,
    name: str,
    description: str,
    scaffolding_slug: str,
    block_refs: list[dict[str, Any]],
    is_default: bool,
    llm_config: dict[str, Any] | None = None,
    log=None,
) -> tuple[int, int]:
    """Create a new bundle version if its behavior-defining fields changed."""
    llm_config = llm_config or {}
    new_hash = content_hash({"scaffolding_slug": scaffolding_slug, "block_refs": block_refs, "llm_config": llm_config})

    latest = TemplateBundleModel.objects.using(using).filter(slug=slug, is_system=True).order_by("-version").first()

    if latest and latest.content_hash == new_hash:
        changed = latest.name != name or latest.description != description or latest.is_default != is_default
        if changed:
            latest.name = name
            latest.description = description
            latest.is_default = is_default
            latest.save(update_fields=["name", "description", "is_default", "updated_at"])
        if log:
            log(f"  Up to date bundle: {slug} v{latest.version}")
        return 0, int(changed)

    next_version = (latest.version + 1) if latest else 1
    TemplateBundleModel.objects.using(using).create(
        name=name,
        slug=slug,
        version=next_version,
        description=description,
        scaffolding_slug=scaffolding_slug,
        block_refs=block_refs,
        llm_config=llm_config,
        content_hash=new_hash,
        is_system=True,
        is_default=is_default,
    )
    action = "Created" if latest is None else f"Bumped to v{next_version}"
    if log:
        log(f"  {action} bundle: {slug}")
    return int(latest is None), int(latest is not None)


def seed_template_bundles(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Version-aware upsert of system ``TemplateBundle`` rows from ``blocks/catalog.yaml``."""
    from backend.generation.models import TemplateBundle

    catalog = _load_catalog()
    created = updated = 0
    for bundle_data in catalog.get("bundles", []):
        c, u = _upsert_bundle_version(
            TemplateBundle,
            using,
            slug=bundle_data["slug"],
            name=bundle_data.get("name", bundle_data["slug"]),
            description=bundle_data.get("description", ""),
            scaffolding_slug=bundle_data.get("scaffolding_slug", "flask-react"),
            block_refs=bundle_data.get("block_refs", []),
            is_default=bundle_data.get("is_default", False),
            log=log,
        )
        created += c
        updated += u
    return created, updated


def seed_app_bundles(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Version-aware upsert of per-app pilot bundles from ``requirements/manifests/*.yaml``.

    Deliberately does NOT auto-generate an ``app-{slug}`` bundle for every
    requirement — apps without a real manifest fall back to the system
    default bundle at resolve time (see ``bundle_resolver.get_bundle_for_app``)
    instead of carrying a near-duplicate row.
    """
    from backend.generation.models import TemplateBundle

    if not MANIFESTS_DIR.exists():
        return 0, 0

    created = updated = 0
    for yaml_path in sorted(MANIFESTS_DIR.glob("*.yaml")):
        try:
            manifest = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            logger.exception("Failed to read manifest %s", yaml_path)
            continue

        app_slug = manifest.get("app_slug")
        if not app_slug:
            continue

        base_slug = manifest.get("base_bundle_slug", "system-scaffolding-standard")
        base = TemplateBundle.objects.using(using).filter(slug=base_slug, is_system=True).order_by("-version").first()
        if not base:
            if log:
                log(f"  Skip {yaml_path.name}: base bundle {base_slug} missing")
            continue

        refs = list(base.block_refs or [])
        for extra in manifest.get("extra_block_refs", []):
            if extra not in refs:
                refs.append(extra)

        bundle_slug = manifest.get("bundle_slug") or f"app-{app_slug.replace('_', '-')}"
        c, u = _upsert_bundle_version(
            TemplateBundle,
            using,
            slug=bundle_slug,
            name=manifest.get("name", bundle_slug),
            description=manifest.get("description", ""),
            scaffolding_slug=manifest.get("scaffolding_slug", base.scaffolding_slug),
            block_refs=refs,
            is_default=False,
            log=log,
        )
        created += c
        updated += u
    return created, updated


def seed_prompt_templates(*, using: str = DEFAULT_DB_ALIAS, log=None) -> tuple[int, int]:
    """Legacy ``PromptTemplate`` rows, kept for admin UI compatibility. Not versioned."""
    from backend.generation.models import PromptTemplate

    created = updated = 0
    for data in PROMPT_TEMPLATE_SEEDS:
        obj, was_created = PromptTemplate.objects.using(using).update_or_create(
            slug=data["slug"],
            defaults={
                "name": data["name"],
                "stage": data["stage"],
                "role": data["role"],
                "content": _read_prompt_file(data["path"]),
                "description": data["description"],
                "is_default": data["is_default"],
            },
        )
        created += was_created
        updated += not was_created
        if log:
            log(f"  {'Created' if was_created else 'Updated'} prompt: {obj.name}")
    return created, updated


def seed_all(*, using: str = DEFAULT_DB_ALIAS, log=None) -> dict[str, tuple[int, int]]:
    """Run every seeder in dependency order (bundles reference blocks by slug+version)."""
    return {
        "scaffolding": seed_scaffolding(using=using, log=log),
        "requirements": seed_requirements(using=using, log=log),
        "prompt_templates": seed_prompt_templates(using=using, log=log),
        "content_blocks": seed_content_blocks(using=using, log=log),
        "template_bundles": seed_template_bundles(using=using, log=log),
        "app_bundles": seed_app_bundles(using=using, log=log),
    }
