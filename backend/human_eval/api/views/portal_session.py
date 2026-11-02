"""Portal session endpoints (owner B1): join, resume, logout, me, task page, consent, pre/post answers, next,
finish and withdraw (spec §4, §5.5, R1, R2, R3.6, R3.7, R3.12).

**Public operations** (``auth=None``): ``join/{token}/`` (GET and POST), ``resume/``,
``logout/`` and ``me/``. The POSTs set or clear the participant cookie, so they
enforce Django's CSRF check themselves (``auth.enforce_csrf`` → 403): a cross-site
form must not be able to plant a participant session (login CSRF). Every other
operation uses the router's ``PortalAuth`` (CSRF on unsafe methods, 401 without an
identity) and resolves the caller's participant with ``access.portal_participant``
(404 for tasks the caller has no participant in).

**Throttling** counts *failed* invite-token and access-code lookups only (spec R2,
R3.12: 60 per 10 minutes per client key, plus a global 600 per hour while
``TRUSTED_PROXY_CIDRS`` is empty). A blocked client gets 429 ``rate_limited`` before
any lookup, so even a correct code waits out the window. Every resume failure
(unknown code, withdrawn participant, draft task) answers the same 404 "Code not
recognised".

**Cookies**: the session cookie (``access.set_session_cookie``: Path=/, HttpOnly,
SameSite=Lax, Secure on https origins, browser-session lifetime) is issued by join
and resume (resume rotates the token); a long-lived device cookie is set on join
and its sha256 stored as ``Participant.device_hash`` (duplicate-join flag). A cookie
of another participant replaced by join/resume has its server-side session ended.

**Participant-level writes** (consent, pre/post answers, finish) need a participant
that is neither withdrawn nor completed, on a task that is open or paused (and, except
for previews, not past ``closes_at``); otherwise 409 ``read_only``. Pre-answers lock
once the participant received its first assignment (they describe the participant
before any app was seen). Withdrawal is always possible.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.db.models import F
from django.db.models import Q
from django.http import HttpResponse  # noqa: TC002 - ninja resolves the temporal-response annotation at runtime
from django.utils import timezone

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.portal import AnswersInputSchema
from backend.human_eval.api.schemas.portal import ConsentInputSchema
from backend.human_eval.api.schemas.portal import JoinInfoSchema
from backend.human_eval.api.schemas.portal import JoinInputSchema
from backend.human_eval.api.schemas.portal import JoinResultSchema
from backend.human_eval.api.schemas.portal import NextResultSchema
from backend.human_eval.api.schemas.portal import PortalMeSchema
from backend.human_eval.api.schemas.portal import PortalTaskSchema
from backend.human_eval.api.schemas.portal import ResumeInputSchema
from backend.human_eval.api.schemas.portal import ResumeResultSchema
from backend.human_eval.api.schemas.portal import WithdrawInputSchema
from backend.human_eval.api.views._routers import portal_router
from backend.human_eval.auth import PortalIdentity
from backend.human_eval.auth import enforce_csrf
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import Participant
from backend.human_eval.services import access
from backend.human_eval.services import allocation
from backend.human_eval.services import design as design_service
from backend.human_eval.services import responses as responses_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict

if TYPE_CHECKING:
    from django.http import HttpRequest

__all__ = ["portal_router"]

TOKEN_MAX_LENGTH = 64
CODE_MAX_LENGTH = 64
ANSWERS_MAX_BYTES = 64 * 1024
GENERIC_CODE_ERROR = "Code not recognised"
GENERIC_INVITE_ERROR = "This invite link is not valid"
RATE_LIMITED_DETAIL = "Too many failed attempts. Wait a few minutes and try again."
_DEVICE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

_OPEN = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)
_LIVE_TASK_STATUSES = (EvalTask.Status.OPEN, EvalTask.Status.PAUSED)
_AVAILABILITY_DETAIL = {
    "inactive": "This invite link is no longer active",
    "expired": "This invite link has expired",
    "full": "This invite link has reached its maximum number of participants",
    "not_open": "This task is not open yet",
    "closed": "This task is closed",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rate_limit(request: HttpRequest) -> None:
    """429 ``rate_limited`` while this client is blocked by earlier failed lookups (checked before any lookup)."""
    if access.lookup_failures_blocked(request):
        raise HumanEvalServiceError(429, RATE_LIMITED_DETAIL, "rate_limited")


def _lookup_failed(request: HttpRequest, detail: str) -> HumanEvalServiceError:
    """Count a failed join/resume lookup and return the generic 404 to raise."""
    access.record_lookup_failure(request)
    return HumanEvalServiceError(404, detail, "not_found")


def _cookie_participant(request: HttpRequest) -> Participant | None:
    identity = access.portal_identity(request)
    return identity.participant if identity else None


def _end_other_cookie_session(request: HttpRequest, keep: Participant) -> None:
    """The browser's cookie is about to be replaced: end the old cookie participant's server-side session."""
    previous = _cookie_participant(request)
    if previous is not None and previous.pk != keep.pk:
        access.end_session(previous)


def _design(task: EvalTask) -> dict[str, Any]:
    return task.design if isinstance(task.design, dict) else {}


def _consent_config(design: dict[str, Any]) -> dict[str, Any]:
    consent = design.get("consent") or {}
    return {"enabled": bool(consent.get("enabled")), "text": str(consent.get("text") or "")}


def availability(invite: EvalInvite, task: EvalTask) -> str:
    """TS ``JoinInfo.availability`` of an invite (checked in this order, spec §5.5)."""
    now = timezone.now()
    settings = design_service.merged_settings(task.settings)
    if not invite.is_active or not settings["access"].get("allow_invite_links", True):
        return "inactive"
    if invite.expires_at is not None and invite.expires_at <= now:
        return "expired"
    if invite.max_uses is not None and invite.uses >= invite.max_uses:
        return "full"
    if task.status in (EvalTask.Status.DRAFT, EvalTask.Status.PAUSED) or (
        task.opens_at is not None and now < task.opens_at
    ):
        return "not_open"
    if task.status in (EvalTask.Status.CLOSED, EvalTask.Status.ARCHIVED) or (
        task.closes_at is not None and now >= task.closes_at
    ):
        return "closed"
    return "open"


def _invite(request: HttpRequest, token: str, *, for_update: bool = False) -> EvalInvite:
    if not token or len(token) > TOKEN_MAX_LENGTH:
        raise _lookup_failed(request, GENERIC_INVITE_ERROR)
    qs = EvalInvite.objects.select_related("task")
    if for_update:
        qs = qs.select_for_update(of=("self",))
    invite = qs.filter(token=token).first()
    if invite is None:
        raise _lookup_failed(request, GENERIC_INVITE_ERROR)
    return invite


def _device_token(request: HttpRequest) -> str:
    """The browser's device token (kept when it looks like one of ours), else a new one."""
    raw = request.COOKIES.get(access.device_cookie_name()) or ""
    if _DEVICE_TOKEN_RE.fullmatch(raw):
        return raw
    return access.new_device_token()[0]


def _task_live(task: EvalTask, participant: Participant) -> bool:
    """Open or paused (previews ignore ``closes_at``; a passed ``closes_at`` means closed for everyone else)."""
    if task.status not in _LIVE_TASK_STATUSES:
        return False
    if participant.kind == Participant.Kind.PREVIEW:
        return True
    return not (task.closes_at is not None and timezone.now() >= task.closes_at)


def _ensure_participant_writable(participant: Participant) -> None:
    """409 ``read_only`` unless the participant may still change its task-level answers (see the module doc)."""
    if participant.status == Participant.Status.WITHDRAWN:
        msg = "You have withdrawn from this task"
        raise conflict(msg, "read_only")
    if participant.status == Participant.Status.COMPLETED:
        msg = "You have already finished this task"
        raise conflict(msg, "read_only")
    if not _task_live(participant.task, participant):
        msg = "This task is not accepting answers"
        raise conflict(msg, "read_only")


def _activate(participant: Participant) -> list[str]:
    """Invited → active (accounts on first visit while the task accepts); returns the fields to save."""
    if participant.status != Participant.Status.INVITED:
        return []
    participant.status = Participant.Status.ACTIVE
    participant.joined_at = participant.joined_at or timezone.now()
    return ["status", "joined_at"]


def _validated_answers(questions: list[dict[str, Any]] | None, answers: dict[str, Any]) -> dict[str, Any]:
    """Pre/post answers cleaned strictly against the design questions (400 ``invalid_answers`` otherwise)."""
    if len(design_service.canonical_json(answers).encode("utf-8")) > ANSWERS_MAX_BYTES:
        raise HumanEvalServiceError(400, "The answers are too large", "invalid_answers")
    cleaned, errors = responses_service.validate_question_answers(list(questions or []), answers, strict=True)
    if errors:
        raise HumanEvalServiceError(400, "Some answers are missing or invalid", "invalid_answers", errors)
    # Store every question id (None = left blank) so a saved all-optional questionnaire stays saved.
    return {str(q.get("id")): cleaned.get(str(q.get("id"))) for q in questions or [] if q.get("id")}


def _portal_task(participant: Participant) -> dict[str, Any]:
    """TS ``PortalTask`` for the participant (attention ``expected`` values stripped)."""
    task = participant.task
    design = _design(task)
    settings = design_service.merged_settings(task.settings)
    allocation_settings = settings["allocation"]
    counts = Assignment.objects.filter(participant_id=participant.pk).aggregate(
        submitted=Count("id", filter=Q(status=Assignment.Status.SUBMITTED)),
        skipped=Count("id", filter=Q(status=Assignment.Status.SKIPPED)),
    )
    current = (
        Assignment.objects.filter(participant_id=participant.pk, status__in=_OPEN)
        .order_by("position")
        .values_list("id", flat=True)
        .first()
    )
    return {
        "id": task.pk,
        "title": task.title,
        "description": task.description,
        "instructions": task.instructions,
        "mode": task.mode,
        "status": task.status,
        "accepting": access.is_accepting(task, participant=participant),
        "participant": {
            "label": participant.label,
            "kind": participant.kind,
            "status": participant.status,
            "consent_at": participant.consent_at,
            "completion_code": participant.completion_code,
        },
        "consent": _consent_config(design),
        "pre_questions": design_service.participant_questions(design.get("pre_questions") or []),
        "pre_answers": participant.pre_answers if isinstance(participant.pre_answers, dict) else {},
        # Background answers lock once any item was handed out (expired ones included).
        "pre_answers_locked": Assignment.objects.filter(participant_id=participant.pk).exists(),
        "post_questions": design_service.participant_questions(design.get("post_questions") or []),
        "post_answers": participant.post_answers if isinstance(participant.post_answers, dict) else {},
        "progress": {
            "submitted": counts["submitted"],
            "skipped": counts["skipped"],
            "limit": allocation_settings.get("max_items_per_participant"),
            "remaining_available": allocation.available_count(participant),
        },
        "current_assignment_id": current,
        "blocks": [
            {key: str(block.get(key) or "") for key in ("id", "type", "title")} for block in design.get("blocks") or []
        ],
        "allow_skip": bool(allocation_settings.get("allow_skip")),
    }


def _participations(participants: list[Participant]) -> list[dict[str, Any]]:
    """TS ``PortalParticipation`` rows (submitted counts and targets in two queries)."""
    if not participants:
        return []
    submitted = dict(
        Assignment.objects.filter(participant__in=participants, status=Assignment.Status.SUBMITTED)
        .order_by()
        .values("participant_id")
        .annotate(n=Count("id"))
        .values_list("participant_id", "n"),
    )
    items = dict(
        EvalItem.objects.filter(task_id__in={p.task_id for p in participants}, is_active=True)
        .order_by()
        .values("task_id")
        .annotate(n=Count("id"))
        .values_list("task_id", "n"),
    )
    return [
        {
            "task_id": participant.task_id,
            "task_title": participant.task.title,
            "task_status": participant.task.status,
            "participant_label": participant.label,
            "participant_status": participant.status,
            "kind": participant.kind,
            "submitted": submitted.get(participant.pk, 0),
            "target": allocation.participant_target(participant.task, n_items=items.get(participant.task_id, 0)),
        }
        for participant in participants
    ]


# ---------------------------------------------------------------------------
# Public: join, resume, logout, me
# ---------------------------------------------------------------------------


@portal_router.get("/join/{token}/", response={200: JoinInfoSchema, **error_responses(404, 429)}, auth=None)
def join_info(request, token: str):
    """What an invite link leads to: task title, availability, consent text; ``already_joined`` for this browser."""
    _rate_limit(request)
    invite = _invite(request, token)
    task = invite.task
    cookie_participant = _cookie_participant(request)
    already = None
    if cookie_participant is not None and cookie_participant.task_id == task.pk:
        already = {"task_id": task.pk, "participant_label": cookie_participant.label}
    return {
        "task_title": task.title,
        "task_description": task.description,
        "availability": availability(invite, task),
        "already_joined": already,
        "consent": _consent_config(_design(task)),
        "estimated_items": allocation.participant_target(task),
    }


@portal_router.post(
    "/join/{token}/",
    response={200: JoinResultSchema, **error_responses(400, 403, 404, 409, 429)},
    auth=None,
)
def join(request, token: str, data: JoinInputSchema, response: HttpResponse):
    """Join through an invite link: a new participant, its resume code (shown once) and the session cookie."""
    enforce_csrf(request)
    _rate_limit(request)
    with transaction.atomic():
        invite = _invite(request, token, for_update=True)
        task = invite.task
        cookie_participant = _cookie_participant(request)
        if cookie_participant is not None and cookie_participant.task_id == task.pk:
            msg = "You have already joined this task in this browser"
            raise conflict(msg, "already_joined")
        state = availability(invite, task)
        if state != "open":
            raise conflict(_AVAILABILITY_DETAIL[state], state)
        if _consent_config(_design(task))["enabled"] and not data.consent:
            msg = "Please read and accept the consent information to join"
            raise HumanEvalServiceError(400, msg, "consent_required")

        now = timezone.now()
        device_raw = _device_token(request)
        code_raw, code_hash, code_hint = access.new_access_code()
        participant = Participant(
            task=task,
            number=access.allocate_participant_number(task),
            kind=Participant.Kind.INVITE,
            status=Participant.Status.ACTIVE,
            invite=invite,
            joined_at=now,
            consent_at=now if data.consent else None,
            access_code_hash=code_hash,
            access_code_hint=code_hint,
            device_hash=access.hash_secret(device_raw),
        )
        session_raw = access.issue_session(participant)
        EvalInvite.objects.filter(pk=invite.pk).update(uses=F("uses") + 1)
        if cookie_participant is not None:
            access.end_session(cookie_participant)
    access.set_session_cookie(response, session_raw)
    access.set_device_cookie(response, device_raw)
    return {"task_id": task.pk, "participant_label": participant.label, "access_code": code_raw}


@portal_router.post("/resume/", response={200: ResumeResultSchema, **error_responses(403, 404, 429)}, auth=None)
def resume(request, data: ResumeInputSchema, response: HttpResponse):
    """Continue with an access code (pre-generated or the resume code from joining): rotates the session token."""
    enforce_csrf(request)
    _rate_limit(request)
    normalized = access.normalize_code(data.code)
    if not normalized or len(normalized) > CODE_MAX_LENGTH:
        raise _lookup_failed(request, GENERIC_CODE_ERROR)
    with transaction.atomic():
        participant = (
            Participant.objects.select_for_update(of=("self",))
            .select_related("task")
            .filter(access_code_hash=access.hash_secret(normalized))
            .first()
        )
        if (
            participant is None
            or participant.status == Participant.Status.WITHDRAWN
            or participant.task.status == EvalTask.Status.DRAFT
        ):
            raise _lookup_failed(request, GENERIC_CODE_ERROR)
        task = participant.task
        if task.status in _LIVE_TASK_STATUSES:
            fields = _activate(participant)
            if fields:
                participant.save(update_fields=fields)
        _end_other_cookie_session(request, participant)
        session_raw = access.issue_session(participant)
    access.set_session_cookie(response, session_raw)
    return {"task_id": task.pk, "participant_label": participant.label}


@portal_router.post("/logout/", response={204: None, **error_responses(403)}, auth=None)
def logout(request, response: HttpResponse):
    """End the cookie session (server-side too) and clear the cookie. Accounts also sign out through allauth."""
    enforce_csrf(request)
    raw = request.COOKIES.get(access.cookie_name()) or ""
    if raw and len(raw) <= access.MAX_COOKIE_LENGTH:
        Participant.objects.filter(session_token_hash=access.hash_secret(raw)).update(session_token_hash=None)
    access.clear_session_cookie(response)
    return 204, None


@portal_router.get("/me/", response=PortalMeSchema, auth=None)
def me(request):
    """Who this browser is in the portal: the cookie participant and/or the signed-in user, and their tasks."""
    identity = access.portal_identity(request)
    participant = identity.participant if identity else None
    user = identity.user if identity else None
    rows: list[Participant] = [participant] if participant is not None else []
    if user is not None:
        accounts = Participant.objects.select_related("task").filter(
            user=user,
            kind__in=(Participant.Kind.ACCOUNT, Participant.Kind.PREVIEW),
        )
        rows.extend(row for row in accounts.order_by("-task__created_at", "number") if row not in rows)
    if participant is not None:
        kind = "participant"
    elif user is not None:
        kind = "account"
    else:
        kind = "anonymous"
    return {
        "kind": kind,
        "participant": {"task_id": participant.task_id, "label": participant.label} if participant else None,
        "user": {
            "email": user.email,
            "name": user.name or "",
            "is_evaluator": access.is_restricted_evaluator(user),
        }
        if user is not None
        else None,
        "participations": _participations(rows),
    }


# ---------------------------------------------------------------------------
# Task page and participant-level actions (PortalAuth)
# ---------------------------------------------------------------------------


@portal_router.get("/tasks/{uuid:task_id}/", response={200: PortalTaskSchema, **error_responses(404)})
def portal_task(request, task_id: UUID):
    """The task page: instructions, consent, pre/post questions, progress and the current assignment."""
    participant = access.portal_participant(request, task_id)
    if participant.kind == Participant.Kind.ACCOUNT and access.is_accepting(participant.task, participant=participant):
        fields = _activate(participant)
        if fields:
            participant.save(update_fields=fields)
    return _portal_task(participant)


@portal_router.post(
    "/tasks/{uuid:task_id}/consent/",
    response={200: PortalTaskSchema, **error_responses(400, 404, 409)},
)
def give_consent(request, task_id: UUID, data: ConsentInputSchema):
    """Record the participant's consent (once; the first time is kept)."""
    participant = access.portal_participant(request, task_id)
    _ensure_participant_writable(participant)
    if not data.agree:
        msg = "Consent is required to take part"
        raise HumanEvalServiceError(400, msg, "consent_required")
    fields = _activate(participant)
    if participant.consent_at is None:
        participant.consent_at = timezone.now()
        fields.append("consent_at")
    if fields:
        participant.save(update_fields=fields)
    return _portal_task(participant)


@portal_router.put(
    "/tasks/{uuid:task_id}/pre-answers/",
    response={200: PortalTaskSchema, **error_responses(400, 404, 409)},
)
def save_pre_answers(request, task_id: UUID, data: AnswersInputSchema):
    """Answers to the pre-questionnaire, validated strictly; locked once the first item was handed out."""
    participant = access.portal_participant(request, task_id)
    _ensure_participant_writable(participant)
    if Assignment.objects.filter(participant_id=participant.pk).exists():
        msg = "The background questions cannot be changed after you started evaluating"
        raise conflict(msg, "read_only")
    design = _design(participant.task)
    participant.pre_answers = _validated_answers(design.get("pre_questions"), data.answers)
    participant.save(update_fields=["pre_answers", *_activate(participant)])
    return _portal_task(participant)


@portal_router.put(
    "/tasks/{uuid:task_id}/post-answers/",
    response={200: PortalTaskSchema, **error_responses(400, 404, 409)},
)
def save_post_answers(request, task_id: UUID, data: AnswersInputSchema):
    """Answers to the post-questionnaire, validated strictly (required before ``finish``)."""
    participant = access.portal_participant(request, task_id)
    _ensure_participant_writable(participant)
    design = _design(participant.task)
    participant.post_answers = _validated_answers(design.get("post_questions"), data.answers)
    participant.save(update_fields=["post_answers"])
    return _portal_task(participant)


@portal_router.post("/tasks/{uuid:task_id}/next/", response={200: NextResultSchema, **error_responses(404)})
def next_item(request, task_id: UUID):
    """The participant's current or next assignment (``services.allocation.next_assignment``)."""
    participant = access.portal_participant(request, task_id)
    assignment, reason = allocation.next_assignment(participant)
    return {"assignment_id": assignment.pk if assignment is not None else None, "reason": reason}


@portal_router.post(
    "/tasks/{uuid:task_id}/finish/",
    response={200: PortalTaskSchema, **error_responses(400, 404, 409)},
)
def finish(request, task_id: UUID):
    """Finish the task: completion code (8 characters), once the post-answers are given and no open
    assignment holds saved work.

    An open assignment without saved work (no response, report or finding verdict — typically the app
    handed out automatically after the last submit) is released (``expired``), so a participant can
    stop whenever they like without recording a fake skip; one with saved work must be submitted or
    skipped first (409 ``assignment_open``). Every refusal leaves the assignments as they were.
    """
    participant = access.portal_participant(request, task_id)
    if participant.status == Participant.Status.COMPLETED:
        return _portal_task(participant)
    _ensure_participant_writable(participant)
    with transaction.atomic():  # a refusal below rolls the release back
        # Lock order: the task row before assignment rows (as next / submit / skip).
        task = EvalTask.objects.select_for_update(of=("self",)).get(pk=participant.task_id)
        allocation.expire_stale(task)
        allocation.release_untouched(participant)
        if Assignment.objects.filter(participant_id=participant.pk, status__in=_OPEN).exists():
            msg = "Submit or skip the app you are working on before finishing"
            raise conflict(msg, "assignment_open")
        design = _design(participant.task)
        missing = allocation.missing_required_answers(design.get("post_questions"), participant.post_answers)
        if missing:
            msg = "Please answer the final questions first"
            raise HumanEvalServiceError(400, msg, "post_questions_required", missing)
        participant.status = Participant.Status.COMPLETED
        participant.completed_at = timezone.now()
        participant.completion_code = access.new_completion_code()
        participant.save(update_fields=["status", "completed_at", "completion_code"])
    return _portal_task(participant)


@portal_router.post("/tasks/{uuid:task_id}/withdraw/", response={204: None, **error_responses(404)})
def withdraw(request, task_id: UUID, data: WithdrawInputSchema, response: HttpResponse):
    """Withdraw: open assignments expire, the session ends; ``delete_data`` deletes assignments and answers."""
    participant = access.portal_participant(request, task_id)
    identity = request.auth if isinstance(request.auth, PortalIdentity) else access.portal_identity(request)
    from_cookie = bool(identity and identity.participant is not None and identity.participant.pk == participant.pk)
    with transaction.atomic():
        Assignment.objects.filter(participant_id=participant.pk, status__in=_OPEN).update(
            status=Assignment.Status.EXPIRED,
        )
        fields = ["status", "withdrawn_at", "session_token_hash"]
        if participant.status != Participant.Status.WITHDRAWN:
            participant.status = Participant.Status.WITHDRAWN
            participant.withdrawn_at = timezone.now()
        participant.session_token_hash = None
        if data.delete_data:
            # Cascades to responses, observations (+ attachment files on commit), verdicts and HTTP exchanges.
            Assignment.objects.filter(participant_id=participant.pk).delete()
            participant.pre_answers = {}
            participant.post_answers = {}
            fields += ["pre_answers", "post_answers"]
        participant.save(update_fields=fields)
    if from_cookie:
        access.clear_session_cookie(response)
    return 204, None
