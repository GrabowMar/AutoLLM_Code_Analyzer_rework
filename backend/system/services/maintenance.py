"""Maintenance actions: reconcile stuck runs, purge orphans, clear caches."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationJob
from backend.runtime.models import ContainerInstance
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

# Docker label of the v1 per-user analyzer workspaces. v2 runs every tool in
# an ephemeral, labelled container (``llmlab.analysis-run``), so any
# container still carrying this label is a leftover of the old engine.
LEGACY_WORKSPACE_LABEL = "backend.kind=analyzer-workspace"
# Grace period before an analysis container whose run is not live counts as
# an orphan: covers the moment between a run finishing and its own cleanup.
ANALYSIS_REAP_GRACE_SECONDS = 300
# Longest agent run; agent sandboxes older than this belong to a dead process.
AGENT_REAP_AGE_SECONDS = 5 * 3600


def reconcile_analysis_runs() -> dict[str, Any]:
    """Run the analysis executor's reconciler now (stale heartbeats, lost queue entries, orphans)."""
    from backend.analysis.engine import executor

    return executor.reconcile()


def action_count(result: Any) -> int:
    """How many runs/resources a reconcile result says it acted on (shape-agnostic)."""
    if isinstance(result, bool):
        return 0
    if isinstance(result, int):
        return result
    if isinstance(result, (list, tuple, set)):
        return len(result)
    if isinstance(result, dict):
        # Error counters/messages are not actions taken.
        return sum(action_count(v) for k, v in result.items() if "error" not in str(k))
    return 0


def clear_stuck_analysis_tasks(older_than_minutes: int = 60) -> int:
    """Recover stuck analysis runs through the executor's reconciler.

    v2 runs carry a heartbeat: the reconciler requeues runs whose heartbeat
    went stale (or fails them after the retry budget), re-enqueues lost
    queued runs and reaps orphaned containers. Blindly marking runs FAILED
    by age (the v1 behaviour) would kill healthy long runs and race the
    worker, so ``older_than_minutes`` is accepted for API compatibility only:
    staleness is ``ANALYSIS_STALE_SECONDS`` of missed heartbeats.
    """
    del older_than_minutes  # heartbeat-based now (see docstring)
    return action_count(reconcile_analysis_runs())


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


def clear_stuck_reports(older_than_minutes: int = 60) -> int:
    """Fail reports whose generation thread died with the web process."""
    from backend.reports.services import fail_stuck_reports

    return fail_stuck_reports(older_than_minutes)


def _mark_missing_instances(existing_ids: set[str]) -> int:
    active_statuses = [
        ContainerInstance.Status.PENDING,
        ContainerInstance.Status.BUILDING,
        ContainerInstance.Status.RUNNING,
    ]
    candidates = ContainerInstance.objects.filter(status__in=active_statuses).exclude(container_id="")
    orphan_ids = []
    for inst in candidates:
        cid = inst.container_id
        is_present = cid in existing_ids or any(full_id.startswith(cid) for full_id in existing_ids)
        if not is_present:
            orphan_ids.append(inst.pk)
    if orphan_ids:
        ContainerInstance.objects.filter(pk__in=orphan_ids).update(status=ContainerInstance.Status.REMOVED)
    return len(orphan_ids)


def _remove_legacy_workspaces(client) -> int:
    removed = 0
    for container in client.containers.list(all=True, filters={"label": LEGACY_WORKSPACE_LABEL}):
        container.remove(force=True)
        removed += 1
    # Their DB rows (runtime ContainerInstance, metadata kind analyzer-workspace)
    # can never be used again either.
    ContainerInstance.objects.filter(metadata__kind="analyzer-workspace").exclude(
        status=ContainerInstance.Status.REMOVED,
    ).update(status=ContainerInstance.Status.REMOVED)
    return removed


def purge_orphans() -> dict[str, Any]:
    """Reconcile container state with the apps daemon and remove leftovers.

    Aborts before touching anything when the daemon cannot be listed: an
    unreachable daemon says nothing about which containers exist, so marking
    every instance removed (the v1 behaviour) would corrupt the DB.
    """
    details: dict[str, Any] = {
        "listing_ok": False,
        "marked_removed": 0,
        "agent_reaped": 0,
        "analysis_reaped": {},
        "legacy_workspaces_removed": 0,
        "errors": [],
    }
    try:
        client = docker_manager.new_client()
        existing_ids: set[str] = set()
        for c in client.containers.list(all=True):
            existing_ids.add(c.id)
            existing_ids.add(c.short_id)
    except Exception as exc:  # noqa: BLE001 - daemon down: report, change nothing
        logger.warning("purge_orphans: docker listing failed: %s", exc)
        details["errors"].append(f"docker listing failed: {exc}")
        return details
    details["listing_ok"] = True
    details["marked_removed"] = _mark_missing_instances(existing_ids)

    # Agent-run sandboxes outlive their job only if the process running it
    # died; anything older than the longest allowed run is such a leftover.
    try:
        from backend.generation.services.agent.sandbox import reap_orphans

        details["agent_reaped"] = reap_orphans(client, older_than_seconds=AGENT_REAP_AGE_SECONDS)
    except Exception as exc:  # noqa: BLE001 - one reaper failing must not stop the others
        logger.warning("purge_orphans: agent reap failed: %s", exc)
        details["errors"].append(f"agent reap failed: {exc}")

    # Analysis tool containers/volumes whose run is not queued or running.
    try:
        from backend.analysis.engine import sandbox

        live_ids = AnalysisRun.objects.filter(status__in=AnalysisRun.ACTIVE_STATUSES).values_list("id", flat=True)
        reaped = sandbox.reap(live_run_ids={str(i) for i in live_ids}, older_than_s=ANALYSIS_REAP_GRACE_SECONDS)
        details["analysis_reaped"] = dict(reaped or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("purge_orphans: analysis reap failed: %s", exc)
        details["errors"].append(f"analysis reap failed: {exc}")

    try:
        details["legacy_workspaces_removed"] = _remove_legacy_workspaces(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("purge_orphans: legacy workspace cleanup failed: %s", exc)
        details["errors"].append(f"legacy workspace cleanup failed: {exc}")
    return details


def purged_total(details: dict[str, Any]) -> int:
    return (
        details["marked_removed"]
        + details["agent_reaped"]
        + action_count(details["analysis_reaped"])
        + details["legacy_workspaces_removed"]
    )


def purge_orphan_containers() -> int:
    """Total number of orphans handled by :func:`purge_orphans` (API-compatible count)."""
    return purged_total(purge_orphans())


def clear_caches() -> dict[str, Any]:
    try:
        cache.clear()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    else:
        return {"success": True, "message": "All caches cleared"}
