"""Health and reachability checks for containers, Redis, and Celery."""

from __future__ import annotations

import time
from typing import Any

import docker
import redis as redis_lib
from celery import current_app as celery_app
from django.conf import settings


def container_health() -> list[dict[str, Any]]:
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]

    result = []
    for c in containers:
        health = "N/A"
        if c.attrs.get("State", {}).get("Health"):
            health = c.attrs["State"]["Health"].get("Status", "N/A")
        started = c.attrs.get("State", {}).get("StartedAt", "")
        image_tag = c.image.tags[0] if c.image.tags else str(c.image.short_id)
        result.append(
            {
                "id": c.short_id,
                "name": c.name,
                "image": image_tag,
                "status": c.status,
                "started": started,
                "health": health,
            },
        )
    return result


def redis_status() -> dict[str, Any]:
    url = getattr(settings, "REDIS_URL", "redis://redis:6379/0")
    try:
        client = redis_lib.from_url(url, socket_connect_timeout=2)
        start = time.monotonic()
        client.ping()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        info = client.info()
        return {
            "reachable": True,
            "ping_latency_ms": latency_ms,
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
            "total_commands_processed": info.get("total_commands_processed"),
            "redis_version": info.get("redis_version"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)}


def celery_status() -> dict[str, Any]:
    try:
        inspector = celery_app.control.inspect(timeout=2)
        active = inspector.active() or {}
        scheduled = inspector.scheduled() or {}
        reserved = inspector.reserved() or {}

        workers = list(active.keys())
        active_count = sum(len(v) for v in active.values())
        scheduled_count = sum(len(v) for v in scheduled.values())
        reserved_count = sum(len(v) for v in reserved.values())

        queue_lengths: dict[str, int] = {}
        try:
            url = getattr(settings, "REDIS_URL", "redis://redis:6379/0")
            rclient = redis_lib.from_url(url, socket_connect_timeout=2)
            for queue in ["celery", "default"]:
                length = rclient.llen(queue)
                if length:
                    queue_lengths[queue] = length
        except Exception as _queue_err:  # noqa: BLE001
            queue_lengths["error"] = str(_queue_err)

        return {
            "reachable": True,
            "worker_count": len(workers),
            "workers": workers,
            "active_tasks": active_count,
            "scheduled_tasks": scheduled_count,
            "reserved_tasks": reserved_count,
            "queue_lengths": queue_lengths,
        }
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "worker_count": 0, "error": str(exc)}
