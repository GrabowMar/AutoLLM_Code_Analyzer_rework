"""Seed benchmark scores from a JSON fixture file.

Usage:
    python manage.py seed_benchmarks path/to/benchmarks.json [--source manual]

Fixture format:
    [
      {"model_id": "openai/gpt-4o", "humaneval": 90.2, "mbpp": 87.6, ...},
      ...
    ]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from llm_lab.rankings.models import BenchmarkResult


class Command(BaseCommand):
    help = "Seed BenchmarkResult rows from a JSON fixture."

    def add_arguments(self, parser):
        parser.add_argument("fixture", type=str)
        parser.add_argument(
            "--source",
            type=str,
            default=BenchmarkResult.Source.SEED,
            choices=[c[0] for c in BenchmarkResult.Source.choices],
        )
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Delete all existing benchmark rows before seeding.",
        )

    def handle(self, *args, **options):
        path = Path(options["fixture"])
        if not path.exists():
            msg = f"Fixture not found: {path}"
            raise CommandError(msg)

        try:
            data: list[dict[str, Any]] = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            msg = f"Invalid JSON: {e}"
            raise CommandError(msg) from e

        if options["purge"]:
            n = BenchmarkResult.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(f"Purged {n} rows"))

        created, updated = 0, 0
        for entry in data:
            model_id = entry.get("model_id")
            if not model_id:
                continue
            for key, value in entry.items():
                if key == "model_id" or value is None:
                    continue
                _, was_created = BenchmarkResult.objects.update_or_create(
                    model_id=model_id,
                    benchmark=key,
                    defaults={
                        "score": float(value),
                        "source": options["source"],
                    },
                )
                created += int(was_created)
                updated += int(not was_created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded benchmarks: {created} created, {updated} updated",
            ),
        )
