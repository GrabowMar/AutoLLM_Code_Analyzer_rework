"""``manage.py reevaluate_under_suite --suite eval-v2@2 [RUN_ID ...] [--study ID ...] [--apply]``.

Re-evaluates existing runs under another ``EvalSuite`` version (the instrument fix eval-v2@1 -> eval-v2@2).
**Append-only**: every re-evaluation is a new ``RunEvaluation`` row; the evaluations stored under the old suite
are final and are never changed or deleted.

Selection: the given run ids and / or every run of the given studies (``--study`` may repeat). Without
``--apply`` (the default) nothing is written: the command lists what it would queue and why it would skip a
run. A run is skipped when it

* already has a final, non-retest evaluation under the target suite (``--force`` evaluates it again),
* is still active (pending / running, or an evaluation of it is pending / running),
* has no collected tree, or was decided by the generation stage (``output_invalid`` / ``excluded``),
* does not match ``--outcome`` (a comma-separated filter on the run's current outcome).

``--apply`` queues the re-evaluations through the normal eval lane (``evaluation.supersede.requeue_under_suite``
-> ``agentic.actions.evaluate``): the executor's eval slots bound how many run at once, and ``--max-queued N``
(default 8, ``0`` = no bound) bounds how many runs this command keeps in the lane at a time -- it queues the
next ones as earlier ones finish. Each queued run gets an ``execution.eval_supersessions`` entry (reason
``suite_upgrade``, from / to suite, the superseded evaluation and its metrics mirror) and the info note
``eval_superseded:suite_upgrade``. ``--wait`` keeps the command running until every queued evaluation is final
and prints the outcome counts.

Starter baselines belong to a suite (``EvalBaseline`` per starter, task version, auth mode, suite). Before any
run is queued, ``--apply`` queues the baselines the target suite lacks for the selection and waits for them
(``--baseline-timeout``; ``--skip-baselines`` to go on without: ``baseline_pass_rate`` is then ``None`` for
those runs, never 0).

**Which evaluation is current** after this: the newest final non-retest evaluation of the run
(``evaluation.supersede.current_evaluation``) -- the one ``run.outcome`` and ``run.metrics["eval"]`` follow;
``metrics.eval.eval_suite`` names its suite. Analyses that must not mix instruments filter on the suite.

Before ``--apply``: the eval worker runs in the executor, which does not autoreload. It must run the same code
as this command (compiler, overlays) and the tester image the target suite pins, else every re-evaluation ends
``infra_error`` (``image_digest_mismatch`` / ``fixtures_digest_mismatch``) -- never a wrong score. The command
checks this process's tree against the suite's ``fixtures_digest`` and compiler version and refuses to queue on
a mismatch.
"""

from __future__ import annotations

import getpass
import os
import time
from collections import Counter
from typing import TYPE_CHECKING
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

if TYPE_CHECKING:
    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun

_GENERATION_DECIDED = frozenset({"output_invalid", "excluded"})
_ACTIVE = frozenset({"pending", "running"})


def _os_user() -> str:
    try:
        return os.environ.get("SUDO_USER") or getpass.getuser()
    except Exception:  # noqa: BLE001 - no passwd entry in a container
        return "unknown"


def _suite(ref: str) -> EvalSuite:
    from backend.generation.evaluation.suite import _suite_from_ref

    try:
        return _suite_from_ref(ref)
    except (LookupError, ValueError) as exc:
        msg = f"--suite {ref!r}: {exc}"
        raise CommandError(msg) from exc


def instrument_problems(suite: EvalSuite) -> list[str]:
    """Why this process's tree cannot evaluate under ``suite`` (empty: it can, as far as the code is concerned)."""
    from backend.generation.evaluation import acceptance
    from backend.generation.evaluation import suite as suite_mod

    content = suite.content or {}
    problems: list[str] = []
    pinned = suite_mod._bare_digest(content.get("fixtures_digest"))  # noqa: SLF001 - the suite's own normaliser
    if pinned:
        tree = suite_mod.tree_fixtures_digest()
        if tree != pinned:
            problems.append(f"fixtures_digest: suite pins {pinned[:12]}, this tree has {(tree or 'none')[:12]}")
        if content.get("acceptance_compiler_version") != acceptance.COMPILER_VERSION:
            problems.append(
                f"compiler: suite pins v{content.get('acceptance_compiler_version')}, "
                f"this tree has v{acceptance.COMPILER_VERSION}",
            )
    return problems


class Command(BaseCommand):
    help = "List (default) or queue (--apply) re-evaluations of existing runs under another eval suite version."

    # Seams for the tests: no real sleeping, no real clock.
    sleep = staticmethod(time.sleep)
    clock = staticmethod(time.monotonic)

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("run_ids", nargs="*", help="run ids to re-evaluate")
        parser.add_argument("--study", action="append", default=[], help="every run of this study id (repeatable)")
        parser.add_argument("--suite", required=True, help="target suite ref, e.g. eval-v2@2 (or eval-v2@2#<hash12>)")
        parser.add_argument("--outcome", default="", help="only runs whose outcome is one of these (comma-separated)")
        parser.add_argument("--limit", type=int, default=0, help="queue at most this many runs (0 = all selected)")
        parser.add_argument("--force", action="store_true", help="re-evaluate runs already evaluated under the suite")
        parser.add_argument("--force-rebuild", action="store_true", help="rebuild the app image (default: cache)")
        parser.add_argument("--max-queued", type=int, default=8, help="runs kept in the eval lane at a time (0 = all)")
        parser.add_argument("--poll-s", type=float, default=15.0, help="seconds between checks of the eval lane")
        parser.add_argument("--wait", action="store_true", help="wait until every queued evaluation is final")
        parser.add_argument("--skip-baselines", action="store_true", help="do not queue / wait for starter baselines")
        parser.add_argument("--baseline-timeout", type=float, default=3600.0, help="seconds to wait for baselines")
        parser.add_argument("--note", default="", help="free text stored in each supersession entry")
        parser.add_argument("--by", default="", help="who applies it (default: the OS user running the command)")
        parser.add_argument("--apply", action="store_true", help="queue the re-evaluations (default: list only)")

    # selection -----------------------------------------------------------------------------

    def _runs(self, options: dict[str, Any]) -> list[GenerationRun]:
        from django.core.exceptions import ValidationError
        from django.db.models import Q

        from backend.generation.models import GenerationRun

        ids, studies = list(options["run_ids"]), list(options["study"])
        if not ids and not studies:
            msg = "name run ids and / or --study: this command never selects every run by default"
            raise CommandError(msg)
        where = Q()
        if ids:
            where |= Q(pk__in=ids)
        if studies:
            where |= Q(study_id__in=studies)
        try:
            runs = list(
                GenerationRun.objects.filter(where)
                .select_related("starter", "task_version", "parent_run")
                .order_by("created_at", "pk"),
            )
        except (ValidationError, ValueError) as exc:
            msg = f"bad run or study id: {exc}"
            raise CommandError(msg) from exc
        found = {str(r.pk) for r in runs}
        for run_id in ids:
            if str(run_id) not in found:
                self.stderr.write(f"skip {run_id}: no such run")
        return runs

    def _skip_reason(  # noqa: PLR0911 - one reason per rule
        self,
        run: GenerationRun,
        suite: EvalSuite,
        options: dict[str, Any],
    ) -> str | None:
        from backend.generation.evaluation import supersede as supersede_mod
        from backend.generation.models import RunEvaluation
        from backend.generation.models.choices import RunKind

        wanted = {o.strip() for o in str(options["outcome"]).split(",") if o.strip()}
        if wanted and run.outcome not in wanted:
            return f"outcome {run.outcome or '-'} not in --outcome"
        if run.status in _ACTIVE:
            return f"active ({run.status}/{run.phase or '-'})"
        if RunEvaluation.objects.filter(run=run, retest_of__isnull=True, status__in=list(_ACTIVE)).exists():
            return "an evaluation of it is pending or running"
        if not run.tree_hash and run.kind != RunKind.REFERENCE:
            return "no collected tree"
        if run.outcome in _GENERATION_DECIDED:
            return f"decided by the generation stage ({run.outcome})"
        done = supersede_mod.current_evaluation(run, suite=suite)
        if done is not None and not options["force"]:
            return f"already under {suite.ref}: evaluation {done.pk} ({done.status})"
        return None

    # baselines -----------------------------------------------------------------------------

    def _missing_baselines(self, runs: list[GenerationRun], suite: EvalSuite) -> list[tuple[Any, Any, str]]:
        from backend.generation.evaluation.suite import _auth_mode
        from backend.generation.evaluation.suite import _inherited
        from backend.generation.models import EvalBaseline
        from backend.generation.models.choices import ReferenceOf

        if str((suite.content or {}).get("baseline") or "starter") != "starter":
            return []
        seen: dict[tuple[Any, Any, str], tuple[Any, Any, str]] = {}
        for run in runs:
            if run.reference_of == ReferenceOf.STARTER_BASELINE:
                continue
            starter, task_version = _inherited(run, "starter"), _inherited(run, "task_version")
            if starter is None or task_version is None:
                continue
            seen.setdefault((starter.pk, task_version.pk, _auth_mode(run)), (starter, task_version, _auth_mode(run)))
        return [
            combo
            for combo in seen.values()
            if not EvalBaseline.objects.filter(
                starter=combo[0],
                task_version=combo[1],
                auth_mode=combo[2],
                eval_suite=suite,
            ).exists()
        ]

    def _queue_baselines(self, missing: list[tuple[Any, Any, str]], suite: EvalSuite, timeout_s: float) -> None:
        from backend.generation.evaluation import baseline
        from backend.generation.models import GenerationRun

        queued: list[Any] = []
        for starter, task_version, auth_mode in missing:
            run = baseline.queue_baseline(starter, task_version, auth_mode, suite)
            if run is not None:
                queued.append(run.pk)
                self.stdout.write(f"queued baseline run={run.pk} starter={starter.pk} auth={auth_mode}")
        deadline = self.clock() + timeout_s
        while queued:
            active = GenerationRun.objects.filter(pk__in=queued, status__in=list(_ACTIVE)).count()
            if not active:
                break
            if self.clock() >= deadline:
                msg = f"{active} baseline run(s) still active after {timeout_s:.0f}s; nothing else was queued"
                raise CommandError(msg)
            self.sleep(float(self.poll_s))
        still = len(self._missing_now(missing, suite))
        if still:
            self.stderr.write(
                f"{still} starter baseline(s) were not measured under {suite.ref}: baseline_pass_rate stays None "
                "for their runs (never 0)",
            )

    def _missing_now(self, combos: list[tuple[Any, Any, str]], suite: EvalSuite) -> list[tuple[Any, Any, str]]:
        from backend.generation.models import EvalBaseline

        return [
            c
            for c in combos
            if not EvalBaseline.objects.filter(
                starter=c[0],
                task_version=c[1],
                auth_mode=c[2],
                eval_suite=suite,
            ).exists()
        ]

    # main ----------------------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        from backend.generation.evaluation import supersede as supersede_mod
        from backend.generation.models import RunEvaluation

        self.poll_s = max(0.0, float(options["poll_s"]))
        suite = _suite(options["suite"])
        runs = self._runs(options)
        chosen: list[GenerationRun] = []
        skipped: Counter[str] = Counter()
        for run in runs:
            reason = self._skip_reason(run, suite, options)
            current = supersede_mod.current_evaluation(run)
            now = f"{current.pk}@{current.eval_suite.ref}" if current is not None else "-"
            mark = " " if reason else "*"
            self.stdout.write(
                f"{mark} run={run.pk} study={run.study_id or '-'} outcome={run.outcome or '-'}:"
                f"{run.outcome_reason or '-'} current={now}" + (f" skip: {reason}" if reason else ""),
            )
            if reason:
                skipped[reason.split(":")[0].split(" (")[0]] += 1
            else:
                chosen.append(run)
        if options["limit"] and len(chosen) > options["limit"]:
            self.stdout.write(f"--limit {options['limit']}: {len(chosen) - options['limit']} selected run(s) left out")
            chosen = chosen[: options["limit"]]
        self.stdout.write(f"suite {suite.pinned_ref}: {len(runs)} run(s), {len(chosen)} to re-evaluate (*)")
        for reason, n in sorted(skipped.items()):
            self.stdout.write(f"  skipped {n}: {reason}")
        problems = instrument_problems(suite)
        for problem in problems:
            self.stderr.write(f"instrument mismatch: {problem}")
        missing = [] if options["skip_baselines"] else self._missing_baselines(chosen, suite)
        if missing:
            self.stdout.write(
                f"{len(missing)} starter baseline(s) missing under {suite.ref} (queued first by --apply)",
            )
        if not options["apply"]:
            self.stdout.write("dry run: pass --apply to queue the re-evaluations")
            return
        if problems:
            msg = "this tree is not the instrument the suite pins; nothing was queued"
            raise CommandError(msg)
        if missing:
            self._queue_baselines(missing, suite, float(options["baseline_timeout"]))
        actor = f"manage.py reevaluate_under_suite ({options['by'] or _os_user()})"
        bound = max(0, int(options["max_queued"]))
        queued: list[int] = []
        refused = 0
        todo = list(chosen)
        while todo:
            if bound:
                inflight = RunEvaluation.objects.filter(pk__in=queued, status__in=list(_ACTIVE)).count()
                room = bound - inflight
                if room <= 0:
                    self.sleep(self.poll_s)
                    continue
            else:
                room = len(todo)
            batch, todo = todo[:room], todo[room:]
            for run in batch:
                try:
                    pending = supersede_mod.requeue_under_suite(
                        run,
                        suite,
                        force=options["force"],
                        force_rebuild=options["force_rebuild"],
                        actor=actor,
                        note=options["note"],
                    )
                except supersede_mod.SupersedeRefusedError as exc:
                    refused += 1
                    self.stderr.write(f"refused: {exc}")
                    continue
                queued.append(pending.pk)
                self.stdout.write(f"queued run={run.pk} evaluation={pending.pk}")
        self.stdout.write(f"queued {len(queued)} re-evaluation(s) under {suite.pinned_ref}")
        if options["wait"] and queued:
            while RunEvaluation.objects.filter(pk__in=queued, status__in=list(_ACTIVE)).exists():
                self.sleep(self.poll_s)
            counts = Counter(RunEvaluation.objects.filter(pk__in=queued).values_list("status", flat=True))
            self.stdout.write("final: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if refused:
            msg = f"{refused} run(s) refused"
            raise CommandError(msg)
