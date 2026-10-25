"""Maintenance actions: clear stuck records, purge orphans, clear caches."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationJob
from backend.runtime.models import ContainerInstance
from backend.runtime.services import docker_manager


def clear_stuck_analysis_tasks(older_than_minutes: int = 60) -> int:
    threshold = timezone.now() - timedelta(minutes=older_than_minutes)
    return AnalysisRun.objects.filter(
        status__in=[AnalysisRun.Status.PENDING, AnalysisRun.Status.RUNNING],
        updated_at__lt=threshold,
    ).update(
        status=AnalysisRun.Status.FAILED,
        error_message=(f"Marked failed by maintenance: stuck for >{older_than_minutes}m"),
    )


def clear_stuck_generation_jobs(older_than_minutes: int = 60) -> int:
    """Fail generation jobs that can no longer finish on their own.

    Only two kinds of rows are touched, never a healthy PENDING job the
    executor will still claim:

    * legacy thread-era rows (``queued_at IS NULL``) still PENDING/RUNNING and
      untouched for ``older_than_minutes`` (their threads died with the old
      process);
    * executor-owned RUNNING rows whose lease expired more than
      ``older_than_minutes`` ago (the reconciler normally recovers these
      within seconds; this is the manual backstop).

    Batches of the touched jobs are refreshed and status events published.
    """
    from backend.execution import transitions
    from backend.generation.models import GenerationBatch
    from backend.generation.services.batches import refresh_batch
    from backend.generation.services.result_writer import publish_status

    now = timezone.now()
    threshold = now - timedelta(minutes=older_than_minutes)
    legacy = Q(
        queued_at__isnull=True,
        status__in=[GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING],
        updated_at__lt=threshold,
    )
    expired = Q(
        queued_at__isnull=False,
        status=GenerationJob.Status.RUNNING,
        lease_expires_at__lt=threshold,
    )
    stuck = list(GenerationJob.objects.filter(legacy | expired).only("id", "batch_id", "experiment_id"))
    if not stuck:
        return 0
    count = (
        GenerationJob.objects.filter(id__in=[j.id for j in stuck])
        .filter(legacy | expired)
        .update(
            status=GenerationJob.Status.FAILED,
            phase=GenerationJob.Phase.NONE,
            completed_at=now,
            lease_owner="",
            lease_expires_at=None,
            error_message=f"Marked failed by maintenance: stuck for >{older_than_minutes}m",
            updated_at=now,
        )
    )
    for batch_id in {j.batch_id for j in stuck if j.batch_id}:
        refresh_batch(GenerationBatch(id=batch_id))
    for job in GenerationJob.objects.filter(id__in=[j.id for j in stuck]):
        publish_status(job)
    for experiment_id in {j.experiment_id for j in stuck if j.experiment_id}:
        transitions.maybe_complete_experiment(experiment_id)
    return count


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

    # Agent-run sandboxes outlive their job only if the process running it
    # died; anything older than the longest allowed run is such a leftover.
    reaped = 0
    try:
        from backend.generation.services.agent.sandbox import reap_orphans

        reaped = reap_orphans(older_than_seconds=5 * 3600)
    except Exception:  # noqa: BLE001 - apps daemon unreachable: nothing to reap
        reaped = 0

    return len(orphan_ids) + reaped


def clear_caches() -> dict[str, Any]:
    try:
        cache.clear()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    else:
        return {"success": True, "message": "All caches cleared"}
