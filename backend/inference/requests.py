"""The durable LLM request queue (spec §D.2 request flow, §E.2, §C.7 step 3).

Every non-OpenRouter LLM call is one ``LLMRequest`` row. Relay polls and
direct pumps claim rows under a per-endpoint advisory lock, so the endpoint's
``max_concurrency`` (and a relay session's capacity) is enforced at claim
time: the durable queue *is* the semaphore.

Redis only rings doorbells (:mod:`backend.inference.doorbell`); all state lives
in the rows.

Lease rules:

- A relay claim gets a short *ack lease* (``RELAY_ACK_LEASE_S``). It is
  extended to ``request_timeout_s + 30`` only when the relay echoes
  ``(id, claim_token)`` in a later poll's ``inflight`` list
  (:func:`process_inflight`).
- A pump claim is acked at once with the long lease.
- An expired lease requeues the row (``attempt + 1`` when it had been acked)
  or fails it with ``lease_expired`` once ``max_attempts`` is spent. Leases
  that were never acked never started upstream, so they never use an attempt.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Case
from django.db.models import F
from django.db.models import IntegerField
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.utils import timezone

from backend.inference import doorbell
from backend.inference.db import close_db
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.params import SOURCE_OFFSET
from backend.inference.timeouts import REQUEST_MAX_ATTEMPTS
from backend.inference.timeouts import chain

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.inference.access import Access
    from backend.inference.models import RelaySession

logger = logging.getLogger(__name__)

Status = LLMRequest.Status
TERMINAL = frozenset({Status.DONE, Status.FAILED, Status.CANCELLED, Status.EXPIRED})
ACTIVE = (Status.QUEUED, Status.LEASED)
WAIT_SLICE_S = 5.0
# A claim older than this that a poll does not list in ``inflight`` was lost
# on the way to the relay (spec §E.2 poll step 4).
LOST_CLAIM_GRACE_S = 10
# Error kinds a relay/pump reports that may be retried on another attempt.
RETRYABLE_RESULT_KINDS = frozenset({"connect", "timeout"})
RESULT_ERROR_KINDS = frozenset(
    {"connect", "timeout", "http", "too_large", "cancelled", "bad_json", "forbidden_path"},
)
_CLEARED_LEASE = {
    "session": None,
    "pump_owner": "",
    "claim_token": "",
    "acked_at": None,
    "lease_expires_at": None,
    "leased_at": None,
}

__all__ = [
    "WaitTimeout",
    "cancel_for_jobs",
    "cancel_request",
    "claim",
    "complete",
    "enqueue",
    "process_inflight",
    "purge_old",
    "requeue_expired",
    "requeue_pump_orphans",
    "requeue_session",
    "wait_result",
]


class WaitTimeout(Exception):  # noqa: N818 — contract name (§D.2)
    """``wait_result`` hit ``max_wait`` while the request is still queued or leased (the request is untouched)."""


# ── Enqueue ─────────────────────────────────────────────────────────────


def enqueue(
    access: Access,
    path: str,
    body: dict[str, Any] | None,
    job=None,
    source: str = "scaffolding",
    priority: int = 0,
    digest: str = "",
    dedupe_key: str = "",
    *,
    method: str = "POST",
    model_name: str | None = None,
) -> uuid.UUID:
    """INSERT an ``LLMRequest(status=queued)`` for ``access.endpoint`` and ring its doorbell; returns the id.

    ``deadline_at = now + request_timeout_s * max_attempts + 600``.
    ``job_attempt`` is copied from ``job.attempt``. ``model_name`` defaults
    to ``access.served_model_name``. The doorbell rings after commit.
    """
    endpoint = access.endpoint
    now = timezone.now()
    row = LLMRequest.objects.create(
        endpoint=endpoint,
        job=job,
        job_attempt=int(getattr(job, "attempt", 0) or 0) if job is not None else 0,
        source=source,
        dedupe_key=dedupe_key,
        model_name=access.served_model_name if model_name is None else model_name,
        digest=digest or "",
        priority=int(priority or 0),
        method=method,
        path=path,
        body=body,
        max_attempts=REQUEST_MAX_ATTEMPTS,
        deadline_at=now + timedelta(seconds=chain(endpoint).deadline_s),
    )
    endpoint_id = endpoint.pk
    transaction.on_commit(lambda: doorbell.ring_endpoint(endpoint_id))
    return row.pk


# ── Waiting ─────────────────────────────────────────────────────────────


def _expire(rid, *, reason: str = "deadline") -> bool:
    now = timezone.now()
    changed = LLMRequest.objects.filter(pk=rid, status__in=ACTIVE).update(
        status=Status.EXPIRED,
        error_kind=reason,
        error="The request did not complete before its deadline.",
        completed_at=now,
        body=None,
        **{k: v for k, v in _CLEARED_LEASE.items() if k != "leased_at"},
    )
    if changed:
        doorbell.signal_done(rid)
    return bool(changed)


def cancel_request(rid) -> bool:
    """Cancel one queued/leased request, signal its waiters and ring its endpoint."""
    row = LLMRequest.objects.filter(pk=rid).values("endpoint_id").first()
    if row is None:
        return False
    changed = LLMRequest.objects.filter(pk=rid, status__in=ACTIVE).update(
        status=Status.CANCELLED,
        completed_at=timezone.now(),
        body=None,
    )
    if changed:
        doorbell.signal_done(rid)
        # The next relay poll lists the claim in ``cancel`` (§E.3 Cancel).
        doorbell.ring_endpoint(row["endpoint_id"])
    return bool(changed)


def wait_result(
    rid: uuid.UUID | str,
    deadline: datetime | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_wait: float | None = None,
) -> LLMRequest:
    """Block until request ``rid`` is ``done`` / ``failed`` / ``cancelled`` / ``expired`` and return the row.

    Closes the DB connection first, then loops on the ``inference:done:<rid>``
    key (5 s slices on the pub/sub channel), re-reading the row each time.
    ``cancel_check()`` True cancels the request; ``max_wait`` raises
    :class:`WaitTimeout` without touching it; the effective deadline is the
    earlier of ``deadline`` and the row's ``deadline_at``, after which the
    request is marked ``expired``. Sets ``delivered_at`` on first delivery
    (``response`` is kept) and then ``row.first_delivery`` is True.
    """
    import time

    started = time.monotonic()
    close_db()
    while True:
        row = LLMRequest.objects.select_related("endpoint").get(pk=rid)
        if row.status in TERMINAL:
            row.first_delivery = False
            if row.delivered_at is None:
                now = timezone.now()
                row.first_delivery = bool(
                    LLMRequest.objects.filter(pk=rid, delivered_at__isnull=True).update(delivered_at=now),
                )
                row.delivered_at = row.delivered_at or now
            close_db()
            return row
        if cancel_check is not None and cancel_check():
            cancel_request(rid)
            continue
        effective = row.deadline_at if deadline is None else min(deadline, row.deadline_at)
        now = timezone.now()
        if now >= effective:
            _expire(rid)
            continue
        slice_s = min(WAIT_SLICE_S, (effective - now).total_seconds())
        if max_wait is not None:
            remaining = max_wait - (time.monotonic() - started)
            if remaining <= 0:
                close_db()
                raise WaitTimeout(str(rid))
            slice_s = min(slice_s, remaining)
        close_db()
        doorbell.wait_done(rid, max(slice_s, 0.05))


# ── Cancellation and lease maintenance ─────────────────────────────────


def _signal_and_ring(rids: Iterable[Any], endpoint_ids: Iterable[Any] = ()) -> None:
    for rid in rids:
        doorbell.signal_done(rid)
    for endpoint_id in set(endpoint_ids):
        doorbell.ring_endpoint(endpoint_id)


def cancel_for_jobs(job_ids: Iterable[Any], *, attempt: int | None = None) -> int:
    """Cancel queued/leased requests of these jobs (optionally one attempt).

    Signals their waiters and rings the endpoint doorbells (so relays get the
    ``cancel`` list promptly); returns the count.
    """
    job_ids = list(job_ids)
    if not job_ids:
        return 0
    qs = LLMRequest.objects.filter(job_id__in=job_ids, status__in=ACTIVE)
    if attempt is not None:
        qs = qs.filter(job_attempt=attempt)
    rows = list(qs.values_list("id", "endpoint_id"))
    if not rows:
        return 0
    changed = LLMRequest.objects.filter(pk__in=[r[0] for r in rows], status__in=ACTIVE).update(
        status=Status.CANCELLED,
        completed_at=timezone.now(),
        body=None,
    )
    rids = [r[0] for r in rows]
    endpoint_ids = [r[1] for r in rows]
    transaction.on_commit(lambda: _signal_and_ring(rids, endpoint_ids))
    return changed


def _requeue_rows(qs: QuerySet[LLMRequest], *, bump: bool) -> tuple[int, list[Any], list[Any]]:
    """Requeue (or, when the attempts are spent, fail) the leased rows of ``qs``.

    Returns ``(requeued, failed_ids, endpoint_ids)``. ``bump`` uses an attempt
    (the request had started upstream); without it the row simply returns
    to the queue.
    """
    rows = list(qs.filter(status=Status.LEASED).values_list("id", "endpoint_id", "attempt", "max_attempts"))
    if not rows:
        return 0, [], []
    now = timezone.now()
    requeue_ids: list[Any] = []
    fail_ids: list[Any] = []
    for rid, _endpoint_id, attempt, max_attempts in rows:
        if bump and attempt + 1 >= max_attempts:
            fail_ids.append(rid)
        else:
            requeue_ids.append(rid)
    requeued = 0
    if requeue_ids:
        update: dict[str, Any] = {"status": Status.QUEUED, **_CLEARED_LEASE}
        if bump:
            update["attempt"] = F("attempt") + 1
        requeued = LLMRequest.objects.filter(pk__in=requeue_ids, status=Status.LEASED).update(**update)
    failed: list[Any] = []
    if fail_ids:
        LLMRequest.objects.filter(pk__in=fail_ids, status=Status.LEASED).update(
            status=Status.FAILED,
            error_kind="lease_expired",
            error="The endpoint did not answer within its lease, on every attempt.",
            completed_at=now,
            body=None,
            **{k: v for k, v in _CLEARED_LEASE.items() if k != "leased_at"},
        )
        failed = fail_ids
    return requeued, failed, [r[1] for r in rows]


def requeue_expired(now: datetime | None = None, *, endpoint_id: int | None = None) -> int:
    """Reconciler step 3: expired ``leased`` rows → ``queued`` (``attempt+1``) or ``failed``/``lease_expired``.

    Leases that were never acked return to the queue without using an
    attempt. Rows past their ``deadline_at`` (queued or leased) become
    ``expired``. ``endpoint_id`` limits the sweep to one endpoint (relay polls
    run it opportunistically). Returns the number of rows changed.
    """
    now = now or timezone.now()
    base = LLMRequest.objects.all()
    if endpoint_id is not None:
        base = base.filter(endpoint_id=endpoint_id)
    expired_lease = base.filter(status=Status.LEASED, lease_expires_at__lt=now)
    total = 0
    signal: list[Any] = []
    endpoints: list[Any] = []
    n, failed, eps = _requeue_rows(expired_lease.filter(acked_at__isnull=True), bump=False)
    total += n
    endpoints += eps
    n, failed, eps = _requeue_rows(expired_lease.filter(acked_at__isnull=False), bump=True)
    total += n + len(failed)
    signal += failed
    endpoints += eps
    overdue = list(base.filter(status__in=ACTIVE, deadline_at__lt=now).values_list("id", "endpoint_id"))
    for rid, endpoint in overdue:
        if _expire(rid):
            total += 1
            endpoints.append(endpoint)
    _signal_and_ring(signal, endpoints)
    return total


def requeue_session(session: RelaySession, *, bump_attempt: bool = False, reason: str = "") -> int:
    """Requeue every request leased to ``session`` (session ended, token revoked, ``bye``).

    Un-acked leases never bump ``attempt``; acked ones do when
    ``bump_attempt`` (and fail with ``lease_expired`` when that spends the
    last attempt). Returns the number of rows requeued or failed.
    """
    qs = LLMRequest.objects.filter(session=session, status=Status.LEASED)
    n1, _f, eps1 = _requeue_rows(qs.filter(acked_at__isnull=True), bump=False)
    n2, failed, eps2 = _requeue_rows(qs.filter(acked_at__isnull=False), bump=bump_attempt)
    if n1 or n2 or failed:
        logger.info("Requeued %d request(s) of relay session %s (%s)", n1 + n2 + len(failed), session.pk, reason)
    _signal_and_ring(failed, [*eps1, *eps2])
    return n1 + n2 + len(failed)


def requeue_pump_orphans(live_owners: Iterable[str]) -> int:
    """Requeue pump-leased requests whose ``pump_owner`` is not a live executor instance (§C.2 startup)."""
    live = [o for o in live_owners if o]
    qs = LLMRequest.objects.filter(status=Status.LEASED).exclude(pump_owner="").exclude(pump_owner__in=live)
    n, failed, eps = _requeue_rows(qs, bump=True)
    _signal_and_ring(failed, eps)
    return n + len(failed)


def purge_old(now: datetime | None = None) -> int:
    """Daily: clear ``body``/``response`` of rows older than ``LLM_REQUEST_RETENTION_DAYS``.

    Metrics columns are kept. Returns the count.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(days=int(getattr(settings, "LLM_REQUEST_RETENTION_DAYS", 7)))
    return (
        LLMRequest.objects.filter(created_at__lt=cutoff)
        .filter(Q(body__isnull=False) | Q(response__isnull=False))
        .update(body=None, response=None)
    )


# ── Claiming (relay poll and direct pump) ──────────────────────────────


def effective_priority():
    """``priority + SOURCE_OFFSET[source]`` as a SQL expression (claim order, §B.1)."""
    return F("priority") + Case(
        *[When(source=source, then=Value(offset)) for source, offset in SOURCE_OFFSET.items()],
        default=Value(0),
        output_field=IntegerField(),
    )


def _served_filter(served: Iterable[tuple[str, str]]) -> Q:
    """Rows a server listing ``served`` ``(name, digest)`` pairs can execute (§E.2 poll step 6)."""
    pairs = {(str(n), str(d)) for n, d in served}
    names = {n for n, _d in pairs}
    by_digest = Q(digest="")
    for name, digest in pairs:
        if digest:
            by_digest |= Q(model_name=name, digest=digest)
    return by_digest & (Q(model_name="") | Q(model_name__in=names))


def claim(
    endpoint_id: int,
    *,
    limit: int,
    session: RelaySession | None = None,
    pump_owner: str = "",
    served: Iterable[tuple[str, str]] | None = None,
) -> list[LLMRequest]:
    """Claim up to ``limit`` queued requests of an endpoint, in one short transaction.

    Under ``pg_advisory_xact_lock(hashtext('llmreq:' || E))`` the number of
    claims is computed *after* the lock: ``min(limit, max_concurrency -
    leased(E), session.capacity - leased(session))``. ``served`` restricts the
    claim to requests the caller's server can run (None: any). Relay claims
    get the ack lease; pump claims are acked with the long lease.
    """
    if limit <= 0:
        return []
    now = timezone.now()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"llmreq:{endpoint_id}"])
        endpoint = LLMEndpoint.objects.only("max_concurrency", "request_timeout_s").get(pk=endpoint_id)
        leased = LLMRequest.objects.filter(endpoint_id=endpoint_id, status=Status.LEASED)
        n = min(limit, endpoint.max_concurrency - leased.count())
        if session is not None:
            n = min(n, session.capacity - leased.filter(session=session).count())
        if n <= 0:
            return []
        qs = LLMRequest.objects.filter(endpoint_id=endpoint_id, status=Status.QUEUED, deadline_at__gt=now)
        if served is not None:
            qs = qs.filter(_served_filter(served))
        rows = list(
            qs.annotate(effective_priority=effective_priority())
            .order_by("-effective_priority", "created_at")
            .select_for_update(skip_locked=True, of=("self",))[:n],
        )
        if not rows:
            return []
        if session is not None:
            lease_until = now + timedelta(seconds=int(settings.RELAY_ACK_LEASE_S))
            acked = None
        else:
            lease_until = now + timedelta(seconds=int(endpoint.request_timeout_s) + 30)
            acked = now
        for row in rows:
            row.status = Status.LEASED
            row.session = session
            row.pump_owner = pump_owner
            row.claim_token = secrets.token_hex(16)
            row.leased_at = now
            row.acked_at = acked
            row.lease_expires_at = lease_until
        LLMRequest.objects.bulk_update(
            rows,
            ["status", "session", "pump_owner", "claim_token", "leased_at", "acked_at", "lease_expires_at"],
        )
    return rows


@dataclass
class InflightOutcome:
    acked: list[str]
    cancel: list[dict[str, str]]
    lost: int


def process_inflight(
    session: RelaySession,
    inflight: Iterable[dict[str, Any]],
    *,
    polled_at: datetime,
    request_timeout_s: int,
) -> InflightOutcome:
    """Poll steps 2-4: ack/extend matching claims, list stale ones to cancel, requeue lost ones.

    A pair matches only when the row is ``leased`` to this session with that
    exact ``claim_token``; its lease becomes ``now + timeout + 30`` and
    ``acked_at`` is set once. Every other pair goes back in ``cancel``.
    Requests leased to the session more than 10 s before the poll but missing
    from ``inflight`` never reached the relay: they are requeued without
    using an attempt.
    """
    pairs: dict[str, str] = {}
    cancel: list[dict[str, str]] = []
    for item in inflight:
        rid = str((item or {}).get("id") or "")
        token = str((item or {}).get("claim_token") or "")
        try:
            uuid.UUID(rid)
        except ValueError:
            continue
        pairs[rid] = token
    now = timezone.now()
    rows = {
        str(r["id"]): r
        for r in LLMRequest.objects.filter(pk__in=list(pairs)).values("id", "status", "session_id", "claim_token")
    }
    acked: list[str] = []
    for rid, token in pairs.items():
        row = rows.get(rid)
        if (
            row is not None
            and row["status"] == Status.LEASED
            and row["session_id"] == session.pk
            and token
            and secrets.compare_digest(row["claim_token"], token)
        ):
            acked.append(rid)
        else:
            cancel.append({"id": rid, "claim_token": token})
    if acked:
        lease_until = now + timedelta(seconds=int(request_timeout_s) + 30)
        LLMRequest.objects.filter(pk__in=acked, status=Status.LEASED, session=session, acked_at__isnull=True).update(
            acked_at=now,
        )
        LLMRequest.objects.filter(pk__in=acked, status=Status.LEASED, session=session).update(
            lease_expires_at=lease_until,
        )
    lost_qs = LLMRequest.objects.filter(
        session=session,
        status=Status.LEASED,
        leased_at__lt=polled_at - timedelta(seconds=LOST_CLAIM_GRACE_S),
    ).exclude(pk__in=acked)
    lost, _failed, eps = _requeue_rows(lost_qs, bump=False)
    if lost:
        _signal_and_ring([], eps)
    return InflightOutcome(acked=acked, cancel=cancel, lost=lost)


def cancelled_among(session: RelaySession, inflight: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """The ``inflight`` pairs that no longer match a live claim of ``session`` (cheap re-check during a poll)."""
    pairs = {}
    for item in inflight:
        rid = str((item or {}).get("id") or "")
        try:
            uuid.UUID(rid)
        except ValueError:
            continue
        pairs[rid] = str((item or {}).get("claim_token") or "")
    if not pairs:
        return []
    live = set(
        LLMRequest.objects.filter(
            pk__in=list(pairs),
            status=Status.LEASED,
            session=session,
        ).values_list("id", "claim_token"),
    )
    live_pairs = {(str(rid), token) for rid, token in live}
    return [{"id": rid, "claim_token": token} for rid, token in pairs.items() if (rid, token) not in live_pairs]


# ── Results ─────────────────────────────────────────────────────────────


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _ns_to_ms(value: Any) -> int | None:
    try:
        return int(int(value) / 1_000_000) if value is not None else None
    except (TypeError, ValueError):
        return None


def _usage_fields(payload: Any) -> dict[str, Any]:
    """Token counts and timings from an Ollama native or OpenAI-shaped response."""
    if not isinstance(payload, dict):
        return {}
    fields: dict[str, Any] = {}
    if "prompt_eval_count" in payload or "eval_count" in payload:
        fields["prompt_tokens"] = _as_int(payload.get("prompt_eval_count"))
        fields["completion_tokens"] = _as_int(payload.get("eval_count"))
    elif isinstance(payload.get("usage"), dict):
        fields["prompt_tokens"] = _as_int(payload["usage"].get("prompt_tokens"))
        fields["completion_tokens"] = _as_int(payload["usage"].get("completion_tokens"))
    if (load := _ns_to_ms(payload.get("load_duration"))) is not None:
        fields["load_ms"] = load
    if (evaluation := _ns_to_ms(payload.get("eval_duration"))) is not None:
        fields["eval_ms"] = evaluation
    return fields


@dataclass(frozen=True)
class CompleteOutcome:
    accepted: bool
    reason: str = ""
    status: str = ""


def complete(
    rid: uuid.UUID | str,
    claim_token: str,
    *,
    session: RelaySession | None = None,
    pump_owner: str | None = None,
    ok: bool,
    status_code: int | None = None,
    payload: Any = None,
    error_kind: str = "",
    error_message: str = "",
    upstream_ms: int | None = None,
) -> CompleteOutcome:
    """Store a relay/pump result; idempotent and only for the current claim (§E.2 result).

    Accepted only while the row is ``leased`` with this ``claim_token`` (and
    to this session / pump owner). ``ok``: ``done`` with the response and
    metrics. ``connect``/``timeout`` with attempts left: back to the queue
    (``attempt + 1``). ``http``: ``done`` with the error status (the caller
    maps it). Otherwise ``failed``. Waiters are signalled after commit.
    """
    try:
        uuid.UUID(str(rid))
    except ValueError:
        return CompleteOutcome(accepted=False, reason="not_found")
    now = timezone.now()
    requeued = False
    with transaction.atomic():
        row = LLMRequest.objects.select_for_update().filter(pk=rid).first()
        if row is None:
            return CompleteOutcome(accepted=False, reason="not_found")
        if row.status != Status.LEASED:
            same_claim = bool(claim_token) and secrets.compare_digest(row.claim_token or "", claim_token)
            if row.status in TERMINAL and same_claim:
                return CompleteOutcome(accepted=False, reason="already_done", status=row.status)
            return CompleteOutcome(accepted=False, reason="stale_claim", status=row.status)
        owner_ok = (session is None or row.session_id == session.pk) and (
            pump_owner is None or row.pump_owner == pump_owner
        )
        if not owner_ok or not claim_token or not secrets.compare_digest(row.claim_token, claim_token):
            return CompleteOutcome(accepted=False, reason="stale_claim", status=row.status)

        row.upstream_ms = _as_int(upstream_ms) if upstream_ms is not None else row.upstream_ms
        if ok and status_code and int(status_code) >= 400:
            ok, error_kind = False, "http"
        kind = error_kind if error_kind in RESULT_ERROR_KINDS else ("http" if not ok and status_code else "")
        if ok:
            row.status = Status.DONE
            row.response = payload
            row.response_status = status_code or 200
            for key, value in _usage_fields(payload).items():
                setattr(row, key, value)
        elif kind in RETRYABLE_RESULT_KINDS and row.attempt + 1 < row.max_attempts:
            row.status = Status.QUEUED
            row.attempt += 1
            row.error_kind = kind
            row.error = (error_message or "")[:2000]
            for key, value in _CLEARED_LEASE.items():
                setattr(row, key, value)
            requeued = True
        elif kind == "http":
            row.status = Status.DONE
            row.response = payload if payload is not None else {"error": {"message": error_message[:2000]}}
            row.response_status = status_code or 502
            row.error_kind = "http"
            row.error = (error_message or "")[:2000]
        else:
            row.status = Status.FAILED
            row.error_kind = kind or "upstream"
            row.error = (error_message or "")[:2000]
        if not requeued:
            row.completed_at = now
            row.body = None
            row.lease_expires_at = None
        row.save()
        endpoint_id = row.endpoint_id
        if row.status == Status.FAILED:
            LLMEndpoint.objects.filter(pk=endpoint_id).update(last_error=f"{row.error_kind}: {row.error}"[:2000])
    if requeued:
        transaction.on_commit(lambda: doorbell.ring_endpoint(endpoint_id))
    else:
        transaction.on_commit(lambda: _signal_and_ring([rid], [endpoint_id]))
    return CompleteOutcome(accepted=True, reason="", status=row.status)
