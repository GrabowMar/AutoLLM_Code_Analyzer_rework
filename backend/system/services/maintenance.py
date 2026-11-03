"""Maintenance actions: clear stuck records, purge orphans, clear caches."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationRun
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunPhase
from backend.generation.models.choices import RunStatus
from backend.runtime.models import ContainerInstance
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)


def clear_stuck_analysis_tasks(older_than_minutes: int = 60) -> int:
    from backend.analysis.services.runner import reap_stale_runs

    # Runs whose executing thread is gone (stale heartbeat), regardless of age.
    reaped = reap_stale_runs()
    threshold = timezone.now() - timedelta(minutes=older_than_minutes)
    return reaped + AnalysisRun.objects.filter(
        status__in=[AnalysisRun.Status.PENDING, AnalysisRun.Status.RUNNING],
        updated_at__lt=threshold,
    ).update(
        status=AnalysisRun.Status.FAILED,
        error_message=(f"Marked failed by maintenance: stuck for >{older_than_minutes}m"),
    )


def clear_stuck_generation_jobs(older_than_minutes: int = 60) -> int:
    """Fail generation runs that can no longer finish on their own.

    Only two kinds of rows are touched, never a healthy PENDING run the
    executor will still claim:

    * rows never handed to the executor (``queued_at IS NULL``) still
      PENDING/RUNNING and untouched for ``older_than_minutes``;
    * executor-owned RUNNING rows whose lease expired more than
      ``older_than_minutes`` ago (the reconciler normally recovers these
      within seconds; this is the manual backstop).

    A failed run is ``infra_error`` (never a model outcome) and not usable.
    Status events are published and the touched studies are checked for
    completion; both are best effort (the row update is what matters).
    """
    now = timezone.now()
    threshold = now - timedelta(minutes=older_than_minutes)
    unqueued = Q(
        queued_at__isnull=True,
        status__in=[RunStatus.PENDING, RunStatus.RUNNING],
        updated_at__lt=threshold,
    )
    expired = Q(
        queued_at__isnull=False,
        status=RunStatus.RUNNING,
        lease_expires_at__lt=threshold,
    )
    stuck = list(GenerationRun.objects.filter(unqueued | expired).only("id", "study_id"))
    if not stuck:
        return 0
    stuck_ids = [run.id for run in stuck]
    count = (
        GenerationRun.objects.filter(id__in=stuck_ids)
        .filter(unqueued | expired)
        .update(
            status=RunStatus.FAILED,
            phase=RunPhase.NONE,
            outcome=Outcome.INFRA_ERROR,
            outcome_reason="maintenance_stuck",
            usable=False,
            completed_at=now,
            lease_owner="",
            lease_expires_at=None,
            error_message=f"Marked failed by maintenance: stuck for >{older_than_minutes}m",
            updated_at=now,
        )
    )
    _after_runs_failed(stuck_ids, {run.study_id for run in stuck if run.study_id})
    return count


def _after_runs_failed(run_ids: list[Any], study_ids: set[Any]) -> None:
    """Publish the status of each failed run and let finished studies complete (execution owns both)."""
    from backend.execution import transitions

    for run in GenerationRun.objects.filter(id__in=run_ids):
        try:
            transitions.publish_run(run)
        except Exception:
            logger.exception("publish_run(%s) failed", run.id)
    for study_id in study_ids:
        try:
            transitions.maybe_complete_study(study_id)
        except Exception:
            logger.exception("maybe_complete_study(%s) failed", study_id)


def purge_orphan_containers() -> int:
    try:
        client = docker_manager.new_client()
        all_containers = client.containers.list(all=True)
        existing_ids: set[str] = set()
        for c in all_containers:
            existing_ids.add(c.id)
            existing_ids.add(c.short_id)
    except Exception:  # noqa: BLE001
        existing_ids = set()

    active_statuses = [
        ContainerInstance.Status.PENDING,
        ContainerInstance.Status.BUILDING,
        ContainerInstance.Status.RUNNING,
    ]
    candidates = ContainerInstance.objects.filter(
        status__in=active_statuses,
    ).exclude(container_id="")

    orphan_ids = []
    for inst in candidates:
        cid = inst.container_id
        is_present = cid in existing_ids or any(full_id.startswith(cid) for full_id in existing_ids)
        if not is_present:
            orphan_ids.append(inst.pk)

    if orphan_ids:
        ContainerInstance.objects.filter(pk__in=orphan_ids).update(
            status=ContainerInstance.Status.REMOVED,
        )

    # Agent-run sandboxes outlive their run only if the process running it
    # died; anything older than the longest allowed run is such a leftover.
    reaped = 0
    try:
        from backend.generation.agentic.sandbox import reap_orphans  # WP-08 port

        reaped = reap_orphans(older_than_seconds=5 * 3600)
    except Exception:  # noqa: BLE001 - not ported yet / apps daemon unreachable: nothing to reap
        reaped = 0

    return len(orphan_ids) + reaped


def clear_caches() -> dict[str, Any]:
    try:
        cache.clear()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    else:
        return {"success": True, "message": "All caches cleared"}
