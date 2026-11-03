"""Run state transitions owned by the executor (spec §C.3, §C.7, §C.8; v3 sections 3.7, 6.12-6.14).

Every write a worker makes to a run it executes goes through :func:`guarded_save`
(``WHERE status='running' AND lease_owner=:me AND attempt=:a``), so a worker
whose run was requeued or cancelled can never write into the next attempt.
:func:`finish_stage` runs as separate autocommit steps, never one transaction
(the run row first, then the cell's current run, then the study).

Outcomes follow section 6.12: an exhausted infra retry ends ``infra_error`` (not
usable), a cancelled generation ends ``excluded:cancelled``; neither is ever
recorded as a clean result. ``usable`` is derived from ``outcome`` on every
write (queryset updates bypass ``GenerationRun.derive_fields``).
"""

from __future__ import annotations

import json
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

from backend.generation.contracts.outcome import USABLE_OUTCOMES
from backend.generation.models import GenerationRun
from backend.generation.models.choices import ExclusionReason
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus

if TYPE_CHECKING:
    from backend.generation.models import Study

logger = logging.getLogger(__name__)

# finish_stage outcomes
OUTCOME_DONE = "done"  # terminal: COMPLETED (or FAILED with run.error_message)
OUTCOME_FAILED = "failed"
OUTCOME_EVALUATE = "evaluate"  # → RUNNING/eval_queued, lease cleared (sandbox slot released)
OUTCOME_INFRA_INTERRUPTED = "infra_interrupted"  # → infra_retry(run, "infra_interrupted")
OUTCOME_CANCELLED = "cancelled"

# infra_retry reasons
REASON_EXECUTOR_LOST = "executor_lost"  # own counter (EXECUTOR_LOST_RETRIES); does not use infra_retries
REASON_SUBMIT_FAILED = "submit_failed"
REASON_INFRA = "infra"
REASON_INFRA_INTERRUPTED = "infra_interrupted"

# Reasons that count against the endpoint's circuit breaker (executor-side failures do not).
BREAKER_REASONS = frozenset({REASON_INFRA, REASON_INFRA_INTERRUPTED})
# retries.infra_retries when the run has no resource policy (the "standard" tier's value).
DEFAULT_INFRA_RETRIES = 2
LOST_RETRY_DELAY_S = 5
RETRY_BACKOFF_BASE_S = 30

S = RunStatus
P = RunPhase
ACTIVE_STATUSES = (S.PENDING, S.RUNNING)

# Columns a worker computes and finish_stage persists with the final transition.
RESULT_FIELDS = (
    "outcome",
    "outcome_reason",
    "usable",
    "validity",
    "result",
    "metrics",
    "error_message",
    "tree_hash",
    "workspace_blob",
    "resolved_lock_blob",
    "lock_source",
    "registry_snapshot_at",
)
# What a requeue wipes: the attempt's results, so the next attempt starts clean (same manifest and seed).
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
_LEASE_CLEARED = {"lease_owner": "", "lease_expires_at": None, "heartbeat_at": None}

_JSON = JSONField()


class LeaseLost(Exception):  # noqa: N818 — contract name (§C.3)
    """A guarded write matched 0 rows: the run was cancelled, requeued or reclaimed. Workers stop quietly."""


def derive_usable(outcome: str) -> bool | None:
    """Section 6.13: ``usable = outcome ∈ USABLE_OUTCOMES``; no outcome yet means unknown (``None``)."""
    return (outcome in USABLE_OUTCOMES) if outcome else None


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
    "UPDATE generation_run SET execution = jsonb_set(coalesce(execution, '{}'::jsonb), "
    "ARRAY[%s], coalesce(execution->%s, '[]'::jsonb) || %s::jsonb) "
    "WHERE id = %s AND attempt = %s"
)


def append_execution(run_id, attempt: int, key: str, item: dict[str, Any]) -> bool:
    """Append ``item`` to ``execution[key]`` for this attempt only (one guarded statement)."""
    if key not in ("infra_errors", "attempts", "relay_sessions"):
        msg = f"unsupported execution list {key!r}"
        raise ValueError(msg)
    with connection.cursor() as cursor:
        cursor.execute(_APPEND_SQL, [key, key, json.dumps([item], default=str), str(run_id), attempt])
        return cursor.rowcount == 1


# ── events ─────────────────────────────────────────────────────────────


def _pass_rate(run: GenerationRun) -> float | None:
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    summary = metrics.get("eval") if isinstance(metrics.get("eval"), dict) else {}
    value = summary.get("agent_pass_rate")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def run_events(run: GenerationRun) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The ``run:<id>`` status event and, for a study run, the ``study:<id>`` ``run`` event."""
    ts = timezone.now().isoformat()
    status_event: dict[str, Any] = {
        "type": "status",
        "run_id": str(run.id),
        "status": run.status,
        "phase": run.phase or "",
        "outcome": run.outcome or "",
        "usable": run.usable,
        "attempt": run.attempt,
        "ts": ts,
    }
    if not run.study_id:
        return status_event, None
    study_event: dict[str, Any] = {
        "type": "run",
        "run_id": str(run.id),
        "cell_id": run.cell_id,
        "condition_id": run.condition_id,
        "repeat_index": run.repeat_index,
        "status": run.status,
        "phase": run.phase or "",
        "outcome": run.outcome or "",
        "attempt": run.attempt,
        "endpoint_id": run.endpoint_id,
        "ts": ts,
    }
    pass_rate = _pass_rate(run)
    if pass_rate is not None:
        study_event["agent_pass_rate"] = pass_rate
    return status_event, study_event


def publish_run(run: GenerationRun) -> None:
    """``run:<id>`` status event, plus the ``study:<id>`` ``run`` event for study runs (never raises)."""
    from backend.realtime.events import publish_run_event
    from backend.realtime.events import publish_study_event

    status_event, study_event = run_events(run)
    publish_run_event(run.id, status_event)
    if study_event is not None:
        publish_study_event(run.study_id, study_event)


def _wake() -> None:
    from backend.execution.api import wake

    wake()


def refresh_cell(cell_id) -> None:
    """Recompute the cell's ``current_run`` after an outcome change (``runs.queries.refresh_current``, WP-10)."""
    if cell_id is None:
        return
    from backend.generation.models import StudyCell
    from backend.generation.runs.queries import refresh_current

    try:
        cell = StudyCell.objects.filter(pk=cell_id).first()
        if cell is not None:
            refresh_current(cell)
    except NotImplementedError:
        logger.debug("refresh_current is not implemented yet; cell %s keeps its current run", cell_id)
    except DatabaseError:
        logger.exception("refresh_current(%s) failed", cell_id)


def _clear_responses(run_id, attempt: int) -> None:
    """Responses are kept after delivery (dedupe waiters); the attempt is over, so drop them (§B.1)."""
    from backend.inference.models import LLMRequest

    LLMRequest.objects.filter(job_id=run_id, job_attempt=attempt).exclude(response=None).update(response=None)


def _cancel_requests(run_ids: list, *, attempt: int | None = None) -> None:
    """Cancel queued/leased LLMRequests of these runs and signal their waiters."""
    if not run_ids:
        return
    try:
        from backend.inference.requests import cancel_for_jobs

        cancel_for_jobs(run_ids, attempt=attempt)
    except NotImplementedError:
        from backend.inference.models import LLMRequest

        qs = LLMRequest.objects.filter(
            job_id__in=run_ids,
            status__in=[LLMRequest.Status.QUEUED, LLMRequest.Status.LEASED],
        )
        if attempt is not None:
            qs = qs.filter(job_attempt=attempt)
        qs.update(status=LLMRequest.Status.CANCELLED, completed_at=timezone.now())
    except Exception:
        logger.exception("Cancelling LLM requests of %d run(s) failed", len(run_ids))


def _record_breaker(endpoint_id, reason: str) -> None:
    if reason in BREAKER_REASONS:
        from backend.execution.breaker import breaker

        breaker.record_failure(endpoint_id)


# ── guarded writes ─────────────────────────────────────────────────────


def _guard(run: GenerationRun):
    return GenerationRun.objects.filter(
        id=run.id,
        status=S.RUNNING,
        lease_owner=run.lease_owner or "",
        attempt=run.attempt,
    )


def guarded_save(run: GenerationRun, fields: Iterable[str]) -> None:
    """Persist ``fields`` of ``run`` only while this executor still owns the current attempt.

    ``UPDATE … WHERE id=:id AND status='running' AND lease_owner=run.lease_owner AND attempt=run.attempt``;
    raises :class:`LeaseLost` on a 0-row result. Saving ``outcome`` also saves the derived ``usable``.
    """
    now = timezone.now()
    names = list(dict.fromkeys(fields))
    if "outcome" in names:
        run.usable = derive_usable(run.outcome)
        names.append("usable")
    values: dict[str, Any] = {}
    for name in dict.fromkeys(names):
        if name == "updated_at":
            continue
        field = GenerationRun._meta.get_field(name)  # noqa: SLF001
        values[field.attname] = getattr(run, field.attname)
    values["updated_at"] = now
    if not _guard(run).update(**values):
        msg = f"run {run.id} attempt {run.attempt} is no longer owned by {run.lease_owner or 'this worker'}"
        raise LeaseLost(msg)
    run.updated_at = now


def _release(run: GenerationRun) -> bool:
    """A cancel (or the reconciler) won: give up the lease this worker still holds, so capacity frees up."""
    released = (
        GenerationRun.objects.filter(id=run.id, lease_owner=run.lease_owner or "", attempt=run.attempt)
        .exclude(status=S.RUNNING)
        .exclude(lease_owner="")
        .update(phase=P.NONE, **_LEASE_CLEARED)
    )
    if released:
        _wake()
    return bool(released)


def finish_stage(run: GenerationRun, outcome: str) -> bool:
    """End the current stage of ``run`` (spec §C.3), as separate autocommit steps:

    1. conditional run UPDATE (``id``, ``status=running``, ``lease_owner=me``, ``attempt=a``; clears
       ``lease_owner``) with the worker's ``RESULT_FIELDS`` — 0 rows means a cancel or the reconciler won,
       and nothing else happens (beyond releasing a lease this worker still holds on a cancelled row);
    2. ``LLMRequest.objects.filter(job=run, job_attempt=a).update(response=None)``;
    3. ``runs.queries.refresh_current(cell)`` on a terminal transition;
    4. the ``run:<id>`` / ``study:<id>`` events;
    5. ``maybe_complete_study`` (terminal only);
    6. ``execution.api.wake()``.

    ``outcome`` is one of the ``OUTCOME_*`` constants. Returns True when this call made the transition.
    """
    if outcome == OUTCOME_INFRA_INTERRUPTED:
        return infra_retry(run, REASON_INFRA_INTERRUPTED, error=run.error_message or "")

    now = timezone.now()
    execution = run.execution if isinstance(run.execution, dict) else {}
    if outcome == OUTCOME_CANCELLED and not run.outcome:
        run.outcome, run.outcome_reason = Outcome.EXCLUDED, ExclusionReason.CANCELLED
    run.usable = derive_usable(run.outcome)
    values: dict[str, Any] = {name: getattr(run, name) for name in RESULT_FIELDS}
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
            status = run.status if run.status in (S.COMPLETED, S.FAILED, S.CANCELLED) else S.COMPLETED
        if execution.get("reeval") and outcome != OUTCOME_CANCELLED:
            # A re-evaluation always ends in the status the run had before it (never FAILED by itself).
            status = execution.get("reeval_prev_status") or S.COMPLETED
            values["execution"] = Func(
                Coalesce(F("execution"), Value({}, output_field=_JSON)),
                Value("reeval"),
                Value("reeval_prev_status"),
                template="(%(expressions)s)",
                arg_joiner=" - ",
                output_field=_JSON,
            )
            values["completed_at"] = run.completed_at or now
        else:
            values["completed_at"] = now
            if run.started_at:
                values["duration_seconds"] = (now - run.started_at).total_seconds()
    values["status"] = status
    values["phase"] = phase
    values["updated_at"] = now

    if not _guard(run).update(**values):
        _release(run)
        return False
    for name, value in values.items():
        if name != "execution":
            setattr(run, name, value)
    if "execution" in values:
        run.execution = {k: v for k, v in execution.items() if k not in ("reeval", "reeval_prev_status")}

    try:
        _clear_responses(run.id, run.attempt)
    except DatabaseError:
        logger.exception("Clearing LLM responses of run %s failed", run.id)
    if terminal:
        refresh_cell(run.cell_id)
    publish_run(run)
    if terminal and run.study_id:
        maybe_complete_study(run.study_id)
    _wake()
    return True


# ── infra retries ──────────────────────────────────────────────────────


def infra_retries_for(run_id) -> int:
    """``retries.infra_retries`` of the run's resource policy (``DEFAULT_INFRA_RETRIES`` without one)."""
    content = GenerationRun.objects.filter(pk=run_id).values_list("resource_policy__content", flat=True).first()
    retries = (content or {}).get("retries") if isinstance(content, dict) else None
    value = retries.get("infra_retries") if isinstance(retries, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return DEFAULT_INFRA_RETRIES


_SPEND_KEYS = ("cost_usd", "list_cost_usd", "prompt_tokens", "completion_tokens", "total_tokens")


def _attempt_spend(metrics: Any) -> dict[str, float | int]:
    """An attempt's LLM spend from its ``metrics`` (empty when it recorded none)."""
    if not isinstance(metrics, dict):
        return {}
    spend: dict[str, float | int] = {}
    for key in _SPEND_KEYS:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            spend[key] = value
    return spend


def _accumulate_prior_spend(execution: dict[str, Any], spend: dict[str, float | int]) -> None:
    """Add a wiped attempt's spend to ``execution.prior_attempts_{cost_usd,total_tokens,...}``."""
    for key, value in spend.items():
        name = f"prior_attempts_{key}"
        total = (execution.get(name) or 0) + value
        execution[name] = round(total, 8) if key.endswith("_usd") else int(total)


def infra_retry(run: GenerationRun, reason: str, error: str = "") -> bool:
    """Requeue the same row as attempt+1 (same manifest and seed) or end it ``infra_error`` (spec §C.7, 6.12).

    Returns True when requeued. ``executor_lost`` uses ``execution.executor_lost_count`` against
    ``EXECUTOR_LOST_RETRIES``; other reasons use ``run.attempt < retries.infra_retries`` of the run's
    resource policy (attempts consumed by ``executor_lost`` do not count). An exhausted run ends FAILED
    with ``outcome=infra_error`` (not usable, never a clean result).

    Each attempt's LLM spend (``metrics.cost_usd`` / tokens) is kept in its ``execution.attempts[]``
    record; on a requeue (which wipes the attempt's results and collected files) it is also summed into
    ``execution.prior_attempts_*``. ``execution.requeued_at`` is the new attempt's enqueue time (the
    scheduler's ``queue_seconds`` counts from it). The settled spend itself is the ledger's (WP-11).
    """
    from backend.generation.models import GenerationArtifact
    from backend.generation.models import RunFile

    now = timezone.now()
    attempt = run.attempt
    with transaction.atomic():
        row = (
            GenerationRun.objects.select_for_update()
            .filter(id=run.id, attempt=attempt, status=S.RUNNING)
            .values("execution", "metrics", "started_at", "study_id", "endpoint_id", "cell_id")
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
                allowed = attempt - lost < infra_retries_for(run.id)
            record = {"attempt": attempt, "reason": reason, "error": (error or "")[:1000], "at": now.isoformat()}
            if execution.get("infra_errors"):
                record["infra_errors"] = execution.pop("infra_errors")[-20:]
            spend = _attempt_spend(row["metrics"])
            if spend:
                record.update(spend)
            execution["attempts"] = [*(execution.get("attempts") or []), record]
            if reason == REASON_EXECUTOR_LOST:
                execution["executor_lost_count"] = lost + 1
            if allowed:
                # The attempt's metrics are wiped below: carry its spend forward.
                if spend:
                    _accumulate_prior_spend(execution, spend)
                execution["requeued_at"] = now.isoformat()
                if reason == REASON_EXECUTOR_LOST:
                    delay = LOST_RETRY_DELAY_S
                else:
                    delay = RETRY_BACKOFF_BASE_S * 2 ** max(0, attempt - lost)
                GenerationRun.objects.filter(id=run.id).update(
                    status=S.PENDING,
                    phase=P.NONE,
                    attempt=attempt + 1,
                    run_after=now + timedelta(seconds=delay),
                    started_at=None,
                    completed_at=None,
                    duration_seconds=None,
                    stage_entered_at=None,
                    execution=execution,
                    updated_at=now,
                    **_ATTEMPT_WIPE,
                    **_LEASE_CLEARED,
                )
                RunFile.objects.filter(run_id=run.id).delete()
                if row["started_at"] is not None:
                    GenerationArtifact.objects.filter(run_id=run.id, created_at__gte=row["started_at"]).delete()
                run.status, run.phase, run.attempt = S.PENDING, P.NONE, attempt + 1
                for name, value in _ATTEMPT_WIPE.items():
                    setattr(run, name, value)
            else:
                n = attempt + 1
                message = f"Infrastructure failure after {n} attempts: {reason}"
                if error:
                    message = f"{message}: {error}"
                values: dict[str, Any] = {
                    "status": S.FAILED,
                    "phase": P.NONE,
                    "outcome": Outcome.INFRA_ERROR,
                    "outcome_reason": reason[:64],
                    "usable": False,
                    "error_message": message[:2000],
                    "completed_at": now,
                    "execution": execution,
                    "updated_at": now,
                    **_LEASE_CLEARED,
                }
                if row["started_at"] is not None:
                    values["duration_seconds"] = (now - row["started_at"]).total_seconds()
                GenerationRun.objects.filter(id=run.id).update(**values)
                for name in ("status", "phase", "outcome", "outcome_reason", "usable", "error_message"):
                    setattr(run, name, values[name])
            run.execution = execution
            requeued = allowed
    if requeued is None:
        _release(run)
        return False

    run.lease_owner = ""
    _cancel_requests([run.id], attempt=attempt)
    _record_breaker(row["endpoint_id"], reason)
    if not requeued:
        refresh_cell(row["cell_id"])
    publish_run(run)
    if not requeued and row["study_id"]:
        maybe_complete_study(row["study_id"])
    _wake()
    logger.info(
        "Run %s attempt %s: %s (%s)%s",
        run.id,
        attempt,
        "requeued" if requeued else "gave up",
        reason,
        f": {error}" if error else "",
    )
    return bool(requeued)


# ── cancel ─────────────────────────────────────────────────────────────


def _lock_rows(ids: list) -> list[dict[str, Any]]:
    return list(
        GenerationRun.objects.select_for_update(skip_locked=True)
        .filter(id__in=ids, status__in=ACTIVE_STATUSES)
        .order_by("id")
        .values("id", "status", "phase", "queued_at", "study_id", "endpoint_id", "cell_id"),
    )


_CANCELLED = {
    "status": S.CANCELLED,
    "phase": P.NONE,
    "outcome": Outcome.EXCLUDED,
    "outcome_reason": ExclusionReason.CANCELLED,
    "usable": False,
}
_TERMINAL_STUDY = ("completed", "cancelled", "stopped")


def cancel_runs(qs, scope: str = "all", *, study: Study | None = None) -> dict[str, int]:
    """Cancel the runs in ``qs`` (spec §C.8).

    PENDING → CANCELLED; RUNNING ``phase=running`` → CANCELLED with ``lease_owner`` kept until the
    worker's ``finally``; ``eval_queued`` / ``evaluating`` are left to finish (their generation is
    done). Rows never handed to the executor (``queued_at IS NULL``) have no live worker and are
    cancelled in every phase. A cancelled generation ends ``excluded:cancelled`` (section 6.12).
    Queued/leased LLMRequests are cancelled after commit. ``scope`` is ``"all"`` or ``"pending"``.
    With ``study`` and scope ``all`` the study itself becomes ``cancelled`` at once. Returns
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
                    elif scope == "all" and (row["queued_at"] is None or row["phase"] in (P.RUNNING, P.NONE)):
                        running.append(row)
                    elif scope == "all":
                        counts["finishing_evaluations"] += 1
                GenerationRun.objects.filter(id__in=[r["id"] for r in pending]).update(
                    completed_at=now,
                    updated_at=now,
                    **_CANCELLED,
                )
                # lease_owner stays: capacity stays counted until the worker's finally releases it.
                GenerationRun.objects.filter(id__in=[r["id"] for r in running]).update(
                    completed_at=now,
                    updated_at=now,
                    **_CANCELLED,
                )
                counts["cancelled_pending"] += len(pending)
                counts["signalled_running"] += len(running)
                cancelled.extend(pending + running)
                locked = {r["id"] for r in rows}
        except DatabaseError as exc:
            if "deadlock" not in str(exc).lower():
                raise
            logger.warning("cancel_runs: deadlock, retrying")
            locked = set()
        still_active = set(
            GenerationRun.objects.filter(id__in=remaining, status__in=statuses).values_list("id", flat=True),
        )
        remaining = [i for i in remaining if i not in locked and i in still_active]
        if remaining and attempt_no < 4:
            time.sleep(0.05 * (attempt_no + 1))
    if remaining:
        logger.warning("cancel_runs: %d row(s) stayed locked; the reconciler finishes them", len(remaining))

    if study is not None and scope == "all":
        from backend.generation.models import Study

        now = timezone.now()
        Study.objects.filter(pk=study.pk).exclude(status__in=_TERMINAL_STUDY).update(
            status="cancelled",
            cancelled_at=now,
            updated_at=now,
        )

    run_ids = [r["id"] for r in cancelled]
    study_ids = {r["study_id"] for r in cancelled if r["study_id"]}
    cell_ids = {r["cell_id"] for r in cancelled if r["cell_id"]}

    def after_commit() -> None:
        _cancel_requests(run_ids)
        for cell_id in cell_ids:
            refresh_cell(cell_id)
        runs = GenerationRun.objects.filter(id__in=run_ids).only(
            "id",
            "status",
            "phase",
            "outcome",
            "usable",
            "attempt",
            "study_id",
            "condition_id",
            "cell_id",
            "repeat_index",
            "endpoint_id",
            "metrics",
        )
        for run in runs:
            publish_run(run)
        if study is not None and scope == "all":
            from backend.realtime.events import publish_study_event

            publish_study_event(study.pk, {"type": "state", "status": "cancelled", "pause_reason": ""})
        for study_id in study_ids:
            maybe_complete_study(study_id)
        _wake()

    transaction.on_commit(after_commit)
    return counts


# ── study completion ───────────────────────────────────────────────────

_COMPLETE_SQL = """
UPDATE generation_study SET status = 'completed', completed_at = now(), updated_at = now()
WHERE id = %s AND status = 'running'
  AND NOT EXISTS (
    SELECT 1 FROM generation_run j WHERE j.study_id = %s AND j.status IN ('pending', 'running')
  )
"""


def maybe_complete_study(study_id: Any) -> bool:
    """Mark a RUNNING study COMPLETED when it has no active runs (idempotent, spec §F.5).

    ``UPDATE … SET status='completed', completed_at=now() WHERE id=:id AND status='running' AND NOT
    EXISTS (active runs)``; only on rowcount 1 is the ``state`` event published. A piloting study is
    never completed here (its pilot gates decide, WP-10/WP-11). Post-runs attached at launch become
    claimable once the study is completed (``workers.claimable_post_runs``). Returns True when it completed.
    """
    if study_id is None:
        return False
    with connection.cursor() as cursor:
        cursor.execute(_COMPLETE_SQL, [str(study_id), str(study_id)])
        completed = cursor.rowcount == 1
    if not completed:
        return False
    from backend.realtime.events import publish_study_event

    publish_study_event(study_id, {"type": "state", "status": "completed", "pause_reason": ""})
    _wake()  # a post-run may now be claimable
    logger.info("Study %s completed", study_id)
    return True
