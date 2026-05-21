"""In-memory cache of Docker container status with 30-second TTL."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_TTL = 30.0
_cache: dict[str, dict[str, Any]] = {}


def get(name: str) -> dict[str, Any]:
    """Return cached status, refreshing if stale or missing."""
    from llm_lab.runtime.services import docker_manager

    entry = _cache.get(name)
    if entry and (time.monotonic() - entry["fetched_at"]) < _TTL:
        return entry

    data = docker_manager.health(name)
    result: dict[str, Any] = {
        "status": data.get("status", "unknown"),
        "health": data.get("health", "unknown"),
        "fetched_at": time.monotonic(),
    }
    _cache[name] = result
    return result


def invalidate(name: str) -> None:
    """Remove a name from the cache."""
    _cache.pop(name, None)
