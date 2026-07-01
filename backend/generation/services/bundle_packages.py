"""Import/export helpers for portable generation template packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import PromptTemplate
from backend.generation.models import ScaffoldingTemplate
from backend.generation.models import TemplateBundle

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

BUNDLE_PACKAGE_SCHEMA_VERSION = 1
BUNDLE_PACKAGE_KIND = "llm-lab-template-bundle"
TEMPLATE_PACKAGE_SCHEMA_VERSION = 1
TEMPLATE_PACKAGE_KIND = "llm-lab-template-package"
ALLOWED_CONFLICT_STRATEGIES = {"rename", "overwrite", "error"}
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

STARTER_TEMPLATE_PACKAGES = {
    "fastapi-stack-starter": {
        "name": "FastAPI starter pack",
        "description": (
            "Imports FastAPI + React/Vue scaffoldings, three richer sample app requirements, "
            "stack-specific prompts, and ready-to-use bundles."
        ),
        "scaffolding_templates": [
            {
                "name": "FastAPI + Vue",
                "slug": "fastapi-vue",
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
            },
            {
                "name": "FastAPI + React",
                "slug": "fastapi-react",
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
            },
        ],
        "app_template_files": [
            "requirements/analytics_campaign_monitor.json",
            "requirements/operations_incident_center.json",
            "requirements/commerce_subscription_billing.json",
        ],
        "prompt_templates": [
            {
                "name": "FastAPI Backend System Prompt v1",
                "slug": "fastapi-backend-system-v1",
                "stage": "backend",
                "role": "system",
                "description": "FastAPI-oriented system prompt for sample generation",
                "path": "prompts/fastapi/backend/system.md.jinja2",
                "version": 1,
            },
            {
                "name": "FastAPI Backend User Prompt v1",
                "slug": "fastapi-backend-user-v1",
                "stage": "backend",
                "role": "user",
                "description": "FastAPI-oriented user prompt for sample generation",
                "path": "prompts/fastapi/backend/user.md.jinja2",
                "version": 1,
            },
            {
                "name": "Vue Frontend System Prompt v1",
                "slug": "vue-frontend-system-v1",
                "stage": "frontend",
                "role": "system",
                "description": "Vue-oriented system prompt for sample generation",
                "path": "prompts/vue/frontend/system.md.jinja2",
                "version": 1,
            },
            {
                "name": "Vue Frontend User Prompt v1",
                "slug": "vue-frontend-user-v1",
                "stage": "frontend",
                "role": "user",
                "description": "Vue-oriented user prompt for sample generation",
                "path": "prompts/vue/frontend/user.md.jinja2",
                "version": 1,
            },
        ],
        "blocks": [
            {
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "slug": "fastapi-backend-system",
                "version": 1,
                "name": "Fastapi Backend System",
                "description": "backend system prompt",
                "path": "prompts/fastapi/backend/system.md.jinja2",
                "metadata": {"stage": "backend", "role": "system"},
            },
            {
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "slug": "fastapi-backend-user",
                "version": 1,
                "name": "Fastapi Backend User",
                "description": "backend user prompt",
                "path": "prompts/fastapi/backend/user.md.jinja2",
                "metadata": {"stage": "backend", "role": "user"},
            },
            {
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "slug": "vue-frontend-system",
                "version": 1,
                "name": "Vue Frontend System",
                "description": "frontend system prompt",
                "path": "prompts/vue/frontend/system.md.jinja2",
                "metadata": {"stage": "frontend", "role": "system"},
            },
            {
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "slug": "vue-frontend-user",
                "version": 1,
                "name": "Vue Frontend User",
                "description": "frontend user prompt",
                "path": "prompts/vue/frontend/user.md.jinja2",
                "metadata": {"stage": "frontend", "role": "user"},
            },
            {
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "slug": "base-frontend-system",
                "version": 1,
                "name": "Base Frontend System",
                "description": "frontend system prompt",
                "path": "prompts/v2/frontend/system.md.jinja2",
                "metadata": {"stage": "frontend", "role": "system"},
            },
            {
                "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                "slug": "base-frontend-user",
                "version": 1,
                "name": "Base Frontend User",
                "description": "frontend user prompt",
                "path": "prompts/v2/frontend/user.md.jinja2",
                "metadata": {"stage": "frontend", "role": "user"},
            },
            {"yaml_path": "blocks/prompt_tone/tone-standard.yaml"},
            {"yaml_path": "blocks/prompt_rules/rules-fastapi-global.yaml"},
        ],
        "bundles": [
            {
                "name": "FastAPI + Vue standard",
                "slug": "system-fastapi-vue-standard",
                "description": (
                    "FastAPI backend prompts paired with Vue frontend prompts and shared FastAPI rules"
                ),
                "scaffolding_slug": "fastapi-vue",
                "block_refs": [
                    {"type": "prompt_stage", "slug": "fastapi-backend-system", "version": 1},
                    {"type": "prompt_stage", "slug": "fastapi-backend-user", "version": 1},
                    {"type": "prompt_stage", "slug": "vue-frontend-system", "version": 1},
                    {"type": "prompt_stage", "slug": "vue-frontend-user", "version": 1},
                    {"type": "prompt_tone", "slug": "tone-standard", "version": 1},
                    {"type": "prompt_rules", "slug": "rules-fastapi-global", "version": 1},
                ],
                "llm_config": {},
            },
            {
                "name": "FastAPI + React standard",
                "slug": "system-fastapi-react-standard",
                "description": "FastAPI backend prompts paired with the default React frontend prompts",
                "scaffolding_slug": "fastapi-react",
                "block_refs": [
                    {"type": "prompt_stage", "slug": "fastapi-backend-system", "version": 1},
                    {"type": "prompt_stage", "slug": "fastapi-backend-user", "version": 1},
                    {"type": "prompt_stage", "slug": "base-frontend-system", "version": 1},
                    {"type": "prompt_stage", "slug": "base-frontend-user", "version": 1},
                    {"type": "prompt_tone", "slug": "tone-standard", "version": 1},
                    {"type": "prompt_rules", "slug": "rules-fastapi-global", "version": 1},
                ],
                "llm_config": {},
            },
        ],
    },
}


def visible_scaffolding_templates_for(user: AbstractUser | None):
    qs = ScaffoldingTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_app_templates_for(user: AbstractUser | None):
    qs = AppRequirementTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_prompt_templates_for(user: AbstractUser | None):
    qs = PromptTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_blocks_for(user: AbstractUser | None):
    qs = ContentBlock.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)


def visible_bundles_for(user: AbstractUser | None):
    qs = TemplateBundle.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)


def list_starter_template_packages() -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for slug in sorted(STARTER_TEMPLATE_PACKAGES):
        package = build_starter_template_package(slug)
        assets = package.get("assets") or {}
        packages.append(
            {
                "slug": slug,
                "name": str(package.get("starter_name", slug)),
                "description": str(package.get("starter_description", "")),
                "scaffolding_count": len(assets.get("scaffolding_templates", [])),
                "app_template_count": len(assets.get("app_templates", [])),
                "prompt_template_count": len(assets.get("prompt_templates", [])),
                "block_count": len(assets.get("blocks", [])),
                "bundle_count": len(assets.get("bundles", [])),
            },
        )
    return packages


def build_starter_template_package(slug: str) -> dict[str, Any]:
    spec = STARTER_TEMPLATE_PACKAGES.get(slug)
    if not spec:
        msg = f"Unknown starter package: {slug}"
        raise FileNotFoundError(msg)

    return {
        "template_package_schema_version": TEMPLATE_PACKAGE_SCHEMA_VERSION,
        "kind": TEMPLATE_PACKAGE_KIND,
        "exported_at": timezone.now().isoformat(),
        "starter_slug": slug,
        "starter_name": spec["name"],
        "starter_description": spec["description"],
        "assets": {
            "scaffolding_templates": list(spec["scaffolding_templates"]),
            "app_templates": [
                _load_json_data_file(relative_path)
                for relative_path in spec["app_template_files"]
            ],
            "prompt_templates": [
                {
                    "name": prompt["name"],
                    "slug": prompt["slug"],
                    "stage": prompt["stage"],
                    "role": prompt["role"],
                    "content": _read_data_text(prompt["path"]),
                    "description": prompt["description"],
                    "version": prompt["version"],
                }
                for prompt in spec["prompt_templates"]
            ],
            "blocks": [_build_starter_block(block) for block in spec["blocks"]],
            "bundles": list(spec["bundles"]),
        },
    }


def import_starter_template_package(
    *,
    slug: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> dict[str, list[Any]]:
    package = build_starter_template_package(slug)
    return import_template_package(
        package_text=json.dumps(package),
        user=user,
        conflict_strategy=conflict_strategy,
    )


def export_bundle_package(bundle: TemplateBundle) -> dict[str, Any]:
    return {
        "bundle_package_schema_version": BUNDLE_PACKAGE_SCHEMA_VERSION,
        "kind": BUNDLE_PACKAGE_KIND,
        "exported_at": timezone.now().isoformat(),
        "bundle": _serialize_bundle(bundle),
        "blocks": [
            _serialize_block(block)
            for block in _blocks_for_bundle_refs(bundle.block_refs or [])
        ],
    }


def export_template_package(
    *,
    user: AbstractUser,
    scaffolding_slugs: list[str] | None = None,
    app_template_slugs: list[str] | None = None,
    prompt_template_slugs: list[str] | None = None,
    bundle_slugs: list[str] | None = None,
    block_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scaffolding_slugs = scaffolding_slugs or []
    app_template_slugs = app_template_slugs or []
    prompt_template_slugs = prompt_template_slugs or []
    bundle_slugs = bundle_slugs or []
    block_refs = block_refs or []

    bundles = list(visible_bundles_for(user).filter(slug__in=bundle_slugs).order_by("name"))
    explicit_blocks = []
    for ref in block_refs:
        block = visible_blocks_for(user).filter(
            slug=ref.get("slug"),
            version=int(ref.get("version", 1)),
        ).first()
        if block:
            explicit_blocks.append(block)

    bundle_blocks = _blocks_for_bundle_refs(
        [ref for bundle in bundles for ref in (bundle.block_refs or [])],
    )
    blocks = _dedupe_blocks([*explicit_blocks, *bundle_blocks])

    scaffolding = list(
        visible_scaffolding_templates_for(user)
        .filter(slug__in=scaffolding_slugs)
        .order_by("name"),
    )
    scaffolding_by_slug = {item.slug: item for item in scaffolding}
    for bundle in bundles:
        if bundle.scaffolding_slug and bundle.scaffolding_slug not in scaffolding_by_slug:
            scaffold = visible_scaffolding_templates_for(user).filter(
                slug=bundle.scaffolding_slug,
            ).first()
            if scaffold:
                scaffolding_by_slug[scaffold.slug] = scaffold

    return {
        "template_package_schema_version": TEMPLATE_PACKAGE_SCHEMA_VERSION,
        "kind": TEMPLATE_PACKAGE_KIND,
        "exported_at": timezone.now().isoformat(),
        "assets": {
            "scaffolding_templates": [
                _serialize_scaffolding(item)
                for item in sorted(scaffolding_by_slug.values(), key=lambda value: value.name)
            ],
            "app_templates": [
                _serialize_app_template(item)
                for item in visible_app_templates_for(user)
                .filter(slug__in=app_template_slugs)
                .order_by("name")
            ],
            "prompt_templates": [
                _serialize_prompt_template(item)
                for item in visible_prompt_templates_for(user)
                .filter(slug__in=prompt_template_slugs)
                .order_by("stage", "role", "name")
            ],
            "blocks": [_serialize_block(block) for block in blocks],
            "bundles": [_serialize_bundle(bundle) for bundle in bundles],
        },
    }


def parse_bundle_package_text(package_text: str) -> dict[str, Any]:
    data = _parse_package_text(package_text)
    if data.get("kind") != BUNDLE_PACKAGE_KIND:
        msg = f"Unsupported package kind: {data.get('kind')!r}"
        raise ValueError(msg)
    if data.get("bundle_package_schema_version") != BUNDLE_PACKAGE_SCHEMA_VERSION:
        msg = (
            "Unsupported bundle package schema version: "
            f"{data.get('bundle_package_schema_version')!r}"
        )
        raise ValueError(msg)
    if not isinstance(data.get("bundle"), dict):
        msg = "Bundle package is missing a valid 'bundle' section"
        raise ValueError(msg)
    if not isinstance(data.get("blocks", []), list):
        msg = "Bundle package 'blocks' must be a list"
        raise ValueError(msg)
    return data


def parse_template_package_text(package_text: str) -> dict[str, Any]:
    data = _parse_package_text(package_text)
    if data.get("kind") != TEMPLATE_PACKAGE_KIND:
        msg = f"Unsupported package kind: {data.get('kind')!r}"
        raise ValueError(msg)
    if data.get("template_package_schema_version") != TEMPLATE_PACKAGE_SCHEMA_VERSION:
        msg = (
            "Unsupported template package schema version: "
            f"{data.get('template_package_schema_version')!r}"
        )
        raise ValueError(msg)
    assets = data.get("assets")
    if not isinstance(assets, dict):
        msg = "Template package is missing a valid 'assets' section"
        raise ValueError(msg)
    return data


def dump_bundle_package(package: dict[str, Any], fmt: str) -> tuple[str, str]:
    return _dump_package(package, fmt)


def dump_template_package(package: dict[str, Any], fmt: str) -> tuple[str, str]:
    return _dump_package(package, fmt)


def import_bundle_package(
    *,
    package_text: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> TemplateBundle:
    package = parse_bundle_package_text(package_text)
    imported = _import_assets(
        assets={
            "blocks": package.get("blocks", []),
            "bundles": [package["bundle"]],
        },
        user=user,
        conflict_strategy=conflict_strategy,
    )
    bundles = imported.get("bundles", [])
    if not bundles:
        msg = "Bundle package did not import any bundles"
        raise ValueError(msg)
    return bundles[0]


def import_template_package(
    *,
    package_text: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> dict[str, list[Any]]:
    package = parse_template_package_text(package_text)
    assets = package.get("assets") or {}
    return _import_assets(
        assets=assets,
        user=user,
        conflict_strategy=conflict_strategy,
    )


def _import_assets(
    *,
    assets: dict[str, Any],
    user: AbstractUser,
    conflict_strategy: str,
) -> dict[str, list[Any]]:
    if conflict_strategy not in ALLOWED_CONFLICT_STRATEGIES:
        msg = f"Unsupported conflict strategy: {conflict_strategy}"
        raise ValueError(msg)

    scaffolding_map: dict[str, str] = {}
    block_ref_map: dict[tuple[str, int], tuple[str, int]] = {}
    imported: dict[str, list[Any]] = {
        "scaffolding_templates": [],
        "app_templates": [],
        "prompt_templates": [],
        "blocks": [],
        "bundles": [],
    }

    with transaction.atomic():
        for raw_scaffolding in assets.get("scaffolding_templates", []):
            template = _import_scaffolding_template(
                raw_scaffolding,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            scaffolding_map[str(raw_scaffolding.get("slug", "")).strip()] = template.slug
            imported["scaffolding_templates"].append(template)

        for raw_app in assets.get("app_templates", []):
            template = _import_app_template(
                raw_app,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            imported["app_templates"].append(template)

        for raw_prompt in assets.get("prompt_templates", []):
            template = _import_prompt_template(
                raw_prompt,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            imported["prompt_templates"].append(template)

        for raw_block in assets.get("blocks", []):
            original_slug = str(raw_block.get("slug", "")).strip()
            original_version = int(raw_block.get("version", 1))
            block = _import_block(
                raw_block,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            block_ref_map[(original_slug, original_version)] = (block.slug, block.version)
            imported["blocks"].append(block)

        for raw_bundle in assets.get("bundles", []):
            bundle = _import_bundle(
                raw_bundle,
                user=user,
                conflict_strategy=conflict_strategy,
                block_ref_map=block_ref_map,
                scaffolding_map=scaffolding_map,
            )
            imported["bundles"].append(bundle)

    return imported


def _import_scaffolding_template(
    raw_template: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
) -> ScaffoldingTemplate:
    payload = _validate_scaffolding_payload(raw_template)
    existing = ScaffoldingTemplate.objects.filter(slug=payload["slug"]).first()
    if existing is None:
        return ScaffoldingTemplate.objects.create(
            **payload,
            is_default=False,
            created_by=user,
        )

    if _scaffolding_matches(existing, payload) and _is_visible_defaultish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        _assign_fields(existing, payload)
        existing.save(
            update_fields=[
                "name",
                "description",
                "tech_stack",
                "substitution_vars",
                "updated_at",
            ],
        )
        return existing

    if conflict_strategy == "error":
        msg = f"Scaffolding template conflict for slug {payload['slug']}"
        raise ValueError(msg)

    payload["slug"] = _unique_slug(ScaffoldingTemplate, payload["slug"])
    return ScaffoldingTemplate.objects.create(
        **payload,
        is_default=False,
        created_by=user,
    )


def _import_app_template(
    raw_template: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
) -> AppRequirementTemplate:
    payload = _validate_app_template_payload(raw_template)
    existing = AppRequirementTemplate.objects.filter(slug=payload["slug"]).first()
    if existing is None:
        return AppRequirementTemplate.objects.create(
            **payload,
            is_default=False,
            created_by=user,
        )

    if _app_template_matches(existing, payload) and _is_visible_defaultish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        _assign_fields(existing, payload)
        existing.save(
            update_fields=[
                "name",
                "category",
                "description",
                "backend_requirements",
                "frontend_requirements",
                "admin_requirements",
                "api_endpoints",
                "data_model",
                "admin_api_endpoints",
                "updated_at",
            ],
        )
        return existing

    if conflict_strategy == "error":
        msg = f"App template conflict for slug {payload['slug']}"
        raise ValueError(msg)

    payload["slug"] = _unique_slug(AppRequirementTemplate, payload["slug"])
    return AppRequirementTemplate.objects.create(
        **payload,
        is_default=False,
        created_by=user,
    )


def _import_prompt_template(
    raw_template: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
) -> PromptTemplate:
    payload = _validate_prompt_template_payload(raw_template)
    existing = PromptTemplate.objects.filter(slug=payload["slug"]).first()
    if existing is None:
        return PromptTemplate.objects.create(
            **payload,
            is_default=False,
            created_by=user,
        )

    if _prompt_template_matches(existing, payload) and _is_visible_defaultish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        _assign_fields(existing, payload)
        existing.save(
            update_fields=[
                "name",
                "stage",
                "role",
                "content",
                "description",
                "version",
                "updated_at",
            ],
        )
        return existing

    if conflict_strategy == "error":
        msg = f"Prompt template conflict for slug {payload['slug']}"
        raise ValueError(msg)

    payload["slug"] = _unique_slug(PromptTemplate, payload["slug"])
    return PromptTemplate.objects.create(
        **payload,
        is_default=False,
        created_by=user,
    )


def _import_block(
    raw_block: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
) -> ContentBlock:
    payload = _validate_block_payload(raw_block)
    slug = payload["slug"]
    version = payload["version"]
    existing = ContentBlock.objects.filter(slug=slug, version=version).first()
    if existing is None:
        return ContentBlock.objects.create(
            **payload,
            is_system=False,
            created_by=user,
        )

    if _block_matches(existing, payload) and _is_visible_systemish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        _assign_fields(existing, payload)
        existing.save(
            update_fields=[
                "block_type",
                "name",
                "description",
                "content",
                "metadata",
                "updated_at",
            ],
        )
        return existing

    if conflict_strategy == "error":
        msg = f"Block conflict for {slug} v{version}"
        raise ValueError(msg)

    payload["slug"] = _unique_block_slug(slug, version)
    return ContentBlock.objects.create(
        **payload,
        is_system=False,
        created_by=user,
    )


def _import_bundle(
    raw_bundle: Any,
    *,
    user: AbstractUser,
    conflict_strategy: str,
    block_ref_map: dict[tuple[str, int], tuple[str, int]],
    scaffolding_map: dict[str, str],
) -> TemplateBundle:
    payload = _validate_bundle_payload(raw_bundle)
    payload["scaffolding_slug"] = scaffolding_map.get(
        payload["scaffolding_slug"],
        payload["scaffolding_slug"],
    )
    normalized_refs = []
    for raw_ref in payload["block_refs"]:
        ref = _validate_block_ref(raw_ref)
        mapped = block_ref_map.get((ref["slug"], ref["version"]))
        if mapped:
            ref["slug"], ref["version"] = mapped
        else:
            existing = visible_blocks_for(user).filter(
                slug=ref["slug"],
                version=ref["version"],
            ).first()
            if not existing:
                msg = f"Bundle references missing block {ref['slug']} v{ref['version']}"
                raise ValueError(msg)
        normalized_refs.append(ref)
    payload["block_refs"] = normalized_refs

    existing = TemplateBundle.objects.filter(slug=payload["slug"]).first()
    if existing is None:
        return TemplateBundle.objects.create(
            **payload,
            is_system=False,
            is_default=False,
            created_by=user,
        )

    if _bundle_matches(existing, payload) and _is_visible_systemish(existing, user):
        return existing

    if conflict_strategy == "overwrite" and existing.created_by_id == user.id:
        _assign_fields(existing, payload)
        existing.save(
            update_fields=[
                "name",
                "description",
                "scaffolding_slug",
                "block_refs",
                "llm_config",
                "updated_at",
            ],
        )
        return existing

    if conflict_strategy == "error":
        msg = f"Bundle conflict for slug {payload['slug']}"
        raise ValueError(msg)

    payload["slug"] = _unique_slug(TemplateBundle, payload["slug"])
    return TemplateBundle.objects.create(
        **payload,
        is_system=False,
        is_default=False,
        created_by=user,
    )


def _parse_package_text(package_text: str) -> dict[str, Any]:
    try:
        data = json.loads(package_text)
    except json.JSONDecodeError:
        data = yaml.safe_load(package_text)
    if not isinstance(data, dict):
        msg = "Package must be a JSON or YAML object"
        raise ValueError(msg)
    return data


def _read_data_text(relative_path: str) -> str:
    return (DATA_DIR / relative_path).read_text(encoding="utf-8")


def _load_json_data_file(relative_path: str) -> dict[str, Any]:
    return json.loads(_read_data_text(relative_path))


def _build_starter_block(spec: dict[str, Any]) -> dict[str, Any]:
    yaml_path = spec.get("yaml_path")
    if yaml_path:
        raw = yaml.safe_load(_read_data_text(str(yaml_path))) or {}
        return {
            "block_type": raw.get("block_type", ContentBlock.BlockType.PROMPT_TONE),
            "slug": str(raw.get("slug", "")).strip(),
            "version": int(raw.get("version", 1)),
            "name": str(raw.get("name", "")).strip(),
            "description": str(raw.get("description", "")).strip(),
            "content": str(raw.get("content", "")),
            "metadata": raw.get("metadata") or {},
        }

    return {
        "block_type": spec["block_type"],
        "slug": spec["slug"],
        "version": spec["version"],
        "name": spec["name"],
        "description": spec["description"],
        "content": _read_data_text(spec["path"]),
        "metadata": spec.get("metadata") or {},
    }


def _dump_package(package: dict[str, Any], fmt: str) -> tuple[str, str]:
    if fmt == "yaml":
        return yaml.safe_dump(package, sort_keys=False, allow_unicode=False), "application/yaml"
    return json.dumps(package, indent=2, sort_keys=True), "application/json"


def _blocks_for_bundle_refs(block_refs: list[dict[str, Any]]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for ref in block_refs:
        block = ContentBlock.objects.filter(
            slug=ref.get("slug"),
            version=int(ref.get("version", 1)),
        ).first()
        if block:
            blocks.append(block)
    return _dedupe_blocks(blocks)


def _dedupe_blocks(blocks: list[ContentBlock]) -> list[ContentBlock]:
    unique: dict[tuple[str, int], ContentBlock] = {}
    for block in blocks:
        unique[(block.slug, block.version)] = block
    return sorted(unique.values(), key=lambda item: (item.block_type, item.slug, item.version))


def _serialize_scaffolding(template: ScaffoldingTemplate) -> dict[str, Any]:
    return {
        "name": template.name,
        "slug": template.slug,
        "description": template.description,
        "tech_stack": template.tech_stack or {},
        "substitution_vars": template.substitution_vars or [],
    }


def _serialize_app_template(template: AppRequirementTemplate) -> dict[str, Any]:
    return {
        "name": template.name,
        "slug": template.slug,
        "category": template.category,
        "description": template.description,
        "backend_requirements": template.backend_requirements or [],
        "frontend_requirements": template.frontend_requirements or [],
        "admin_requirements": template.admin_requirements or [],
        "api_endpoints": template.api_endpoints or [],
        "data_model": template.data_model or {},
        "admin_api_endpoints": template.admin_api_endpoints or [],
    }


def _serialize_prompt_template(template: PromptTemplate) -> dict[str, Any]:
    return {
        "name": template.name,
        "slug": template.slug,
        "stage": template.stage,
        "role": template.role,
        "content": template.content,
        "description": template.description,
        "version": template.version,
    }


def _serialize_block(block: ContentBlock) -> dict[str, Any]:
    return {
        "block_type": block.block_type,
        "slug": block.slug,
        "version": block.version,
        "name": block.name,
        "description": block.description,
        "content": block.content,
        "metadata": block.metadata or {},
    }


def _serialize_bundle(bundle: TemplateBundle) -> dict[str, Any]:
    return {
        "name": bundle.name,
        "slug": bundle.slug,
        "description": bundle.description,
        "scaffolding_slug": bundle.scaffolding_slug,
        "block_refs": bundle.block_refs or [],
        "llm_config": bundle.llm_config or {},
    }


def _validate_scaffolding_payload(raw_template: Any) -> dict[str, Any]:
    if not isinstance(raw_template, dict):
        msg = "Scaffolding template must be an object"
        raise ValueError(msg)
    slug = str(raw_template.get("slug", "")).strip()
    name = str(raw_template.get("name", "")).strip()
    if not slug or not name:
        msg = "Scaffolding template requires name and slug"
        raise ValueError(msg)
    tech_stack = raw_template.get("tech_stack") or {}
    substitution_vars = raw_template.get("substitution_vars") or []
    if not isinstance(tech_stack, dict):
        msg = f"Scaffolding {slug} has invalid tech_stack"
        raise ValueError(msg)
    if not isinstance(substitution_vars, list):
        msg = f"Scaffolding {slug} has invalid substitution_vars"
        raise ValueError(msg)
    return {
        "name": name,
        "slug": slug,
        "description": str(raw_template.get("description", "")).strip(),
        "tech_stack": tech_stack,
        "substitution_vars": substitution_vars,
    }


def _validate_app_template_payload(raw_template: Any) -> dict[str, Any]:
    if not isinstance(raw_template, dict):
        msg = "App template must be an object"
        raise ValueError(msg)
    slug = str(raw_template.get("slug", "")).strip()
    name = str(raw_template.get("name", "")).strip()
    if not slug or not name:
        msg = "App template requires name and slug"
        raise ValueError(msg)
    return {
        "name": name,
        "slug": slug,
        "category": str(raw_template.get("category", "")).strip(),
        "description": str(raw_template.get("description", "")).strip(),
        "backend_requirements": raw_template.get("backend_requirements") or [],
        "frontend_requirements": raw_template.get("frontend_requirements") or [],
        "admin_requirements": raw_template.get("admin_requirements") or [],
        "api_endpoints": raw_template.get("api_endpoints") or [],
        "data_model": raw_template.get("data_model") or {},
        "admin_api_endpoints": raw_template.get("admin_api_endpoints") or [],
    }


def _validate_prompt_template_payload(raw_template: Any) -> dict[str, Any]:
    if not isinstance(raw_template, dict):
        msg = "Prompt template must be an object"
        raise ValueError(msg)
    slug = str(raw_template.get("slug", "")).strip()
    name = str(raw_template.get("name", "")).strip()
    if not slug or not name:
        msg = "Prompt template requires name and slug"
        raise ValueError(msg)
    return {
        "name": name,
        "slug": slug,
        "stage": str(raw_template.get("stage", "")).strip(),
        "role": str(raw_template.get("role", "")).strip(),
        "content": str(raw_template.get("content", "")),
        "description": str(raw_template.get("description", "")).strip(),
        "version": int(raw_template.get("version", 1)),
    }


def _validate_block_payload(raw_block: Any) -> dict[str, Any]:
    if not isinstance(raw_block, dict):
        msg = "Each block must be an object"
        raise ValueError(msg)
    slug = str(raw_block.get("slug", "")).strip()
    name = str(raw_block.get("name", "")).strip()
    content = str(raw_block.get("content", ""))
    if not slug:
        msg = "Each block must include a slug"
        raise ValueError(msg)
    if not name:
        msg = f"Block {slug} is missing a name"
        raise ValueError(msg)
    if not content:
        msg = f"Block {slug} is missing content"
        raise ValueError(msg)
    return {
        "block_type": str(raw_block.get("block_type", "")).strip()
        or ContentBlock.BlockType.PROMPT_TONE,
        "slug": slug,
        "version": int(raw_block.get("version", 1)),
        "name": name,
        "description": str(raw_block.get("description", "")).strip(),
        "content": content,
        "metadata": raw_block.get("metadata") or {},
    }


def _validate_bundle_payload(raw_bundle: Any) -> dict[str, Any]:
    if not isinstance(raw_bundle, dict):
        msg = "Bundle must be an object"
        raise ValueError(msg)
    slug = str(raw_bundle.get("slug", "")).strip()
    name = str(raw_bundle.get("name", "")).strip()
    if not slug or not name:
        msg = "Bundle requires name and slug"
        raise ValueError(msg)
    block_refs = raw_bundle.get("block_refs") or []
    if not isinstance(block_refs, list):
        msg = f"Bundle {slug} has invalid block_refs"
        raise ValueError(msg)
    llm_config = raw_bundle.get("llm_config") or {}
    if not isinstance(llm_config, dict):
        msg = f"Bundle {slug} has invalid llm_config"
        raise ValueError(msg)
    return {
        "name": name,
        "slug": slug,
        "description": str(raw_bundle.get("description", "")).strip(),
        "scaffolding_slug": str(raw_bundle.get("scaffolding_slug", "flask-react")).strip()
        or "flask-react",
        "block_refs": block_refs,
        "llm_config": llm_config,
    }


def _validate_block_ref(raw_ref: Any) -> dict[str, Any]:
    if not isinstance(raw_ref, dict):
        msg = "Each block reference must be an object"
        raise ValueError(msg)
    slug = str(raw_ref.get("slug", "")).strip()
    if not slug:
        msg = "Block reference missing slug"
        raise ValueError(msg)
    return {
        "type": str(raw_ref.get("type", "")).strip() or "",
        "slug": slug,
        "version": int(raw_ref.get("version", 1)),
    }


def _assign_fields(instance: Any, payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        setattr(instance, key, value)


def _is_visible_defaultish(instance: Any, user: AbstractUser) -> bool:
    return bool(getattr(instance, "is_default", False) or instance.created_by_id == user.id)


def _is_visible_systemish(instance: Any, user: AbstractUser) -> bool:
    return bool(getattr(instance, "is_system", False) or instance.created_by_id == user.id)


def _scaffolding_matches(existing: ScaffoldingTemplate, payload: dict[str, Any]) -> bool:
    return (
        existing.name == payload["name"]
        and existing.description == payload["description"]
        and (existing.tech_stack or {}) == payload["tech_stack"]
        and (existing.substitution_vars or []) == payload["substitution_vars"]
    )


def _app_template_matches(existing: AppRequirementTemplate, payload: dict[str, Any]) -> bool:
    return (
        existing.name == payload["name"]
        and existing.category == payload["category"]
        and existing.description == payload["description"]
        and (existing.backend_requirements or []) == payload["backend_requirements"]
        and (existing.frontend_requirements or []) == payload["frontend_requirements"]
        and (existing.admin_requirements or []) == payload["admin_requirements"]
        and (existing.api_endpoints or []) == payload["api_endpoints"]
        and (existing.data_model or {}) == payload["data_model"]
        and (existing.admin_api_endpoints or []) == payload["admin_api_endpoints"]
    )


def _prompt_template_matches(existing: PromptTemplate, payload: dict[str, Any]) -> bool:
    return (
        existing.name == payload["name"]
        and existing.stage == payload["stage"]
        and existing.role == payload["role"]
        and existing.content == payload["content"]
        and existing.description == payload["description"]
        and existing.version == payload["version"]
    )


def _block_matches(existing: ContentBlock, payload: dict[str, Any]) -> bool:
    return (
        existing.block_type == payload["block_type"]
        and existing.name == payload["name"]
        and existing.description == payload["description"]
        and existing.content == payload["content"]
        and (existing.metadata or {}) == (payload["metadata"] or {})
    )


def _bundle_matches(existing: TemplateBundle, payload: dict[str, Any]) -> bool:
    return (
        existing.name == payload["name"]
        and existing.description == payload["description"]
        and existing.scaffolding_slug == payload["scaffolding_slug"]
        and (existing.block_refs or []) == payload["block_refs"]
        and (existing.llm_config or {}) == payload["llm_config"]
    )


def _unique_slug(model, base_slug: str) -> str:
    candidate = slugify(base_slug) or "imported-item"
    if not model.objects.filter(slug=candidate).exists():
        return candidate
    idx = 2
    while True:
        next_candidate = f"{candidate}-{idx}"
        if not model.objects.filter(slug=next_candidate).exists():
            return next_candidate
        idx += 1


def _unique_block_slug(base_slug: str, version: int) -> str:
    candidate = slugify(base_slug) or "imported-block"
    if not ContentBlock.objects.filter(slug=candidate, version=version).exists():
        return candidate
    idx = 2
    while True:
        next_candidate = f"{candidate}-{idx}"
        if not ContentBlock.objects.filter(slug=next_candidate, version=version).exists():
            return next_candidate
        idx += 1
