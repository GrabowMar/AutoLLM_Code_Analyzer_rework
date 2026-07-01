"""SSE generator that subscribes to Redis pub/sub channels."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING
from typing import Any

import redis
from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 15  # seconds


def _build_sse(event_type: str, data: Any) -> str:
    """Format a single SSE message per the spec."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event_type}\ndata: {payload}\n\n"


def _heartbeat() -> str:
    return ": heartbeat\n\n"


def event_stream(channels: list[str]) -> Generator[str]:  # type: ignore[type-arg]
    """Yield SSE-formatted lines for the given Redis channels.

    Sends a heartbeat comment every ``HEARTBEAT_INTERVAL`` seconds so the
    connection stays alive through proxies.  Stops iteration cleanly when
    the caller stops consuming (``GeneratorExit``).
    """
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    try:
        pubsub.subscribe(*channels)
        last_heartbeat = time.monotonic()

        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

            if message is not None and message.get("type") == "message":
                raw_data = message.get("data", "{}")
                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError:
                    payload = {"raw": raw_data}

                event_type = payload.get("type", "update")
                yield _build_sse(event_type, payload)

            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                yield _heartbeat()
                last_heartbeat = now

    except GeneratorExit:
        pass
    except Exception:
        logger.exception("SSE stream error on channels %s", channels)
    finally:
        try:
            pubsub.unsubscribe()
            pubsub.close()
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("Error cleaning up SSE pubsub connection")
