"""Shared access helpers: secrets, codes, cookies, owner scoping, portal lookups, throttles.

Foundation-owned (spec R3.4): other modules may APPEND helpers here but never
change these signatures.

Secrets are random and stored as sha256 only (the ``backend.tokens`` pattern):

- **access codes** — 12 characters from a 30-letter alphabet without look-alikes
  (Crockford base32 minus ``0`` and ``1``; no I, L, O, U), shown grouped by four
  (``K7QM-4XT9-BW2R``), about 59 bits; stored as the sha256 of the normalized code
  (upper case, separators removed) plus a 4-character hint;
- **session tokens** — ``secrets.token_urlsafe(32)`` in the portal cookie;
- **completion codes** — 8 characters of the same alphabet, stored in plain text
  (the participant shows it to get credit).

Owner scoping: researcher lookups answer 404 for other users' tasks and for
malformed ids (staff see every task). Portal lookups answer 404 for anything the
caller's identity does not own.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.db.models import Q
from django.utils import timezone

from backend.human_eval.auth import PortalIdentity
from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django.http import HttpResponse

logger = logging.getLogger(__name__)

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
ACCESS_CODE_LENGTH = 12
ACCESS_CODE_GROUP = 4
ACCESS_CODE_HINT_LENGTH = 4
COMPLETION_CODE_LENGTH = 8
SESSION_TOKEN_BYTES = 32
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
LAST_SEEN_THROTTLE_SECONDS = 60
MAX_COOKIE_LENGTH = 256

#: Failed join/resume lookups per client key (spec R3.12) ...
LOOKUP_FAILURE_LIMIT = 60
LOOKUP_FAILURE_WINDOW_SECONDS = 10 * 60
#: ... plus a global ceiling while TRUSTED_PROXY_CIDRS is empty (every client then shares one IP).
GLOBAL_LOOKUP_FAILURE_LIMIT = 600
GLOBAL_LOOKUP_FAILURE_WINDOW_SECONDS = 60 * 60

_PORTAL_KINDS = (Participant.Kind.ACCOUNT, Participant.Kind.PREVIEW)
_OPEN_STATUSES = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)
_IDENTITY_ATTR = "_human_eval_portal_identity"
_MISSING = object()

# ---------------------------------------------------------------------------
# Secrets and codes
# ---------------------------------------------------------------------------


def hash_secret(raw: str) -> str:
    """sha256 hex digest of a secret (session tokens, normalized access codes, device tokens)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_code(code: str) -> str:
    """An access code as typed → its canonical form: upper case, dashes and whitespace removed."""
    return "".join(ch for ch in (code or "").upper() if ch != "-" and not ch.isspace())


def _random_code(length: int) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def new_access_code() -> tuple[str, str, str]:
    """A new access code: ``(raw "XXXX-XXXX-XXXX", sha256 of the normalized code, hint = last 4 chars)``."""
    code = _random_code(ACCESS_CODE_LENGTH)
    raw = "-".join(code[i : i + ACCESS_CODE_GROUP] for i in range(0, ACCESS_CODE_LENGTH, ACCESS_CODE_GROUP))
    return raw, hash_secret(code), code[-ACCESS_CODE_HINT_LENGTH:]


def access_code_hash(code: str) -> str:
    """The stored hash for a code as the participant typed it (``hash_secret(normalize_code(code))``)."""
    return hash_secret(normalize_code(code))


def new_session_token() -> tuple[str, str]:
    """A new portal session token: ``(raw, sha256)``."""
    raw = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    return raw, hash_secret(raw)


def new_device_token() -> tuple[str, str]:
    """A new long-lived device token for the device cookie: ``(raw, sha256)`` (``Participant.device_hash``)."""
    return new_session_token()


def new_completion_code() -> str:
    """An 8-character completion code (same alphabet as access codes)."""
    return _random_code(COMPLETION_CODE_LENGTH)


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------


def cookie_name() -> str:
    """The portal session cookie (``__Host-llml_eval`` on https deployments, else ``llml_eval``)."""
    return getattr(settings, "HUMAN_EVAL_COOKIE_NAME", "llml_eval")


def device_cookie_name() -> str:
    """The long-lived device cookie (``__Host-llml_eval_device`` on https deployments)."""
    return getattr(settings, "HUMAN_EVAL_DEVICE_COOKIE_NAME", "llml_eval_device")


def cookie_secure() -> bool:
    """Whether portal cookies carry ``Secure`` (FRONTEND_PUBLIC_ORIGIN is https; not tied to DEBUG)."""
    return bool(getattr(settings, "HUMAN_EVAL_COOKIE_SECURE", False))


def set_session_cookie(response: HttpResponse, raw: str) -> None:
    """Set the portal session cookie: Path=/, HttpOnly, SameSite=Lax, Secure per settings, no Domain/Max-Age."""
    response.set_cookie(cookie_name(), raw, path="/", secure=cookie_secure(), httponly=True, samesite="Lax")


def clear_session_cookie(response: HttpResponse) -> None:
    """Expire the portal session cookie in the browser."""
    response.delete_cookie(cookie_name(), path="/", samesite="Lax")


def set_device_cookie(response: HttpResponse, raw: str) -> None:
    """Set the long-lived device cookie (same attributes as the session cookie plus Max-Age = 1 year)."""
    response.set_cookie(
        device_cookie_name(),
        raw,
        max_age=DEVICE_COOKIE_MAX_AGE,
        path="/",
        secure=cookie_secure(),
        httponly=True,
        samesite="Lax",
    )


def issue_session(participant: Participant) -> str:
    """Rotate the participant's session token (the old one stops working) and return the new raw token.

    Also stamps ``last_seen_at`` so the idle expiry starts now. The caller sets the cookie with
    :func:`set_session_cookie`.
    """
    raw, digest = new_session_token()
    participant.session_token_hash = digest
    participant.last_seen_at = timezone.now()
    if participant._state.adding:  # noqa: SLF001
        participant.save()
    else:
        participant.save(update_fields=["session_token_hash", "last_seen_at"])
    return raw


def end_session(participant: Participant) -> None:
    """Invalidate the participant's cookie session server-side (logout, withdraw, code regeneration)."""
    if participant.session_token_hash is None:
        return
    participant.session_token_hash = None
    participant.save(update_fields=["session_token_hash"])


# ---------------------------------------------------------------------------
# Researcher scoping
# ---------------------------------------------------------------------------


def parse_uuid(value: Any) -> uuid.UUID | None:
    """``value`` as a UUID, or None when it is not one (callers answer 404)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def researcher_tasks_qs(user: Any) -> QuerySet[EvalTask]:
    """Tasks the user may manage: their own (``created_by``), every task for staff, none when anonymous."""
    qs = EvalTask.objects.select_related("created_by", "experiment")
    if user is None or not getattr(user, "is_authenticated", False):
        return qs.none()
    if getattr(user, "is_staff", False):
        return qs
    return qs.filter(created_by=user)


def researcher_task(request: HttpRequest, task_id: Any, *, for_update: bool = False) -> EvalTask:
    """The task ``task_id`` if ``request.auth`` owns it (or is staff); 404 otherwise, malformed ids included.

    ``for_update`` locks the task row (``SELECT ... FOR UPDATE OF``) for the rest of the transaction.
    """
    pk = parse_uuid(task_id)
    if pk is None:
        msg = "Task not found"
        raise not_found(msg)
    qs = researcher_tasks_qs(getattr(request, "auth", None))
    if for_update:
        qs = qs.select_for_update(of=("self",))
    task = qs.filter(pk=pk).first()
    if task is None:
        msg = "Task not found"
        raise not_found(msg)
    return task


def allocate_participant_number(task: EvalTask) -> int:
    """The next participant number of the task (max + 1), under a row lock on the task.

    Call it inside the transaction that creates the participant: the lock is held until that
    transaction ends, so concurrent joins / code batches never compute the same number.
    """
    with transaction.atomic():
        list(EvalTask.objects.select_for_update().filter(pk=task.pk).values_list("pk", flat=True))
        current = Participant.objects.filter(task_id=task.pk).aggregate(top=Max("number"))["top"]
    return (current or 0) + 1


# ---------------------------------------------------------------------------
# Accepting work / read-only rules
# ---------------------------------------------------------------------------


def is_accepting(task: EvalTask, *, participant: Participant | None = None) -> bool:
    """Whether the participant may start or change work right now.

    Open task inside its ``opens_at`` / ``closes_at`` window. A researcher preview participant
    may also work while the task is paused, and the window does not apply to it (preview data
    never enters results). Withdrawn participants never may.
    """
    if participant is not None and participant.status == Participant.Status.WITHDRAWN:
        return False
    if participant is not None and participant.kind == Participant.Kind.PREVIEW:
        return task.status in (EvalTask.Status.OPEN, EvalTask.Status.PAUSED)
    if task.status != EvalTask.Status.OPEN:
        return False
    now = timezone.now()
    if task.opens_at is not None and now < task.opens_at:
        return False
    return not (task.closes_at is not None and now >= task.closes_at)


def assignment_read_only(assignment: Assignment) -> bool:
    """True once the assignment is closed (submitted / skipped / expired), the task stops accepting, or the
    participant withdrew. Mutations on a read-only assignment are 409 ``read_only``.
    """
    if assignment.status not in _OPEN_STATUSES:
        return True
    participant = assignment.participant
    if participant.status == Participant.Status.WITHDRAWN:
        return True
    return not is_accepting(assignment.task, participant=participant)


def ensure_writable(assignment: Assignment) -> None:
    """Raise 409 ``read_only`` when :func:`assignment_read_only`."""
    if assignment_read_only(assignment):
        msg = "This assignment can no longer be changed"
        raise HumanEvalServiceError(409, msg, "read_only")


# ---------------------------------------------------------------------------
# Portal identity and lookups
# ---------------------------------------------------------------------------


def _session_idle_limit() -> timedelta:
    return timedelta(hours=int(getattr(settings, "HUMAN_EVAL_SESSION_IDLE_HOURS", 24)))


def _cookie_participant(request: HttpRequest) -> Participant | None:
    raw = request.COOKIES.get(cookie_name())
    if not raw or len(raw) > MAX_COOKIE_LENGTH:
        return None
    participant = (
        Participant.objects.select_related("task", "user", "invite")
        .filter(session_token_hash=hash_secret(raw))
        .first()
    )
    if participant is None or participant.status == Participant.Status.WITHDRAWN:
        return None
    now = timezone.now()
    last_seen = participant.last_seen_at or participant.joined_at or participant.created_at
    if last_seen is not None and now - last_seen > _session_idle_limit():
        # Idle-expired: end the session server-side so the token can never be used again.
        Participant.objects.filter(pk=participant.pk, session_token_hash=participant.session_token_hash).update(
            session_token_hash=None,
        )
        return None
    if (
        participant.last_seen_at is None
        or (now - participant.last_seen_at).total_seconds() >= LAST_SEEN_THROTTLE_SECONDS
    ):
        # After commit: a long request (the HTTP console waits up to 25 s) must not hold this
        # participant's row lock and stall its heartbeats and autosaves.
        pk = participant.pk
        transaction.on_commit(lambda: Participant.objects.filter(pk=pk).update(last_seen_at=now))
        participant.last_seen_at = now
    return participant


def _session_user(request: HttpRequest) -> Any:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def portal_identity(request: HttpRequest) -> PortalIdentity | None:
    """The caller's portal identity (cookie participant and/or session user), or None.

    The cookie participant must be valid, not withdrawn and not idle-expired
    (``HUMAN_EVAL_SESSION_IDLE_HOURS`` since ``last_seen_at``); ``last_seen_at`` is refreshed at most
    once a minute. Never raises; cached on the request.
    """
    cached = getattr(request, _IDENTITY_ATTR, _MISSING)
    if cached is not _MISSING:
        return cached
    participant = _cookie_participant(request)
    user = _session_user(request)
    identity = PortalIdentity(participant=participant, user=user) if (participant or user) else None
    setattr(request, _IDENTITY_ATTR, identity)
    return identity


def _identity(request: HttpRequest) -> PortalIdentity:
    auth = getattr(request, "auth", None)
    identity = auth if isinstance(auth, PortalIdentity) else portal_identity(request)
    if not identity:
        raise not_found()
    return identity


def _owner_q(identity: PortalIdentity, participant_path: str) -> Q:
    """Rows whose participant (at ``participant_path``) is the caller's: the cookie participant, or the
    session user's account / preview participants."""
    conditions: list[Q] = []
    if identity.participant is not None:
        conditions.append(Q(**{participant_path: identity.participant.pk}))
    if identity.user is not None:
        conditions.append(
            Q(**{f"{participant_path}__user": identity.user, f"{participant_path}__kind__in": _PORTAL_KINDS}),
        )
    combined = conditions[0]
    for condition in conditions[1:]:
        combined |= condition
    return combined


def portal_participant(request: HttpRequest, task_id: Any) -> Participant:
    """The caller's participant in task ``task_id`` (404 otherwise).

    The cookie participant when it belongs to the task, else the session user's account or preview
    participant of the task (a withdrawn account participant is still returned so it can see its
    status; ``is_accepting`` refuses its work).
    """
    identity = _identity(request)
    pk = parse_uuid(task_id)
    if pk is None:
        msg = "Task not found"
        raise not_found(msg)
    if identity.participant is not None and identity.participant.task_id == pk:
        return identity.participant
    if identity.user is not None:
        participant = (
            Participant.objects.select_related("task", "user", "invite")
            .filter(task_id=pk, user=identity.user, kind__in=_PORTAL_KINDS)
            .first()
        )
        if participant is not None:
            return participant
    msg = "Task not found"
    raise not_found(msg)


def end_task_cookie_session(request: HttpRequest, task_id: Any, response: HttpResponse | None = None) -> bool:
    """End this browser's cookie session when its participant belongs to task ``task_id``; True if one ended.

    For entry points where the signed-in user explicitly picks their own identity for the task — the
    researcher's Preview (``POST tasks/{id}/preview/``). :func:`portal_participant` lets a cookie
    participant of the task win over the session user (spec), so a participant the researcher created
    in this browser (testing their own invite link or access code) would otherwise receive the preview
    work, and that work would count as real data. The session ends server-side (the participant can
    resume with its access code), the cookie is cleared on ``response`` when given, and the identity
    cached on the request drops the participant. Cookies of other tasks are left alone.
    """
    identity = portal_identity(request)
    pk = parse_uuid(task_id)
    if identity is None or identity.participant is None or pk is None or identity.participant.task_id != pk:
        return False
    end_session(identity.participant)
    if response is not None:
        clear_session_cookie(response)
    remaining = PortalIdentity(participant=None, user=identity.user) if identity.user is not None else None
    setattr(request, _IDENTITY_ATTR, remaining)
    return True


def portal_assignment(request: HttpRequest, assignment_id: Any, *, for_update: bool = False) -> Assignment:
    """An assignment of the caller's participant (404 otherwise); ``for_update`` locks its row."""
    identity = _identity(request)
    pk = parse_uuid(assignment_id)
    if pk is None:
        msg = "Assignment not found"
        raise not_found(msg)
    # Snapshots can hold frozen specs and review findings; heartbeats (every 30 s) never read them, and
    # the code that does (context building) loads the deferred field on demand.
    qs = Assignment.objects.select_related("task", "participant", "item", "item__app_a", "item__app_b").defer(
        "item__app_a__snapshot",
        "item__app_b__snapshot",
    )
    if for_update:
        qs = qs.select_for_update(of=("self",))
    assignment = qs.filter(_owner_q(identity, "participant"), pk=pk).first()
    if assignment is None:
        msg = "Assignment not found"
        raise not_found(msg)
    return assignment


def portal_observation(request: HttpRequest, observation_id: Any) -> Observation:
    """An observation logged by the caller's participant (404 otherwise)."""
    identity = _identity(request)
    pk = parse_uuid(observation_id)
    if pk is None:
        msg = "Observation not found"
        raise not_found(msg)
    observation = (
        Observation.objects.select_related("assignment", "assignment__participant", "assignment__task", "task_app")
        .filter(_owner_q(identity, "assignment__participant"), pk=pk)
        .first()
    )
    if observation is None:
        msg = "Observation not found"
        raise not_found(msg)
    return observation


def portal_attachment(request: HttpRequest, attachment_id: Any) -> Attachment:
    """An attachment of an observation of the caller's participant (404 otherwise)."""
    identity = _identity(request)
    pk = parse_uuid(attachment_id)
    if pk is None:
        msg = "Attachment not found"
        raise not_found(msg)
    attachment = (
        Attachment.objects.select_related(
            "observation",
            "observation__assignment",
            "observation__assignment__participant",
            "observation__assignment__task",
        )
        .filter(_owner_q(identity, "observation__assignment__participant"), pk=pk)
        .first()
    )
    if attachment is None:
        msg = "Attachment not found"
        raise not_found(msg)
    return attachment


# ---------------------------------------------------------------------------
# Client key and throttles
# ---------------------------------------------------------------------------


def client_ip(request: HttpRequest) -> str:
    """The client address for throttling: ``backend.inference.relay.views.client_ip`` (right-most hop outside
    ``TRUSTED_PROXY_CIDRS``), else ``REMOTE_ADDR``, else ``"unknown"``."""
    from backend.inference.relay.views import client_ip as relay_client_ip

    return relay_client_ip(request) or request.META.get("REMOTE_ADDR") or "unknown"


def shared_client_ip() -> bool:
    """True when ``TRUSTED_PROXY_CIDRS`` is empty: every client may appear with the proxy's address."""
    return not list(getattr(settings, "TRUSTED_PROXY_CIDRS", []) or [])


def _throttle_cache_key(key: str, window_seconds: int) -> str:
    bucket = int(time.time() // window_seconds)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return f"human_eval:throttle:{window_seconds}:{bucket}:{digest}"


def throttle_hit(key: str, limit: int, window_seconds: int) -> bool:
    """Record one hit in the current fixed window; True when the count is now over ``limit``.

    Fails open (False) when the cache is unavailable.
    """
    cache_key = _throttle_cache_key(key, window_seconds)
    try:
        if cache.add(cache_key, 1, timeout=window_seconds + 1):
            count = 1
        else:
            try:
                count = cache.incr(cache_key)
            except ValueError:  # expired between add() and incr()
                cache.set(cache_key, 1, timeout=window_seconds + 1)
                count = 1
    except Exception:
        logger.exception("human_eval throttle cache unavailable")
        return False
    return count > limit


def throttle_blocked(key: str, limit: int, window_seconds: int) -> bool:
    """True when the current window already holds ``limit`` hits (does not record a hit)."""
    try:
        count = cache.get(_throttle_cache_key(key, window_seconds), 0)
    except Exception:
        logger.exception("human_eval throttle cache unavailable")
        return False
    return int(count or 0) >= limit


def lookup_failures_blocked(request: HttpRequest, scope: str = "portal_lookup") -> bool:
    """Whether join/resume lookups from this client are blocked by earlier failures (spec R2/R3.12).

    Check BEFORE the lookup; answer 429 ``rate_limited`` when True. Successful lookups are never counted.
    """
    if throttle_blocked(f"{scope}:{client_ip(request)}", LOOKUP_FAILURE_LIMIT, LOOKUP_FAILURE_WINDOW_SECONDS):
        return True
    return shared_client_ip() and throttle_blocked(
        f"{scope}:global",
        GLOBAL_LOOKUP_FAILURE_LIMIT,
        GLOBAL_LOOKUP_FAILURE_WINDOW_SECONDS,
    )


def record_lookup_failure(request: HttpRequest, scope: str = "portal_lookup") -> None:
    """Count one failed join/resume lookup for this client (and globally while the client IP is shared)."""
    throttle_hit(f"{scope}:{client_ip(request)}", LOOKUP_FAILURE_LIMIT, LOOKUP_FAILURE_WINDOW_SECONDS)
    if shared_client_ip():
        throttle_hit(f"{scope}:global", GLOBAL_LOOKUP_FAILURE_LIMIT, GLOBAL_LOOKUP_FAILURE_WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# Restricted evaluator accounts (appended by B1: middleware, portal me/, UserSchema.is_evaluator)
# ---------------------------------------------------------------------------


def is_restricted_evaluator(user: Any) -> bool:
    """Whether ``user`` is a restricted evaluator account (portal only, ``middleware.EvaluatorScopeMiddleware``).

    Authenticated, neither staff nor superuser (staff are never restricted), with an
    ``EvaluatorProfile(is_restricted=True)``.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return False
    from backend.human_eval.models import EvaluatorProfile

    return EvaluatorProfile.objects.filter(user_id=user.pk, is_restricted=True).exists()
