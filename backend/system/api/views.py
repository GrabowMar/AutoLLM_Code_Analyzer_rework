"""System monitoring API — admin-only endpoints."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from ninja import Router
from ninja import Schema
from ninja.errors import HttpError

from backend.system import services
from backend.system.services import footprint

router = Router(tags=["system"])


def _assert_staff(request) -> None:
    # ``request.auth`` is the user resolved by whichever API auth scheme
    # matched (TokenAuth or SessionAuth); ``request.user`` is only set by the
    # session middleware, so Bearer callers would always be refused.
    user = getattr(request, "auth", None)
    if not (user is not None and getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False)):
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
    count = services.clear_stuck_analysis_runs(older_than_minutes=older_than_minutes)
    return {"updated": count}


@router.post("/maintenance/clear-stuck-generation", response=dict)
def maintenance_clear_stuck_generation(request, older_than_minutes: int = 60) -> Any:
    _assert_staff(request)
    count = services.clear_stuck_generation_runs(older_than_minutes=older_than_minutes)
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


# ---------------------------------------------------------------------------
# App footprint (preview apps on the apps daemon)
# ---------------------------------------------------------------------------


class FootprintSettingsIn(Schema):
    idle_stop_enabled: bool | None = None
    idle_stop_minutes: int | None = None
    max_running_apps: int | None = None
    app_mem_limit_mb: int | None = None
    auto_prune_enabled: bool | None = None
    auto_prune_interval_hours: int | None = None


@router.get("/footprint", response=dict)
def footprint_overview(request) -> Any:
    _assert_staff(request)
    return footprint.overview()


@router.patch("/footprint/settings", response=dict)
def footprint_update_settings(request, payload: FootprintSettingsIn) -> Any:
    _assert_staff(request)
    try:
        return footprint.update_settings(payload.dict(exclude_unset=True), request.auth)
    except ValidationError as exc:
        raise HttpError(422, "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())) from exc


@router.post("/footprint/stop-all", response=dict)
def footprint_stop_all(request) -> Any:
    _assert_staff(request)
    return footprint.stop_all_apps(request.auth)


@router.post("/footprint/remove", response=dict)
def footprint_remove(request, include_running: bool = False) -> Any:
    _assert_staff(request)
    return footprint.remove_apps(request.auth, include_running=include_running)


@router.post("/footprint/stop-idle", response=dict)
def footprint_stop_idle(request, idle_minutes: int = 60) -> Any:
    _assert_staff(request)
    if idle_minutes < 1:
        raise HttpError(422, "idle_minutes must be at least 1")
    return footprint.stop_idle_apps(idle_minutes=idle_minutes)


@router.post("/footprint/apply-mem-limit", response=dict)
def footprint_apply_mem_limit(request) -> Any:
    _assert_staff(request)
    return footprint.apply_mem_limit()


@router.post("/footprint/prune", response=dict)
def footprint_prune(request, aggressive: bool = False) -> Any:
    _assert_staff(request)
    result = footprint.prune_apps_daemon(aggressive=aggressive)
    if result.get("busy"):
        raise HttpError(409, "A prune is already running")
    return result
