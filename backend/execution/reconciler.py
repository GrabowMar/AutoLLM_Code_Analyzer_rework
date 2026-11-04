"""Reconciliation (spec §C.7): lost runs, expired leases, orphans, LLM request leases, sessions, completion.

:func:`reconcile` runs at leader start (before the first claim) and every
15 s; :func:`requeue_lost` (step 0) runs every tick. Neither ever touches an
id in this process's run registry: those have a live worker.

Because exactly one executor holds the leader lock, every lease owned by
another executor id is dead (a second instance never claims): at start the
new leader treats them as expired immediately instead of waiting out 90 s.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.utils import timezone

from backend.execution import transitions
from backend.execution.models import ExecutorInstance
from backend.generation.models import GenerationRun
from backend.generation.models.choices import Lane
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus

if TYPE_CHECKING:
    from backend.execution.registry import JobRegistry

logger = logging.getLogger(__name__)

S = RunStatus
P = RunPhase

LOST_GRACE_S = 30
ORPHAN_MIN_AGE_S = 120
STALE_INSTANCE_S = 90
AGENT_LABEL = "llmlab.agent-run"
EVAL_LABEL = "llmlab.evaluation"


@dataclass
class ReconcileReport:
    lost: int = 0
    expired: int = 0
    evaluations_requeued: int = 0
    released: int = 0
    orphans_removed: int = 0
    requests_requeued: int = 0
    sessions_ended: int = 0
    instances_stopped: int = 0
    studies_completed: int = 0
    ledger_settled: int = 0
    blocks_closed: int = 0
    studies_resumed: int = 0
    errors: list[str] = field(default_factory=list)


def _step(report: ReconcileReport, name: str, fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except NotImplementedError:
        return None
    except Exception as exc:
        logger.exception("reconcile step %s failed", name)
        report.errors.append(f"{name}: {type(exc).__name__}: {exc}")
        return None


# ── step 0: lost runs ──────────────────────────────────────────────────


def requeue_lost(executor_id: str, registry: JobRegistry, *, grace_s: int = LOST_GRACE_S) -> int:
    """Runs this executor owns that no live worker holds (submit failed, thread died before start/finish).

    ``lease_owner = me``, not in the registry, stage entered more than
    ``grace_s`` ago → ``infra_retry(executor_lost)``; a lost evaluation goes
    back to ``eval_queued``.
    """
    cutoff = timezone.now() - timedelta(seconds=grace_s)
    live = set(registry.ids())
    rows = list(
        GenerationRun.objects.filter(lease_owner=executor_id, stage_entered_at__lt=cutoff)
        .exclude(id__in=live)
        .only(*_RUN_FIELDS),
    )
    count = 0
    for run in rows:
        if str(run.id) in registry:
            continue
        if _recover(run, executor_id=executor_id):
            count += 1
    return count


_RUN_FIELDS = (
    "id",
    "status",
    "phase",
    "lane",
    "attempt",
    "lease_owner",
    "study_id",
    "endpoint_id",
    "condition_id",
    "cell_id",
    "repeat_index",
    "execution",
    "started_at",
    "completed_at",
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
    "created_by_id",
)


def _recover(run: GenerationRun, *, executor_id: str | None, docker=None) -> bool:
    """Bring one run whose worker is gone back to a consistent state."""
    if run.status != S.RUNNING:
        # Cancelled (or finished) while a worker held it: just give the slot back.
        if docker is not None:
            _reap_run_containers(docker, run)
        released = (
            GenerationRun.objects.filter(id=run.id, lease_owner=run.lease_owner, attempt=run.attempt)
            .exclude(status=S.RUNNING)
            .update(lease_owner="", lease_expires_at=None, heartbeat_at=None, phase=P.NONE)
        )
        return bool(released)
    if run.phase == P.EVALUATING:
        if docker is not None:
            _reap_run_containers(docker, run, labels=(EVAL_LABEL,))
        return requeue_evaluation(run)
    if docker is not None and run.lane == Lane.SANDBOX:
        _reap_run_containers(docker, run)
    return transitions.infra_retry(run, transitions.REASON_EXECUTOR_LOST, "the executor lost this run")


def requeue_evaluation(run: GenerationRun) -> bool:
    """A lost evaluation: back to ``eval_queued`` (``execution.eval_requeues += 1``), at most ``EVAL_MAX_REQUEUES``.

    Past the cap the run ends ungraded: COMPLETED (or its pre-re-evaluation status) with
    ``outcome=infra_error`` (section 6.12: a tester that never ran is never a pass rate of 0). A lost
    re-evaluation keeps the outcome the run already had.
    """
    now = timezone.now()
    execution = dict(run.execution or {})
    requeues = int(execution.get("eval_requeues") or 0) + 1
    execution["eval_requeues"] = requeues
    guard = GenerationRun.objects.filter(
        id=run.id,
        status=S.RUNNING,
        phase=P.EVALUATING,
        attempt=run.attempt,
        lease_owner=run.lease_owner,
    )
    if requeues <= settings.EVAL_MAX_REQUEUES:
        done = guard.update(
            phase=P.EVAL_QUEUED,
            stage_entered_at=now,
            execution=execution,
            updated_at=now,
            lease_owner="",
            lease_expires_at=None,
            heartbeat_at=None,
        )
        if done:
            transitions._wake()  # noqa: SLF001
        return bool(done)
    # Give up: the sample stays, ungraded.
    detail = f"evaluation was lost {requeues} times (executor restarts or crashes)"
    result = dict(run.result or {})
    result["error"] = detail
    run.result = result
    if not execution.get("reeval"):
        run.outcome, run.outcome_reason = Outcome.INFRA_ERROR, "eval_lost"
    run.execution = execution
    run.status = S.COMPLETED
    if not guard.update(execution=execution, updated_at=now):
        return False
    return transitions.finish_stage(run, transitions.OUTCOME_DONE)


# ── step 1: expired leases ─────────────────────────────────────────────


def dead_owners(executor_id: str) -> list[str]:
    """Executor ids whose leases count as expired: every other instance (one leader at a time)."""
    return list(ExecutorInstance.objects.exclude(id=executor_id).values_list("id", flat=True))


def expire_leases(executor_id: str, registry: JobRegistry, *, docker=None, now: datetime | None = None) -> int:
    now = now or timezone.now()
    live = set(registry.ids())
    others = Q(lease_owner__gt="") & ~Q(lease_owner=executor_id)
    expired = Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True)
    rows = list(
        GenerationRun.objects.filter(queued_at__isnull=False)
        .exclude(lease_owner="")
        .filter(others | expired)
        .exclude(id__in=live)
        .order_by("id")
        .only(*_RUN_FIELDS),
    )
    count = 0
    for run in rows:
        if str(run.id) in registry:
            continue
        if _recover(run, executor_id=executor_id, docker=docker):
            count += 1
    return count


# ── step 2: orphan containers ──────────────────────────────────────────


def _container_run_id(container, label: str) -> str | None:
    labels = getattr(container, "labels", None) or {}
    return labels.get(label)


def _container_age(container, now: float) -> float:
    labels = getattr(container, "labels", None) or {}
    started = labels.get("llmlab.started")
    if started and str(started).isdigit():
        return now - int(started)
    created = (getattr(container, "attrs", None) or {}).get("Created")
    if created:
        from django.utils.dateparse import parse_datetime

        stamp = parse_datetime(str(created)[:26] + "+00:00" if "Z" in str(created) else str(created))
        if stamp is not None:
            return now - stamp.timestamp()
    return float("inf")


def _reap_run_containers(docker, run: GenerationRun, labels: tuple[str, ...] = (AGENT_LABEL, EVAL_LABEL)) -> int:
    removed = 0
    for label in labels:
        try:
            for container in docker.containers.list(all=True, filters={"label": f"{label}={run.id}"}):
                container.remove(force=True)
                removed += 1
            for network in docker.networks.list(filters={"label": f"{label}={run.id}"}):
                try:
                    network.remove()
                except Exception:  # noqa: BLE001 — still attached somewhere; the orphan pass retries
                    logger.debug("Keeping network %s", getattr(network, "name", "?"))
        except Exception:
            logger.exception("Reaping containers of run %s failed", run.id)
    return removed


def reap_orphans(docker, registry: JobRegistry, *, now: float | None = None) -> int:
    """Remove sandbox / evaluation containers whose run is not running under a live lease.

    Containers are listed **first**, then run states are read, so a container
    started after the read is never mistaken for an orphan; anything younger
    than two minutes is skipped either way.
    """
    now = now or time.time()
    found: list[tuple[Any, str, str]] = []
    for label in (AGENT_LABEL, EVAL_LABEL):
        for container in docker.containers.list(all=True, filters={"label": label}):
            job_id = _container_run_id(container, label)
            if job_id:
                found.append((container, label, job_id))
    if not found:
        return 0
    live_ids = set(registry.ids())
    ids = {job_id for _c, _l, job_id in found}
    valid_ids = []
    for job_id in ids:
        try:
            valid_ids.append(uuid.UUID(job_id))
        except ValueError:
            continue
    alive = {
        str(i)
        for i in GenerationRun.objects.filter(
            id__in=valid_ids,
            status=S.RUNNING,
            lease_expires_at__gt=timezone.now(),
        ).values_list("id", flat=True)
    }
    removed = 0
    for container, _label, job_id in found:
        if job_id in live_ids or job_id in alive:
            continue
        if _container_age(container, now) < ORPHAN_MIN_AGE_S:
            continue
        try:
            container.remove(force=True)
            removed += 1
        except Exception:  # noqa: BLE001
            logger.warning("Removing orphan container %s failed", getattr(container, "name", "?"))
    for label in (AGENT_LABEL, EVAL_LABEL):
        try:
            for network in docker.networks.list(filters={"label": label}):
                job_id = (getattr(network, "attrs", None) or {}).get("Labels", {}).get(label)
                if job_id and job_id not in live_ids and job_id not in alive:
                    try:
                        network.remove()
                    except Exception:  # noqa: BLE001 — still in use
                        logger.debug("Keeping network %s", getattr(network, "name", "?"))
        except Exception:
            logger.exception("Listing %s networks failed", label)
    return removed


# ── steps 3-5: LLM requests, sessions, tokens ──────────────────────────


def requeue_dead_pump_leases(executor_id: str) -> int:
    """Pump-leased requests whose owner is not this (the only live) executor go back to the queue."""
    from backend.inference.requests import requeue_pump_orphans

    return requeue_pump_orphans([executor_id])


def end_stale_sessions(now: datetime | None = None) -> int:
    """Step 4: relay sessions unseen for ``RELAY_SESSION_TTL_S`` end (leases requeued, standby promoted)."""
    from backend.inference.tokens import reap_stale_sessions

    return reap_stale_sessions(now)


def end_expired_token_sessions(now: datetime | None = None) -> int:
    """Step 5: sessions of tokens past their rotation grace end."""
    from backend.inference.tokens import reap_expired_tokens

    return reap_expired_tokens(now)


# ── steps 6-8 ──────────────────────────────────────────────────────────


def stop_dead_instances(executor_id: str, now: datetime | None = None) -> int:
    now = now or timezone.now()
    return (
        ExecutorInstance.objects.filter(stopped_at__isnull=True, last_seen_at__lt=now - timedelta(seconds=90))
        .exclude(id=executor_id)
        .update(stopped_at=now, is_leader=False)
    )


def completion_sweep() -> int:
    """Every RUNNING study with no active runs → completed.

    Also finishes study cancels that skipped rows a concurrent claim held (``cancel_runs`` locks
    with SKIP LOCKED): pending runs and generation stages of cancelled studies are cancelled;
    finished generations waiting for or in evaluation are left to finish, as ``cancel_runs`` does.
    """
    from backend.generation.models import Study

    stray = GenerationRun.objects.filter(
        Q(status=S.PENDING) | Q(status=S.RUNNING, phase=P.RUNNING),
        study__status="cancelled",
    )
    if stray.exists():
        transitions.cancel_runs(stray, scope="all")
    active = GenerationRun.objects.filter(study_id=OuterRef("pk"), status__in=transitions.ACTIVE_STATUSES)
    stopping = Q(status="piloting", stop_reason__isnull=False)
    candidates = Study.objects.filter(Q(status="running") | stopping).exclude(Exists(active))
    completed = 0
    for study_id in candidates.values_list("id", flat=True):
        if transitions.maybe_complete_study(study_id):
            completed += 1
    return completed


def reconcile_ledger() -> dict[str, int]:
    """Spend ledger repair (spec 4.5): settle ended attempts that still hold a reservation and release
    the block reservations of finished blocks (``budget.ledger.reconcile``)."""
    from backend.generation.budget import ledger

    return ledger.reconcile()


def resume_paused_for_caps() -> int:
    """Resume studies paused for ``quota`` / ``spend_cap`` once the next run fits every spend cap again."""
    from backend.generation.budget import claims

    return claims.resume_paused_for_caps()


# ── entry point ────────────────────────────────────────────────────────


def reconcile(
    executor_id: str,
    registry: JobRegistry,
    *,
    docker_factory=None,
    reap: bool = True,
) -> ReconcileReport:
    """One reconciliation pass (§C.7 steps 0-7). Each step is isolated: one failing never stops the rest.

    Schedule catch-up (v2 step 8) belongs to ``study.automation.fire_due_schedules`` (WP-10).
    """
    report = ReconcileReport()
    docker = None
    if docker_factory is not None:
        try:
            docker = docker_factory()
        except Exception as exc:  # noqa: BLE001 — apps-dind down: skip container steps this pass
            report.errors.append(f"docker: {exc}")
    report.lost = _step(report, "lost", requeue_lost, executor_id, registry) or 0
    report.expired = _step(report, "leases", expire_leases, executor_id, registry, docker=docker) or 0
    if docker is not None and reap:
        report.orphans_removed = _step(report, "orphans", reap_orphans, docker, registry) or 0
    _step(report, "pump_leases", requeue_dead_pump_leases, executor_id)

    def _requeue_expired():
        from backend.inference.requests import requeue_expired

        return requeue_expired()

    report.requests_requeued = _step(report, "llm_requests", _requeue_expired) or 0
    report.sessions_ended = (_step(report, "sessions", end_stale_sessions) or 0) + (
        _step(report, "tokens", end_expired_token_sessions) or 0
    )
    report.instances_stopped = _step(report, "instances", stop_dead_instances, executor_id) or 0
    report.studies_completed = _step(report, "completion", completion_sweep) or 0
    ledger_report = _step(report, "ledger", reconcile_ledger) or {}
    report.ledger_settled = ledger_report.get("settled", 0)
    report.blocks_closed = ledger_report.get("blocks_closed", 0)
    report.studies_resumed = _step(report, "spend_caps", resume_paused_for_caps) or 0
    close = getattr(docker, "close", None)
    if callable(close):
        try:
            close()  # a fresh client per pass: never leak its connection pool
        except Exception:  # noqa: BLE001
            logger.debug("Closing the docker client failed", exc_info=True)
    return report


__all__ = [
    "ReconcileReport",
    "completion_sweep",
    "dead_owners",
    "end_expired_token_sessions",
    "end_stale_sessions",
    "expire_leases",
    "reap_orphans",
    "reconcile",
    "reconcile_ledger",
    "requeue_evaluation",
    "requeue_lost",
    "resume_paused_for_caps",
    "stop_dead_instances",
]
