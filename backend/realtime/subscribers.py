"""Async SSE generator over Redis pub/sub (spec §F.6).

One module-level ``redis.asyncio`` connection pool per event loop is shared by
every stream (never ``from_url`` per request). A stream holds no database
connection: the view closes it after authorization, before the first await
on Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from typing import TYPE_CHECKING
from typing import Any

import redis.asyncio as aioredis
from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 15  # seconds
POLL_TIMEOUT = 1.0  # seconds per get_message wait

# Pools are bound to the loop that created their connections (uvicorn runs one;
# tests run several), so they are cached per loop.
_pools: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.ConnectionPool] = weakref.WeakKeyDictionary()


def get_pool() -> aioredis.ConnectionPool:
    """The shared async pool of the running event loop."""
    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    if pool is None:
        pool = aioredis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)
        _pools[loop] = pool
    return pool


def _build_sse(event_type: str, data: Any) -> str:
    """Format a single SSE message per the spec."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event_type}\ndata: {payload}\n\n"


def _heartbeat() -> str:
    return ": heartbeat\n\n"


def _message_to_sse(message: dict[str, Any]) -> str:
    raw = message.get("data", "{}")
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        payload = {"raw": raw}
    event_type = payload.get("type", "update") if isinstance(payload, dict) else "update"
    return _build_sse(event_type, payload)


async def event_stream(
    channels: list[str],
    *,
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
) -> AsyncIterator[str]:
    """Yield SSE frames for *channels* (already authorized) until the client goes away.

    A ``: heartbeat`` comment goes out every ``heartbeat_interval`` seconds so
    proxies keep the connection open. Cancellation (client disconnect)
    unsubscribes and closes the pub/sub connection.
    """
    client = aioredis.Redis(connection_pool=get_pool())
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(*channels)
        last_heartbeat = time.monotonic()
        yield _heartbeat()  # flushes headers through buffering proxies at once
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=POLL_TIMEOUT)
            if message is not None and message.get("type") == "message":
                yield _message_to_sse(message)
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                yield _heartbeat()
                last_heartbeat = now
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("SSE stream error on channels %s", channels)
    finally:
        try:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await client.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("Error cleaning up SSE pubsub connection")
