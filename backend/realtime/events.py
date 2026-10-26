"""Publish events to Redis pub/sub channels for SSE delivery."""

from __future__ import annotations

import json
import logging
from datetime import UTC
from datetime import datetime
from functools import lru_cache

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def publish(channel: str, event: dict) -> None:
    """Publish an event dict to a Redis pub/sub channel.

    Channel naming convention:
      - ``generation:<job_id>``
      - ``analysis:<task_id>``
      - ``runtime:<container_id>``
      - ``capacity`` (broadcast)
    """
    try:
        payload = json.dumps(
            {
                **event,
                "channel": channel,
                "published_at": datetime.now(UTC).isoformat(),
            },
        )
        _get_redis().publish(channel, payload)
    except Exception:
        logger.exception("Failed to publish SSE event to channel %s", channel)


# ── Experiments rework channels (spec §F.6) ────────────────────────────
#   experiment:<uuid>         job | tick | state | post_run
#   endpoint:<id>             endpoint | session   (owner/staff payload)
#   endpoint:<id>:public      the redacted variant for everyone else
#   capacity                  capacity (aggregate)

CAPACITY_CHANNEL = "capacity"


def experiment_channel(experiment_id) -> str:
    return f"experiment:{experiment_id}"


def endpoint_channel(endpoint_id, *, public: bool = False) -> str:
    return f"endpoint:{endpoint_id}:public" if public else f"endpoint:{endpoint_id}"


def publish_experiment_event(experiment_id, event: dict) -> None:
    """Publish ``event`` (``{"type": "job"|"tick"|"state"|"post_run", ...}``) on ``experiment:<id>``.

    Payloads must not carry other tenants' model names, IPs or hostnames.
    """
    publish(experiment_channel(experiment_id), event)


def publish_endpoint_event(endpoint_id, event: dict, public_event: dict | None = None) -> None:
    """Publish the owner/staff ``event`` on ``endpoint:<id>`` and a redacted copy on ``endpoint:<id>:public``.

    ``public_event`` is the counts-only variant (``endpoint``:
    ``{type, status, session_count, inflight, queued, loaded_count}``;
    ``session``: ``{type, event}``). When omitted, only ``type`` (and
    ``event``/``status`` if present) are copied, so no operational detail leaks.
    """
    publish(endpoint_channel(endpoint_id), event)
    if public_event is None:
        public_event = {key: event[key] for key in ("type", "event", "status") if key in event}
    publish(endpoint_channel(endpoint_id, public=True), public_event)


def publish_capacity(event: dict) -> None:
    """Publish an aggregate ``{"type": "capacity", lanes, executor, warnings}`` event on ``capacity``."""
    publish(CAPACITY_CHANNEL, {"type": "capacity", **event})
