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

PROMPT_STAGE_SEEDS = [
    ("base-backend-system", "backend", "system", "backend/system.md.jinja2"),
    ("base-backend-user", "backend", "user", "backend/user.md.jinja2"),
    ("base-frontend-system", "frontend", "system", "frontend/system.md.jinja2"),
    ("base-frontend-user", "frontend", "user", "frontend/user.md.jinja2"),
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
        self._seed_fastapi_scaffolding()
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def _seed_scaffolding(self):
        obj, created = ScaffoldingTemplate.objects.update_or_create(
            slug="flask-react",
            defaults={
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
        prompts = [
            {
                "slug": "v2-backend-system",
                "name": "Backend System Prompt v2",
                "stage": "backend",
                "role": "system",
                "description": "Default system prompt for Flask backend generation",
                "content": self._read_prompt_file("backend/system.md.jinja2"),
            },
            {
                "slug": "v2-backend-user",
                "name": "Backend User Prompt v2",
                "stage": "backend",
                "role": "user",
                "description": "Default user prompt for Flask backend generation",
                "content": self._read_prompt_file("backend/user.md.jinja2"),
            },
            {
                "slug": "v2-frontend-system",
                "name": "Frontend System Prompt v2",
                "stage": "frontend",
                "role": "system",
                "description": "Default system prompt for React frontend generation",
                "content": self._read_prompt_file("frontend/system.md.jinja2"),
            },
            {
                "slug": "v2-frontend-user",
                "name": "Frontend User Prompt v2",
                "stage": "frontend",
                "role": "user",
                "description": "Default user prompt for React frontend generation",
                "content": self._read_prompt_file("frontend/user.md.jinja2"),
            },
        ]

        for data in prompts:
            obj, created = PromptTemplate.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "stage": data["stage"],
                    "role": data["role"],
                    "content": data["content"],
                    "description": data["description"],
                    "is_default": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} prompt: {obj.name}")

    def _seed_content_blocks(self):
        for slug, stage, role, rel_path in PROMPT_STAGE_SEEDS:
            content = self._read_prompt_file(rel_path)
            obj, created = ContentBlock.objects.update_or_create(
                slug=slug,
                version=1,
                defaults={
                    "block_type": ContentBlock.BlockType.PROMPT_STAGE,
                    "name": slug.replace("-", " ").title(),
                    "description": f"v2 {stage} {role} prompt",
                    "content": content,
                    "metadata": {"stage": stage, "role": role},
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

    def _seed_fastapi_scaffolding(self):
        obj, created = ScaffoldingTemplate.objects.update_or_create(
            slug="fastapi-vue",
            defaults={
                "name": "FastAPI + Vue",
                "description": (
                    "FastAPI backend with Vue 3 SPA (Vite). Experimental stack for research comparisons."
                ),
                "tech_stack": {
                    "frontend": "Vue 3 + Vite",
                    "backend": "FastAPI",
                    "database": "SQLite",
                    "runtime": "Single Docker container",
                },
                "substitution_vars": ["{{PROJECT_NAME}}", "{{app_port|8000}}"],
                "is_default": False,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(f"  {action} scaffolding: {obj.name}")

    @staticmethod
    def _read_prompt_file(relative_path: str) -> str:
        prompt_path = PROMPTS_DIR / "v2" / relative_path
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        logger.warning("Prompt not found: %s", prompt_path)
        return ""
