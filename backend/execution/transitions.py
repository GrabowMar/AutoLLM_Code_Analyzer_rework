"""Job state transitions owned by the executor (spec §C.3, §C.7, §C.8).

Every write a worker makes to a job it runs goes through :func:`guarded_save`
(``WHERE status='running' AND lease_owner=:me AND attempt=:a``), so a worker
whose job was requeued or cancelled can never write into the next attempt.
:func:`finish_stage` runs as separate autocommit steps, never one
transaction (Experiment/Batch lock order, ``job_cloning._lock_cell``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import DatabaseError
from django.db import connection
from django.db import transaction
from django.db.models import F
from django.db.models import Func
from django.db.models import JSONField
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from backend.generation.models import GenerationJob

if TYPE_CHECKING:
    from backend.generation.models import Experiment

logger = logging.getLogger(__name__)

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

# Reasons that count against the endpoint's circuit breaker (executor-side failures do not).
BREAKER_REASONS = frozenset({REASON_INFRA, REASON_INFRA_INTERRUPTED})
ADHOC_INFRA_RETRIES = 2
LOST_RETRY_DELAY_S = 5
RETRY_BACKOFF_BASE_S = 30

S = GenerationJob.Status
P = GenerationJob.Phase
ACTIVE_STATUSES = (S.PENDING, S.RUNNING)

# Columns a worker computes and finish_stage persists with the final transition.
RESULT_FIELDS = (
    "stack_slug",
    "experiment_seed",
    "resolved_bundle",
    "prompt_hash",
    "bundle_key",
    "error_message",
    "result_data",
    "metrics",
    "copilot_current_iteration",
)
_LEASE_CLEARED = {"lease_owner": "", "lease_expires_at": None, "heartbeat_at": None}

_JSON = JSONField()


class LeaseLost(Exception):  # noqa: N818 — contract name (§C.3)
    """A guarded write matched 0 rows: the job was cancelled, requeued or reclaimed. Workers stop quietly."""


# ── jsonb helpers ──────────────────────────────────────────────────────


def jsonb_merge(patch: dict[str, Any], field: str = "execution") -> Func:
    """``coalesce(field, '{}') || patch`` (top-level merge, done by the database)."""
    return Func(
        Coalesce(F(field), Value({}, output_field=_JSON)),
        Value(patch, output_field=_JSON),
        template="(%(expressions)s)",
        arg_joiner=" || ",
        output_field=_JSON,
    )


_APPEND_SQL = (
    "UPDATE generation_generationjob SET execution = jsonb_set(coalesce(execution, '{}'::jsonb), "
    "ARRAY[%s], coalesce(execution->%s, '[]'::jsonb) || %s::jsonb) "
    "WHERE id = %s AND attempt = %s"
)


def append_execution(job_id, attempt: int, key: str, item: dict[str, Any]) -> bool:
    """Append ``item`` to ``execution[key]`` for this attempt only (one guarded statement)."""
    import json

    if key not in ("infra_errors", "attempts", "relay_sessions"):
        msg = f"unsupported execution list {key!r}"
        raise ValueError(msg)
    with connection.cursor() as cursor:
        cursor.execute(_APPEND_SQL, [key, key, json.dumps([item], default=str), str(job_id), attempt])
        return cursor.rowcount == 1


# ── events ─────────────────────────────────────────────────────────────


def job_event(job: GenerationJob) -> dict[str, Any]:
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    functional = metrics.get("functional") if isinstance(metrics.get("functional"), dict) else {}
    event = {
        "type": "job",
        "job_id": str(job.id),
        "condition_id": job.condition_id,
        "app_id": job.app_requirement_id,
        "repeat_index": job.repeat_index,
        "status": job.status,
        "phase": job.phase,
        "attempt": job.attempt,
        "endpoint_id": job.endpoint_id,
        "ts": timezone.now().isoformat(),
    }
    if functional.get("pass_rate") is not None:
        event["pass_rate"] = functional["pass_rate"]
    return event


def publish_job(job: GenerationJob) -> None:
    """``generation:<id>`` status event, plus the ``experiment:<id>`` ``job`` event for experiment jobs."""
    from backend.generation.services import result_writer

    result_writer.publish_status(job)


def _wake() -> None:
    from backend.execution.api import wake

    wake()


def _refresh_batch(batch_id) -> None:
    if batch_id is None:
        return
    from backend.generation.models import GenerationBatch
    from backend.generation.services.batches import refresh_batch

    try:
        refresh_batch(GenerationBatch(id=batch_id))
    except DatabaseError:
        logger.exception("refresh_batch(%s) failed", batch_id)


def _clear_responses(job_id, attempt: int) -> None:
    """Responses are kept after delivery (dedupe waiters); the attempt is over, so drop them (§B.1)."""
    from backend.inference.models import LLMRequest

    LLMRequest.objects.filter(job_id=job_id, job_attempt=attempt).exclude(response=None).update(response=None)


def _cancel_requests(job_ids: list, *, attempt: int | None = None) -> None:
    """Cancel queued/leased LLMRequests of these jobs and signal their waiters."""
    if not job_ids:
        return
    try:
        from backend.inference.requests import cancel_for_jobs

        cancel_for_jobs(job_ids, attempt=attempt)
    except NotImplementedError:
        from backend.inference.models import LLMRequest

        qs = LLMRequest.objects.filter(
            job_id__in=job_ids,
            status__in=[LLMRequest.Status.QUEUED, LLMRequest.Status.LEASED],
        )
        if attempt is not None:
            qs = qs.filter(job_attempt=attempt)
        qs.update(status=LLMRequest.Status.CANCELLED, completed_at=timezone.now())
    except Exception:
        logger.exception("Cancelling LLM requests of %d job(s) failed", len(job_ids))


def _record_breaker(endpoint_id, reason: str) -> None:
    if reason in BREAKER_REASONS:
        from backend.execution.breaker import breaker

        breaker.record_failure(endpoint_id)


# ── guarded writes ─────────────────────────────────────────────────────


def _guard(job: GenerationJob):
    return GenerationJob.objects.filter(
        id=job.id,
        status=S.RUNNING,
        lease_owner=job.lease_owner or "",
        attempt=job.attempt,
    )


def guarded_save(job: GenerationJob, fields: Iterable[str]) -> None:
    """Persist ``fields`` of ``job`` only while this executor still owns the current attempt.

    ``UPDATE … WHERE id=:id AND status='running' AND lease_owner=job.lease_owner AND attempt=job.attempt``;
    raises :class:`LeaseLost` on a 0-row result.
    """
    now = timezone.now()
    values: dict[str, Any] = {}
    for name in dict.fromkeys(fields):
        if name == "updated_at":
            continue
        field = GenerationJob._meta.get_field(name)  # noqa: SLF001
        values[field.attname] = getattr(job, field.attname)
    values["updated_at"] = now
    if not _guard(job).update(**values):
        msg = f"job {job.id} attempt {job.attempt} is no longer owned by {job.lease_owner or 'this worker'}"
        raise LeaseLost(msg)
    job.updated_at = now


def _release(job: GenerationJob) -> bool:
    """A cancel (or the reconciler) won: give up the lease this worker still holds, so capacity frees up."""
    released = (
        GenerationJob.objects.filter(id=job.id, lease_owner=job.lease_owner or "", attempt=job.attempt)
        .exclude(status=S.RUNNING)
        .exclude(lease_owner="")
        .update(phase=P.NONE, **_LEASE_CLEARED)
    )
    if released:
        _wake()
    return bool(released)


def finish_stage(job: GenerationJob, outcome: str) -> bool:
    """End the current stage of ``job`` (spec §C.3), as separate autocommit steps:

    1. conditional job UPDATE (``id``, ``status=running``, ``lease_owner=me``, ``attempt=a``; clears ``lease_owner``) —
       0 rows means a cancel or the reconciler won, and nothing else happens (beyond releasing a lease this
       worker still holds on a cancelled row);
    2. ``LLMRequest.objects.filter(job=job, job_attempt=a).update(response=None)``;
    3. ``refresh_batch`` (Batch lock only);
    4. ``publish_status`` and the experiment ``job`` event;
    5. ``maybe_complete_experiment`` (Experiment lock only);
    6. ``execution.api.wake()``.

    ``outcome`` is one of the ``OUTCOME_*`` constants. Returns True when this call made the transition.
    """
    if outcome == OUTCOME_INFRA_INTERRUPTED:
        return infra_retry(job, REASON_INFRA_INTERRUPTED, error=job.error_message or "")

    now = timezone.now()
    execution = job.execution if isinstance(job.execution, dict) else {}
    values: dict[str, Any] = {name: getattr(job, name) for name in RESULT_FIELDS}
    values.update(_LEASE_CLEARED)
    values["stage_entered_at"] = now
    terminal = outcome != OUTCOME_EVALUATE
    if not terminal:
        status, phase = S.RUNNING, P.EVAL_QUEUED
    else:
        phase = P.NONE
        if outcome == OUTCOME_CANCELLED:
            status = S.CANCELLED
        elif outcome == OUTCOME_FAILED:
            status = S.FAILED
        else:
            status = job.status if job.status in (S.COMPLETED, S.FAILED, S.CANCELLED) else S.COMPLETED
        if execution.get("reeval") and outcome != OUTCOME_CANCELLED:
            # A re-evaluation always ends in the status the job had before it (never FAILED by itself).
            status = execution.get("reeval_prev_status") or S.COMPLETED
            values["execution"] = Func(
                Coalesce(F("execution"), Value({}, output_field=_JSON)),
                Value("reeval"),
                Value("reeval_prev_status"),
                template="(%(expressions)s)",
                arg_joiner=" - ",
                output_field=_JSON,
            )
            values["completed_at"] = job.completed_at or now
        else:
            values["completed_at"] = now
            if job.started_at:
                values["duration_seconds"] = (now - job.started_at).total_seconds()
    values["status"] = status
    values["phase"] = phase
    values["updated_at"] = now

    if not _guard(job).update(**values):
        _release(job)
        return False
    for name, value in values.items():
        if name != "execution":
            setattr(job, name, value)
    if "execution" in values:
        execution = {k: v for k, v in execution.items() if k not in ("reeval", "reeval_prev_status")}
        job.execution = execution

    try:
        _clear_responses(job.id, job.attempt)
    except DatabaseError:
        logger.exception("Clearing LLM responses of job %s failed", job.id)
    _refresh_batch(job.batch_id)
    publish_job(job)
    if terminal and job.experiment_id:
        maybe_complete_experiment(job.experiment_id)
    _wake()
    return True


# ── infra retries ──────────────────────────────────────────────────────


def _infra_retries(experiment_id) -> int:
    if experiment_id is None:
        return ADHOC_INFRA_RETRIES
    from backend.generation.models import Experiment

    value = Experiment.objects.filter(pk=experiment_id).values_list("infra_retries", flat=True).first()
    return ADHOC_INFRA_RETRIES if value is None else int(value)


def _interrupted_validity(result_data: dict[str, Any], detail: str) -> dict[str, Any]:
    result = dict(result_data or {})
    agent = dict(result.get("agent") or {})
    validity = dict(agent.get("validity") or {"valid": True, "notes": []})
    notes = [n for n in validity.get("notes") or [] if n.get("code") != "infra_interrupted"]
    notes.append({"code": "infra_interrupted", "severity": "exclude", "detail": detail})
    agent["validity"] = {"valid": False, "notes": notes}
    result["agent"] = agent
    result["infra_interrupted"] = True
    return result


def infra_retry(job: GenerationJob, reason: str, error: str = "") -> bool:
    """Requeue the same row as attempt+1 (same snapshot and seed) or fail it for good (spec §C.7).

    Returns True when requeued. ``executor_lost`` uses ``execution.executor_lost_count``
    against ``EXECUTOR_LOST_RETRIES``; other reasons use ``job.attempt < experiment.infra_retries``
    (2 for ad-hoc jobs; attempts consumed by ``executor_lost`` do not count). Exhausted
    ``infra_interrupted`` ends COMPLETED with validity ``invalid: infra_interrupted``; other
    exhausted reasons end FAILED.
    """
    from backend.generation.models import GenerationArtifact

    now = timezone.now()
    attempt = job.attempt
    with transaction.atomic():
        row = (
            GenerationJob.objects.select_for_update()
            .filter(id=job.id, attempt=attempt, status=S.RUNNING)
            .values("execution", "started_at", "experiment_id", "endpoint_id", "batch_id")
            .first()
        )
        if row is None:
            requeued = None
        else:
            execution = dict(row["execution"] or {})
            lost = int(execution.get("executor_lost_count") or 0)
            if reason == REASON_EXECUTOR_LOST:
                allowed = lost < settings.EXECUTOR_LOST_RETRIES
            else:
                allowed = attempt - lost < _infra_retries(row["experiment_id"])
            record = {"attempt": attempt, "reason": reason, "error": (error or "")[:1000], "at": now.isoformat()}
            if execution.get("infra_errors"):
                record["infra_errors"] = execution.pop("infra_errors")[-20:]
            execution["attempts"] = [*(execution.get("attempts") or []), record]
            if reason == REASON_EXECUTOR_LOST:
                execution["executor_lost_count"] = lost + 1
            if allowed:
                if reason == REASON_EXECUTOR_LOST:
                    delay = LOST_RETRY_DELAY_S
                else:
                    delay = RETRY_BACKOFF_BASE_S * 2 ** max(0, attempt - lost)
                GenerationJob.objects.filter(id=job.id).update(
                    status=S.PENDING,
                    phase=P.NONE,
                    attempt=attempt + 1,
                    run_after=now + timedelta(seconds=delay),
                    started_at=None,
                    completed_at=None,
                    duration_seconds=None,
                    stage_entered_at=None,
                    metrics={},
                    error_message="",
                    result_data={},
                    execution=execution,
                    updated_at=now,
                    **_LEASE_CLEARED,
                )
                if row["started_at"] is not None:
                    GenerationArtifact.objects.filter(job_id=job.id, created_at__gte=row["started_at"]).delete()
                job.status, job.phase, job.attempt = S.PENDING, P.NONE, attempt + 1
            else:
                values: dict[str, Any] = {
                    "phase": P.NONE,
                    "completed_at": now,
                    "execution": execution,
                    "updated_at": now,
                    **_LEASE_CLEARED,
                }
                n = attempt + 1
                if reason == REASON_INFRA_INTERRUPTED:
                    detail = f"the run was interrupted by an infrastructure error {n} times: {error}"[:500]
                    values.update(
                        status=S.COMPLETED,
                        result_data=_interrupted_validity(job.result_data, detail),
                        metrics=job.metrics if isinstance(job.metrics, dict) else {},
                    )
                else:
                    message = f"Infrastructure failure after {n} attempts: {reason}"
                    if error:
                        message = f"{message}: {error}"
                    values.update(status=S.FAILED, error_message=message[:2000])
                if row["started_at"] is not None:
                    values["duration_seconds"] = (now - row["started_at"]).total_seconds()
                GenerationJob.objects.filter(id=job.id).update(**values)
                job.status, job.phase = values["status"], P.NONE
            job.execution = execution
            requeued = allowed
    if requeued is None:
        _release(job)
        return False

    job.lease_owner = ""
    _cancel_requests([job.id], attempt=attempt)
    _record_breaker(row["endpoint_id"], reason)
    _refresh_batch(row["batch_id"])
    publish_job(job)
    if not requeued and row["experiment_id"]:
        maybe_complete_experiment(row["experiment_id"])
    _wake()
    logger.info(
        "Job %s attempt %s: %s (%s)%s",
        job.id,
        attempt,
        "requeued" if requeued else "gave up",
        reason,
        f": {error}" if error else "",
    )
    return bool(requeued)


# ── cancel ─────────────────────────────────────────────────────────────


def _lock_rows(ids: list) -> list[dict[str, Any]]:
    return list(
        GenerationJob.objects.select_for_update(skip_locked=True)
        .filter(id__in=ids, status__in=ACTIVE_STATUSES)
        .order_by("id")
        .values("id", "status", "phase", "queued_at", "experiment_id", "endpoint_id", "batch_id"),
    )


def cancel_jobs(qs, scope: str = "all", *, experiment: Experiment | None = None) -> dict[str, int]:
    """Cancel the jobs in ``qs`` (spec §C.8).

    PENDING → CANCELLED; RUNNING ``phase=running`` → CANCELLED with
    ``lease_owner`` kept until the worker's ``finally``; ``eval_queued`` /
    ``evaluating`` are left to finish (their generation is done). Legacy
    thread-era rows (``queued_at IS NULL``) have no live worker and are
    cancelled in every phase. Their queued/leased LLMRequests are cancelled.
    ``scope`` is ``"all"`` or ``"pending"``. With ``experiment`` and scope
    ``all`` the experiment itself becomes ``cancelled`` at once. Returns
    ``{"cancelled_pending", "signalled_running", "finishing_evaluations"}``.
    """
    if scope not in ("all", "pending"):
        msg = f"unknown cancel scope {scope!r}"
        raise ValueError(msg)
    statuses = [S.PENDING] if scope == "pending" else list(ACTIVE_STATUSES)
    ids = list(qs.filter(status__in=statuses).order_by().values_list("id", flat=True))
    counts = {"cancelled_pending": 0, "signalled_running": 0, "finishing_evaluations": 0}
    cancelled: list[dict[str, Any]] = []
    remaining = list(ids)
    for attempt_no in range(5):
        if not remaining:
            break
        try:
            with transaction.atomic():
                rows = _lock_rows(remaining)
                now = timezone.now()
                pending, running = [], []
                for row in rows:
                    if row["status"] == S.PENDING:
                        pending.append(row)
                    elif scope == "all" and (
                        row["queued_at"] is None or row["phase"] in (P.RUNNING, P.NONE, P.QUEUED)
                    ):
                        running.append(row)
                    elif scope == "all":
                        counts["finishing_evaluations"] += 1
                GenerationJob.objects.filter(id__in=[r["id"] for r in pending]).update(
                    status=S.CANCELLED,
                    phase=P.NONE,
                    completed_at=now,
                    updated_at=now,
                )
                # lease_owner stays: capacity stays counted until the worker's finally releases it.
                GenerationJob.objects.filter(id__in=[r["id"] for r in running]).update(
                    status=S.CANCELLED,
                    phase=P.NONE,
                    completed_at=now,
                    updated_at=now,
                )
                counts["cancelled_pending"] += len(pending)
                counts["signalled_running"] += len(running)
                cancelled.extend(pending + running)
                locked = {r["id"] for r in rows}
        except DatabaseError as exc:
            if "deadlock" not in str(exc).lower():
                raise
            logger.warning("cancel_jobs: deadlock, retrying")
            locked = set()
        still_active = set(
            GenerationJob.objects.filter(id__in=remaining, status__in=statuses).values_list("id", flat=True),
        )
        remaining = [i for i in remaining if i not in locked and i in still_active]
        if remaining and attempt_no < 4:
            time.sleep(0.05 * (attempt_no + 1))
    if remaining:
        logger.warning("cancel_jobs: %d row(s) stayed locked; the reconciler finishes them", len(remaining))

    if experiment is not None and scope == "all":
        from backend.generation.models import Experiment

        now = timezone.now()
        Experiment.objects.filter(pk=experiment.pk).exclude(
            status__in=[Experiment.Status.COMPLETED, Experiment.Status.ARCHIVED, Experiment.Status.CANCELLED],
        ).update(status=Experiment.Status.CANCELLED, cancelled_at=now, updated_at=now)

    for batch_id in {r["batch_id"] for r in cancelled if r["batch_id"]}:
        _refresh_batch(batch_id)

    job_ids = [r["id"] for r in cancelled]
    experiment_ids = {r["experiment_id"] for r in cancelled if r["experiment_id"]}

    def after_commit() -> None:
        _cancel_requests(job_ids)
        jobs = GenerationJob.objects.filter(id__in=job_ids).only(
            "id",
            "status",
            "phase",
            "attempt",
            "started_at",
            "completed_at",
            "experiment_id",
            "condition_id",
            "app_requirement_id",
            "repeat_index",
            "endpoint_id",
            "metrics",
        )
        for job in jobs:
            publish_job(job)
        if experiment is not None and scope == "all":
            from backend.realtime.events import publish_experiment_event

            publish_experiment_event(experiment.pk, {"type": "state", "status": "cancelled", "pause_reason": ""})
        for experiment_id in experiment_ids:
            maybe_complete_experiment(experiment_id)
        _wake()

    transaction.on_commit(after_commit)
    return counts


# ── experiment completion ──────────────────────────────────────────────

_COMPLETE_SQL = """
UPDATE generation_experiment SET status = 'completed', completed_at = now(), updated_at = now()
WHERE id = %s AND status = 'running'
  AND NOT EXISTS (
    SELECT 1 FROM generation_generationjob j WHERE j.experiment_id = %s AND j.status IN ('pending', 'running')
  )
"""


def maybe_complete_experiment(experiment_id: Any) -> bool:
    """Mark a RUNNING experiment COMPLETED when it has no active jobs (idempotent, spec §F.5).

    ``UPDATE … SET status='completed', completed_at=now()
    WHERE id=:id AND status='running' AND NOT EXISTS (active jobs)``; only on
    rowcount 1 calls ``experiment_automation.on_experiment_completed``.
    Returns True when it completed.
    """
    if experiment_id is None:
        return False
    with connection.cursor() as cursor:
        cursor.execute(_COMPLETE_SQL, [str(experiment_id), str(experiment_id)])
        completed = cursor.rowcount == 1
    if not completed:
        return False
    from backend.generation.models import Experiment
    from backend.realtime.events import publish_experiment_event

    publish_experiment_event(experiment_id, {"type": "state", "status": "completed", "pause_reason": ""})
    experiment = Experiment.objects.filter(pk=experiment_id).first()
    if experiment is not None:
        try:
            from backend.generation.services.experiment_automation import on_experiment_completed

            on_experiment_completed(experiment)
        except NotImplementedError:
            logger.warning("on_experiment_completed is not implemented yet; no post-run for %s", experiment_id)
        except Exception:
            logger.exception("on_experiment_completed(%s) failed", experiment_id)
    logger.info("Experiment %s completed", experiment_id)
    return True
