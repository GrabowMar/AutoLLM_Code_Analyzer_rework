"""Starter template packages bundled with the app (seed data)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import yaml
from django.utils import timezone

from backend.generation.models import ContentBlock
from backend.generation.services.packages.constants import DATA_DIR
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_KIND
from backend.generation.services.packages.constants import TEMPLATE_PACKAGE_SCHEMA_VERSION
from backend.generation.services.packages.importers import import_template_package

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

STARTER_TEMPLATE_PACKAGES = {
    "fastapi-stack-starter": {
        "name": "FastAPI starter pack",
        "description": (
            "Imports FastAPI + React/Vue scaffoldings, three richer sample app requirements, "
            "stack-specific prompts, and ready-to-use bundles."
        ),
        "app_template_files": [
            "requirements/analytics_campaign_monitor.json",
            "requirements/operations_incident_center.json",
            "requirements/commerce_subscription_billing.json",
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
                "description": ("FastAPI backend prompts paired with Vue frontend prompts and shared FastAPI rules"),
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
                "app_template_count": len(assets.get("app_templates", [])),
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
            "app_templates": [_load_json_data_file(relative_path) for relative_path in spec["app_template_files"]],
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
