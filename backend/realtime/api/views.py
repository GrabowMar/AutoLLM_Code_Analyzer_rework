"""Async Django view for SSE streaming (not Ninja — Ninja doesn't stream).

The view authorizes every channel (``realtime.authz``), closes its database
connection, then streams from Redis on the event loop, so an open browser tab
pins neither a worker thread nor a Postgres connection.
"""

from __future__ import annotations

import json
import logging

from asgiref.sync import sync_to_async
from django.db import connection
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.views.decorators.http import require_GET

from backend.realtime.authz import MAX_CHANNELS
from backend.realtime.authz import authorize_channels
from backend.realtime.subscribers import event_stream

logger = logging.getLogger(__name__)


def _json(status: int, detail: str) -> HttpResponse:
    return HttpResponse(json.dumps({"detail": detail}), status=status, content_type="application/json")


def _close_db() -> None:
    """Close this thread's connection.

    ``sync_to_async(connection.close)`` would be wrong: the bound method is
    resolved on the event-loop thread (whose wrapper this is not), and then
    called on the sync thread, which fails Django's thread-sharing check.
    """
    connection.close()


def _channels(request: HttpRequest) -> list[str]:
    """``?channels=a,b`` and ``?channels=a&channels=b`` both work."""
    channels: list[str] = []
    for entry in request.GET.getlist("channels"):
        channels.extend(c.strip() for c in entry.split(",") if c.strip())
    return channels


@transaction.non_atomic_requests
@require_GET
async def sse_stream(request: HttpRequest) -> HttpResponse:
    """GET /api/realtime/stream?channels=experiment:<uuid>,capacity

    401 when unauthenticated, 400 without channels or with more than
    ``MAX_CHANNELS``, 403 when any channel is malformed, unknown or not the
    caller's. Non-owners asking for ``endpoint:<id>`` receive its redacted
    ``:public`` variant.
    """
    user = await request.auser()
    if not user.is_authenticated:
        return _json(401, "Authentication required")
    channels = _channels(request)
    if not channels:
        return _json(400, "No channels specified")
    if len(channels) > MAX_CHANNELS:
        return _json(400, f"At most {MAX_CHANNELS} channels per stream")
    resolved = await sync_to_async(authorize_channels)(user, channels)
    # Never hold a Postgres connection for the life of a stream.
    await sync_to_async(_close_db)()
    if resolved is None:
        return _json(403, "Channel not allowed")

    response = StreamingHttpResponse(event_stream(resolved), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
