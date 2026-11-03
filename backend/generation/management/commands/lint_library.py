"""``manage.py lint_library``: lint the seed data (default) or the stored library (``--db``).

Seed data is linted in memory as system entities (refs resolve to stored rows or to the other entries of the
directory), so it works on an empty database and writes nothing. ``--db`` lints the latest version of every
stored slug. Starters of a phase outside ``build.starters.ACTIVE_PHASES`` are listed as deferred (not
linted); a starter of an active phase (A, B) that cannot be composed is an ``error``. The command exits
non-zero on ``error`` issues (``--fail-on warn`` also fails on warnings).
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.library import service
from backend.generation.library.seeding import SEED_ORDER
from backend.generation.library.seeding import lint_seed_data

_RANK = {"info": 0, "warn": 1, "error": 2}


class Command(BaseCommand):
    help = "Lint the generation library seed data (or, with --db, the stored library)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--data-dir", help="lint this directory instead of GENERATION_LIBRARY_DATA_DIR")
        parser.add_argument("--db", action="store_true", help="lint the stored latest versions instead")
        parser.add_argument("--kind", action="append", choices=SEED_ORDER, help="lint only these kinds")
        parser.add_argument("--fail-on", choices=("error", "warn"), default="error")
        parser.add_argument("--quiet", action="store_true", help="print only entries with warnings or errors")
        parser.add_argument("--json", action="store_true", help="print {key: [issues]} as JSON")

    def handle(self, *args: Any, **options: Any) -> None:
        kinds = options.get("kind") or list(SEED_ORDER)
        deferred: list[dict[str, Any]] = []
        if options["db"]:
            results = self._db(kinds)
        else:
            results = lint_seed_data(options.get("data_dir"), kinds=kinds, deferred=deferred)
        threshold = _RANK[options["fail_on"]]
        failing = 0
        if options["json"]:
            payload = {key: [issue.model_dump(mode="json") for issue in issues] for key, issues in results.items()}
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        for key, issues in sorted(results.items()):
            worst = max((_RANK[issue.severity] for issue in issues), default=-1)
            failing += worst >= threshold
            if options["json"] or (options["quiet"] and worst < 1):
                continue
            label = {2: self.style.ERROR("error"), 1: self.style.WARNING("warn"), 0: "info"}.get(worst, "ok")
            self.stdout.write(f"{label:>5} {key}")
            for issue in issues:
                location = f" [{issue.path}]" if issue.path else ""
                self.stdout.write(f"        {issue.severity} {issue.code}{location}: {issue.message}")
        if not options["json"]:
            for item in deferred:
                self.stdout.write(f"defer {item['key']}: {item['reason']}")
            summary = f"{len(results)} entries linted, {failing} at or above {options['fail_on']}"
            if deferred:
                summary += f", {len(deferred)} deferred (not linted)"
            self.stdout.write(summary)
        if failing:
            msg = f"lint_library: {failing} entries with {options['fail_on']} issues"
            raise CommandError(msg)

    def _db(self, kinds: list[str]) -> dict[str, list[Any]]:
        results: dict[str, list[Any]] = {}
        for kind in kinds:
            field = "task__slug" if kind == "task" else "slug"
            rows = service.model_for(kind).objects.order_by(field, "-version").distinct(field)
            for row in rows:
                system = service.is_system_row(row)
                results[f"{kind}:{row.ref}"] = service.lint_entity(kind, row.content, system=system)
        return results
