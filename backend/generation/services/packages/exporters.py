"""Serialize bundles/templates into portable package payloads."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.utils import timezone

from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile
from backend.generation.services.packages.constants import BUNDLE_PACKAGE_KIND
from backend.generation.services.packages.constants import BUNDLE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_KIND
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.visibility import visible_app_templates_for
from backend.generation.services.packages.visibility import visible_blocks_for
from backend.generation.services.packages.visibility import visible_profiles_for

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import AppRequirementTemplate


def export_bundle_package(bundle: GenerationProfile) -> dict[str, Any]:
    return {
        "bundle_package_schema_version": BUNDLE_PACKAGE_SCHEMA_VERSION,
        "kind": BUNDLE_PACKAGE_KIND,
        "exported_at": timezone.now().isoformat(),
        "bundle": _serialize_bundle(bundle),
        "blocks": [_serialize_block(block) for block in _blocks_for_bundle_refs(bundle.block_refs or [])],
    }


def export_template_package(
    *,
    user: AbstractUser,
    app_template_slugs: list[str] | None = None,
    bundle_slugs: list[str] | None = None,
    block_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    app_template_slugs = app_template_slugs or []
    bundle_slugs = bundle_slugs or []
    block_refs = block_refs or []

    bundles = list(visible_profiles_for(user).filter(slug__in=bundle_slugs).order_by("name"))
    explicit_blocks = []
    for ref in block_refs:
        block = (
            visible_blocks_for(user)
            .filter(
                slug=ref.get("slug"),
                version=int(ref.get("version", 1)),
            )
            .first()
        )
        if block:
            explicit_blocks.append(block)

    bundle_blocks = _blocks_for_bundle_refs(
        [ref for bundle in bundles for ref in (bundle.block_refs or [])],
    )
    blocks = _dedupe_blocks([*explicit_blocks, *bundle_blocks])

    return {
        "template_package_schema_version": TEMPLATE_PACKAGE_SCHEMA_VERSION,
        "kind": TEMPLATE_PACKAGE_KIND,
        "exported_at": timezone.now().isoformat(),
        "assets": {
            "app_templates": [
                _serialize_app_template(item)
                for item in visible_app_templates_for(user).filter(slug__in=app_template_slugs).order_by("name")
            ],
            "blocks": [_serialize_block(block) for block in blocks],
            "bundles": [_serialize_bundle(bundle) for bundle in bundles],
        },
    }


def dump_bundle_package(package: dict[str, Any], fmt: str) -> tuple[str, str]:
    return _dump_package(package, fmt)


def dump_template_package(package: dict[str, Any], fmt: str) -> tuple[str, str]:
    return _dump_package(package, fmt)


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


def _serialize_bundle(bundle: GenerationProfile) -> dict[str, Any]:
    return {
        "name": bundle.name,
        "slug": bundle.slug,
        # Informational only: an import always starts a fresh version-1
        # lineage under the importing user, so this isn't read back in.
        "version": bundle.version,
        "description": bundle.description,
        "scaffolding_slug": bundle.scaffolding_slug,
        "block_refs": bundle.block_refs or [],
        "llm_config": bundle.llm_config or {},
    }
