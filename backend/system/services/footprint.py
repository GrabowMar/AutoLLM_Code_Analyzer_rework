"""Keep the preview apps' footprint small: bulk stop/remove, idle sweep, memory caps, image pruning.

"Apps" are ``ContainerInstance`` rows except analyzer workspaces (those belong to
analysis runs). Every stop/remove goes through ``container_service`` actions so
DB status, routes, ports and live status events stay consistent with a manual
click. Image pruning only ever talks to the apps daemon (``APPS_DOCKER_HOST``)
and never force-removes: Docker itself refuses images a container still uses.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Max
from django.db.models import Q
from django.utils import timezone

from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.services import docker_manager
from backend.system.models import FootprintSettings

logger = logging.getLogger(__name__)

_S = ContainerInstance.Status
_ACCESS_KEY = "system:app_last_access:{}"
_ACCESS_THROTTLE_KEY = "system:app_access_throttle:{}"
_ACCESS_THROTTLE_S = 60
_ACCESS_TTL_S = 30 * 24 * 3600
_SWEEP_LOCK = "system:footprint_sweep_lock"
_PRUNE_LOCK = "system:footprint_prune_lock"
_STATS_CACHE = "system:apps_daemon_stats"
_DF_CACHE = "system:apps_daemon_df"
_OPEN_ACTION = (ContainerAction.Status.PENDING, ContainerAction.Status.RUNNING)
_STARTED_ACTIONS = (
    ContainerAction.ActionType.BUILD,
    ContainerAction.ActionType.START,
    ContainerAction.ActionType.RESTART,
)
_MB = 1024 * 1024
_TOOL_TAG_RE = re.compile(r"llmlab/[a-z0-9._-]+:[0-9a-f]{12}")
_SAMPLE_REPO = "llmlab/sample"
_LEGACY_APP_IMAGE_RE = re.compile(r"^backend/(llm-[0-9a-f]+):latest$")

SETTINGS_FIELDS = (
    "idle_stop_enabled",
    "idle_stop_minutes",
    "max_running_apps",
    "app_mem_limit_mb",
    "auto_prune_enabled",
    "auto_prune_interval_hours",
)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings_dict(obj: FootprintSettings | None = None) -> dict[str, Any]:
    obj = obj or FootprintSettings.get()
    data = {f: getattr(obj, f) for f in SETTINGS_FIELDS}
    data["last_auto_prune_at"] = obj.last_auto_prune_at
    data["last_sweep_at"] = obj.last_sweep_at
    data["last_sweep_result"] = obj.last_sweep_result
    data["updated_at"] = obj.updated_at
    return data


def update_settings(changes: dict[str, Any], user=None) -> dict[str, Any]:
    """Apply the given fields (validated by the model's validators); unknown keys are ignored."""
    obj = FootprintSettings.get()
    for field in SETTINGS_FIELDS:
        if field in changes and changes[field] is not None:
            setattr(obj, field, changes[field])
    obj.updated_by = user if getattr(user, "pk", None) else None
    obj.full_clean()
    obj.save()
    return settings_dict(obj)


def app_mem_limit() -> str:
    """Docker ``mem_limit`` for a new preview app (falls back to 512m when the table is unavailable)."""
    try:
        return f"{FootprintSettings.get().app_mem_limit_mb}m"
    except Exception:  # noqa: BLE001 - never block an app start on the settings row
        logger.warning("Footprint settings unavailable; using the 512m app memory limit", exc_info=True)
        return "512m"


# ---------------------------------------------------------------------------
# Last use (fed by the app proxy)
# ---------------------------------------------------------------------------


def record_app_access(name: str) -> None:
    """Note that someone opened app *name* (at most one cache write per app per minute)."""
    try:
        if cache.add(_ACCESS_THROTTLE_KEY.format(name), 1, _ACCESS_THROTTLE_S):
            cache.set(_ACCESS_KEY.format(name), time.time(), _ACCESS_TTL_S)
    except Exception:  # noqa: BLE001 - access tracking must never break proxying
        logger.debug("Could not record access to app %s", name, exc_info=True)


def _apps():
    # ``exclude(metadata__kind=...)`` would also drop rows without the key (NULL logic); containment can't.
    return ContainerInstance.objects.exclude(metadata__contains={"kind": "analyzer-workspace"})


def _last_used(containers: list[ContainerInstance]) -> dict[Any, datetime]:
    """Latest of: proxy access, last completed build/start/restart, creation."""
    if not containers:
        return {}
    accessed = cache.get_many([_ACCESS_KEY.format(c.name) for c in containers])
    started = dict(
        ContainerAction.objects.filter(
            container_id__in=[c.pk for c in containers],
            action_type__in=_STARTED_ACTIONS,
            status=ContainerAction.Status.COMPLETED,
        )
        .values("container_id")
        .annotate(last=Max("completed_at"))
        .values_list("container_id", "last"),
    )
    result: dict[Any, datetime] = {}
    for c in containers:
        candidates = [c.created_at]
        ts = accessed.get(_ACCESS_KEY.format(c.name))
        if ts:
            candidates.append(datetime.fromtimestamp(float(ts), tz=timezone.get_current_timezone()))
        if started.get(c.pk):
            candidates.append(started[c.pk])
        result[c.pk] = max(x for x in candidates if x is not None)
    return result


def _busy_ids() -> set[Any]:
    """Containers with an action in flight (never stopped/removed under it)."""
    return set(
        ContainerAction.objects.filter(status__in=_OPEN_ACTION, container__isnull=False).values_list(
            "container_id",
            flat=True,
        ),
    )


def _human_eval_ids() -> set[Any]:
    """Apps an open human-evaluation task hosts (its own reaper manages them; pinned ones are never stopped)."""
    try:
        from backend.human_eval.models import EvalTaskApp
        from backend.human_eval.services.hosting import _CLOSED_TASK
    except Exception:  # noqa: BLE001 - module absent: nothing to protect
        return set()
    return set(
        EvalTaskApp.objects.filter(container__isnull=False)
        .exclude(task__status__in=_CLOSED_TASK)
        .filter(Q(pinned=True) | Q(started_by_task=True))
        .values_list("container_id", flat=True),
    )


# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------


def _dispatch(containers, action: str, user) -> int:
    from backend.runtime.services import container_service

    fn = container_service.stop_instance if action == "stop" else container_service.remove_instance
    count = 0
    for c in containers:
        try:
            fn(c, user)
            count += 1
        except Exception:
            logger.exception("Could not %s app %s", action, c.name)
    return count


def stop_all_apps(user=None) -> dict[str, int]:
    """Stop every running preview app (apps with an action in flight are skipped)."""
    busy = _busy_ids()
    running = list(_apps().filter(status=_S.RUNNING))
    targets = [c for c in running if c.pk not in busy]
    return {"stopping": _dispatch(targets, "stop", user), "skipped": len(running) - len(targets)}


def remove_apps(user=None, *, include_running: bool = False) -> dict[str, int]:
    """Remove stopped/failed preview apps (and running ones too with *include_running*)."""
    statuses = [_S.STOPPED, _S.FAILED]
    if include_running:
        statuses.append(_S.RUNNING)
    busy = _busy_ids()
    candidates = list(_apps().filter(status__in=statuses))
    targets = [c for c in candidates if c.pk not in busy]
    return {"removing": _dispatch(targets, "remove", user), "skipped": len(candidates) - len(targets)}


def stop_idle_apps(*, idle_minutes: int | None, max_running: int = 0, now: datetime | None = None) -> dict[str, Any]:
    """Stop running apps unused for *idle_minutes*, then the least recently used above *max_running*.

    Apps with an action in flight and apps hosted by an open human-evaluation
    task are never touched. Returns ``{"idle": n, "over_cap": m, "stopped": [names]}``.
    """
    now = now or timezone.now()
    protected = _busy_ids() | _human_eval_ids()
    running = [c for c in _apps().filter(status=_S.RUNNING) if c.pk not in protected]
    last = _last_used(running)
    idle: list[ContainerInstance] = []
    if idle_minutes:
        cutoff = now - timedelta(minutes=idle_minutes)
        idle = [c for c in running if last[c.pk] < cutoff]
    remaining = sorted((c for c in running if c not in idle), key=lambda c: last[c.pk])
    over_cap: list[ContainerInstance] = []
    if max_running:
        # Protected apps still count toward the cap; they just can't be the ones stopped.
        still_running = len(remaining) + (_apps().filter(status=_S.RUNNING).count() - len(running))
        excess = max(0, still_running - max_running)
        over_cap = remaining[:excess]
    _dispatch(idle, "stop", None)
    _dispatch(over_cap, "stop", None)
    return {"idle": len(idle), "over_cap": len(over_cap), "stopped": [c.name for c in idle + over_cap]}


def apply_mem_limit() -> dict[str, Any]:
    """Apply the configured per-app memory limit to running apps now (no restart)."""
    limit_mb = FootprintSettings.get().app_mem_limit_mb
    client = docker_manager.new_client()
    updated, failed = 0, []
    for c in _apps().filter(status=_S.RUNNING):
        try:
            client.containers.get(c.name).update(mem_limit=f"{limit_mb}m", memswap_limit=f"{limit_mb * 2}m")
            updated += 1
        except Exception as exc:  # noqa: BLE001 - e.g. limit below current usage
            failed.append({"name": c.name, "error": str(exc)[:200]})
    return {"limit_mb": limit_mb, "updated": updated, "failed": failed}


# ---------------------------------------------------------------------------
# Image pruning (apps daemon)
# ---------------------------------------------------------------------------


def _pinned_tool_tags() -> set[str]:
    """Tool image tags that queued/running generation runs recorded (they refuse to run on another tag)."""
    try:
        from backend.generation.models import GenerationRun
        from backend.generation.models import RunManifest
    except Exception:  # noqa: BLE001
        return set()
    active = ("pending", "running")
    blobs: list[Any] = list(RunManifest.objects.filter(run__status__in=active).values_list("manifest", flat=True))
    blobs += list(GenerationRun.objects.filter(status__in=active).values_list("execution", flat=True))
    return set(_TOOL_TAG_RE.findall(json.dumps(blobs, default=str)))


def _image_layers_size(client) -> int:
    try:
        return int(client.df().get("LayersSize") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _try_remove(client, ref: str, stats: dict[str, Any]) -> None:
    try:
        client.images.remove(ref, force=False)
        stats["removed_images"] += 1
    except Exception as exc:  # noqa: BLE001 - in use (409) or already gone
        msg = str(exc)
        if "409" in msg or "conflict" in msg.lower() or "being used" in msg:
            stats["in_use"] += 1
        elif "404" not in msg:
            stats["errors"].append(f"{ref}: {msg[:160]}")


def prune_apps_daemon(*, aggressive: bool = False) -> dict[str, Any]:
    """Free disk on the apps daemon without breaking anything that is still needed.

    Safe (default):
      * sample build cache: its own TTL/quota eviction;
      * old versions of ``llmlab/<tool>`` images — the newest tag per image and
        any tag a queued/running run pinned are kept;
      * legacy ``backend/llm-*`` images whose app is gone;
      * dangling images, stopped containers nobody manages, the classic build cache.
    Aggressive additionally removes every other unused image (base images such
    as node/python/golang are pulled again on the next build).
    """
    if not cache.add(_PRUNE_LOCK, 1, 30 * 60):
        return {"busy": True}
    try:
        return _prune(aggressive=aggressive)
    finally:
        cache.delete(_PRUNE_LOCK)
        cache.delete(_DF_CACHE)


def _prune(*, aggressive: bool) -> dict[str, Any]:
    client = docker_manager.new_client()
    before = _image_layers_size(client)
    stats: dict[str, Any] = {"removed_images": 0, "removed_containers": 0, "in_use": 0, "errors": []}

    try:
        from backend.runtime.services import sample_builds

        sample = sample_builds.cleanup_sample_images(client=client)
        stats["sample_cache_removed"] = int(sample.get("removed", 0))
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"sample cache: {str(exc)[:160]}")

    # Stopped containers nobody manages (left over from manual runs / crashed tools).
    for ct in client.containers.list(all=True, filters={"status": ["exited", "created", "dead"]}):
        labels = ct.labels or {}
        if labels.get("backend.managed") == "true" or ct.name.startswith("buildx_buildkit_"):
            continue
        try:
            ct.remove()
            stats["removed_containers"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"container {ct.name}: {str(exc)[:160]}")

    pinned = _pinned_tool_tags()
    live_app_names = set(_apps().exclude(status=_S.REMOVED).values_list("name", flat=True))
    newest: dict[str, tuple[str, str]] = {}
    images = client.images.list()
    for image in images:
        created = image.attrs.get("Created", "")
        for tag in image.tags:
            repo = tag.rsplit(":", 1)[0]
            if repo.startswith("llmlab/") and repo != _SAMPLE_REPO and _TOOL_TAG_RE.fullmatch(tag):
                if repo not in newest or created > newest[repo][0]:
                    newest[repo] = (created, tag)
    keep = pinned | {tag for _created, tag in newest.values()}

    for image in images:
        for tag in image.tags:
            repo = tag.rsplit(":", 1)[0]
            if repo == _SAMPLE_REPO or tag in keep:
                continue
            if _TOOL_TAG_RE.fullmatch(tag) and repo.startswith("llmlab/"):
                _try_remove(client, tag, stats)
                continue
            legacy = _LEGACY_APP_IMAGE_RE.match(tag)
            if legacy and legacy.group(1) not in live_app_names:
                _try_remove(client, tag, stats)
                continue
            if aggressive and not repo.startswith("llmlab/"):
                _try_remove(client, tag, stats)

    try:
        client.images.prune(filters={"dangling": True})
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"dangling: {str(exc)[:160]}")
    try:
        built = client.api.prune_builds()
        stats["build_cache_reclaimed"] = int((built or {}).get("SpaceReclaimed") or 0)
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"build cache: {str(exc)[:160]}")

    stats["reclaimed_bytes"] = max(0, before - _image_layers_size(client)) + stats.get("build_cache_reclaimed", 0)
    stats["aggressive"] = aggressive
    return stats


# ---------------------------------------------------------------------------
# Sweep (Celery beat)
# ---------------------------------------------------------------------------


def sweep(*, now: datetime | None = None) -> dict[str, Any]:
    """One footprint tick: idle stop + running cap, and the periodic prune when due."""
    now = now or timezone.now()
    if not cache.add(_SWEEP_LOCK, 1, 10 * 60):
        return {"busy": True}
    try:
        cfg = FootprintSettings.get()
        result: dict[str, Any] = {}
        if cfg.idle_stop_enabled or cfg.max_running_apps:
            result.update(
                stop_idle_apps(
                    idle_minutes=cfg.idle_stop_minutes if cfg.idle_stop_enabled else None,
                    max_running=cfg.max_running_apps,
                    now=now,
                ),
            )
        due = cfg.last_auto_prune_at is None or cfg.last_auto_prune_at < now - timedelta(
            hours=cfg.auto_prune_interval_hours,
        )
        if cfg.auto_prune_enabled and due:
            pruned = prune_apps_daemon(aggressive=False)
            if not pruned.get("busy"):
                result["pruned_bytes"] = pruned.get("reclaimed_bytes", 0)
                FootprintSettings.objects.filter(pk=cfg.pk).update(last_auto_prune_at=now)
        FootprintSettings.objects.filter(pk=cfg.pk).update(last_sweep_at=now, last_sweep_result=result)
        return result
    finally:
        cache.delete(_SWEEP_LOCK)


# ---------------------------------------------------------------------------
# Overview (System page)
# ---------------------------------------------------------------------------


def _container_mem(container) -> dict[str, int] | None:
    try:
        raw = container.stats(stream=False, one_shot=True)
    except Exception:  # noqa: BLE001
        return None
    mem = raw.get("memory_stats") or {}
    usage = int(mem.get("usage") or 0)
    inner = mem.get("stats") or {}
    # Same figure as `docker stats`: page cache that can be dropped doesn't count.
    usage -= int(inner.get("inactive_file") or inner.get("total_inactive_file") or 0)
    return {"usage": max(usage, 0), "limit": int(mem.get("limit") or 0)}


def _daemon_stats(client) -> dict[str, dict[str, Any]]:
    cached = cache.get(_STATS_CACHE)
    if cached is not None:
        return cached
    running = client.containers.list()
    with ThreadPoolExecutor(max_workers=8) as pool:
        mems = list(pool.map(_container_mem, running))
    res = {}
    for ct, mem in zip(running, mems, strict=True):
        labels = ct.labels or {}
        res[ct.name] = {
            "name": ct.name,
            "managed": labels.get("backend.managed") == "true",
            "kind": labels.get("backend.kind", ""),
            "mem_usage": (mem or {}).get("usage"),
            "mem_limit": (mem or {}).get("limit"),
        }
    cache.set(_STATS_CACHE, res, 15)
    return res


def _daemon_df(client) -> dict[str, Any]:
    cached = cache.get(_DF_CACHE)
    if cached is not None:
        return cached
    df = client.df()
    images = df.get("Images") or []
    build = df.get("BuildCache") or []
    volumes = df.get("Volumes") or []
    containers = df.get("Containers") or []
    res = {
        "images_count": len(images),
        "images_bytes": int(df.get("LayersSize") or 0),
        "images_unused_count": sum(1 for i in images if not i.get("Containers")),
        # Unique bytes only (Size - SharedSize), as `docker system df` counts "reclaimable".
        "images_unused_bytes": sum(
            max(0, int(i.get("Size") or 0) - max(0, int(i.get("SharedSize") or 0)))
            for i in images
            if not i.get("Containers")
        ),
        "build_cache_bytes": sum(int(b.get("Size") or 0) for b in build),
        "volumes_bytes": sum(int((v.get("UsageData") or {}).get("Size") or 0) for v in volumes),
        "containers_count": len(containers),
        "containers_bytes": sum(int(c.get("SizeRw") or 0) for c in containers),
    }
    cache.set(_DF_CACHE, res, 120)
    return res


def overview() -> dict[str, Any]:
    """Apps, their memory and the apps daemon's disk, for the System page."""
    from django.conf import settings

    now = timezone.now()
    apps = list(_apps().exclude(status=_S.REMOVED).select_related("created_by").order_by("-created_at"))
    by_status: dict[str, int] = {}
    for c in apps:
        by_status[c.status] = by_status.get(c.status, 0) + 1

    daemon: dict[str, Any] = {"reachable": True, "mem_budget": getattr(settings, "APPS_DIND_MEM_LIMIT", "")}
    stats: dict[str, dict[str, Any]] = {}
    try:
        client = docker_manager.new_client()
        stats = _daemon_stats(client)
        daemon["disk"] = _daemon_df(client)
    except Exception as exc:  # noqa: BLE001
        daemon = {"reachable": False, "error": str(exc)[:300], "mem_budget": daemon["mem_budget"]}

    last = _last_used([c for c in apps if c.status == _S.RUNNING])
    busy = _busy_ids()
    human_eval = _human_eval_ids()
    rows = []
    for c in apps:
        st = stats.get(c.name, {})
        used = last.get(c.pk)
        rows.append(
            {
                "id": str(c.pk),
                "name": c.name,
                "status": c.status,
                "owner": getattr(c.created_by, "username", "") or "",
                "run_id": str(c.generation_job_id) if c.generation_job_id else None,
                "created_at": c.created_at,
                "last_used_at": used,
                "idle_seconds": int((now - used).total_seconds()) if used else None,
                "mem_usage": st.get("mem_usage"),
                "mem_limit": st.get("mem_limit"),
                "busy": c.pk in busy,
                "human_eval": c.pk in human_eval,
            },
        )

    others = [s for name, s in stats.items() if name not in {c.name for c in apps}]
    daemon["apps_mem_bytes"] = sum(r["mem_usage"] or 0 for r in rows)
    daemon["other_mem_bytes"] = sum(s.get("mem_usage") or 0 for s in others)
    daemon["other_running"] = [
        {"name": s["name"], "kind": s["kind"] or ("managed" if s["managed"] else ""), "mem_usage": s["mem_usage"]}
        for s in sorted(others, key=lambda s: -(s.get("mem_usage") or 0))
    ]
    return {"settings": settings_dict(), "counts": by_status, "apps": rows, "daemon": daemon}
