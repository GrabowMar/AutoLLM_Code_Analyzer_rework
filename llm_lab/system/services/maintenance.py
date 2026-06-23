"""Maintenance actions: clear stuck records, purge orphans, clear caches."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import docker
from django.core.cache import cache
from django.utils import timezone

from llm_lab.analysis.models import AnalysisRun
from llm_lab.generation.models import GenerationJob
from llm_lab.runtime.models import ContainerInstance


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
    threshold = timezone.now() - timedelta(minutes=older_than_minutes)
    return GenerationJob.objects.filter(
        status__in=[GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING],
        updated_at__lt=threshold,
    ).update(
        status=GenerationJob.Status.FAILED,
        error_message=(f"Marked failed by maintenance: stuck for >{older_than_minutes}m"),
    )


def purge_orphan_containers() -> int:
    try:
        client = docker.from_env()
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

    return len(orphan_ids)


def clear_caches() -> dict[str, Any]:
    try:
        cache.clear()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    else:
        return {"success": True, "message": "All caches cleared"}
