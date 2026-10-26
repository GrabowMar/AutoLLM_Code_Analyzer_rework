"""``endpoint:<id>`` live events (spec §F.6).

``{type: "endpoint", status, sessions, inflight, queued, loaded, last_error}``
goes to the owner/staff channel, the counts-only variant ``{type, status,
session_count, inflight, queued, loaded_count}`` to ``endpoint:<id>:public``.
Published on relay connect/disconnect and inventory changes (and by the
executor's heartbeat when it wants to), throttled to one per endpoint every
:data:`THROTTLE_S` seconds unless forced.
"""

from __future__ import annotations

import logging

from django.db.models import Count

from backend.inference import doorbell

logger = logging.getLogger(__name__)

THROTTLE_S = 2


def loaded_summary(endpoint) -> list[dict]:
    """``[{name, size_vram, cpu_spill}]`` for the resident models (same shape as REST ``live.loaded``)."""
    out = []
    for item in endpoint.loaded_models or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        size, vram = item.get("size"), item.get("size_vram")
        out.append(
            {
                "name": item["name"],
                "size_vram": vram,
                "cpu_spill": bool(size and vram is not None and vram < size),
            },
        )
    return out


def endpoint_state(endpoint) -> tuple[dict, dict]:
    """``(owner payload, public payload)`` for ``endpoint``."""
    from backend.inference.live import endpoint_status
    from backend.inference.live import live_sessions
    from backend.inference.models import LLMRequest

    sessions = list(live_sessions([endpoint.pk]))
    counts = dict(
        LLMRequest.objects.filter(endpoint=endpoint, status__in=["queued", "leased"])
        .values("status")
        .annotate(n=Count("id"))
        .values_list("status", "n"),
    )
    has_active = any(s.role == "active" for s in sessions)
    status = endpoint_status(endpoint, has_live_session=has_active)
    loaded = loaded_summary(endpoint)
    owner = {
        "type": "endpoint",
        "status": status,
        "sessions": [
            {
                "id": str(s.pk),
                "role": s.role,
                "hostname": s.hostname,
                "relay_version": s.relay_version,
                "client_ip": s.client_ip,
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in sessions
        ],
        "inflight": counts.get("leased", 0),
        "queued": counts.get("queued", 0),
        "loaded": loaded,
        "last_error": endpoint.last_error or "",
    }
    public = {
        "type": "endpoint",
        "status": status,
        "session_count": len(sessions),
        "inflight": owner["inflight"],
        "queued": owner["queued"],
        "loaded_count": len(loaded),
    }
    return owner, public


def publish_endpoint_state(endpoint_id: int, *, force: bool = False) -> bool:
    """Publish the endpoint's state unless one went out in the last 2 s; never raises. True if published."""
    from backend.inference.models import LLMEndpoint
    from backend.realtime.events import publish_endpoint_event

    try:
        if not force:
            key = f"{doorbell._prefix()}inference:endpoint-event:{endpoint_id}"  # noqa: SLF001
            if not doorbell.client().set(key, 1, nx=True, ex=THROTTLE_S):
                return False
        endpoint = LLMEndpoint.objects.filter(pk=endpoint_id).first()
        if endpoint is None:
            return False
        owner, public = endpoint_state(endpoint)
        publish_endpoint_event(endpoint_id, owner, public)
    except Exception:  # noqa: BLE001 — live updates are best effort; pages poll as a fallback
        logger.debug("endpoint event for %s failed", endpoint_id, exc_info=True)
        return False
    return True
