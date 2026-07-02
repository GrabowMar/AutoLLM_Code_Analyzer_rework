"""Seed the analyzer tool catalog ("shop") from ``data/tools/*.yaml``.

Thin wrapper around :func:`backend.analysis.seeding.seed_tools`, which also
runs automatically after every ``migrate``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from backend.analysis.seeding import seed_tools


class Command(BaseCommand):
    help = "Seed analyzer tool catalog from data/tools/*.yaml"

    def handle(self, *args, **options) -> None:
        created, updated = seed_tools(log=self.stdout.write)
        self.stdout.write(
            self.style.SUCCESS(f"Done. {created} created, {updated} updated."),
        )
