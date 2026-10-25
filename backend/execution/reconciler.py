"""Reconciliation (spec §C.7): lost jobs, expired leases, orphans, LLM request leases, sessions, completion.

:func:`reconcile` runs at leader start (before the first claim) and every
15 s; :func:`requeue_lost` (step 0) runs every tick. Neither ever touches an
id in this process's job registry: those have a live worker.

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
from backend.generation.models import GenerationJob

if TYPE_CHECKING:
    from backend.execution.registry import JobRegistry

logger = logging.getLogger(__name__)

S = GenerationJob.Status
P = GenerationJob.Phase

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
    experiments_completed: int = 0
    schedules_fast_forwarded: int = 0
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


# ── step 0: lost jobs ──────────────────────────────────────────────────


def requeue_lost(executor_id: str, registry: JobRegistry, *, grace_s: int = LOST_GRACE_S) -> int:
    """Jobs this executor owns that no live worker holds (submit failed, thread died before start/finish).

    ``lease_owner = me``, not in the registry, stage entered more than
    ``grace_s`` ago → ``infra_retry(executor_lost)``; a lost evaluation goes
    back to ``eval_queued``.
    """
    cutoff = timezone.now() - timedelta(seconds=grace_s)
    live = set(registry.ids())
    rows = list(
        GenerationJob.objects.filter(lease_owner=executor_id, stage_entered_at__lt=cutoff)
        .exclude(id__in=live)
        .only(*_JOB_FIELDS),
    )
    count = 0
    for job in rows:
        if str(job.id) in registry:
            continue
        if _recover(job, executor_id=executor_id):
            count += 1
    return count


_JOB_FIELDS = (
    "id",
    "status",
    "phase",
    "lane",
    "attempt",
    "lease_owner",
    "experiment_id",
    "batch_id",
    "endpoint_id",
    "condition_id",
    "app_requirement_id",
    "repeat_index",
    "execution",
    "started_at",
    "completed_at",
    "result_data",
    "metrics",
    "created_by_id",
)


def _recover(job: GenerationJob, *, executor_id: str | None, docker=None) -> bool:
    """Bring one job whose worker is gone back to a consistent state."""
    if job.status != S.RUNNING:
        # Cancelled (or finished) while a worker held it: just give the slot back.
        if docker is not None:
            _reap_job_containers(docker, job)
        released = (
            GenerationJob.objects.filter(id=job.id, lease_owner=job.lease_owner, attempt=job.attempt)
            .exclude(status=S.RUNNING)
            .update(lease_owner="", lease_expires_at=None, heartbeat_at=None, phase=P.NONE)
        )
        return bool(released)
    if job.phase == P.EVALUATING:
        if docker is not None:
            _reap_job_containers(docker, job, labels=(EVAL_LABEL,))
        return requeue_evaluation(job)
    if docker is not None and job.lane == GenerationJob.Lane.SANDBOX:
        _reap_job_containers(docker, job)
    return transitions.infra_retry(job, transitions.REASON_EXECUTOR_LOST, "the executor lost this job")


def requeue_evaluation(job: GenerationJob) -> bool:
    """A lost evaluation: back to ``eval_queued`` (``execution.eval_requeues += 1``), at most ``EVAL_MAX_REQUEUES``.

    Past the cap the job ends ungraded: COMPLETED (or its pre-re-evaluation
    status) with ``evaluation.status = infra_error``.
    """
    now = timezone.now()
    execution = dict(job.execution or {})
    requeues = int(execution.get("eval_requeues") or 0) + 1
    execution["eval_requeues"] = requeues
    guard = GenerationJob.objects.filter(
        id=job.id,
        status=S.RUNNING,
        phase=P.EVALUATING,
        attempt=job.attempt,
        lease_owner=job.lease_owner,
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
    result = dict(job.result_data or {})
    result["evaluation"] = {
        "status": "infra_error",
        "error": f"evaluation was lost {requeues} times (executor restarts or crashes)",
    }
    job.result_data = result
    job.execution = execution
    job.status = S.COMPLETED
    if not guard.update(execution=execution, updated_at=now):
        return False
    return transitions.finish_stage(job, transitions.OUTCOME_DONE)


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
        GenerationJob.objects.filter(queued_at__isnull=False)
        .exclude(lease_owner="")
        .filter(others | expired)
        .exclude(id__in=live)
        .order_by("id")
        .only(*_JOB_FIELDS),
    )
    count = 0
    for job in rows:
        if str(job.id) in registry:
            continue
        if _recover(job, executor_id=executor_id, docker=docker):
            count += 1
    return count


# ── step 2: orphan containers ──────────────────────────────────────────


def _container_job_id(container, label: str) -> str | None:
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


def _reap_job_containers(docker, job: GenerationJob, labels: tuple[str, ...] = (AGENT_LABEL, EVAL_LABEL)) -> int:
    removed = 0
    for label in labels:
        try:
            for container in docker.containers.list(all=True, filters={"label": f"{label}={job.id}"}):
                container.remove(force=True)
                removed += 1
            for network in docker.networks.list(filters={"label": f"{label}={job.id}"}):
                try:
                    network.remove()
                except Exception:  # noqa: BLE001 — still attached somewhere; the orphan pass retries
                    logger.debug("Keeping network %s", getattr(network, "name", "?"))
        except Exception:
            logger.exception("Reaping containers of job %s failed", job.id)
    return removed


def reap_orphans(docker, registry: JobRegistry, *, now: float | None = None) -> int:
    """Remove sandbox / evaluation containers whose job is not running under a live lease.

    Containers are listed **first**, then job states are read, so a container
    started after the read is never mistaken for an orphan; anything younger
    than two minutes is skipped either way.
    """
    now = now or time.time()
    found: list[tuple[Any, str, str]] = []
    for label in (AGENT_LABEL, EVAL_LABEL):
        for container in docker.containers.list(all=True, filters={"label": label}):
            job_id = _container_job_id(container, label)
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
        for i in GenerationJob.objects.filter(
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
    """Every RUNNING experiment with no active jobs → completed.

    Also finishes experiment cancels that skipped rows a concurrent claim held
    (``cancel_jobs`` locks with SKIP LOCKED): pending jobs and generation runs
    of cancelled experiments are cancelled; finished generations waiting for
    or in evaluation are left to finish, as ``cancel_jobs`` does.
    """
    from backend.generation.models import Experiment

    stray = GenerationJob.objects.filter(
        Q(status=S.PENDING) | Q(status=S.RUNNING, phase=P.RUNNING),
        experiment__status=Experiment.Status.CANCELLED,
    )
    if stray.exists():
        transitions.cancel_jobs(stray, scope="all")
    active = GenerationJob.objects.filter(experiment_id=OuterRef("pk"), status__in=transitions.ACTIVE_STATUSES)
    candidates = Experiment.objects.filter(status=Experiment.Status.RUNNING).exclude(Exists(active))
    completed = 0
    for experiment_id in candidates.values_list("id", flat=True):
        if transitions.maybe_complete_experiment(experiment_id):
            completed += 1
    return completed


def fast_forward_schedules(now: datetime | None = None) -> int:
    """Step 8: ``next_run_at`` more than one period in the past → the next future fire time (no burst catch-up)."""
    from backend.generation.services import experiment_automation

    return experiment_automation.fast_forward_schedules(now or timezone.now())


# ── entry point ────────────────────────────────────────────────────────


def reconcile(
    executor_id: str,
    registry: JobRegistry,
    *,
    docker_factory=None,
    reap: bool = True,
) -> ReconcileReport:
    """One reconciliation pass (§C.7 steps 0-8). Each step is isolated: one failing never stops the rest."""
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
    report.experiments_completed = _step(report, "completion", completion_sweep) or 0
    report.schedules_fast_forwarded = _step(report, "schedules", fast_forward_schedules) or 0
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
    "fast_forward_schedules",
    "reap_orphans",
    "reconcile",
    "requeue_evaluation",
    "requeue_lost",
    "stop_dead_instances",
]
