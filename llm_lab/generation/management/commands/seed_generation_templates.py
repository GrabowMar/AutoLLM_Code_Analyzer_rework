"""Seed default generation templates from ThesisAppRework data."""

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import PromptTemplate
from llm_lab.generation.models import ScaffoldingTemplate

logger = logging.getLogger(__name__)

REQUIREMENTS_DIR = Path("/home/ubuntu/ThesisAppRework/misc/requirements")


class Command(BaseCommand):
    help = "Seed default generation templates (scaffolding, requirements, prompts)"

    def handle(self, *args, **options):
        self._seed_scaffolding()
        self._seed_requirements()
        self._seed_prompts()
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def _seed_scaffolding(self):
        """Create default React+Flask scaffolding template."""
        obj, created = ScaffoldingTemplate.objects.update_or_create(
            slug="react-flask",
            defaults={
                "name": "React + Flask",
                "description": (
                    "Docker-based scaffolding with React 18 frontend (Vite + Tailwind) "
                    "and Flask 3.x backend (SQLAlchemy + JWT auth). "
                    "Includes docker-compose, nginx proxy, and health checks."
                ),
                "tech_stack": {
                    "frontend": "React 18 + Vite + Tailwind CSS",
                    "backend": "Flask 3.x + SQLAlchemy + JWT",
                    "database": "SQLite",
                    "runtime": "Docker Compose",
                },
                "substitution_vars": [
                    "{{APP_NAME}}",
                    "{{BACKEND_PORT}}",
                    "{{FRONTEND_PORT}}",
                ],
                "is_default": True,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(f"  {action} scaffolding: {obj.name}")

    def _seed_requirements(self):
        """Import all requirement templates from ThesisAppRework."""
        if not REQUIREMENTS_DIR.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Requirements directory not found: {REQUIREMENTS_DIR}",
                ),
            )
            return

        count = 0
        for json_path in sorted(REQUIREMENTS_DIR.glob("*.json")):
            try:
                data = json.loads(json_path.read_text())
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
                    "is_default": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} requirement: {obj.name}")
            count += 1

        self.stdout.write(f"  Total requirements: {count}")

    def _seed_prompts(self):
        """Create default prompt templates (ported from ThesisAppRework v2)."""
        prompts = [
            {
                "slug": "v2-backend-system",
                "name": "Backend System Prompt v2",
                "stage": "backend",
                "role": "system",
                "description": "Default system prompt for Flask backend generation",
                "content": self._read_old_prompt("backend/system.md.jinja2"),
            },
            {
                "slug": "v2-backend-user",
                "name": "Backend User Prompt v2",
                "stage": "backend",
                "role": "user",
                "description": "Default user prompt for Flask backend generation",
                "content": self._read_old_prompt("backend/user.md.jinja2"),
            },
            {
                "slug": "v2-frontend-system",
                "name": "Frontend System Prompt v2",
                "stage": "frontend",
                "role": "system",
                "description": "Default system prompt for React frontend generation",
                "content": self._read_old_prompt("frontend/system.md.jinja2"),
            },
            {
                "slug": "v2-frontend-user",
                "name": "Frontend User Prompt v2",
                "stage": "frontend",
                "role": "user",
                "description": "Default user prompt for React frontend generation",
                "content": self._read_old_prompt("frontend/user.md.jinja2"),
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

    @staticmethod
    def _read_old_prompt(relative_path: str) -> str:
        """Read a prompt template from ThesisAppRework."""
        prompt_path = (
            Path(
                "/home/ubuntu/ThesisAppRework/misc/prompts/v2",
            )
            / relative_path
        )
        if prompt_path.exists():
            return prompt_path.read_text()
        logger.warning("Old prompt not found: %s", prompt_path)
        return ""
