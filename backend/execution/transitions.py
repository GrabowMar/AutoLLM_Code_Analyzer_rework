"""Job state transitions owned by the executor (spec §C.3, §C.7, §C.8). Contract stub → Track A.

Every write a worker makes to a job it runs goes through :func:`guarded_save`
(``WHERE status='running' AND lease_owner=:me AND attempt=:a``), so a worker
whose job was requeued or cancelled can never write into the next attempt.
:func:`finish_stage` runs as separate autocommit steps, never one
transaction (Experiment/Batch lock order, ``job_cloning._lock_cell``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

# finish_stage outcomes
OUTCOME_DONE = "done"  # terminal: COMPLETED (or FAILED with job.error_message)
OUTCOME_FAILED = "failed"
OUTCOME_EVALUATE = "evaluate"  # → RUNNING/eval_queued, lease cleared (sandbox slot released)
OUTCOME_INFRA_INTERRUPTED = "infra_interrupted"  # → infra_retry(job, "infra_interrupted")
OUTCOME_CANCELLED = "cancelled"

# infra_retry reasons
REASON_EXECUTOR_LOST = "executor_lost"  # own counter (EXECUTOR_LOST_RETRIES); does not use infra_retries
REASON_SUBMIT_FAILED = "submit_failed"
REASON_INFRA = "infra"
REASON_INFRA_INTERRUPTED = "infra_interrupted"  # local endpoints only; exhausted → COMPLETED + invalid


class LeaseLost(Exception):  # noqa: N818 — contract name (§C.3)
    """A guarded write matched 0 rows: the job was cancelled, requeued or reclaimed. Workers stop quietly."""


def guarded_save(job: GenerationJob, fields: Iterable[str]) -> None:
    """Persist ``fields`` of ``job`` only while this executor still owns the current attempt.

    ``UPDATE … WHERE id=:id AND status='running' AND lease_owner=job.lease_owner AND attempt=job.attempt``;
    raises :class:`LeaseLost` on a 0-row result.
    """
    msg = "execution.transitions.guarded_save: Track A"
    raise NotImplementedError(msg)


def finish_stage(job: GenerationJob, outcome: str) -> None:
    """End the current stage of ``job`` (spec §C.3), as separate autocommit steps:

    1. conditional job UPDATE (``id``, ``status=running``, ``lease_owner=me``, ``attempt=a``; clears ``lease_owner``) —
       0 rows means a cancel or the reconciler won, and nothing else happens;
    2. ``LLMRequest.objects.filter(job=job, job_attempt=a).update(response=None)``;
    3. ``refresh_batch`` (Batch lock only);
    4. ``publish_status`` and the experiment ``job`` event;
    5. ``maybe_complete_experiment`` (Experiment lock only);
    6. ``execution.api.wake()``.

    ``outcome`` is one of the ``OUTCOME_*`` constants.
    """
    msg = "execution.transitions.finish_stage: Track A"
    raise NotImplementedError(msg)


def infra_retry(job: GenerationJob, reason: str, error: str = "") -> bool:
    """Requeue the same row as attempt+1 (same snapshot and seed) or fail it for good (spec §C.7).

    Returns True when requeued. ``executor_lost`` uses ``execution.executor_lost_count``
    against ``EXECUTOR_LOST_RETRIES``; other reasons use ``job.attempt < experiment.infra_retries``
    (2 for ad-hoc jobs). Exhausted ``infra_interrupted`` ends COMPLETED with validity
    ``invalid: infra_interrupted``; other exhausted reasons end FAILED.
    """
    msg = "execution.transitions.infra_retry: Track A"
    raise NotImplementedError(msg)


def cancel_jobs(qs, scope: str = "all") -> dict[str, int]:
    """Cancel the jobs in ``qs`` (spec §C.8).

    PENDING → CANCELLED; RUNNING ``phase=running`` → CANCELLED with
    ``lease_owner`` kept until the worker's ``finally``; ``eval_queued`` /
    ``evaluating`` are left to finish. Their queued/leased LLMRequests are
    cancelled. ``scope`` is ``"all"`` or ``"pending"``. Returns
    ``{"cancelled_pending", "signalled_running", "finishing_evaluations"}``.
    """
    msg = "execution.transitions.cancel_jobs: Track A"
    raise NotImplementedError(msg)


def maybe_complete_experiment(experiment_id: Any) -> bool:
    """Mark a RUNNING experiment COMPLETED when it has no active jobs (idempotent, spec §F.5).

    ``UPDATE … SET status='completed', completed_at=now()
    WHERE id=:id AND status='running' AND NOT EXISTS (active jobs)``; only on
    rowcount 1 calls ``experiment_automation.on_experiment_completed``.
    Returns True when it completed.
    """
    msg = "execution.transitions.maybe_complete_experiment: Track A"
    raise NotImplementedError(msg)
