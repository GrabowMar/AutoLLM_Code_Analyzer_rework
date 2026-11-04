"""``manage.py seed_library``: seed the v3 library from ``generation/data/`` (spec section 11.1).

Idempotent: a content hash equal to an existing version is a no-op, a new hash creates version+1 as a system
entity, and user-owned slugs are never touched (reported as skipped). ``--strict`` exits non-zero when an
entry fails (lint errors, unresolved refs) or is skipped.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.library.seeding import SEED_ORDER
from backend.generation.library.seeding import seed_library


class Command(BaseCommand):
    help = "Seed the generation library (tasks, stacks, starters, prompts, presets, ...) from data/."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--data-dir", help="seed from this directory instead of GENERATION_LIBRARY_DATA_DIR")
        parser.add_argument("--kind", action="append", choices=SEED_ORDER, help="seed only these kinds")
        parser.add_argument("--dry-run", action="store_true", help="report what would change and roll back")
        parser.add_argument("--strict", action="store_true", help="fail when an entry errors or is skipped")
        parser.add_argument("--json", action="store_true", help="print the full report as JSON")
        parser.add_argument("--database", default="default")

    def handle(self, *args: Any, **options: Any) -> None:
        report = seed_library(
            using=options["database"],
            data_dir=options.get("data_dir"),
            kinds=options.get("kind"),
            dry_run=options["dry_run"],
        )
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            prefix = "[dry run] " if report["dry_run"] else ""
            self.stdout.write(
                f"{prefix}{len(report['created'])} created, {len(report['updated'])} updated, "
                f"{report['unchanged']} unchanged, {len(report['skipped'])} skipped, "
                f"{len(report['deferred'])} deferred, "
                f"{len(report['errors'])} errors, {len(report['lint_errors'])} with lint errors",
            )
            for ref in report["created"]:
                self.stdout.write(f"  + {ref}")
            for ref in report["updated"]:
                self.stdout.write(f"  ~ {ref}")
            for item in report["skipped"]:
                self.stdout.write(self.style.WARNING(f"  skipped {item['key']}: {item['reason']}"))
            for item in report["errors"]:
                self.stdout.write(self.style.ERROR(f"  error {item['key']}: {item['detail']}"))
                for issue in item.get("issues") or []:
                    self.stdout.write(f"      {issue['severity']} {issue['code']}: {issue['message']}")
            for key, issues in report["lint_errors"].items():
                self.stdout.write(self.style.ERROR(f"  lint errors {key} (seeded):"))
                for issue in issues:
                    self.stdout.write(f"      {issue['code']}: {issue['message']}")
        if options["strict"] and (report["errors"] or report["skipped"] or report["lint_errors"]):
            msg = (
                f"seed_library: {len(report['errors'])} errors, {len(report['lint_errors'])} with lint errors, "
                f"{len(report['skipped'])} skipped"
            )
            raise CommandError(msg)
