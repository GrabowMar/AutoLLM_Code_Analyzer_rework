"""Services of the co-authors module: invite links, joining, workspace selection, members and the activity trail.

**Tokens.** An invite token is ``coi_`` + 43 url-safe characters (256 random bits).
Only its sha256 hex is stored, in a unique column; lookups go through that index,
so no secret-dependent comparison happens in Python. The raw value is returned
once by :func:`create_invite` and travels only in a URL fragment and in POST bodies.

**One resolver.** :func:`usable_coauthorships` / :func:`resolve_selection` are the
only way a co-authorship becomes usable: the per-request check of the middleware,
switching, joining, the login restore and the stream re-check all go through them.
A co-authorship is usable while it is not ended, its owner is active and the owner
is not limited to the evaluation portal.

**Throttling** counts *failed* token lookups only. Signed-in callers are counted
per account (20 per 10 minutes); the shared per-address bucket of
``human_eval.services.access`` only covers the signed-out preview, so a flood of
bad tokens cannot lock invites for people who are signed in.

Errors are :class:`~backend.coauthors.errors.CoauthorError`; writes that may be
followed by a raise sit inside ``transaction.atomic()``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import secrets
import unicodedata
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from backend.coauthors import errors
from backend.coauthors.identity import real_user
from backend.coauthors.models import CoauthorAction
from backend.coauthors.models import CoauthorInvite
from backend.coauthors.models import Coauthorship
from backend.common.threading import dispatch_in_thread
from backend.human_eval.services import access

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

SESSION_KEY = "coauthor.workspace"
SIGNUP_SESSION_KEY = "coauthor.signup_invite"

TOKEN_PREFIX = "coi_"  # noqa: S105 - a public marker, not a secret
TOKEN_RE = re.compile(r"^coi_[A-Za-z0-9_-]{43}$")
TOKEN_PREFIX_LENGTH = 12

MAX_ACTIVE_INVITES = 20
MAX_INVITES_LISTED = 50
MAX_MEMBERS_LISTED = 100
NAME_MAX_LENGTH = 80
LABEL_MAX_LENGTH = 120
PATH_MAX_LENGTH = 300
ACTIVE_TOUCH_SECONDS = 60

THROTTLE_SCOPE = "coauthor_invite"
USER_FAILURE_LIMIT = 20
USER_FAILURE_WINDOW_SECONDS = 600

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SETTINGS_PATH = "/users/settings#coauthors"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def new_token() -> str:
    """A fresh raw invite token (``coi_`` + 43 url-safe characters)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """The stored form of a token: its sha256 hex digest."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _token_hash_or_none(raw: Any) -> str | None:
    """The hash to look up, or None when ``raw`` does not have the shape of a token."""
    if not isinstance(raw, str) or not TOKEN_RE.match(raw):
        return None
    return hash_token(raw)


# ---------------------------------------------------------------------------
# Names shown to other people
# ---------------------------------------------------------------------------


def _clean(value: Any, limit: int) -> str:
    """``value`` without control and direction characters, whitespace collapsed, cut at ``limit``."""
    kept: list[str] = []
    for char in str(value or ""):
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) not in ("Cc", "Cf"):
            kept.append(char)
    return " ".join("".join(kept).split())[:limit].strip()


def clean_name(value: Any) -> str:
    """A profile name safe to show to someone else (no control or direction characters, at most 80 characters)."""
    return _clean(value, NAME_MAX_LENGTH)


def display_name(user: Any) -> str:
    """The cleaned profile name, or the e-mail address when the name is blank."""
    return clean_name(getattr(user, "name", "")) or str(getattr(user, "email", "") or "")


def masked_email(email: str) -> str:
    """``j•••@example.org``: enough for a signed-out visitor to recognise the inviter, not the full address."""
    local, _, domain = str(email or "").partition("@")
    return f"{local[:1]}•••@{domain}"


def person_payload(user: Any) -> dict[str, Any]:
    """API ``Person``."""
    return {"id": user.pk, "email": user.email, "name": clean_name(user.name), "display_name": display_name(user)}


def workspace_payload(coauthorship: Coauthorship) -> dict[str, Any]:
    """API ``Workspace`` (the member's view of a co-authorship)."""
    owner = coauthorship.owner
    return {
        "coauthorship_id": coauthorship.pk,
        "owner": person_payload(owner),
        "owner_is_staff": bool(owner.is_staff),
        "joined_at": coauthorship.created_at,
        "last_active_at": coauthorship.last_active_at,
    }


def invite_payload(invite: CoauthorInvite, now: datetime | None = None) -> dict[str, Any]:
    """API ``Invite`` (never the token or its hash)."""
    return {
        "id": invite.pk,
        "label": invite.label,
        "token_prefix": invite.token_prefix,
        "status": invite.status(now),
        "max_uses": invite.max_uses,
        "uses": invite.uses,
        "expires_at": invite.expires_at,
        "created_at": invite.created_at,
        "revoked_at": invite.revoked_at,
    }


def member_payload(coauthorship: Coauthorship) -> dict[str, Any]:
    """API ``Member`` (the owner's view of a co-authorship)."""
    if coauthorship.revoked_at is None:
        status = "active"
    elif coauthorship.revoked_by_id == coauthorship.member_id:
        status = "left"
    else:
        status = "removed"
    invite = coauthorship.invite if coauthorship.invite_id else None
    return {
        "coauthorship_id": coauthorship.pk,
        "member": person_payload(coauthorship.member),
        "status": status,
        "joined_at": coauthorship.created_at,
        "last_active_at": coauthorship.last_active_at,
        "ended_at": coauthorship.revoked_at,
        "invite_label": invite.label if invite is not None else "",
    }


# ---------------------------------------------------------------------------
# Throttle (failed token lookups only)
# ---------------------------------------------------------------------------


def _signed_in(user: Any) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def _user_throttle_key(user: Any) -> str:
    return f"{THROTTLE_SCOPE}:user:{user.pk}"


def throttle_blocked(request: HttpRequest) -> bool:
    """Whether earlier failed lookups block this caller (check BEFORE the lookup; answer 429)."""
    user = real_user(request)
    if _signed_in(user):
        return access.throttle_blocked(_user_throttle_key(user), USER_FAILURE_LIMIT, USER_FAILURE_WINDOW_SECONDS)
    return access.lookup_failures_blocked(request, scope=THROTTLE_SCOPE)


def record_failure(request: HttpRequest) -> None:
    """Count one malformed or unknown token for this caller (a known but dead link is not counted)."""
    user = real_user(request)
    if _signed_in(user):
        access.throttle_hit(_user_throttle_key(user), USER_FAILURE_LIMIT, USER_FAILURE_WINDOW_SECONDS)
    else:
        access.record_lookup_failure(request, scope=THROTTLE_SCOPE)


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def usable_coauthorships(member: Any) -> QuerySet[Coauthorship]:
    """The workspaces ``member`` can work in right now (owner loaded).

    Not ended, owner active, and owner not limited to the evaluation portal (the
    rule of ``human_eval.services.access.is_restricted_evaluator``: staff are
    never restricted).
    """
    return (
        Coauthorship.objects.select_related("owner")
        .filter(member_id=member.pk, revoked_at__isnull=True, owner__is_active=True)
        .exclude(
            owner__is_staff=False,
            owner__is_superuser=False,
            owner__evaluator_profile__is_restricted=True,
        )
    )


def resolve_selection(real: Any, selected: Any, *, touch: bool = False) -> Coauthorship | None:
    """The usable co-authorship ``selected`` (a primary key) of ``real``, or None.

    None covers every way a selection can be wrong: removed, left, owner inactive,
    restricted or deleted, another person's row, or a value that is not an integer.
    With ``touch``, ``last_active_at`` is refreshed at most once a minute.
    """
    if isinstance(selected, bool) or not isinstance(selected, int):
        return None
    if not _signed_in(real):
        return None
    coauthorship = usable_coauthorships(real).filter(pk=selected).first()
    if coauthorship is None:
        return None
    if touch:
        now = timezone.now()
        last = coauthorship.last_active_at
        if last is None or (now - last).total_seconds() > ACTIVE_TOUCH_SECONDS:
            Coauthorship.objects.filter(pk=coauthorship.pk).update(last_active_at=now)
            coauthorship.last_active_at = now
    return coauthorship


def owns_shared_workspace(user_pk: Any) -> bool:
    """Whether anyone has ever been a co-author of this user (ended rows count: what they built is still there)."""
    return Coauthorship.objects.filter(owner_id=user_pk).exists()


# ---------------------------------------------------------------------------
# Context and switching
# ---------------------------------------------------------------------------


def context_payload(actor: Any, current: Coauthorship | None) -> dict[str, Any]:
    """API ``Context``: the real person, the workspace they work in (if any) and the ones they could."""
    workspaces = usable_coauthorships(actor).order_by("-created_at", "-id")
    return {
        "actor": person_payload(actor),
        "acting_as": workspace_payload(current) if current is not None else None,
        "workspaces": [workspace_payload(row) for row in workspaces],
    }


def _select(request: HttpRequest, coauthorship: Coauthorship, now: datetime) -> None:
    request.session[SESSION_KEY] = coauthorship.pk
    Coauthorship.objects.filter(pk=coauthorship.pk).update(last_selected_at=now)
    coauthorship.last_selected_at = now


def select_workspace(request: HttpRequest, coauthorship_id: int | None) -> Coauthorship | None:
    """Choose the workspace of this session: a co-authorship of the caller, or None for their own account.

    404 ``not_found`` unless ``coauthorship_id`` is a usable membership of the caller.
    """
    user = real_user(request)
    if coauthorship_id is None:
        request.session.pop(SESSION_KEY, None)
        Coauthorship.objects.filter(member_id=user.pk, last_selected_at__isnull=False).update(last_selected_at=None)
        return None
    coauthorship = resolve_selection(user, coauthorship_id)
    if coauthorship is None:
        msg = "Workspace not found"
        raise errors.not_found(msg)
    _select(request, coauthorship, timezone.now())
    return coauthorship


def restore_selection(request: HttpRequest, user: Any) -> None:
    """After login: put the workspace this person last chose (if still usable) into the new session."""
    session = getattr(request, "session", None)
    if session is None:
        return
    selected = (
        usable_coauthorships(user)
        .filter(last_selected_at__isnull=False)
        .order_by("-last_selected_at", "-id")
        .values_list("pk", flat=True)
        .first()
    )
    if selected is not None:
        session[SESSION_KEY] = selected


# ---------------------------------------------------------------------------
# Invite links (owner)
# ---------------------------------------------------------------------------


def create_invite(
    owner: Any,
    *,
    label: str = "",
    expires_in_days: int = 7,
    max_uses: int = 1,
) -> tuple[str, CoauthorInvite]:
    """Create an invite link for ``owner``; returns ``(raw token, invite)``. The raw token is not stored.

    409 ``too_many_invites`` when the owner already has 20 active links.
    """
    now = timezone.now()
    raw = new_token()
    with transaction.atomic():
        # Serialise creation per owner so the cap cannot be raced.
        list(get_user_model().objects.select_for_update().filter(pk=owner.pk).values_list("pk", flat=True))
        active = CoauthorInvite.objects.filter(
            created_by_id=owner.pk,
            revoked_at__isnull=True,
            expires_at__gt=now,
            uses__lt=F("max_uses"),
        ).count()
        if active >= MAX_ACTIVE_INVITES:
            msg = f"You already have {MAX_ACTIVE_INVITES} active invite links. Revoke one to create another."
            raise errors.conflict(msg, "too_many_invites")
        invite = CoauthorInvite.objects.create(
            created_by=owner,
            token_hash=hash_token(raw),
            token_prefix=raw[:TOKEN_PREFIX_LENGTH],
            label=_clean(label, LABEL_MAX_LENGTH),
            max_uses=max_uses,
            expires_at=now + dt.timedelta(days=expires_in_days),
        )
    return raw, invite


def invite_path(raw: str) -> str:
    """The relative link of an invite: the token sits in the URL fragment, which browsers never send."""
    return f"/join#{raw}"


def list_invites(owner: Any) -> list[CoauthorInvite]:
    """The owner's invite links, newest first (at most 50)."""
    return list(
        CoauthorInvite.objects.filter(created_by_id=owner.pk).order_by("-created_at", "-id")[:MAX_INVITES_LISTED],
    )


def revoke_invite(owner: Any, invite_id: int) -> None:
    """Revoke one of the owner's links (idempotent). People who already joined keep their access."""
    with transaction.atomic():
        invite = CoauthorInvite.objects.select_for_update().filter(pk=invite_id, created_by_id=owner.pk).first()
        if invite is None:
            msg = "Invite link not found"
            raise errors.not_found(msg)
        if invite.revoked_at is None:
            invite.revoked_at = timezone.now()
            invite.save(update_fields=["revoked_at"])


# ---------------------------------------------------------------------------
# Preview and accept
# ---------------------------------------------------------------------------


def registration_open() -> bool:
    """The instance-wide sign-up switch."""
    return bool(getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True))


def _drop_signup_flag(request: HttpRequest) -> None:
    session = getattr(request, "session", None)
    if session is not None and SIGNUP_SESSION_KEY in session:
        del session[SIGNUP_SESSION_KEY]


def _may_sign_up_with(invite: CoauthorInvite, now: datetime) -> bool:
    """Whether this link lets a visitor register while registration is closed.

    Only an administrator's link does, and only under its sign-up cap.
    """
    owner = invite.created_by
    return bool(owner.is_staff) and invite.signup_uses < invite.max_uses and invite.is_usable(now)


def _flag_signup(request: HttpRequest, invite: CoauthorInvite, now: datetime) -> bool:
    """``can_sign_up`` for a signed-out visitor holding a usable link; sets or clears the session flag.

    With registration open nothing is written (no session is created for the visitor).
    """
    if registration_open():
        _drop_signup_flag(request)
        return True
    if _may_sign_up_with(invite, now):
        request.session[SIGNUP_SESSION_KEY] = invite.pk
        return True
    _drop_signup_flag(request)
    return False


def preview(request: HttpRequest, token: str) -> dict[str, Any]:
    """What an invite link offers, for the confirm screen. Grants nothing and changes no row.

    One 404 ``invite_invalid`` for every unusable link; 429 ``rate_limited`` while blocked.
    """
    user = real_user(request)
    authenticated = _signed_in(user)
    if throttle_blocked(request):
        raise errors.rate_limited()
    token_hash = _token_hash_or_none(token)
    invite = (
        CoauthorInvite.objects.select_related("created_by").filter(token_hash=token_hash).first()
        if token_hash is not None
        else None
    )
    if invite is None:
        record_failure(request)
        _drop_signup_flag(request)
        raise errors.invite_invalid()
    now = timezone.now()
    owner = invite.created_by
    state = "valid"
    coauthorship_id: int | None = None
    if authenticated and owner.pk == user.pk:
        state = "own_invite"
    else:
        existing = usable_coauthorships(user).filter(owner_id=owner.pk).first() if authenticated else None
        if existing is not None:
            state = "already_member"
            coauthorship_id = existing.pk
        elif not invite.is_usable(now) or (authenticated and removed_since(invite, user)):
            _drop_signup_flag(request)
            raise errors.invite_invalid()
    if authenticated:
        _drop_signup_flag(request)
        can_sign_up = False
    else:
        can_sign_up = _flag_signup(request, invite, now)
    return {
        "state": state,
        "inviter_name": clean_name(owner.name),
        "inviter_email": owner.email if authenticated else masked_email(owner.email),
        "expires_at": invite.expires_at,
        "authenticated": authenticated,
        "can_sign_up": can_sign_up,
        "coauthorship_id": coauthorship_id,
    }


def removed_since(invite: CoauthorInvite, user: Any) -> bool:
    """Whether the invite's owner removed ``user`` after this link was made.

    A removed co-author can come back only through a link created after the removal,
    never through another link of the owner that was already shared (a multi-use team link).
    """
    return Coauthorship.objects.filter(
        owner_id=invite.created_by_id,
        member_id=user.pk,
        revoked_by_id=invite.created_by_id,
        revoked_at__gt=invite.created_at,
    ).exists()


def accept(request: HttpRequest, token: str) -> tuple[str, Coauthorship]:
    """Join the workspace an invite link belongs to; returns ``("joined" | "already_member", coauthorship)``.

    The invite row is locked for the whole operation, so ``max_uses`` is exact. The
    new (or existing) co-authorship becomes the selected workspace of this session.
    The owner is told by e-mail about a new join once the transaction commits.
    """
    from backend.human_eval.services.access import is_restricted_evaluator

    user = real_user(request)
    if throttle_blocked(request):
        raise errors.rate_limited()
    token_hash = _token_hash_or_none(token)
    with transaction.atomic():
        invite = (
            CoauthorInvite.objects.select_for_update(of=("self",))
            .select_related("created_by")
            .filter(token_hash=token_hash)
            .first()
            if token_hash is not None
            else None
        )
        if invite is None:
            record_failure(request)
            raise errors.invite_invalid()
        owner = invite.created_by
        if owner.pk == user.pk:
            msg = "This is your own invite link."
            raise errors.conflict(msg, "own_invite")
        if is_restricted_evaluator(user):
            msg = "Evaluator accounts cannot join a workspace."
            raise errors.CoauthorError(403, msg, "evaluator_denied")
        if not owner.is_active or is_restricted_evaluator(owner):
            raise errors.invite_invalid()
        now = timezone.now()
        status = "already_member"
        coauthorship = Coauthorship.objects.filter(
            owner_id=owner.pk,
            member_id=user.pk,
            revoked_at__isnull=True,
        ).first()
        if coauthorship is None:
            if invite.status(now) != CoauthorInvite.STATUS_ACTIVE or removed_since(invite, user):
                raise errors.invite_invalid()
            try:
                with transaction.atomic():
                    coauthorship = Coauthorship.objects.create(owner=owner, member=user, invite=invite)
            except IntegrityError:
                # The same person joined through another link of this owner at the same moment.
                coauthorship = Coauthorship.objects.get(
                    owner_id=owner.pk,
                    member_id=user.pk,
                    revoked_at__isnull=True,
                )
            else:
                status = "joined"
                CoauthorInvite.objects.filter(pk=invite.pk).update(uses=F("uses") + 1)
                notify_owner_of_join(coauthorship, invite, now)
        coauthorship.owner = owner
        _select(request, coauthorship, now)
    return status, coauthorship


def join_message(coauthorship: Coauthorship, invite: CoauthorInvite, when: datetime) -> dict[str, str]:
    """The plain-text e-mail that tells an owner somebody joined their workspace."""
    from backend.human_eval.services.participants import public_origin

    link = invite.label or invite.token_prefix
    joined = when.astimezone(dt.UTC).strftime("%d %B %Y, %H:%M UTC")
    lines = [
        f'{coauthorship.member.email} joined your workspace as a co-author on {joined} with the invite link "{link}".',
        "",
        "They can see and change everything in your workspace.",
        "",
        f"If you did not expect this, remove them here: {public_origin()}{SETTINGS_PATH}",
        "",
    ]
    return {
        "to": coauthorship.owner.email,
        "subject": "New co-author in your LLM Lab workspace",
        "body": "\n".join(lines),
    }


def _send_join_email(message: dict[str, str]) -> None:
    """Thread target: send the join notification (failures are logged, never raised)."""
    from django.core.mail import send_mail

    try:
        send_mail(message["subject"], message["body"], None, [message["to"]], fail_silently=False)
    except Exception:
        logger.exception("Could not send the co-author join email")


def _dispatch_join_email(message: dict[str, str]) -> None:
    try:
        dispatch_in_thread(_send_join_email, message, name="coauthor-join-email")
    except Exception:
        logger.exception("Could not start the co-author join email thread")


def notify_owner_of_join(coauthorship: Coauthorship, invite: CoauthorInvite, when: datetime) -> None:
    """Tell the owner about a new co-author once the surrounding transaction commits (never raises).

    Without it, someone with brief access to an owner's open session could add
    themselves quietly.
    """
    try:
        message = join_message(coauthorship, invite, when)
    except Exception:
        logger.exception("Could not build the co-author join email")
        return
    transaction.on_commit(lambda: _dispatch_join_email(message))


# ---------------------------------------------------------------------------
# Sign-up while registration is closed
# ---------------------------------------------------------------------------


def signup_allowed_by_invite(request: HttpRequest) -> bool:
    """Whether this visitor may register although registration is closed.

    True only when the visitor's own session carries the flag the invite preview
    set, and that link is still usable, was made by an administrator and is under
    its sign-up cap. Without the flag there is no query (allauth asks on every
    signed-out session poll). On ``POST`` the invite row is locked: the sign-up
    runs in the request transaction, so a parallel sign-up on the same link waits
    and then sees the incremented counter. Any error means False.
    """
    try:
        session = getattr(request, "session", None)
        if session is None:
            return False
        invite_pk = session.get(SIGNUP_SESSION_KEY)
        if isinstance(invite_pk, bool) or not isinstance(invite_pk, int):
            return False
        invites = CoauthorInvite.objects.select_related("created_by").filter(pk=invite_pk)
        if request.method == "POST" and transaction.get_connection().in_atomic_block:
            invites = invites.select_for_update(of=("self",))
        invite = invites.first()
        return invite is not None and _may_sign_up_with(invite, timezone.now())
    except Exception:
        logger.exception("Could not check the invite of a sign-up")
        return False


def count_signup(request: HttpRequest) -> None:
    """A sign-up went through: count it on the invite that allowed it and drop the session flag."""
    session = getattr(request, "session", None)
    if session is None or registration_open():
        return
    invite_pk = session.pop(SIGNUP_SESSION_KEY, None)
    if isinstance(invite_pk, bool) or not isinstance(invite_pk, int):
        return
    CoauthorInvite.objects.filter(pk=invite_pk).update(signup_uses=F("signup_uses") + 1)


# ---------------------------------------------------------------------------
# Members, removal and leaving
# ---------------------------------------------------------------------------


def list_members(owner: Any) -> list[Coauthorship]:
    """The owner's co-authors: active ones first, then ended ones (at most 100)."""
    return list(
        Coauthorship.objects.filter(owner_id=owner.pk)
        .select_related("member", "invite")
        .order_by(F("revoked_at").desc(nulls_first=True), "-created_at", "-id")[:MAX_MEMBERS_LISTED],
    )


def remove_member(owner: Any, coauthorship_id: int) -> None:
    """End a co-authorship of the owner (idempotent) and revoke the link that member joined with.

    The member's next request gets the single 409 ``workspace_ended``; revoking
    the link keeps them from walking back in through a multi-use link, and
    :func:`removed_since` refuses every other link of the owner made before now.
    """
    with transaction.atomic():
        coauthorship = (
            Coauthorship.objects.select_for_update(of=("self",))
            .select_related("invite")
            .filter(pk=coauthorship_id, owner_id=owner.pk)
            .first()
        )
        if coauthorship is None:
            msg = "Co-author not found"
            raise errors.not_found(msg)
        if coauthorship.revoked_at is not None:
            return
        now = timezone.now()
        coauthorship.revoked_at = now
        coauthorship.revoked_by = owner
        coauthorship.last_selected_at = None
        coauthorship.save(update_fields=["revoked_at", "revoked_by", "last_selected_at"])
        invite = coauthorship.invite if coauthorship.invite_id else None
        if invite is not None and invite.status(now) == CoauthorInvite.STATUS_ACTIVE:
            CoauthorInvite.objects.filter(pk=invite.pk, revoked_at__isnull=True).update(revoked_at=now)


def leave(request: HttpRequest, coauthorship_id: int) -> None:
    """The caller leaves a workspace they are a co-author of (idempotent); clears the selection if it was this one."""
    user = real_user(request)
    with transaction.atomic():
        coauthorship = Coauthorship.objects.select_for_update().filter(pk=coauthorship_id, member_id=user.pk).first()
        if coauthorship is None:
            msg = "Workspace not found"
            raise errors.not_found(msg)
        if coauthorship.revoked_at is None:
            coauthorship.revoked_at = timezone.now()
            coauthorship.revoked_by = user
            coauthorship.last_selected_at = None
            coauthorship.save(update_fields=["revoked_at", "revoked_by", "last_selected_at"])
    if request.session.get(SESSION_KEY) == coauthorship.pk:
        del request.session[SESSION_KEY]


# ---------------------------------------------------------------------------
# The activity trail
# ---------------------------------------------------------------------------


def list_actions(
    owner: Any,
    coauthorship_id: int,
    *,
    limit: int = 50,
    before: int | None = None,
) -> list[CoauthorAction]:
    """Recorded requests of one co-author of the owner, newest first; ``before`` pages to older rows."""
    coauthorship = Coauthorship.objects.filter(pk=coauthorship_id, owner_id=owner.pk).only("pk").first()
    if coauthorship is None:
        msg = "Co-author not found"
        raise errors.not_found(msg)
    actions = CoauthorAction.objects.filter(coauthorship_id=coauthorship.pk).order_by("-id")
    if before is not None:
        actions = actions.filter(pk__lt=before)
    return list(actions[:limit])


def record_action(coauthorship: Coauthorship, request: HttpRequest, status: int) -> None:
    """Store one changing request a co-author made in a workspace (GET, HEAD and OPTIONS are skipped; never raises).

    Only the method, ``path_info`` and the status code are kept: no query string,
    no body, no headers.
    """
    method = (request.method or "").upper()
    if method in SAFE_METHODS:
        return
    try:
        path = (request.path_info or "")[:PATH_MAX_LENGTH]
        CoauthorAction.objects.create(
            coauthorship_id=coauthorship.pk,
            method=method[:8],
            path=path,
            status_code=int(status),
        )
        logger.info(
            "coauthor action",
            extra={
                "actor_id": coauthorship.member_id,
                "owner_id": coauthorship.owner_id,
                "method": method,
                "path": path,
                "status": int(status),
            },
        )
    except Exception:
        logger.exception("Could not record a co-author action")
