"""Participants (owner A): access codes, accounts and setup links, invites, preview, exclusion, deletion.

Spec §5.4 + R2/R3:

- **Numbers** come from ``access.allocate_participant_number`` (max + 1 under a lock on the
  task row, held until the request's transaction ends), so batches get consecutive numbers.
- **Access codes** (``allow_access_codes``, else 409 ``access_disabled``): ``count`` code
  participants (status invited); the plaintext codes are returned once, only their sha256
  and a 4-character hint are stored. ``regenerate-code`` replaces hash + hint and ends any
  cookie session (``session_token_hash = None``).
- **Accounts** (``allow_accounts``): per normalized email — an existing user is linked as an
  account participant and left untouched (no profile, no token changes); a missing user is
  created only as a *restricted evaluator* (``restricted: false`` only links existing users:
  missing emails get a per-email error). New users get an unusable password, a verified
  primary allauth ``EmailAddress``, ``EvaluatorProfile(is_restricted=True, created_by=researcher)``
  and a one-time set-password link (allauth's reset-token generator). If the users app promotes
  the new user to staff/superuser, that email's savepoint is rolled back ("cannot create this
  account"). Quotas per researcher and UTC day: 200 new users, 200 emails and 1000 looked-up
  addresses (409 ``quota_exceeded``, checked before anything is written). Every valid address
  counts as a lookup in both modes, whatever its outcome: the per-email results tell which
  addresses have an account, so without that cap the endpoint would be an unlimited
  "is this email registered?" oracle. The caps are check-then-write, so one researcher's
  requests (on any task) are serialized by a row lock on their user, held until the
  transaction ends; the cache counters are raised before it ends. Emails go out after commit
  in a thread; failures are logged, never raised. Links use ``settings.FRONTEND_PUBLIC_ORIGIN``
  only (never the request host).
- **Setup links** are reissued only for accounts the caller created that never logged in.
- **Invites** (creation needs ``allow_invite_links``): share links ``/eval/join/<token>``.
- **Preview** (refused on drafts, 409 ``not_frozen``): the researcher's own preview participant.
- **PATCH** exclude / include (with ``excluded_at`` / ``excluded_by``), note, withdraw (open
  assignments expire, the session ends); **DELETE** removes the participant and their data.

Every nested lookup is scoped to its task (404 otherwise).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from backend.human_eval.api.schemas.common import paginate
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvaluatorProfile
from backend.human_eval.models import Participant
from backend.human_eval.services import access
from backend.human_eval.services import quality
from backend.human_eval.services.design import merged_settings
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

MAX_CODES_PER_CALL = 500
MAX_EMAILS_PER_CALL = 200
NEW_USERS_PER_DAY = 200
EMAILS_PER_DAY = 200
#: Valid addresses looked up per researcher and UTC day (any mode, any outcome): caps the account oracle.
LOOKUPS_PER_DAY = 1000
MAX_NOTE = 5000
MAX_INVITE_LABEL = 120
MAX_INVITE_USES = 100_000
SETUP_PATH_PREFIX = "/auth/password/reset/"
PORTAL_PATH = "/eval"

_OPEN = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)
_LABEL_SEARCH = re.compile(r"^[Pp]?0*(\d{1,9})$")
_CODE_KINDS = (Participant.Kind.CODE, Participant.Kind.INVITE)


class _RollbackEmail(Exception):  # noqa: N818 - control flow inside a savepoint, never escapes
    """Raised inside one email's savepoint to undo it."""


# ---------------------------------------------------------------------------
# Lookups and serialization
# ---------------------------------------------------------------------------


def participant_or_404(task: EvalTask, participant_id: Any) -> Participant:
    """A participant of this task (404 for other tasks' participants and malformed ids)."""
    pk = access.parse_uuid(participant_id)
    participant = (
        Participant.objects.select_related("user", "invite").filter(task=task, pk=pk).first()
        if pk is not None
        else None
    )
    if participant is None:
        msg = "Participant not found"
        raise not_found(msg)
    return participant


def invite_or_404(task: EvalTask, invite_id: Any) -> EvalInvite:
    """An invite of this task (404 otherwise)."""
    pk = access.parse_uuid(invite_id)
    invite = EvalInvite.objects.filter(task=task, pk=pk).first() if pk is not None else None
    if invite is None:
        msg = "Invite not found"
        raise not_found(msg)
    return invite


def _with_counts(qs: QuerySet[Participant]) -> QuerySet[Participant]:
    return qs.select_related("user", "invite").annotate(
        n_submitted=Count("assignments", filter=Q(assignments__status=Assignment.Status.SUBMITTED)),
        n_skipped=Count("assignments", filter=Q(assignments__status=Assignment.Status.SKIPPED)),
        n_in_progress=Count("assignments", filter=Q(assignments__status__in=_OPEN)),
    )


def serialize_participant(participant: Participant, flags: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """TS ``ResearcherParticipant`` (counts from the ``_with_counts`` annotations when present)."""

    def count(name: str, statuses: tuple[str, ...]) -> int:
        value = getattr(participant, name, None)
        if value is not None:
            return int(value)
        return participant.assignments.filter(status__in=statuses).count()

    invite = participant.invite if participant.invite_id else None
    return {
        "id": str(participant.id),
        "label": participant.label,
        "kind": participant.kind,
        "status": participant.status,
        "email": participant.user.email if participant.user_id else None,
        "invite": {"id": str(invite.id), "label": invite.label} if invite is not None else None,
        "access_code_hint": participant.access_code_hint,
        "consent_at": participant.consent_at,
        "joined_at": participant.joined_at,
        "last_seen_at": participant.last_seen_at,
        "completed_at": participant.completed_at,
        "submitted": count("n_submitted", (Assignment.Status.SUBMITTED,)),
        "skipped": count("n_skipped", (Assignment.Status.SKIPPED,)),
        "in_progress": count("n_in_progress", _OPEN),
        "excluded": participant.excluded,
        "exclusion_reason": participant.exclusion_reason,
        "excluded_at": participant.excluded_at,
        "note": participant.note,
        "quality_flags": list((flags or {}).get(str(participant.id), [])),
        "created_at": participant.created_at,
    }


def participant_payload(task: EvalTask, participant: Participant) -> dict[str, Any]:
    """One participant as TS ``ResearcherParticipant`` (fresh counts and quality flags)."""
    fresh = _with_counts(Participant.objects.filter(pk=participant.pk)).first() or participant
    return serialize_participant(fresh, quality.participant_flags(task))


def list_participants(
    task: EvalTask,
    *,
    kind: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """TS ``Paginated<ResearcherParticipant>`` by participant number (preview participants included)."""
    qs = Participant.objects.filter(task=task)
    if kind:
        qs = qs.filter(kind=kind) if kind in Participant.Kind.values else qs.none()
    if status:
        qs = qs.filter(status=status) if status in Participant.Status.values else qs.none()
    if search and search.strip():
        term = search.strip()
        condition = (
            Q(user__email__icontains=term)
            | Q(note__icontains=term)
            | Q(invite__label__icontains=term)
            | Q(access_code_hint__iexact=term)
        )
        match = _LABEL_SEARCH.match(term)
        if match:
            condition |= Q(number=int(match.group(1)))
        qs = qs.filter(condition)
    flags = quality.participant_flags(task)
    return paginate(
        _with_counts(qs).order_by("number"),
        page,
        per_page,
        serialize=lambda participant: serialize_participant(participant, flags),
    )


# ---------------------------------------------------------------------------
# Access settings
# ---------------------------------------------------------------------------


def _access(task: EvalTask) -> dict[str, Any]:
    return merged_settings(task.settings)["access"]


def _require_access(task: EvalTask, key: str, what: str) -> None:
    if not _access(task).get(key):
        msg = f"{what} are turned off for this task"
        raise conflict(msg, "access_disabled")


# ---------------------------------------------------------------------------
# Access codes
# ---------------------------------------------------------------------------


def create_codes(task: EvalTask, count: Any) -> dict[str, Any]:
    """Create ``count`` access-code participants; TS ``CodesCreated`` with the plaintext codes (shown once)."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_CODES_PER_CALL:
        msg = f"Create between 1 and {MAX_CODES_PER_CALL} codes at a time"
        raise invalid(msg, [{"path": "count", "message": msg}])
    _require_access(task, "allow_access_codes", "Access codes")
    with transaction.atomic():
        first = access.allocate_participant_number(task)
        rows = []
        codes = []
        for offset in range(count):
            raw, digest, hint = access.new_access_code()
            participant = Participant(
                task=task,
                number=first + offset,
                kind=Participant.Kind.CODE,
                status=Participant.Status.INVITED,
                access_code_hash=digest,
                access_code_hint=hint,
            )
            rows.append(participant)
            codes.append(raw)
        Participant.objects.bulk_create(rows)
    return {
        "codes": [
            {"participant_id": str(participant.id), "label": participant.label, "code": code}
            for participant, code in zip(rows, codes, strict=True)
        ],
    }


def regenerate_code(task: EvalTask, participant: Participant) -> dict[str, Any]:
    """New access code for a code / invite-link participant (old code and cookie sessions stop working)."""
    if participant.kind not in _CODE_KINDS:
        msg = "Only access-code and invite-link participants have access codes"
        raise conflict(msg, "forbidden")
    if participant.kind == Participant.Kind.CODE:
        _require_access(task, "allow_access_codes", "Access codes")
    raw, digest, hint = access.new_access_code()
    participant.access_code_hash = digest
    participant.access_code_hint = hint
    participant.session_token_hash = None
    participant.save(update_fields=["access_code_hash", "access_code_hint", "session_token_hash"])
    return {"participant_id": str(participant.id), "label": participant.label, "code": raw}


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def public_origin() -> str:
    """The browser origin for links in emails: FRONTEND_PUBLIC_ORIGIN, else https://DJANGO_DOMAIN."""
    origin = (getattr(settings, "FRONTEND_PUBLIC_ORIGIN", "") or "").strip().rstrip("/")
    return origin or f"https://{getattr(settings, 'DJANGO_DOMAIN', 'localhost')}"


def setup_path(user: Any) -> str:
    """The relative set-password link of an account (allauth's password-reset key)."""
    from allauth.account.forms import default_token_generator
    from allauth.account.utils import user_pk_to_url_str

    key = f"{user_pk_to_url_str(user)}-{default_token_generator.make_token(user)}"
    return f"{SETUP_PATH_PREFIX}{key}"


def _utc_day_start() -> dt.datetime:
    return timezone.now().astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _quota_key(kind: str, user: Any) -> str:
    return f"human_eval:account_{kind}:{user.pk}:{_utc_day_start().date().isoformat()}"


def _quota_used(kind: str, user: Any) -> int:
    """Today's (UTC) cache counter ``kind`` of this researcher; 0 when the cache is unavailable."""
    try:
        return int(cache.get(_quota_key(kind, user), 0) or 0)
    except Exception:
        logger.exception("human_eval account quota cache unavailable")
        return 0


def _quota_add(kind: str, user: Any, amount: int) -> None:
    if amount <= 0:
        return
    key = _quota_key(kind, user)
    try:
        if not cache.add(key, amount, timeout=2 * 24 * 3600):
            cache.incr(key, amount)
    except Exception:
        logger.exception("human_eval account quota cache unavailable")


def emails_sent_today(user: Any) -> int:
    """Account emails this researcher queued today (UTC; cache counter)."""
    return _quota_used("emails", user)


def _count_emails(user: Any, sent: int) -> None:
    _quota_add("emails", user, sent)


def lookups_today(user: Any) -> int:
    """Valid email addresses this researcher looked up in ``add_accounts`` today (UTC; cache counter)."""
    return _quota_used("lookups", user)


def _lock_researcher(researcher: Any) -> None:
    """Row-lock the researcher's user until the transaction ends: serializes their account additions.

    The daily caps are check-then-write (``users_created_today`` counts committed profiles, the
    cache counters are raised after the check), so two requests of one researcher on different
    tasks (different task-row locks) would otherwise both pass them. ``FOR NO KEY UPDATE`` does
    not block inserts that only reference the user (foreign keys take ``FOR KEY SHARE``), and no
    other code locks user rows, so this adds no lock-order cycle.
    """
    from backend.users.models import User

    list(User.objects.select_for_update(no_key=True).filter(pk=researcher.pk).values_list("pk", flat=True))


def users_created_today(user: Any) -> int:
    """Evaluator accounts this researcher created today (UTC)."""
    return EvaluatorProfile.objects.filter(created_by=user, created_at__gte=_utc_day_start()).count()


def _normalize_emails(emails: Any) -> list[tuple[str, str | None]]:
    """``[(email, error | None)]`` in input order: lower-cased, validated, repeats flagged."""
    seen: set[str] = set()
    result: list[tuple[str, str | None]] = []
    for raw in emails:
        email = str(raw or "").strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            result.append((email or str(raw or ""), "Not a valid email address"))
            continue
        if email in seen:
            result.append((email, "Listed more than once"))
            continue
        seen.add(email)
        result.append((email, None))
    return result


def _existing_users(emails: list[str]) -> dict[str, Any]:
    from allauth.account.models import EmailAddress

    from backend.users.models import User

    found: dict[str, Any] = {}
    if not emails:
        return found
    query = Q()
    for email in emails:
        query |= Q(email__iexact=email)
    for user in User.objects.filter(query):
        found.setdefault(user.email.lower(), user)
    # A user may sign in with a secondary address: never create a second account for it.
    for address in EmailAddress.objects.filter(query).select_related("user"):
        found.setdefault(address.email.lower(), address.user)
    return found


def _create_evaluator(email: str, researcher: Any) -> Any:
    """Create a restricted evaluator user inside the current savepoint; raises ``_RollbackEmail`` for staff."""
    from allauth.account.models import EmailAddress

    from backend.users.models import User

    user = User.objects.create_user(email=email, password=None)
    flags = User.objects.filter(pk=user.pk).values("is_staff", "is_superuser").first() or {}
    if flags.get("is_staff") or flags.get("is_superuser"):
        raise _RollbackEmail
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    EvaluatorProfile.objects.create(user=user, is_restricted=True, created_by=researcher)
    return user


def _send_account_emails(messages: list[dict[str, Any]]) -> None:
    """Thread target: send the account emails one by one (failures are logged, never raised)."""
    from django.core.mail import send_mail

    for message in messages:
        try:
            send_mail(message["subject"], message["body"], None, [message["to"]], fail_silently=False)
        except Exception:
            logger.exception("Could not send the human-eval account email")


def _email_message(task: EvalTask, email: str, link: str | None) -> dict[str, Any]:
    origin = public_origin()
    lines = [
        "Hello,",
        "",
        f'You have been invited to take part in the evaluation study "{task.title}".',
        "",
    ]
    if link:
        lines += [
            "An account has been created for you. Choose your password here:",
            f"{origin}{link}",
            "",
            "The link expires after a few days or once you have signed in.",
            "",
        ]
    lines += [f"Then sign in and open the evaluation portal: {origin}{PORTAL_PATH}", ""]
    return {"to": email, "subject": f"Invitation: {task.title}"[:200], "body": "\n".join(lines)}


def _plan_accounts(task: EvalTask, entries: list[tuple[str, str | None]], *, restricted: bool) -> list[tuple]:
    """``[(email, action, user | error)]`` with action ``error`` / ``link`` (existing user) / ``create``."""
    existing = _existing_users([email for email, error in entries if error is None])
    taken = set(Participant.objects.filter(task=task, user__isnull=False).values_list("user_id", flat=True))
    plan: list[tuple] = []
    for email, error in entries:
        if error is not None:
            plan.append((email, "error", error))
        elif email in existing:
            user = existing[email]
            if user.pk in taken:
                plan.append((email, "error", "Already a participant of this task"))
            else:
                plan.append((email, "link", user))
        elif restricted:
            plan.append((email, "create", None))
        else:
            plan.append((email, "error", "No account uses this email address"))
    return plan


def add_accounts(task: EvalTask, researcher: Any, data: dict[str, Any]) -> dict[str, Any]:
    """TS ``AccountsCreated`` for TS ``AccountsInput`` (per-email results; 409 for disabled access or quotas)."""
    emails = data.get("emails")
    restricted = data.get("restricted", True)
    send_email = data.get("send_email", False)
    if not isinstance(emails, list) or not emails:
        msg = "List at least one email address"
        raise invalid(msg, [{"path": "emails", "message": msg}])
    if len(emails) > MAX_EMAILS_PER_CALL:
        msg = f"At most {MAX_EMAILS_PER_CALL} email addresses at a time"
        raise invalid(msg, [{"path": "emails", "message": msg}])
    if not isinstance(restricted, bool) or not isinstance(send_email, bool):
        msg = "restricted and send_email must be true or false"
        raise invalid(
            msg,
            [{"path": "restricted" if not isinstance(restricted, bool) else "send_email", "message": msg}],
        )
    _require_access(task, "allow_accounts", "Account participants")

    entries = _normalize_emails(emails)
    looked_up = sum(1 for _, error in entries if error is None)
    results: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    created: list[Participant] = []
    with transaction.atomic():
        # Locks the task row first: concurrent additions to this task queue here, so ``taken`` stays true.
        number = access.allocate_participant_number(task)
        # Then the researcher: their requests on other tasks queue here, so every cap below sees the
        # accounts, emails and lookups of the previous request.
        _lock_researcher(researcher)
        # Counted before the lookup and kept whatever happens next: the per-email results (and even a
        # new-user quota refusal) tell which addresses have an account.
        if lookups_today(researcher) + looked_up > LOOKUPS_PER_DAY:
            msg = f"You can look up at most {LOOKUPS_PER_DAY} email addresses per day"
            raise conflict(msg, "quota_exceeded")
        _quota_add("lookups", researcher, looked_up)
        plan = _plan_accounts(task, entries, restricted=restricted)
        new_users = sum(1 for _, action, _ in plan if action == "create")
        if new_users and users_created_today(researcher) + new_users > NEW_USERS_PER_DAY:
            msg = f"You can create at most {NEW_USERS_PER_DAY} evaluator accounts per day"
            raise conflict(msg, "quota_exceeded")
        planned_emails = sum(1 for _, action, _ in plan if action in ("create", "link"))
        if send_email and planned_emails and emails_sent_today(researcher) + planned_emails > EMAILS_PER_DAY:
            msg = f"You can send at most {EMAILS_PER_DAY} invitation emails per day"
            raise conflict(msg, "quota_exceeded")
        for email, action, value in plan:
            if action == "error":
                results.append(_account_result(email, error=value))
                continue
            try:
                with transaction.atomic():
                    user = _create_evaluator(email, researcher) if action == "create" else value
                    participant = Participant.objects.create(
                        task=task,
                        number=number,
                        kind=Participant.Kind.ACCOUNT,
                        status=Participant.Status.INVITED,
                        user=user,
                    )
            except _RollbackEmail:
                results.append(_account_result(email, error="Cannot create this account"))
                continue
            except IntegrityError:
                # Lost a race: the user joined this task, or the address was registered meanwhile.
                error = "Already a participant of this task" if action == "link" else "This account already exists"
                results.append(_account_result(email, error=error))
                continue
            number += 1
            link = setup_path(user) if action == "create" else None
            created.append(participant)
            results.append(
                {"email": email, "participant": participant, "user_created": action == "create", "setup_path": link},
            )
            if send_email:
                messages.append(_email_message(task, email, link))
        if messages:
            transaction.on_commit(lambda: _dispatch_emails(messages))
        # While the researcher lock is still held: the next request must see these emails counted.
        _count_emails(researcher, len(messages))
    flags = quality.participant_flags(task)
    fresh = {p.pk: p for p in _with_counts(Participant.objects.filter(pk__in=[p.pk for p in created]))}
    for result in results:
        participant = result.get("participant")
        if isinstance(participant, Participant):
            result["participant"] = serialize_participant(fresh.get(participant.pk, participant), flags)
            result["error"] = None
    return {"results": results}


def _dispatch_emails(messages: list[dict[str, Any]]) -> None:
    from backend.common.threading import dispatch_in_thread

    dispatch_in_thread(_send_account_emails, messages, name="human-eval-account-emails")


def _account_result(email: str, *, error: str) -> dict[str, Any]:
    return {"email": email, "participant": None, "user_created": False, "setup_path": None, "error": error}


def issue_setup_link(task: EvalTask, researcher: Any, participant: Participant) -> dict[str, Any]:
    """TS ``SetupLink`` for an account the caller created that never logged in (else 409 ``forbidden``)."""
    user = participant.user if participant.user_id else None
    profile = EvaluatorProfile.objects.filter(user=user).first() if user is not None else None
    allowed = (
        user is not None
        and participant.kind == Participant.Kind.ACCOUNT
        and profile is not None
        and profile.created_by_id == getattr(researcher, "pk", None)
        and user.last_login is None
        and not user.is_staff
        and not user.is_superuser
    )
    if not allowed:
        msg = "Set-password links can only be issued for accounts you created that have never signed in"
        raise conflict(msg, "forbidden")
    timeout = int(getattr(settings, "PASSWORD_RESET_TIMEOUT", 3 * 24 * 3600))
    return {"setup_path": setup_path(user), "expires_at": timezone.now() + dt.timedelta(seconds=timeout)}


# ---------------------------------------------------------------------------
# Participant edits
# ---------------------------------------------------------------------------


def update_participant(task: EvalTask, researcher: Any, participant: Participant, data: dict[str, Any]) -> Participant:
    """PATCH TS ``ParticipantUpdateInput``: exclusion (+ reason), note, ``status: "withdrawn"``."""
    errors: list[dict[str, str]] = []
    excluded = data.get("excluded")
    if "excluded" in data and not isinstance(excluded, bool):
        errors.append({"path": "excluded", "message": "Expected true or false"})
    for key in ("exclusion_reason", "note"):
        value = data.get(key)
        if key in data and value is not None and (not isinstance(value, str) or len(value) > MAX_NOTE):
            errors.append({"path": key, "message": f"Text of at most {MAX_NOTE} characters"})
    if "status" in data and data["status"] != Participant.Status.WITHDRAWN:
        errors.append({"path": "status", "message": 'Only "withdrawn" can be set'})
    if errors:
        msg = "The participant update is invalid"
        raise invalid(msg, errors)
    now = timezone.now()
    with transaction.atomic():
        fields: set[str] = set()
        if "excluded" in data and excluded != participant.excluded:
            participant.excluded = excluded
            participant.excluded_at = now if excluded else None
            participant.excluded_by = researcher if excluded else None
            if not excluded:
                participant.exclusion_reason = ""
            fields |= {"excluded", "excluded_at", "excluded_by", "exclusion_reason"}
        if data.get("exclusion_reason") is not None and participant.excluded:
            participant.exclusion_reason = data["exclusion_reason"]
            fields.add("exclusion_reason")
        if data.get("note") is not None:
            participant.note = data["note"]
            fields.add("note")
        if data.get("status") == Participant.Status.WITHDRAWN and participant.status != Participant.Status.WITHDRAWN:
            participant.status = Participant.Status.WITHDRAWN
            participant.withdrawn_at = now
            participant.session_token_hash = None
            fields |= {"status", "withdrawn_at", "session_token_hash"}
            Assignment.objects.filter(participant=participant, status__in=_OPEN).update(
                status=Assignment.Status.EXPIRED,
            )
        if fields:
            participant.save(update_fields=sorted(fields))
    return participant


def delete_participant(participant: Participant, *, user: Any = None) -> None:
    """Delete the participant and all of their data (assignments, responses, observations, files...).

    Deleting a non-preview participant who already submitted or skipped work after the task opened is
    appended to ``task.protocol_log`` as ``participant_deleted:<number>`` so results report it
    (``analysis.PROTOCOL_PARTICIPANT_DELETED``: no longer confirmatory) instead of silently shrinking.
    """
    with transaction.atomic():
        task = EvalTask.objects.select_for_update(of=("self",)).get(pk=participant.task_id)
        had_work = (
            task.opened_at is not None
            and participant.kind != Participant.Kind.PREVIEW
            and participant.assignments.filter(
                status__in=[Assignment.Status.SUBMITTED, Assignment.Status.SKIPPED],
            ).exists()
        )
        if had_work:
            log = list(task.protocol_log or [])
            log.append(
                {
                    "at": timezone.now().isoformat(),
                    "by": getattr(user, "email", "") or "",
                    "field": f"participant_deleted:{participant.number}",
                    "old_sha256": "",
                    "new_sha256": "",
                },
            )
            task.protocol_log = log
            task.save(update_fields=["protocol_log"])
        participant.delete()


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


def serialize_invite(invite: EvalInvite) -> dict[str, Any]:
    """TS ``Invite``."""
    return {
        "id": str(invite.id),
        "token": invite.token,
        "path": f"{PORTAL_PATH}/join/{invite.token}",
        "label": invite.label,
        "max_uses": invite.max_uses,
        "uses": invite.uses,
        "expires_at": invite.expires_at,
        "is_active": invite.is_active,
        "created_at": invite.created_at,
    }


def list_invites(task: EvalTask) -> list[dict[str, Any]]:
    """Every invite of the task, newest first."""
    return [serialize_invite(invite) for invite in EvalInvite.objects.filter(task=task).order_by("-created_at")]


def _invite_fields(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    fields: dict[str, Any] = {}
    if "label" in data:
        label = data["label"]
        if label is None:
            label = ""
        if not isinstance(label, str) or len(label) > MAX_INVITE_LABEL:
            errors.append({"path": "label", "message": f"Text of at most {MAX_INVITE_LABEL} characters"})
        else:
            fields["label"] = label.strip()
    if "max_uses" in data:
        max_uses = data["max_uses"]
        if max_uses is not None and (
            isinstance(max_uses, bool) or not isinstance(max_uses, int) or not 1 <= max_uses <= MAX_INVITE_USES
        ):
            errors.append({"path": "max_uses", "message": f"Empty (unlimited) or 1 to {MAX_INVITE_USES}"})
        else:
            fields["max_uses"] = max_uses
    if "expires_at" in data:
        value = data["expires_at"]
        if value in (None, ""):
            fields["expires_at"] = None
        else:
            parsed = (
                value if isinstance(value, dt.datetime) else parse_datetime(value) if isinstance(value, str) else None
            )
            if parsed is None:
                errors.append({"path": "expires_at", "message": "Expected an ISO 8601 date-time"})
            else:
                fields["expires_at"] = timezone.make_aware(parsed, dt.UTC) if timezone.is_naive(parsed) else parsed
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            errors.append({"path": "is_active", "message": "Expected true or false"})
        else:
            fields["is_active"] = data["is_active"]
    if errors:
        msg = "The invite is invalid"
        raise invalid(msg, errors)
    return fields


def create_invite(task: EvalTask, researcher: Any, data: dict[str, Any]) -> EvalInvite:
    """A new invite link (``allow_invite_links``, else 409 ``access_disabled``)."""
    fields = _invite_fields(data)
    _require_access(task, "allow_invite_links", "Invite links")
    return EvalInvite.objects.create(task=task, created_by=researcher, **fields)


def update_invite(invite: EvalInvite, data: dict[str, Any]) -> EvalInvite:
    """PATCH an invite (label, max_uses, expires_at, is_active)."""
    fields = _invite_fields(data)
    for key, value in fields.items():
        setattr(invite, key, value)
    if fields:
        invite.save(update_fields=list(fields))
    return invite


def delete_invite(invite: EvalInvite) -> None:
    """Delete an invite link (its participants stay; their invite reference is cleared)."""
    invite.delete()


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def start_preview(task: EvalTask, researcher: Any) -> dict[str, Any]:
    """TS ``PreviewSession``: get or create the researcher's preview participant (409 ``not_frozen`` on drafts)."""
    if not task.is_frozen or task.status == EvalTask.Status.DRAFT:
        msg = "Open the task before previewing it"
        raise conflict(msg, "not_frozen")
    with transaction.atomic():
        number = access.allocate_participant_number(task)  # locks the task row: no duplicate preview rows
        existing = Participant.objects.filter(task=task, user=researcher).first()
        if existing is not None and existing.kind != Participant.Kind.PREVIEW:
            msg = "You already take part in this task with your account"
            raise conflict(msg, "already_participant")
        if existing is not None and existing.status == Participant.Status.WITHDRAWN:
            # A withdrawn preview cannot work any more; previews are throwaway, start a fresh one.
            existing.delete()
            existing = None
        if existing is None:
            now = timezone.now()
            existing = Participant.objects.create(
                task=task,
                number=number,
                kind=Participant.Kind.PREVIEW,
                status=Participant.Status.ACTIVE,
                user=researcher,
                consent_at=now,
                joined_at=now,
            )
    return {"participant_id": str(existing.id), "portal_path": f"{PORTAL_PATH}/t/{task.id}"}


def delete_preview(task: EvalTask, researcher: Any) -> int:
    """Delete the researcher's preview participant of the task and its data; returns how many were deleted."""
    with transaction.atomic():
        deleted, _ = Participant.objects.filter(task=task, user=researcher, kind=Participant.Kind.PREVIEW).delete()
    return deleted
