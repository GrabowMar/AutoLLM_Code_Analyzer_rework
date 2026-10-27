"""Validate generation data: requirement specs, catalog and manifests, run presets, starter packages.

Pure data checks (no DB access) — thin wrapper around
:mod:`backend.generation.services.data_lint`. Warnings (v3 spec request
samples the conformance tester would send verbatim, redirect statuses) are
printed but do not fail the command.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.services.data_lint import lint_all
from backend.generation.services.data_lint import lint_requirement_warnings


class Command(BaseCommand):
    help = (
        "Validate generation data: requirement specs (schema.json), catalog/manifest profiles, "
        "system run presets and starter packages"
    )

    def handle(self, *args, **options) -> None:
        for warning in lint_requirement_warnings():
            self.stderr.write(self.style.WARNING(f"warning: {warning}"))
        errors = lint_all()
        if errors:
            for err in errors:
                self.stderr.write(self.style.ERROR(err))
            msg = f"{len(errors)} lint error(s) in generation data"
            raise CommandError(msg)
        self.stdout.write(self.style.SUCCESS("Generation data OK"))
