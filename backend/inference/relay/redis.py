"""The shared ``redis.asyncio`` client for async views (relay long-polls, SSE; spec §E.1).

One client (with its connection pool) per running event loop, created on
first use and reused by every request on that loop; never ``from_url`` per
request. uvicorn runs one loop, so in production this is a single pool.
(Connections are bound to the loop that created them, which is why test
helpers that spin up short-lived loops get their own client.)
"""

from __future__ import annotations

import asyncio
import weakref
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    import redis.asyncio as aioredis

_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis] = weakref.WeakKeyDictionary()


def get_async_redis() -> aioredis.Redis:
    """The ``redis.asyncio`` client of the running loop (``decode_responses=True``)."""
    import redis.asyncio as aioredis

    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            # Must exceed the longest BLPOP (5 s slices).
            socket_timeout=15,
            health_check_interval=30,
        )
        _clients[loop] = client
    return client
