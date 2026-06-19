"""Seed default generation templates, content blocks, and bundles."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand

from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import ContentBlock
from llm_lab.generation.models import PromptTemplate
from llm_lab.generation.models import ScaffoldingTemplate
from llm_lab.generation.models import TemplateBundle

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
REQUIREMENTS_DIR = DATA_DIR / "requirements"
PROMPTS_DIR = DATA_DIR / "prompts"
BLOCKS_DIR = DATA_DIR / "blocks"
CATALOG_PATH = BLOCKS_DIR / "catalog.yaml"
MANIFESTS_DIR = DATA_DIR / "requirements" / "manifests"

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

PROMPT_STAGE_SEEDS = [
    {
        "slug": "base-backend-system",
        "stage": "backend",
        "role": "system",
        "path": "v2/backend/system.md.jinja2",
    },
    {
        "slug": "base-backend-user",
        "stage": "backend",
        "role": "user",
        "path": "v2/backend/user.md.jinja2",
    },
    {
        "slug": "base-frontend-system",
        "stage": "frontend",
        "role": "system",
        "path": "v2/frontend/system.md.jinja2",
    },
    {
        "slug": "base-frontend-user",
        "stage": "frontend",
        "role": "user",
        "path": "v2/frontend/user.md.jinja2",
    },
    {
        "slug": "fastapi-backend-system",
        "stage": "backend",
        "role": "system",
        "path": "fastapi/backend/system.md.jinja2",
    },
    {
        "slug": "fastapi-backend-user",
        "stage": "backend",
        "role": "user",
        "path": "fastapi/backend/user.md.jinja2",
    },
    {
        "slug": "vue-frontend-system",
        "stage": "frontend",
        "role": "system",
        "path": "vue/frontend/system.md.jinja2",
    },
    {
        "slug": "vue-frontend-user",
        "stage": "frontend",
        "role": "user",
        "path": "vue/frontend/user.md.jinja2",
    },
]


class Command(BaseCommand):
    help = "Seed scaffolding, requirements, prompts, content blocks, and template bundles"

    def handle(self, *args, **options):
        self._seed_scaffolding()
        self._seed_requirements()
        self._seed_prompts()
        self._seed_content_blocks()
        self._seed_template_bundles()
        self._seed_app_bundles()
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def _seed_scaffolding(self):
        for seed in SCAFFOLDING_SEEDS:
            obj, created = ScaffoldingTemplate.objects.update_or_create(
                slug=seed["slug"],
                defaults={
                    "name": seed["name"],
                    "description": seed["description"],
                    "tech_stack": seed["tech_stack"],
                    "substitution_vars": seed["substitution_vars"],
                    "is_default": seed["is_default"],
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} scaffolding: {obj.name}")

    def _seed_requirements(self):
        if not REQUIREMENTS_DIR.exists():
            self.stdout.write(
                self.style.WARNING(f"Requirements directory not found: {REQUIREMENTS_DIR}"),
            )
            return

        count = 0
        for json_path in sorted(REQUIREMENTS_DIR.glob("*.json")):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.exception("Failed to read %s", json_path)
                continue

            obj, created = AppRequirementTemplate.objects.update_or_create(
                slug=data.get("slug", json_path.stem),
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
                    "is_default": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} requirement: {obj.name}")
            count += 1

        self.stdout.write(f"  Total requirements: {count}")

    def _seed_prompts(self):
        """Legacy PromptTemplate rows (kept for admin UI compatibility)."""
        for data in PROMPT_TEMPLATE_SEEDS:
            obj, created = PromptTemplate.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "stage": data["stage"],
                    "role": data["role"],
                    "content": self._read_prompt_file(data["path"]),
                    "description": data["description"],
                    "is_default": data["is_default"],
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} prompt: {obj.name}")

    def _seed_content_blocks(self):
        for seed in PROMPT_STAGE_SEEDS:
            content = self._read_prompt_file(seed["path"])
            obj, created = ContentBlock.objects.update_or_create(
                slug=seed["slug"],
                version=1,
                defaults={
                    "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                    "name": seed["slug"].replace("-", " ").title(),
                    "description": f"{seed['stage']} {seed['role']} prompt",
                    "content": content,
                    "metadata": {"stage": seed["stage"], "role": seed["role"]},
                    "is_system": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} block: {obj.slug}")

        for yaml_path in sorted(BLOCKS_DIR.rglob("*.yaml")):
            if yaml_path.name == "catalog.yaml":
                continue
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                logger.exception("Failed to read block %s", yaml_path)
                continue
            if not data or not data.get("slug"):
                continue

            obj, created = ContentBlock.objects.update_or_create(
                slug=data["slug"],
                version=int(data.get("version", 1)),
                defaults={
                    "block_type": data.get("block_type", "prompt_tone"),
                    "name": data.get("name", data["slug"]),
                    "description": data.get("description", ""),
                    "content": data.get("content", ""),
                    "metadata": data.get("metadata") or {},
                    "is_system": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} block: {obj.slug}")

    def _seed_template_bundles(self):
        if not CATALOG_PATH.is_file():
            self.stdout.write(self.style.WARNING(f"No catalog: {CATALOG_PATH}"))
            return

        catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
        for bundle_data in catalog.get("bundles", []):
            obj, created = TemplateBundle.objects.update_or_create(
                slug=bundle_data["slug"],
                defaults={
                    "name": bundle_data.get("name", bundle_data["slug"]),
                    "description": bundle_data.get("description", ""),
                    "scaffolding_slug": bundle_data.get("scaffolding_slug", "flask-react"),
                    "block_refs": bundle_data.get("block_refs", []),
                    "is_system": True,
                    "is_default": bundle_data.get("is_default", False),
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} bundle: {obj.slug}")

    def _seed_app_bundles(self):
        """Per-app bundles from requirements/manifests/*.yaml (pilot decomposition)."""
        if not MANIFESTS_DIR.exists():
            return

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
            try:
                base = TemplateBundle.objects.get(slug=base_slug, is_system=True)
            except TemplateBundle.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(f"  Skip {yaml_path.name}: base bundle {base_slug} missing"),
                )
                continue

            refs = list(base.block_refs or [])
            for extra in manifest.get("extra_block_refs", []):
                if extra not in refs:
                    refs.append(extra)

            bundle_slug = manifest.get("bundle_slug") or f"app-{app_slug.replace('_', '-')}"
            obj, created = TemplateBundle.objects.update_or_create(
                slug=bundle_slug,
                defaults={
                    "name": manifest.get("name", bundle_slug),
                    "description": manifest.get("description", ""),
                    "scaffolding_slug": manifest.get(
                        "scaffolding_slug",
                        base.scaffolding_slug,
                    ),
                    "block_refs": refs,
                    "is_system": True,
                    "is_default": False,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} app bundle: {obj.slug} ({app_slug})")

        self._seed_app_bundles_for_all_requirements()

    def _seed_app_bundles_for_all_requirements(self):
        """Ensure every seeded app requirement has an ``app-{slug}`` bundle."""
        try:
            base = TemplateBundle.objects.get(
                slug="system-scaffolding-standard",
                is_system=True,
            )
        except TemplateBundle.DoesNotExist:
            self.stdout.write(
                self.style.WARNING("  Skip auto app bundles: system-scaffolding-standard missing"),
            )
            return

        for req in AppRequirementTemplate.objects.order_by("slug"):
            bundle_slug = f"app-{req.slug.replace('_', '-')}"
            if TemplateBundle.objects.filter(slug=bundle_slug).exists():
                continue
            obj, created = TemplateBundle.objects.update_or_create(
                slug=bundle_slug,
                defaults={
                    "name": f"{req.name} (standard)",
                    "description": f"Default bundle for study app `{req.slug}`",
                    "scaffolding_slug": base.scaffolding_slug,
                    "block_refs": list(base.block_refs or []),
                    "is_system": True,
                    "is_default": False,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} auto app bundle: {obj.slug}")

    @staticmethod
    def _read_prompt_file(relative_path: str) -> str:
        prompt_path = PROMPTS_DIR / relative_path
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        logger.warning("Prompt not found: %s", prompt_path)
        return ""
