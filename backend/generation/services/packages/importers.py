"""Parse, validate, and import bundle/template package payloads."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.db import transaction
from django.utils.text import slugify

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile
from backend.generation.services.packages.constants import ALLOWED_CONFLICT_STRATEGIES
from backend.generation.services.packages.constants import BUNDLE_PACKAGE_KIND
from backend.generation.services.packages.constants import BUNDLE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_KIND
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.visibility import visible_blocks_for

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger(__name__)


def parse_bundle_package_text(package_text: str) -> dict[str, Any]:
    data = _parse_package_text(package_text)
    if data.get("kind") != BUNDLE_PACKAGE_KIND:
        msg = f"Unsupported package kind: {data.get('kind')!r}"
        raise ValueError(msg)
    if data.get("bundle_package_schema_version") != BUNDLE_PACKAGE_SCHEMA_VERSION:
        msg = f"Unsupported bundle package schema version: {data.get('bundle_package_schema_version')!r}"
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
    schema_version = data.get("template_package_schema_version")
    if schema_version not in (1, TEMPLATE_PACKAGE_SCHEMA_VERSION):
        msg = f"Unsupported template package schema version: {schema_version!r}"
        raise ValueError(msg)
    assets = data.get("assets")
    if not isinstance(assets, dict):
        msg = "Template package is missing a valid 'assets' section"
        raise ValueError(msg)
    if schema_version == 1:
        data["assets"] = _upgrade_v1_assets(assets)
    return data


def _upgrade_v1_assets(assets: dict[str, Any]) -> dict[str, Any]:
    """Map v1 asset keys to the v2 vocabulary (prompt_templates stay for the skip path)."""
    upgraded = dict(assets)
    if "app_specs" not in upgraded:
        upgraded["app_specs"] = upgraded.pop("app_templates", [])
    if "profiles" not in upgraded:
        upgraded["profiles"] = upgraded.pop("bundles", [])
    upgraded.setdefault("stacks", [])
    return upgraded


def import_bundle_package(
    *,
    package_text: str,
    user: AbstractUser,
    conflict_strategy: str = "rename",
) -> GenerationProfile:
    package = parse_bundle_package_text(package_text)
    imported = _import_assets(
        assets={
            "blocks": package.get("blocks", []),
            "bundles": [package["bundle"]],
        },
        user=user,
        conflict_strategy=conflict_strategy,
    )
    profiles = imported.get("profiles", [])
    if not profiles:
        msg = "Bundle package did not import any profiles"
        raise ValueError(msg)
    return profiles[0]


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

    block_ref_map: dict[tuple[str, int], tuple[str, int]] = {}
    imported: dict[str, list[Any]] = {
        "app_specs": [],
        "blocks": [],
        "profiles": [],
        "stacks": [],
    }

    # Pre-rename package files may still carry a prompt_templates section for
    # the removed PromptTemplate model; the rest imports normally.
    skipped_prompts = len(assets.get("prompt_templates") or [])
    if skipped_prompts:
        logger.warning(
            "Skipping %d legacy prompt_templates entries in package import",
            skipped_prompts,
        )

    # Accept raw v1 keys too (import_bundle_package builds assets directly).
    app_specs = assets.get("app_specs", assets.get("app_templates", []))
    profiles = assets.get("profiles", assets.get("bundles", []))

    with transaction.atomic():
        for raw_app in app_specs:
            template = _import_app_template(
                raw_app,
                user=user,
                conflict_strategy=conflict_strategy,
            )
            imported["app_specs"].append(template)

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

        for raw_bundle in profiles:
            bundle = _import_bundle(
                raw_bundle,
                user=user,
                conflict_strategy=conflict_strategy,
                block_ref_map=block_ref_map,
            )
            imported["profiles"].append(bundle)

        for raw_stack in assets.get("stacks", []):
            imported["stacks"].append(
                _import_stack(raw_stack, user=user, conflict_strategy=conflict_strategy),
            )

    return imported


def _import_stack(raw_stack: Any, *, user: AbstractUser, conflict_strategy: str):
    """Import a user stack, re-validating against this install's allowlist.

    Imported stacks are always ``generated`` dockerfile mode and enter the
    approval queue like locally created ones.
    """
    from django.conf import settings

    from backend.generation.services.versioning import content_hash
    from backend.runtime.models import Stack
    from backend.runtime.services.scaffolding import is_known_stack_slug
    from backend.runtime.services.stack_validation import validate_stack_config
    from backend.runtime.services.stack_validation import validate_stack_files

    if not isinstance(raw_stack, dict):
        msg = "Each stack must be an object"
        raise ValueError(msg)
    slug = str(raw_stack.get("slug", "")).strip()
    if not slug:
        msg = "Each stack must include a slug"
        raise ValueError(msg)

    files = raw_stack.get("files") or {}
    payload = {
        "name": str(raw_stack.get("name", slug)),
        "description": str(raw_stack.get("description", "")),
        "has_frontend": bool(raw_stack.get("has_frontend")),
        "default_port": int(raw_stack.get("default_port", 8000)),
        "patch_profile": raw_stack.get("patch_profile")
        if raw_stack.get("patch_profile") in ("flask", "none")
        else "none",
        "frontend_component": str(raw_stack.get("frontend_component", "")),
        "backend_filename": str(raw_stack.get("backend_filename", "app.py")),
        "backend_base_image": str(raw_stack.get("backend_base_image", "")),
        "frontend_base_image": str(raw_stack.get("frontend_base_image", "")),
        "server_kind": raw_stack.get("server_kind")
        if raw_stack.get("server_kind") in ("python", "uvicorn")
        else "python",
    }
    errors = validate_stack_files(files) + validate_stack_config(
        backend_base_image=payload["backend_base_image"],
        frontend_base_image=payload["frontend_base_image"],
        has_frontend=payload["has_frontend"],
        default_port=payload["default_port"],
        backend_filename=payload["backend_filename"],
    )
    if errors:
        msg = f"Stack {slug}: {'; '.join(errors)}"
        raise ValueError(msg)

    if Stack.objects.filter(slug=slug).exists() or is_known_stack_slug(slug):
        if conflict_strategy == "error":
            msg = f"Stack conflict for slug {slug}"
            raise ValueError(msg)
        # "overwrite" would mutate immutable versions; rename in both cases.
        slug = _unique_slug(Stack, slug)

    approved = bool(getattr(user, "is_staff", False)) or not settings.STACK_REQUIRE_APPROVAL
    return Stack.objects.create(
        slug=slug,
        version=1,
        is_builtin=False,
        is_approved=approved,
        created_by=user,
        aliases=[],
        files=files,
        content_hash=content_hash({**payload, "files": files}),
        dockerfile_mode=Stack.DockerfileMode.GENERATED,
        **payload,
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
) -> GenerationProfile:
    payload = _validate_bundle_payload(raw_bundle)
    normalized_refs = []
    for raw_ref in payload["block_refs"]:
        ref = _validate_block_ref(raw_ref)
        mapped = block_ref_map.get((ref["slug"], ref["version"]))
        if mapped:
            ref["slug"], ref["version"] = mapped
        else:
            existing = (
                visible_blocks_for(user)
                .filter(
                    slug=ref["slug"],
                    version=ref["version"],
                )
                .first()
            )
            if not existing:
                msg = f"Bundle references missing block {ref['slug']} v{ref['version']}"
                raise ValueError(msg)
        normalized_refs.append(ref)
    payload["block_refs"] = normalized_refs

    # Compare against the latest version of the slug — multiple versions can
    # now exist, and an unordered .first() would pick an arbitrary one.
    existing = GenerationProfile.objects.filter(slug=payload["slug"]).order_by("-version").first()
    if existing is None:
        return GenerationProfile.objects.create(
            **payload,
            version=1,
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

    payload["slug"] = _unique_slug(GenerationProfile, payload["slug"])
    return GenerationProfile.objects.create(
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
        "block_type": str(raw_block.get("block_type", "")).strip() or ContentBlock.BlockType.PROMPT_TONE,
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
        "scaffolding_slug": str(raw_bundle.get("scaffolding_slug", "flask-react")).strip() or "flask-react",
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


def _block_matches(existing: ContentBlock, payload: dict[str, Any]) -> bool:
    return (
        existing.block_type == payload["block_type"]
        and existing.name == payload["name"]
        and existing.description == payload["description"]
        and existing.content == payload["content"]
        and (existing.metadata or {}) == (payload["metadata"] or {})
    )


def _bundle_matches(existing: GenerationProfile, payload: dict[str, Any]) -> bool:
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
