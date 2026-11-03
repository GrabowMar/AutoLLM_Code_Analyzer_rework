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
      - ``run:<run_id>`` / ``study:<study_id>`` (payloads in ``generation.contracts.events``)
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


# ── channels (SAMPLE_GEN_V3 §2.3, §9.5) ──────────────────────────────────
#   run:<uuid>                status | step | usage | trajectory_step | log | evaluation
#   study:<uuid>              run | tick | state | post_run | spend | budget_alert | stopped | paused
#   endpoint:<id>             endpoint | session   (owner/staff payload)
#   endpoint:<id>:public      the redacted variant for everyone else
#   capacity                  capacity (aggregate)

CAPACITY_CHANNEL = "capacity"


def run_channel(run_id) -> str:
    return f"run:{run_id}"


def study_channel(study_id) -> str:
    return f"study:{study_id}"


def endpoint_channel(endpoint_id, *, public: bool = False) -> str:
    return f"endpoint:{endpoint_id}:public" if public else f"endpoint:{endpoint_id}"


def _publish_checked(channel: str, event: dict, parse) -> None:
    """Publish ``event`` only when it matches its contract; a malformed event is logged, never sent."""
    try:
        parse(event)
    except Exception:
        logger.exception("Refusing to publish a malformed %s event on %s", event.get("type"), channel)
        return
    publish(channel, event)


def publish_run_event(run_id, event: dict) -> None:
    """Publish a ``run:<id>`` event (``contracts.events.RunEvent``).

    Types: status, step, usage, trajectory_step, log, evaluation.
    """
    from backend.generation.contracts.events import parse_run_event

    _publish_checked(run_channel(run_id), event, parse_run_event)


def publish_study_event(study_id, event: dict) -> None:
    """Publish a ``study:<id>`` event (``contracts.events.StudyEvent``: run, tick, state, post_run, spend, ...).

    Payloads must not carry other tenants' model names, IPs or hostnames.
    """
    from backend.generation.contracts.events import parse_study_event

    _publish_checked(study_channel(study_id), event, parse_study_event)


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
