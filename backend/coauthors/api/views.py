"""The co-authors API (mounted at ``/api/coauthors/``).

Three groups of routes, all session-only (``auth.CoauthorSessionAuth``: ninja's
CSRF check on unsafe methods, never on behalf of a workspace owner, never with an
API token; the router is also excluded from MCP):

- **for everyone signed in**, always as the real person: ``context/`` (who am I,
  where am I working, where could I), ``workspace/`` (switch), ``join/accept/``
  and ``memberships/{id}/`` (leave);
- **the invite preview**, ``join/preview/``: no login needed, CSRF enforced
  explicitly, throttled. It grants nothing and takes the token in the POST body;
  no route takes a token in its URL and nothing is accepted on GET;
- **owner management** (``invites/…``, ``members/…``) on a sub-router with
  ``auth.OwnAccountAuth``: only from one's own account, never while a workspace is
  selected, never by API token.

Errors are ``{"detail", "code", "errors": []}`` (``CoauthorError``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ninja import Query
from ninja import Router
from ninja.responses import Status

from backend.coauthors import services
from backend.coauthors.api.schema import AcceptResultSchema
from backend.coauthors.api.schema import ActionSchema
from backend.coauthors.api.schema import ContextSchema
from backend.coauthors.api.schema import ErrorSchema
from backend.coauthors.api.schema import InviteCreatedSchema
from backend.coauthors.api.schema import InviteCreateSchema
from backend.coauthors.api.schema import InviteSchema
from backend.coauthors.api.schema import MemberSchema
from backend.coauthors.api.schema import PreviewSchema
from backend.coauthors.api.schema import SwitchInputSchema
from backend.coauthors.api.schema import TokenInputSchema
from backend.coauthors.auth import CoauthorSessionAuth
from backend.coauthors.auth import OwnAccountAuth
from backend.human_eval.auth import enforce_csrf

if TYPE_CHECKING:
    from django.http import HttpRequest

router = Router(tags=["coauthors"], auth=CoauthorSessionAuth())
manage_router = Router(tags=["coauthors"], auth=OwnAccountAuth())

_ERRORS = {403: ErrorSchema, 404: ErrorSchema, 409: ErrorSchema, 429: ErrorSchema}


def _current(request: HttpRequest):
    return getattr(request, "coauthorship", None)


# ---------------------------------------------------------------------------
# Everyone signed in (the real person)
# ---------------------------------------------------------------------------


@router.get("/context/", response=ContextSchema)
def get_context(request: HttpRequest):
    """The real person, the workspace this session works in (if any) and the workspaces it could switch to."""
    return services.context_payload(request.auth, _current(request))


@router.post("/workspace/", response={200: ContextSchema, **_ERRORS})
def switch_workspace(request: HttpRequest, data: SwitchInputSchema):
    """Select a workspace for this session, or ``null`` for one's own account."""
    selected = services.select_workspace(request, data.coauthorship_id)
    return services.context_payload(request.auth, selected)


@router.post("/join/preview/", response={200: PreviewSchema, **_ERRORS}, auth=None)
def preview_invite(request: HttpRequest, data: TokenInputSchema):
    """What an invite link offers (inviter, expiry, whether the caller already has access). Changes nothing."""
    enforce_csrf(request)
    return services.preview(request, data.token)


@router.post("/join/accept/", response={200: AcceptResultSchema, **_ERRORS})
def accept_invite(request: HttpRequest, data: TokenInputSchema):
    """Join the workspace an invite link belongs to and select it."""
    status, coauthorship = services.accept(request, data.token)
    return {"status": status, "context": services.context_payload(request.auth, coauthorship)}


@router.delete("/memberships/{coauthorship_id}/", response={204: None, **_ERRORS})
def leave_workspace(request: HttpRequest, coauthorship_id: int):
    """Leave a workspace (idempotent)."""
    services.leave(request, coauthorship_id)
    return Status(204, None)


# ---------------------------------------------------------------------------
# Owner management (own account only)
# ---------------------------------------------------------------------------


@manage_router.get("/invites/", response=list[InviteSchema])
def list_invites(request: HttpRequest):
    """The caller's invite links, newest first."""
    return [services.invite_payload(invite) for invite in services.list_invites(request.auth)]


@manage_router.post("/invites/", response={201: InviteCreatedSchema, **_ERRORS})
def create_invite(request: HttpRequest, data: InviteCreateSchema):
    """Create an invite link. The token is in this response and nowhere else, ever."""
    raw, invite = services.create_invite(
        request.auth,
        label=data.label,
        expires_in_days=data.expires_in_days,
        max_uses=data.max_uses,
    )
    return Status(201, {**services.invite_payload(invite), "token": raw, "path": services.invite_path(raw)})


@manage_router.delete("/invites/{invite_id}/", response={204: None, **_ERRORS})
def revoke_invite(request: HttpRequest, invite_id: int):
    """Revoke an invite link (idempotent). People who already joined keep their access."""
    services.revoke_invite(request.auth, invite_id)
    return Status(204, None)


@manage_router.get("/members/", response=list[MemberSchema])
def list_members(request: HttpRequest):
    """The caller's co-authors: active first, then ended."""
    return [services.member_payload(row) for row in services.list_members(request.auth)]


@manage_router.delete("/members/{coauthorship_id}/", response={204: None, **_ERRORS})
def remove_member(request: HttpRequest, coauthorship_id: int):
    """Remove a co-author (idempotent); the link they joined with stops working."""
    services.remove_member(request.auth, coauthorship_id)
    return Status(204, None)


@manage_router.get("/members/{coauthorship_id}/activity/", response={200: list[ActionSchema], **_ERRORS})
def member_activity(
    request: HttpRequest,
    coauthorship_id: int,
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, ge=1),
):
    """Changing requests a co-author made in the caller's workspace, newest first (``before`` pages back)."""
    return services.list_actions(request.auth, coauthorship_id, limit=limit, before=before)


router.add_router("", manage_router)
