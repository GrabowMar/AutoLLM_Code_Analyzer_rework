"""System monitoring API — admin-only endpoints."""

from __future__ import annotations

from typing import Any

from ninja import Router
from ninja.errors import HttpError

from llm_lab.system import services

router = Router(tags=["system"])


def _assert_staff(request) -> None:
    if not (request.user and request.user.is_authenticated and request.user.is_staff):
        raise HttpError(403, "Forbidden: staff only")


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@router.get("/", response=dict)
def system_snapshot(request) -> Any:
    _assert_staff(request)
    return {
        "host": services.host_metrics(),
        "containers": services.container_health(),
        "redis": services.redis_status(),
        "celery": services.celery_status(),
        "db": services.db_stats(),
        "app_stats": services.app_stats(),
    }


# ---------------------------------------------------------------------------
# Individual metric endpoints
# ---------------------------------------------------------------------------


@router.get("/host", response=dict)
def get_host(request) -> Any:
    _assert_staff(request)
    return services.host_metrics()


@router.get("/containers", response=list)
def get_containers(request) -> Any:
    _assert_staff(request)
    return services.container_health()


@router.get("/redis", response=dict)
def get_redis(request) -> Any:
    _assert_staff(request)
    return services.redis_status()


@router.get("/celery", response=dict)
def get_celery(request) -> Any:
    _assert_staff(request)
    return services.celery_status()


@router.get("/db", response=dict)
def get_db(request) -> Any:
    _assert_staff(request)
    return services.db_stats()


@router.get("/app-stats", response=dict)
def get_app_stats(request) -> Any:
    _assert_staff(request)
    return services.app_stats()


# ---------------------------------------------------------------------------
# Maintenance actions
# ---------------------------------------------------------------------------


@router.post("/maintenance/clear-stuck-analysis", response=dict)
def maintenance_clear_stuck_analysis(request, older_than_minutes: int = 60) -> Any:
    _assert_staff(request)
    count = services.clear_stuck_analysis_tasks(older_than_minutes=older_than_minutes)
    return {"updated": count}


@router.post("/maintenance/clear-stuck-generation", response=dict)
def maintenance_clear_stuck_generation(request, older_than_minutes: int = 60) -> Any:
    _assert_staff(request)
    count = services.clear_stuck_generation_jobs(older_than_minutes=older_than_minutes)
    return {"updated": count}


@router.post("/maintenance/purge-orphan-containers", response=dict)
def maintenance_purge_orphan_containers(request) -> Any:
    _assert_staff(request)
    count = services.purge_orphan_containers()
    return {"purged": count}


@router.post("/maintenance/clear-caches", response=dict)
def maintenance_clear_caches(request) -> Any:
    _assert_staff(request)
    return services.clear_caches()
