"""User actions on one run: retry, cancel, re-evaluate (spec sections 6.14 and 9.5).

Every side effect that hands work to the executor happens after commit (appendix A #12): ``retry`` enqueues
in ``transaction.on_commit``; ``cancel`` goes through ``execution.api.cancel_runs`` (row updates in the
transaction, LLM-request cancellation, events and the executor doorbell after commit); ``evaluate`` writes a
pending ``RunEvaluation`` and the ``eval_queued`` state in the transaction and rings the doorbell on commit.

* **Retry** is allowed only when the run's outcome is ``infra_error`` (409 ``not_infra`` otherwise): it
  makes a new attempt on the same row with the same manifest and seed. Model-caused outcomes
  (``tested``, ``build_fail``, ``start_fail``, ``output_invalid``) are data and are never retried, and
  ``excluded`` runs are re-run by a study top-up, not by a retry.
* **Re-evaluation** creates a new ``RunEvaluation`` under the same or another suite; it never changes which
  run is current (section 6.14). It is refused (409 ``not_evaluable``) for outcomes the generation stage
  decided (``output_invalid``, ``excluded``): a skeleton-only workspace is never tested (section 6.8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.utils import timezone

from backend.common.exceptions import APIError
from backend.generation.agentic.outcome import is_retryable_outcome

if TYPE_CHECKING:
    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

__all__ = ["ActionRefused", "cancel", "evaluate", "retry"]

# Columns a manual retry wipes (the same set as ``execution.transitions`` uses for an infra requeue).
_ATTEMPT_WIPE: dict[str, Any] = {
    "metrics": {},
    "result": {},
    "validity": {},
    "outcome": "",
    "outcome_reason": "",
    "usable": None,
    "error_message": "",
    "tree_hash": "",
    "workspace_blob": "",
    "resolved_lock_blob": "",
    "lock_source": "",
    "registry_snapshot_at": None,
}


# Outcomes the generation stage decides itself (section 6.12): no evaluation may replace them.
_GENERATION_DECIDED: frozenset[str] = frozenset({"output_invalid", "excluded"})


class ActionRefused(APIError):  # noqa: N818 - a refusal, reported as 409/404, not a crash
    """A refused run action: ``{"detail", "code"}`` with status 409 (or 404/400)."""

    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message, status_code=status_code)
        self.code = code


def retry(run: GenerationRun) -> GenerationRun:
    """Requeue an ``infra_error`` run as a new attempt on the same row (same manifest and seed)."""
    from backend.execution.api import enqueue_runs
    from backend.execution.transitions import publish_run
    from backend.execution.transitions import refresh_cell
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunFile
    from backend.generation.models.choices import RunPhase
    from backend.generation.models.choices import RunStatus

    with transaction.atomic():
        row = GenerationRun.objects.select_for_update().get(pk=run.pk)
        if row.status in {RunStatus.PENDING, RunStatus.RUNNING}:
            msg = "the run is still active"
            raise ActionRefused(msg, code="run_active")
        if not is_retryable_outcome(row.outcome):
            msg = f"only infra_error runs can be retried (outcome {row.outcome or 'none'})"
            raise ActionRefused(msg, code="not_infra")
        if not hasattr(row, "manifest"):
            msg = "the run has no frozen manifest"
            raise ActionRefused(msg, code="no_manifest")
        now = timezone.now()
        execution = dict(row.execution or {})
        record = {
            "attempt": row.attempt,
            "reason": "manual_retry",
            "previous_outcome": row.outcome,
            "previous_reason": row.outcome_reason,
            "at": now.isoformat(),
        }
        execution["manual_retries"] = [*(execution.get("manual_retries") or []), record]
        execution.pop("reeval", None)
        execution.pop("reeval_prev_status", None)
        GenerationRun.objects.filter(pk=row.pk).update(
            status=RunStatus.PENDING,
            phase=RunPhase.NONE,
            attempt=row.attempt + 1,
            queued_at=None,
            run_after=None,
            started_at=None,
            completed_at=None,
            duration_seconds=None,
            stage_entered_at=None,
            lease_owner="",
            lease_expires_at=None,
            heartbeat_at=None,
            execution=execution,
            updated_at=now,
            **_ATTEMPT_WIPE,
        )
        RunFile.objects.filter(run_id=row.pk).delete()
        run_id, cell_id = row.pk, row.cell_id

        def after_commit() -> None:
            enqueue_runs([run_id])
            refresh_cell(cell_id)
            fresh = GenerationRun.objects.filter(pk=run_id).first()
            if fresh is not None:
                publish_run(fresh)

        transaction.on_commit(after_commit)
    run.refresh_from_db()
    return run


def cancel(run: GenerationRun) -> GenerationRun:
    """Cancel one run (pending, or in its generation stage); evaluations already queued finish."""
    from backend.execution.api import cancel_runs
    from backend.generation.models import GenerationRun

    cancel_runs(GenerationRun.objects.filter(pk=run.pk), scope="all")
    run.refresh_from_db()
    return run


def _suite(run: GenerationRun, suite_ref: str | None) -> EvalSuite:
    from backend.generation.models import EvalSuite

    ref = suite_ref
    if not ref:
        manifest = getattr(getattr(run, "manifest", None), "manifest", None) or {}
        ref = ((manifest.get("planned_evaluation") or {}).get("eval_suite") or {}).get("ref")
    if not ref:
        msg = "no eval suite given and the run's manifest plans none"
        raise ActionRefused(msg, code="no_eval_suite", status_code=400)
    slug, _, rest = str(ref).partition("@")
    version, _, short_hash = rest.partition("#")
    if not slug or not version.isdigit():
        msg = f"eval suite refs are slug@version, got {ref!r}"
        raise ActionRefused(msg, code="bad_ref", status_code=400)
    suite = EvalSuite.objects.filter(slug=slug, version=int(version)).first()
    if suite is None or (short_hash and not suite.content_hash.startswith(short_hash)):
        msg = f"eval suite {ref} not found"
        raise ActionRefused(msg, code="eval_suite_not_found", status_code=404)
    return suite


def evaluate(run: GenerationRun, *, suite_ref: str | None = None, force_rebuild: bool = False) -> RunEvaluation:
    """Queue a re-evaluation of a collected run in the eval lane; returns the pending ``RunEvaluation``.

    The executor sees the queued state only after commit; the doorbell rings on commit. The eval stage
    (WP-09) fills the pending row named by ``execution.reeval_evaluation_id``.
    """
    from backend.execution.api import request_reeval
    from backend.execution.transitions import jsonb_merge
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation
    from backend.generation.models.choices import EvalMode
    from backend.generation.models.choices import RunKind
    from backend.generation.models.choices import RunStatus

    with transaction.atomic():
        row = GenerationRun.objects.select_for_update().get(pk=run.pk)
        if row.status in {RunStatus.PENDING, RunStatus.RUNNING}:
            msg = "the run is still active"
            raise ActionRefused(msg, code="run_active")
        if not row.tree_hash and row.kind != RunKind.REFERENCE:
            msg = "the run has no collected tree to evaluate"
            raise ActionRefused(msg, code="no_tree")
        if row.outcome in _GENERATION_DECIDED:
            # 6.8: a skeleton-only (or truncated, or edit-violating) workspace is never tested, and a
            # re-evaluation never turns a model-caused generation outcome into another one.
            msg = f"the generation stage decided this run ({row.outcome}:{row.outcome_reason}); it is not evaluable"
            raise ActionRefused(msg, code="not_evaluable")
        suite = _suite(row, suite_ref)
        manifest = getattr(getattr(row, "manifest", None), "manifest", None) or {}
        planned = manifest.get("planned_evaluation") or {}
        evaluation = RunEvaluation.objects.create(
            run=row,
            eval_suite=suite,
            eval_level=planned.get("eval_level") or "security",
            mode=planned.get("mode") or EvalMode.FULL,
        )
        if not request_reeval(row, force_rebuild=force_rebuild):
            msg = "the run became active"
            raise ActionRefused(msg, code="run_active")
        GenerationRun.objects.filter(pk=row.pk).update(
            execution=jsonb_merge({"reeval_evaluation_id": evaluation.pk, "reeval_suite": suite.pinned_ref}),
        )
    run.refresh_from_db()
    return evaluation
