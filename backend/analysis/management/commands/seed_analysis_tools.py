"""Seed the analyzer tool catalog, system profiles and the reconcile schedule.

Thin wrapper around :func:`backend.analysis.seeding.seed_all`, which also
runs automatically after every ``migrate`` (without Docker). This command
additionally reads tool versions from the built analyzer image's manifest
unless ``--no-image`` is given.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from backend.analysis.seeding import seed_all


class Command(BaseCommand):
    help = "Seed analyzer tools (data/tools), system profiles (data/profiles) and the reconcile beat entry"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--no-image",
            action="store_true",
            help="Do not contact the apps daemon for tool versions (use the spec versions)",
        )

    def handle(self, *args, **options) -> None:
        report = seed_all(log=self.stdout.write, with_image=not options["no_image"])
        errors = {k: v for k, v in report.items() if isinstance(v, dict) and "error" in v}
        tools = report.get("tools")
        if isinstance(tools, tuple):
            self.stdout.write(f"Tools: {tools[0]} created, {tools[1]} updated")
        if isinstance(report.get("profiles"), dict) and "error" not in report["profiles"]:
            self.stdout.write(f"Profiles: {report['profiles']}")
        if errors:
            for key, value in errors.items():
                self.stderr.write(self.style.ERROR(f"{key}: {value['error']}"))
            return
        self.stdout.write(self.style.SUCCESS("Done."))
