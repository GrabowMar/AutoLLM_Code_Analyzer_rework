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
      - ``dashboard`` (broadcast)
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
