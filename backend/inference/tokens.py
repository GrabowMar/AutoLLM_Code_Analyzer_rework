"""Relay tokens and sessions (spec §E.1, §E.3).

Token format ``llr_<prefix8>.<secret43>``: the prefix (``llr_`` + 8 base32
characters) is stored and shown in the UI; only ``sha256(secret)`` is stored.
Authentication looks the prefix up among tokens that are neither revoked
nor expired and compares the hash in constant time. An endpoint's
``enabled`` flag is not part of authentication.

Sessions: v1 allows one ``active`` session per endpoint (DB constraint);
sessions of other ``relay_id`` s are hot standby and are promoted when the
active one ends.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "llr_"  # noqa: S105 — a public prefix, not a secret
LAST_USED_THROTTLE_S = 60
_DUMMY_HASH = hashlib.sha256(b"llmlab-relay-dummy").hexdigest()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _new_prefix() -> str:
    raw = base64.b32encode(secrets.token_bytes(5)).decode().lower()
    return f"{TOKEN_PREFIX}{raw[:8]}"


def generate_token(
    endpoint: LLMEndpoint,
    *,
    label: str = "",
    created_by=None,
) -> tuple[str, RelayToken]:
    """Create a token; returns ``(raw token shown once, row)``."""
    for _ in range(5):
        prefix = _new_prefix()
        secret = secrets.token_urlsafe(32)
        try:
            with transaction.atomic():
                token = RelayToken.objects.create(
                    endpoint=endpoint,
                    label=label[:80],
                    prefix=prefix,
                    secret_hash=_hash(secret),
                    created_by=created_by,
                )
        except IntegrityError:
            continue
        return f"{prefix}.{secret}", token
    msg = "could not allocate a unique relay token prefix"
    raise RuntimeError(msg)


def authenticate(raw: str | None) -> RelayToken | None:
    """The live token for ``raw`` (``llr_xxxxxxxx.secret``) or None; constant time on the secret."""
    raw = (raw or "").strip()
    prefix, _, secret = raw.partition(".")
    now = timezone.now()
    token = None
    if prefix.startswith(TOKEN_PREFIX) and secret and len(prefix) <= 16:
        token = (
            RelayToken.objects.select_related("endpoint")
            .filter(prefix=prefix, revoked_at__isnull=True)
            .exclude(expires_at__lte=now)
            .first()
        )
    expected = token.secret_hash if token is not None else _DUMMY_HASH
    if not hmac.compare_digest(_hash(secret or "x"), expected) or token is None:
        return None
    if token.last_used_at is None or token.last_used_at < now - timedelta(seconds=LAST_USED_THROTTLE_S):
        RelayToken.objects.filter(pk=token.pk).update(last_used_at=now)
    return token


def bearer(header: str | None) -> str:
    header = header or ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def rotate(token: RelayToken, *, grace_minutes: int, user=None) -> tuple[str, RelayToken, datetime]:
    """New token for the same endpoint; the old one expires after ``grace_minutes``."""
    raw, new = generate_token(token.endpoint, label=token.label, created_by=user)
    expires = timezone.now() + timedelta(minutes=grace_minutes)
    RelayToken.objects.filter(pk=token.pk).update(expires_at=expires)
    if grace_minutes == 0:
        end_token_sessions(token, reason="token_rotated")
    return raw, new, expires


def revoke(token: RelayToken) -> dict[str, Any]:
    """Revoke ``token`` now: its sessions end and their leases return to the queue."""
    now = timezone.now()
    RelayToken.objects.filter(pk=token.pk, revoked_at__isnull=True).update(revoked_at=now)
    sessions, requeued = end_token_sessions(token, reason="token_revoked")
    return {"revoked_at": now, "sessions_ended": sessions, "requests_requeued": requeued}


def end_token_sessions(token: RelayToken, *, reason: str) -> tuple[int, int]:
    ended = requeued = 0
    for session in RelaySession.objects.filter(token=token, ended_at__isnull=True):
        requeued += end_session(session, reason=reason)
        ended += 1
    return ended, requeued


# ── Sessions ────────────────────────────────────────────────────────────


def _publish_session(session: RelaySession, event: str) -> None:
    from backend.realtime.events import publish_endpoint_event

    publish_endpoint_event(
        session.endpoint_id,
        {
            "type": "session",
            "event": event,
            "role": session.role,
            "hostname": session.hostname,
            "relay_version": session.relay_version,
        },
        {"type": "session", "event": event},
    )
    from backend.inference.events import publish_endpoint_state

    publish_endpoint_state(session.endpoint_id, force=True)


def promote_standby(endpoint_id: int) -> RelaySession | None:
    """Promote the most recently seen live standby session when no live active session exists."""
    from backend.inference.live import live_sessions

    with transaction.atomic():
        LLMEndpoint.objects.select_for_update().filter(pk=endpoint_id).first()
        live = live_sessions([endpoint_id])
        if live.filter(role=RelaySession.Role.ACTIVE).exists():
            return None
        # A dead "active" row (not ended) would block the unique constraint.
        stale_active = RelaySession.objects.filter(
            endpoint_id=endpoint_id,
            ended_at__isnull=True,
            role=RelaySession.Role.ACTIVE,
        )
        for stale in stale_active:
            end_session(stale, reason="timeout", promote=False)
        candidate = live.filter(role=RelaySession.Role.STANDBY).order_by("-last_seen_at").first()
        if candidate is None:
            return None
        RelaySession.objects.filter(pk=candidate.pk).update(role=RelaySession.Role.ACTIVE)
        candidate.role = RelaySession.Role.ACTIVE
    transaction.on_commit(lambda: _publish_session(candidate, "promoted"))
    return candidate


def end_session(session: RelaySession, *, reason: str, bump_attempt: bool = False, promote: bool = True) -> int:
    """End ``session`` (idempotent), requeue its leases now and promote a standby; returns the requeued count.

    ``promote=False`` when the same relay is reconnecting (it takes the active role back itself).
    """
    from backend.inference.requests import requeue_session

    now = timezone.now()
    changed = RelaySession.objects.filter(pk=session.pk, ended_at__isnull=True).update(
        ended_at=now,
        ended_reason=reason[:40],
    )
    requeued = requeue_session(session, bump_attempt=bump_attempt, reason=reason)
    if changed:
        session.ended_at = now
        session.ended_reason = reason
        transaction.on_commit(lambda: _publish_session(session, "disconnected"))
        if promote and session.role == RelaySession.Role.ACTIVE:
            promote_standby(session.endpoint_id)
    return requeued


def start_session(token: RelayToken, *, hello: dict[str, Any], client_ip: str | None) -> RelaySession:
    """Create a session for ``hello`` (§E.2 hello).

    Earlier sessions of the same relay end; the new one is standby when
    another relay already holds the active role.
    """
    from backend.inference.live import live_sessions

    endpoint = token.endpoint
    relay_id = hello.get("relay_id")
    upstream = hello.get("upstream") or {}
    try:
        parallel = max(1, int(hello.get("parallel") or 1))
    except (TypeError, ValueError):
        parallel = 1
    now = timezone.now()
    if relay_id:
        # Any token of this endpoint: a relay that re-reads a rotated token
        # file reconnects with the new token under the same relay_id, and its
        # old session (still the active one) must not block the new one.
        for previous in RelaySession.objects.filter(endpoint=endpoint, relay_id=relay_id, ended_at__isnull=True):
            # The relay restarted: whatever it was running is lost (an attempt is used).
            end_session(previous, reason="replaced", bump_attempt=True, promote=False)
    fields = {
        "endpoint": endpoint,
        "token": token,
        "relay_id": relay_id,
        "hostname": str(hello.get("hostname") or "")[:255],
        "relay_version": str(hello.get("relay_version") or "")[:32],
        "upstream_kind": str(upstream.get("kind") or "")[:24],
        "upstream_hint": str(upstream.get("url_hint") or "")[:255],
        "capacity": max(1, min(parallel, endpoint.max_concurrency)),
        "client_ip": client_ip,
        "last_seen_at": now,
        "models": [],
    }
    with transaction.atomic():
        LLMEndpoint.objects.select_for_update().filter(pk=endpoint.pk).first()
        for stale in RelaySession.objects.filter(
            endpoint=endpoint,
            ended_at__isnull=True,
            role=RelaySession.Role.ACTIVE,
        ).exclude(pk__in=live_sessions([endpoint.pk]).values("pk")):
            end_session(stale, reason="timeout", promote=False)
        # Every other open active session (live, of another relay) keeps the role.
        has_active = RelaySession.objects.filter(
            endpoint=endpoint,
            ended_at__isnull=True,
            role=RelaySession.Role.ACTIVE,
        ).exists()
        session = None
        if not has_active:
            try:
                with transaction.atomic():
                    session = RelaySession.objects.create(**fields, role=RelaySession.Role.ACTIVE)
            except IntegrityError:
                # relaysession_one_active: another relay took the role concurrently.
                session = None
        if session is None:
            session = RelaySession.objects.create(**fields, role=RelaySession.Role.STANDBY)
    transaction.on_commit(lambda: _publish_session(session, "connected"))
    return session


def reap_stale_sessions(now: datetime | None = None) -> int:
    """Reconciler step 4: end sessions unseen for ``RELAY_SESSION_TTL_S`` (requeue leases, promote standby)."""
    from backend.inference.live import session_cutoff

    count = 0
    cutoff = session_cutoff() if now is None else now - (timezone.now() - session_cutoff())
    for session in RelaySession.objects.filter(ended_at__isnull=True, last_seen_at__lte=cutoff):
        end_session(session, reason="timeout", bump_attempt=True)
        count += 1
    return count


def reap_expired_tokens(now: datetime | None = None) -> int:
    """Reconciler step 5: end the sessions of tokens past their rotation grace."""
    now = now or timezone.now()
    count = 0
    for token in RelayToken.objects.filter(revoked_at__isnull=True, expires_at__lt=now):
        ended, _requeued = end_token_sessions(token, reason="token_expired")
        count += ended
    return count
