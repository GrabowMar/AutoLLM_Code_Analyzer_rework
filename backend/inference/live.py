"""Endpoint liveness: which relay session serves an endpoint and what it can run (spec §C.4, §F.3).

- relay: the endpoint's ``active`` ``RelaySession`` with ``ended_at IS NULL``
  and ``last_seen_at`` within ``RELAY_SESSION_TTL_S``; it serves the
  ``(name, digest)`` pairs in its ``models``.
- direct: the pump's last inventory (``last_inventory_at`` within
  ``DIRECT_INVENTORY_FRESH_S``) and its last error; it serves the endpoint's
  available models.
- system (OpenRouter): always online.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone

from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession

if TYPE_CHECKING:
    from django.db.models import QuerySet

# Direct pumps refresh inventory every 120 s; two missed rounds = offline.
DIRECT_INVENTORY_FRESH_S = 300


def session_cutoff():
    return timezone.now() - timedelta(seconds=int(settings.RELAY_SESSION_TTL_S))


def live_sessions(endpoint_ids=None) -> QuerySet[RelaySession]:
    qs = RelaySession.objects.filter(ended_at__isnull=True, last_seen_at__gt=session_cutoff())
    if endpoint_ids is not None:
        qs = qs.filter(endpoint_id__in=list(endpoint_ids))
    return qs


def active_session(endpoint: LLMEndpoint) -> RelaySession | None:
    return live_sessions([endpoint.pk]).filter(role=RelaySession.Role.ACTIVE).order_by("-last_seen_at").first()


def served_pairs(endpoint: LLMEndpoint, session: RelaySession | None = None) -> set[tuple[str, str]]:
    """``(served name, digest)`` pairs the endpoint can run right now (empty when offline)."""
    if endpoint.is_system:
        return set()
    if endpoint.connection == LLMEndpoint.Connection.RELAY:
        session = session or active_session(endpoint)
        if session is None:
            return set()
        return {
            (str(m.get("name") or ""), str(m.get("digest") or ""))
            for m in (session.models or [])
            if isinstance(m, dict) and m.get("name")
        }
    if not direct_online(endpoint):
        return set()
    from backend.llm_models.models import LLMModel

    return set(
        LLMModel.objects.filter(endpoint=endpoint, is_available=True).values_list("served_model_name", "digest"),
    )


def direct_online(endpoint: LLMEndpoint) -> bool:
    fresh = timezone.now() - timedelta(seconds=DIRECT_INVENTORY_FRESH_S)
    return endpoint.last_inventory_at is not None and endpoint.last_inventory_at > fresh


def endpoint_status(endpoint: LLMEndpoint, *, has_live_session: bool | None = None) -> str:
    """``online`` | ``offline`` | ``degraded`` | ``disabled`` (§F.3 ``EndpointSummary.status``)."""
    if not endpoint.enabled:
        return "disabled"
    if endpoint.is_system:
        return "online"
    if endpoint.connection == LLMEndpoint.Connection.RELAY:
        if has_live_session is None:
            has_live_session = active_session(endpoint) is not None
        return "online" if has_live_session else "offline"
    if not direct_online(endpoint):
        return "offline"
    return "degraded" if endpoint.last_error else "online"


def is_online(endpoint: LLMEndpoint) -> bool:
    return endpoint_status(endpoint) in ("online", "degraded")
