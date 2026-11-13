"""``manage.py reevaluate_platform_failures [RUN_ID ...] [--study ID] [--relock] [--no-force-rebuild] [--apply]``.

Lists (default, read-only) or re-evaluates (``--apply``) runs whose latest evaluation died of a platform CPU
fault (``SIGILL`` / ``Illegal instruction`` / exit 132 in a failed stage's log, or an outcome already
``infra_error:platform_cpu``). ``--apply`` queues one re-evaluation per run through
``evaluation.supersede.supersede``: the run's ``execution.eval_supersessions`` and the validity note
``eval_superseded:platform_cpu`` record what was replaced; the run's outcome and ``metrics.eval`` follow the new
evaluation when the eval worker finishes it. ``--relock`` also drops a **platform** lock so the lock step
re-resolves it seeded with the tree's own ``package-lock.json`` (a model lock is never dropped).

The eval worker runs in the executor, which does not autoreload: restart it on the new code before ``--apply``,
or the queued re-evaluation is classified by the old code.
"""

from __future__ import annotations

import getpass
import os
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError


def _os_user() -> str:
    try:
        return os.environ.get("SUDO_USER") or getpass.getuser()
    except Exception:  # noqa: BLE001 - no passwd entry in a container
        return "unknown"


class Command(BaseCommand):
    help = "List or re-evaluate runs whose latest evaluation failed on a platform CPU fault (SIGILL)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("run_ids", nargs="*", help="run ids (default: scan every run, or --study)")
        parser.add_argument("--study", default="", help="limit the scan to one study id")
        parser.add_argument("--relock", action="store_true", help="drop a platform lock so it is re-resolved")
        parser.add_argument(
            "--no-force-rebuild",
            action="store_true",
            help="allow a cached image (default: rebuild; a SIGILL build was never cached anyway)",
        )
        parser.add_argument(
            "--reason",
            default="platform_cpu",
            choices=["platform_cpu"],
            help="note parameter: the only platform fault this command detects (default platform_cpu)",
        )
        parser.add_argument("--note", default="", help="free text stored in the supersession entry")
        parser.add_argument(
            "--by",
            default="",
            help="who applies it, stored in the supersession entry (default: the OS user running the command)",
        )
        parser.add_argument(
            "--include-reclassified",
            action="store_true",
            help="also re-evaluate runs already ending infra_error:platform_cpu (useful with --relock)",
        )
        parser.add_argument("--apply", action="store_true", help="queue the re-evaluations (default: list only)")

    def handle(self, *args: Any, **options: Any) -> None:
        from backend.generation.evaluation import supersede as supersede_mod
        from backend.generation.models import GenerationRun

        candidates = supersede_mod.find_platform_failures(
            study=options["study"] or None,
            run_ids=options["run_ids"] or None,
        )
        if options["run_ids"]:
            missing = sorted(set(options["run_ids"]) - {c.run_id for c in candidates})
            for run_id in missing:
                self.stderr.write(f"skip {run_id}: no platform fault in its latest evaluation")
        chosen = [c for c in candidates if c.misclassified or options["include_reclassified"]]
        for c in candidates:
            mark = "*" if c in chosen else " "
            self.stdout.write(
                f"{mark} run={c.run_id} study={c.study_id or '-'} outcome={c.outcome}:{c.outcome_reason or '-'} "
                f"evaluation={c.evaluation_id} stage={c.fault_stage or '-'} lock={c.lock_source or '-'}",
            )
        self.stdout.write(f"{len(candidates)} with a platform fault, {len(chosen)} selected (*)")
        if not options["apply"]:
            self.stdout.write("dry run: pass --apply to queue the re-evaluations")
            return
        actor = f"manage.py reevaluate_platform_failures ({options['by'] or _os_user()})"
        failed = 0
        for c in chosen:
            run = GenerationRun.objects.get(pk=c.run_id)
            try:
                pending = supersede_mod.supersede(
                    run,
                    reason=options["reason"],
                    relock=options["relock"],
                    force_rebuild=not options["no_force_rebuild"],
                    actor=actor,
                    note=options["note"],
                )
            except supersede_mod.SupersedeRefusedError as exc:
                failed += 1
                self.stderr.write(f"refused: {exc}")
                continue
            self.stdout.write(f"queued run={c.run_id} evaluation={pending.pk} (supersedes {c.evaluation_id})")
        if failed:
            msg = f"{failed} run(s) refused"
            raise CommandError(msg)
