"""``manage.py oracle_validate [--task SLUG] [--stack SLUG] [--suite REF] [--runs 3] [--kit] [--kinds K,...]``.

Spec 8.5. ``--kit`` validates the probe kinds: each kind's vulnerable mutant of the bundled reference app must
be ``exploited`` and the fixed app ``not_exploited`` (local runtime; the app is standard-library Python); the
verdicts go to ``evaluation/conformance/probe_validation.json`` and a kind without a passing pair stays
disabled (``probe_unvalidated``).

Without ``--kit`` it runs each task's reference (``data/tasks/<slug>/reference/<stack>/``) ``--runs`` times:
every run must pass 100%, the bare starter must score below 0.5, checks with mixed results become the suite's
``flaky_checks`` (a new suite version). The verdict goes to ``TaskSpecVersion.oracle``. Needs the docker
runtime (``EVAL_RUNTIME=docker``) and the pinned tester image.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError


class Command(BaseCommand):
    help = "Validate task oracles (3 runs, flaky list, bare starter) or, with --kit, the probe mutant pairs."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--task",
            action="append",
            default=[],
            help="task slug (repeatable; default: all with references)",
        )
        parser.add_argument(
            "--stack",
            action="append",
            default=[],
            help="stack slug (repeatable; default: every fixed stack)",
        )
        parser.add_argument("--suite", default="", help="eval suite ref (default: the latest eval-v2)")
        parser.add_argument("--runs", type=int, default=3)
        parser.add_argument("--kit", action="store_true", help="validate the probe kinds on the mutant pairs")
        parser.add_argument("--kinds", default="", help="comma-separated probe kinds (with --kit)")
        parser.add_argument("--dry-run", action="store_true", help="with --kit: do not write probe_validation.json")

    def handle(self, *args: Any, **options: Any) -> None:
        from backend.generation.evaluation import conformance

        if options["kit"]:
            kinds = [k for k in options["kinds"].split(",") if k] or None
            verdicts = conformance.validate_probe_kinds(kinds=kinds, write=not options["dry_run"])
            for kind, row in verdicts.items():
                self.stdout.write(
                    f"{kind:24s} mutant={row.get('mutant')!s:14s} fixed={row.get('fixed')!s:14s} "
                    f"validated={row['validated']}",
                )
            failed = [k for k, row in verdicts.items() if not row["validated"]]
            self.stdout.write(f"{len(verdicts) - len(failed)}/{len(verdicts)} probe kinds validated")
            return
        self._oracles(options)

    def _oracles(self, options: dict[str, Any]) -> None:
        from backend.generation.evaluation import baseline
        from backend.generation.evaluation import conformance
        from backend.generation.models import EvalSuite
        from backend.generation.models import TaskSpecVersion

        suite = self._suite(options["suite"], EvalSuite)
        stacks = [s for s in conformance.fixed_stacks() if not options["stack"] or s.slug in options["stack"]]
        if not stacks:
            msg = "no matching fixed stacks are seeded"
            raise CommandError(msg)
        slugs = options["task"] or sorted(p.name for p in baseline.TASKS_ROOT.iterdir() if (p / "reference").is_dir())
        results: list[dict[str, Any]] = []
        for slug in slugs:
            version = TaskSpecVersion.objects.filter(task__slug=slug).order_by("-version").first()
            if version is None:
                self.stderr.write(f"task {slug} is not seeded")
                continue
            for stack in stacks:
                outcome = conformance.validate_oracle(version, stack, suite, runs=options["runs"])
                results.append({"task": slug, "stack": stack.slug, **outcome})
                self.stdout.write(json.dumps(results[-1], default=str))
        self.stdout.write(f"{sum(1 for r in results if r['status'] == 'pass')}/{len(results)} oracles pass")

    @staticmethod
    def _suite(ref: str, model: Any) -> Any:
        if ref:
            from backend.generation.evaluation.suite import _suite_from_ref

            return _suite_from_ref(ref)
        suite = model.objects.filter(slug="eval-v2", is_archived=False).order_by("-version").first()
        if suite is None:
            msg = "no eval-v2 suite is seeded (manage.py seed_library)"
            raise CommandError(msg)
        return suite
