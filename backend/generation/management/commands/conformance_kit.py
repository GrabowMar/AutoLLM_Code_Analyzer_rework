"""``manage.py conformance_kit [--stack SLUG] [--suite REF]``: the tester conformance kit (spec 8.5).

For every backend framework offered as a fixed stack, evaluate the reference apps of the kit tasks
(``crud_todo_list``, ``team_roles_multitenant``, ``file_share_multipart``; trees under
``data/tasks/<slug>/reference/<stack slug>/``). Each must score ``agent_pass_rate == 1.0`` with every probe
``not_exploited``. The verdicts go to ``TaskSpecVersion.oracle`` (``kit:<stack pinned ref>``) and drive
``evaluation.conformance.coverage()``: a framework without a passing kit gives ``conformance_missing`` at plan
resolution (warn exploratory, error confirmatory). Needs the docker runtime and the pinned tester image.
``--status`` prints the stored status only.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError


class Command(BaseCommand):
    help = "Run (or show) the tester conformance kit per backend framework."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--stack",
            action="append",
            default=[],
            help="stack slug (repeatable; default: every fixed stack)",
        )
        parser.add_argument("--suite", default="", help="eval suite ref (default: the latest eval-v2)")
        parser.add_argument("--status", action="store_true", help="print the stored status and coverage only")

    def handle(self, *args: Any, **options: Any) -> None:
        from backend.generation.evaluation import conformance
        from backend.generation.management.commands.oracle_validate import Command as Oracle
        from backend.generation.models import EvalSuite

        if not options["status"]:
            suite = Oracle._suite(options["suite"], EvalSuite)  # noqa: SLF001
            stacks = [s for s in conformance.fixed_stacks() if not options["stack"] or s.slug in options["stack"]]
            if not stacks:
                msg = "no matching fixed stacks are seeded"
                raise CommandError(msg)
            for stack in stacks:
                for row in conformance.run_kit(stack, suite):
                    self.stdout.write(json.dumps({"stack": stack.slug, **row}))
        for row in conformance.status_rows():
            self.stdout.write(json.dumps(row))
        self.stdout.write(json.dumps({"coverage": conformance.coverage()}))
