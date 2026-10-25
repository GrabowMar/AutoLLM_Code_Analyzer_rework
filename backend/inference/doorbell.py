"""Redis doorbells for the LLM request queue (spec §D.2, §E.3). Synchronous side.

Redis never holds state here; every key only shortens a wait that the
database would otherwise answer at the next poll:

- ``inference:wake:<endpoint_id>``: a list trimmed to one element with a 60 s
  expiry. Relay polls and direct pumps ``BLPOP`` it; anything that queues or
  cancels a request for the endpoint pushes it.
- ``inference:done:<rid>``: a plain key (``SET … EX 600``) so *every* waiter of
  a request sees completion (several gateway retries can attach to one
  request), plus a ``PUBLISH inference:done <rid>`` so blocked waiters wake at
  once.

Every helper swallows Redis errors: losing Redis costs latency (callers fall
back to their 5 s database re-check), never correctness.

``settings.INFERENCE_REDIS_PREFIX`` (default ``""``) namespaces every key; tests
set it per test so concurrent suites sharing one Redis never see each other.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

WAKE_TTL_S = 60
DONE_TTL_S = 600


def _prefix() -> str:
    return getattr(settings, "INFERENCE_REDIS_PREFIX", "") or ""


def wake_key(endpoint_id) -> str:
    return f"{_prefix()}inference:wake:{endpoint_id}"


def done_key(rid) -> str:
    return f"{_prefix()}inference:done:{rid}"


def done_channel() -> str:
    return f"{_prefix()}inference:done"


def rate_key(token_id, second: int) -> str:
    return f"{_prefix()}inference:rl:{token_id}:{second}"


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    import redis

    # socket_timeout must exceed the longest blocking call below (5 s slices).
    return redis.from_url(settings.REDIS_URL, socket_timeout=10, socket_connect_timeout=2, decode_responses=True)


def client() -> redis.Redis:
    return _client()


def ring_endpoint(endpoint_id) -> None:
    """Wake whoever serves ``endpoint_id`` (relay poll or pump). Never raises."""
    key = wake_key(endpoint_id)
    try:
        pipe = _client().pipeline()
        pipe.lpush(key, 1)
        pipe.ltrim(key, 0, 0)
        pipe.expire(key, WAKE_TTL_S)
        pipe.execute()
    except Exception:  # noqa: BLE001 — pollers re-check the DB every few seconds anyway
        logger.debug("inference wake for endpoint %s failed", endpoint_id, exc_info=True)


def wait_endpoint(endpoint_id, timeout: float) -> bool:
    """Block up to ``timeout`` seconds on the endpoint doorbell; True if it rang.

    Falls back to sleeping on Redis errors so a caller never spins.
    """
    try:
        return _client().blpop([wake_key(endpoint_id)], timeout=max(1, int(timeout))) is not None
    except Exception:  # noqa: BLE001
        time.sleep(min(timeout, 5))
        return False


def signal_done(rid) -> None:
    """Mark request ``rid`` finished for every waiter. Never raises."""
    try:
        pipe = _client().pipeline()
        pipe.set(done_key(rid), 1, ex=DONE_TTL_S)
        pipe.publish(done_channel(), str(rid))
        pipe.execute()
    except Exception:  # noqa: BLE001
        logger.debug("inference done signal for %s failed", rid, exc_info=True)


def wait_done(rid, timeout: float) -> bool:
    """Wait up to ``timeout`` seconds for :func:`signal_done` of ``rid``; True if signalled.

    Subscribes first and checks the key after, so a signal between the two
    is never missed. On Redis errors it sleeps the slice and returns False;
    the caller re-reads the row either way.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    rid = str(rid)
    pubsub = None
    try:
        pubsub = _client().pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(done_channel())
        if _client().exists(done_key(rid)):
            return True
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            message = pubsub.get_message(timeout=min(remaining, 1.0))
            if message and message.get("type") == "message" and message.get("data") == rid:
                return True
    except Exception:  # noqa: BLE001
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(remaining, 5))
        return False
    finally:
        if pubsub is not None:
            try:
                pubsub.close()
            except Exception:  # noqa: BLE001
                logger.debug("closing inference pubsub failed", exc_info=True)


def is_done(rid) -> bool:
    try:
        return bool(_client().exists(done_key(rid)))
    except Exception:  # noqa: BLE001
        return False


def rate_limited(token_id, limit: int) -> bool:
    """True when ``token_id`` exceeded ``limit`` requests in the current second. Fails open."""
    key = rate_key(token_id, int(time.time()))
    try:
        pipe = _client().pipeline()
        pipe.incr(key)
        pipe.expire(key, 2)
        count, _ = pipe.execute()
    except Exception:  # noqa: BLE001
        return False
    return int(count) > limit
