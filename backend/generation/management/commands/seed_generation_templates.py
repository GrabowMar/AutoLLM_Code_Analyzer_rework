"""Seed requirements, prompts, content blocks, and template bundles.

Thin wrapper around :func:`backend.generation.seeding.seed_all`, which also
runs automatically after every ``migrate``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from backend.generation.seeding import seed_all


class Command(BaseCommand):
    help = "Seed requirements, prompts, content blocks, and template bundles"

    def handle(self, *args, **options) -> None:
        results = seed_all(log=self.stdout.write)
        total_created = sum(c for c, _u in results.values())
        total_updated = sum(u for _c, u in results.values())
        self.stdout.write(
            self.style.SUCCESS(f"Seeding complete. {total_created} created, {total_updated} updated."),
        )
