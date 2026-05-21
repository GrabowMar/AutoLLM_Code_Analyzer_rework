"""Plain Django view for SSE streaming (not Ninja — Ninja doesn't stream)."""

from __future__ import annotations

import logging

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.views.decorators.http import require_GET

from llm_lab.realtime.subscribers import event_stream

logger = logging.getLogger(__name__)


@require_GET
def sse_stream(request: HttpRequest) -> HttpResponse:
    """GET /api/realtime/stream?channels=generation:1,analysis:2

    Accepts ``channels`` as a comma-separated query param or as repeated params.
    Requires the user to be authenticated; returns 401 otherwise.
    """
    if not request.user.is_authenticated:
        return HttpResponse(
            '{"detail": "Authentication required"}',
            status=401,
            content_type="application/json",
        )

    raw = request.GET.getlist("channels")
    # Support both ?channels=a,b and ?channels=a&channels=b
    channels: list[str] = []
    for entry in raw:
        channels.extend(c.strip() for c in entry.split(",") if c.strip())

    if not channels:
        return HttpResponse(
            '{"detail": "No channels specified"}',
            status=400,
            content_type="application/json",
        )

    response = StreamingHttpResponse(
        event_stream(channels),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
