"""Seed the analyzer tool catalog ("shop") from ``data/tools/*.yaml``.

Idempotent: each tool is keyed by ``slug`` and upserted via
``update_or_create`` (mirrors ``seed_generation_templates``).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from django.core.management.base import BaseCommand

from backend.analysis.models import AnalyzerTool

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "tools"

# Fields copied verbatim from YAML into the model (when present).
_FIELDS = (
    "name",
    "description",
    "category",
    "kind",
    "target_language",
    "icon",
    "version",
    "install_cmd",
    "verify_cmd",
    "run_cmd",
    "parser_key",
    "config_schema",
    "default_config",
    "run_timeout",
    "install_timeout",
    "sample_code",
    "display_order",
    "is_enabled",
)


class Command(BaseCommand):
    help = "Seed analyzer tool catalog from data/tools/*.yaml"

    def handle(self, *args, **options) -> None:
        if not DATA_DIR.exists():
            self.stderr.write(f"No data dir at {DATA_DIR}")
            return

        created = updated = 0
        for path in sorted(DATA_DIR.glob("*.yaml")):
            if path.name == "catalog.yaml":
                continue
            data = yaml.safe_load(path.read_text()) or {}
            slug = data.get("slug") or path.stem
            defaults = {f: data[f] for f in _FIELDS if f in data}
            defaults.setdefault("is_system", True)
            _obj, was_created = AnalyzerTool.objects.update_or_create(
                slug=slug,
                defaults=defaults,
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created tool: {slug}"))
            else:
                updated += 1
                self.stdout.write(f"Updated tool: {slug}")

        self.stdout.write(
            self.style.SUCCESS(f"Done. {created} created, {updated} updated."),
        )
