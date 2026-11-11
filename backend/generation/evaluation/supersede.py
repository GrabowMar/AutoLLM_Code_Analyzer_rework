"""Supersede an evaluation that a platform fault decided (operator tool; ``manage.py reevaluate_platform_failures``).

A re-evaluation (``agentic.actions.evaluate``: a pending ``RunEvaluation`` in the eval lane) already makes the
run follow its newest evaluation: ``suite.run_eval_stage`` rewrites ``run.outcome``/``outcome_reason``, the
derived ``usable`` and the ``metrics.eval`` mirror, and ``finish_stage`` refreshes the study cell. Earlier
``RunEvaluation`` rows are final and never change (append-only). What a plain re-evaluation does *not* leave is
a record of **why** an operator replaced a scored evaluation; this module adds that, in the same transaction
that queues the re-evaluation:

* ``execution.eval_supersessions``: one entry per supersession -- when, by whom, the platform reason, the
  superseded evaluation (id, outcome, outcome_reason, the old ``metrics.eval`` mirror, the stage whose log
  shows the fault), the new pending evaluation id and, with ``relock``, the lock that was dropped;
* the validity note ``eval_superseded:<reason>`` (warn), so every export and report that lists notes shows it.

``relock=True`` drops the run's **platform** lock (``resolved_lock_blob``/``lock_source``/
``registry_snapshot_at``; the old blob sha is kept in the entry and the blob store) so the lock step resolves
again -- with the tree's own ``package-lock.json`` as the npm seed (``lock._npm_seed``). That is the one
deliberate exception to "a re-evaluation never re-resolves", for a lock the platform produced and that pulled
a binary the evaluation host cannot run. A model-written lock is never dropped.

Candidates (``find_platform_failures``): runs whose latest evaluation (retests excluded) failed a stage with a
``SIGILL`` log (``suite.stored_platform_cpu_fault``) or already ended ``infra_error:platform_cpu``.

Suite upgrades (``manage.py reevaluate_under_suite``; the instrument fix eval-v2@1 -> eval-v2@2) use the same
record: ``requeue_under_suite`` queues a re-evaluation under another suite version and writes an
``eval_supersessions`` entry with reason ``suite_upgrade`` (from / to suite, the superseded evaluation and its
``metrics.eval`` mirror) and the *info* note ``eval_superseded:suite_upgrade``. Nothing is rewritten: the old
evaluation rows stay as they are.

**Which evaluation is current.** ``current_evaluation(run)`` -- the newest final (measured / not_measured /
error), non-retest ``RunEvaluation`` of the run by ``(created_at, pk)``. It is the row the run's ``outcome`` and
``metrics.eval`` mirror follow (``suite.run_eval_stage``) and the one ``metrics.run_metrics`` reads; its suite is
``metrics.eval.eval_suite`` (``evaluation_id`` names the row). An analysis that must not mix instruments filters
on that suite: ``current_evaluation(run, suite=...)`` is the newest such row under one suite (``None`` when the
run was never evaluated under it). A pending or running re-evaluation is not current until it is final.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

__all__ = [
    "HISTORY_KEY",
    "SUITE_UPGRADE",
    "Candidate",
    "SupersedeRefusedError",
    "current_evaluation",
    "find_platform_failures",
    "requeue_under_suite",
    "supersede",
]

HISTORY_KEY: Final = "eval_supersessions"
NOTE: Final = "eval_superseded"
SUITE_UPGRADE: Final = "suite_upgrade"


class SupersedeRefusedError(RuntimeError):
    """The run cannot be superseded (active, no platform fault, a model lock with ``relock``...)."""


@dataclass(frozen=True)
class Candidate:
    run_id: str
    study_id: str | None
    outcome: str
    outcome_reason: str
    lock_source: str
    evaluation_id: int | None
    fault_stage: str | None

    @property
    def misclassified(self) -> bool:
        """The run is still scored as the model's failure (anything but ``infra_error``)."""
        return self.outcome != "infra_error"


def _latest(run: GenerationRun) -> RunEvaluation | None:
    from backend.generation.models import RunEvaluation

    return (
        RunEvaluation.objects.filter(run=run, retest_of__isnull=True, status__in=RunEvaluation.FINAL_STATUSES)
        .order_by("-created_at", "-pk")
        .first()
    )  # == current_evaluation(run), kept separate: ``supersede`` reads it inside its own row lock


def current_evaluation(run: GenerationRun, *, suite: EvalSuite | None = None) -> RunEvaluation | None:
    """The run's current evaluation (module docstring): newest final non-retest row, optionally under ``suite``."""
    from backend.generation.models import RunEvaluation

    qs = RunEvaluation.objects.filter(run=run, retest_of__isnull=True, status__in=RunEvaluation.FINAL_STATUSES)
    if suite is not None:
        qs = qs.filter(eval_suite=suite)
    return qs.select_related("eval_suite").order_by("-created_at", "-pk").first()


def _candidate(run: GenerationRun) -> Candidate | None:
    from backend.generation.evaluation.suite import PLATFORM_CPU
    from backend.generation.evaluation.suite import stored_platform_cpu_fault

    latest = _latest(run)
    stage = stored_platform_cpu_fault(latest) if latest is not None else None
    if stage is None and run.outcome_reason != PLATFORM_CPU:
        return None
    return Candidate(
        run_id=str(run.pk),
        study_id=str(run.study_id) if run.study_id else None,
        outcome=run.outcome,
        outcome_reason=run.outcome_reason,
        lock_source=run.lock_source,
        evaluation_id=latest.pk if latest is not None else None,
        fault_stage=stage,
    )


def find_platform_failures(
    *,
    study: str | None = None,
    run_ids: Iterable[str] | None = None,
) -> list[Candidate]:
    """Runs whose latest evaluation shows a platform CPU fault (read-only)."""
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

    qs = GenerationRun.objects.exclude(outcome="").order_by("created_at", "pk")
    if study:
        qs = qs.filter(study_id=study)
    if run_ids is not None:
        qs = qs.filter(pk__in=list(run_ids))
    else:
        # Only runs with an evaluation that did not measure; the logs are read in Python (same regex as the suite).
        evaluated = RunEvaluation.objects.filter(
            retest_of__isnull=True,
            status__in=["not_measured", "error"],
        ).values("run_id")
        qs = qs.filter(pk__in=evaluated)
    return [c for c in (_candidate(run) for run in qs) if c is not None]


def supersede(
    run: GenerationRun,
    *,
    reason: str = "platform_cpu",
    relock: bool = False,
    force_rebuild: bool = True,
    actor: str = "",
    note: str = "",
) -> RunEvaluation:
    """Queue a re-evaluation that supersedes ``run``'s platform-failed evaluation; returns the pending row."""
    from backend.execution.transitions import jsonb_merge
    from backend.generation.agentic import actions
    from backend.generation.agentic import validity
    from backend.generation.contracts.outcome import note_code_valid
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import LockSource

    code = f"{NOTE}:{reason}"
    if not note_code_valid(code):
        msg = f"reason {reason!r} is not a valid note parameter (lowercase snake_case)"
        raise SupersedeRefusedError(msg)
    with transaction.atomic():
        row = GenerationRun.objects.select_for_update().get(pk=run.pk)
        candidate = _candidate(row)
        if candidate is None:
            msg = f"run {row.pk}: its latest evaluation shows no platform fault ({row.outcome}:{row.outcome_reason})"
            raise SupersedeRefusedError(msg)
        previous = _latest(row)
        entry: dict[str, Any] = {
            "at": timezone.now().isoformat(),
            "by": actor or "manage.py reevaluate_platform_failures",
            "reason": reason,
            "note": note[:500],
            "superseded_evaluation": previous.pk if previous is not None else None,
            "eval_suite": previous.eval_suite.pinned_ref if previous is not None else None,
            "superseded_outcome": row.outcome,
            "superseded_outcome_reason": row.outcome_reason,
            "superseded_eval": (row.metrics or {}).get("eval"),
            "fault_stage": candidate.fault_stage,
            "force_rebuild": force_rebuild,
            "relock": None,
        }
        if relock:
            if row.lock_source != LockSource.PLATFORM or not row.resolved_lock_blob:
                msg = f"run {row.pk}: --relock drops only a platform lock (lock_source={row.lock_source or 'none'})"
                raise SupersedeRefusedError(msg)
            entry["relock"] = {
                "previous_lock_blob": row.resolved_lock_blob,
                "previous_lock_source": row.lock_source,
                "previous_registry_snapshot_at": (
                    row.registry_snapshot_at.isoformat() if row.registry_snapshot_at else None
                ),
            }
            GenerationRun.objects.filter(pk=row.pk).update(
                resolved_lock_blob="",
                lock_source="",
                registry_snapshot_at=None,
            )
            row.resolved_lock_blob, row.lock_source, row.registry_snapshot_at = "", "", None
        try:
            # Under the superseded evaluation's suite: only the platform fault changes, never the yardstick.
            suite_ref = previous.eval_suite.pinned_ref if previous is not None else None
            pending = actions.evaluate(row, suite_ref=suite_ref, force_rebuild=force_rebuild)
        except actions.ActionRefused as exc:
            msg = f"run {row.pk}: {exc}"
            raise SupersedeRefusedError(msg) from exc  # rolls the relock back too
        entry["evaluation"] = pending.pk
        row.refresh_from_db(fields=["execution", "validity"])
        history = list((row.execution or {}).get(HISTORY_KEY) or [])
        detail = (
            f"evaluation {entry['superseded_evaluation']} ({row.outcome}:{row.outcome_reason or '-'}) superseded by "
            f"{pending.pk}: {reason}" + (" (platform lock re-resolved)" if relock else "")
        )
        GenerationRun.objects.filter(pk=row.pk).update(
            execution=jsonb_merge({HISTORY_KEY: [*history, entry]}),
            validity=validity.add_notes(row.validity, [validity.note(code, level="warn", detail=detail)]),
        )
    run.refresh_from_db()
    return pending


def requeue_under_suite(
    run: GenerationRun,
    suite: EvalSuite,
    *,
    force: bool = False,
    force_rebuild: bool = False,
    actor: str = "",
    note: str = "",
) -> RunEvaluation:
    """Queue a re-evaluation of ``run`` under ``suite`` (append-only) and record the suite upgrade.

    Refused (``SupersedeRefusedError``) when the run is active, has no tree, was decided by the generation
    stage, or -- without ``force`` -- already has a final non-retest evaluation under ``suite``. The pending row
    goes through the normal eval lane (``agentic.actions.evaluate``), so the executor's eval slots bound the
    concurrency; the run's outcome and ``metrics.eval`` follow the new evaluation when it is final.
    """
    from backend.execution.transitions import jsonb_merge
    from backend.generation.agentic import actions
    from backend.generation.agentic import validity
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

    with transaction.atomic():
        row = GenerationRun.objects.select_for_update().get(pk=run.pk)
        done = current_evaluation(row, suite=suite)
        if done is not None and not force:
            msg = f"run {row.pk}: evaluation {done.pk} is already under {suite.pinned_ref}"
            raise SupersedeRefusedError(msg)
        waiting = RunEvaluation.objects.filter(
            run=row,
            retest_of__isnull=True,
            status__in=["pending", "running"],
        ).first()
        if waiting is not None:
            msg = f"run {row.pk}: evaluation {waiting.pk} is still {waiting.status}"
            raise SupersedeRefusedError(msg)
        previous = current_evaluation(row)
        try:
            pending = actions.evaluate(row, suite_ref=suite.pinned_ref, force_rebuild=force_rebuild)
        except actions.ActionRefused as exc:
            msg = f"run {row.pk}: {exc}"
            raise SupersedeRefusedError(msg) from exc
        entry: dict[str, Any] = {
            "at": timezone.now().isoformat(),
            "by": actor or "manage.py reevaluate_under_suite",
            "reason": SUITE_UPGRADE,
            "note": note[:500],
            "superseded_evaluation": previous.pk if previous is not None else None,
            "eval_suite": previous.eval_suite.pinned_ref if previous is not None else None,
            "to_eval_suite": suite.pinned_ref,
            "superseded_outcome": row.outcome,
            "superseded_outcome_reason": row.outcome_reason,
            "superseded_eval": (row.metrics or {}).get("eval"),
            "fault_stage": None,
            "force_rebuild": force_rebuild,
            "relock": None,
            "evaluation": pending.pk,
        }
        row.refresh_from_db(fields=["execution", "validity"])
        history = list((row.execution or {}).get(HISTORY_KEY) or [])
        detail = (
            f"evaluation {entry['superseded_evaluation']} ({entry['eval_suite'] or 'none'}) superseded by "
            f"{pending.pk} under {suite.pinned_ref}"
        )
        GenerationRun.objects.filter(pk=row.pk).update(
            execution=jsonb_merge({HISTORY_KEY: [*history, entry]}),
            validity=validity.add_notes(
                row.validity,
                [validity.note(f"{NOTE}:{SUITE_UPGRADE}", level="info", detail=detail)],
            ),
        )
    run.refresh_from_db()
    return pending
