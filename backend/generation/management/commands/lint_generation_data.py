"""Validate generation data: requirement specs, catalog and manifests, run presets, starter packages.

Pure data checks (no DB access) — thin wrapper around
:mod:`backend.generation.services.data_lint`.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.services.data_lint import lint_all


class Command(BaseCommand):
    help = (
        "Validate generation data: requirement specs (schema.json), catalog/manifest profiles, "
        "system run presets and starter packages"
    )

    def handle(self, *args, **options) -> None:
        errors = lint_all()
        if errors:
            for err in errors:
                self.stderr.write(self.style.ERROR(err))
            msg = f"{len(errors)} lint error(s) in generation data"
            raise CommandError(msg)
        self.stdout.write(self.style.SUCCESS("Generation data OK"))
