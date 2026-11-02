"""Researcher participant endpoints (owner A): participants, access codes, accounts, setup links, invites.

The task comes from ``access.researcher_task``; participants and invites are looked up
within that task (404 otherwise). Literal routes (``codes/``, ``accounts/``) are
registered before the ``{participant_id}`` routes, and every id uses the ``uuid``
converter, so the patterns never shadow each other.
"""

from __future__ import annotations

import uuid

from ninja.responses import Status

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.common import paginated
from backend.human_eval.api.schemas.participants import AccountsCreatedSchema
from backend.human_eval.api.schemas.participants import AccountsIn
from backend.human_eval.api.schemas.participants import CodesCreatedSchema
from backend.human_eval.api.schemas.participants import CodesIn
from backend.human_eval.api.schemas.participants import CreatedCodeSchema
from backend.human_eval.api.schemas.participants import InviteIn
from backend.human_eval.api.schemas.participants import InviteSchema
from backend.human_eval.api.schemas.participants import ParticipantUpdateIn
from backend.human_eval.api.schemas.participants import ResearcherParticipantSchema
from backend.human_eval.api.schemas.participants import SetupLinkSchema
from backend.human_eval.api.views._routers import router
from backend.human_eval.services import access
from backend.human_eval.services import participants as participants_service

__all__ = ["router"]

_BASE = "tasks/{uuid:task_id}/participants/"
_ONE = _BASE + "{uuid:participant_id}/"


@router.get(_BASE, response={200: paginated(ResearcherParticipantSchema), **error_responses(404)})
def get_participants(
    request,
    task_id: uuid.UUID,
    kind: str = "",
    status: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = 20,
):
    """The task's participants by number (TS ``Paginated<ResearcherParticipant>``)."""
    task = access.researcher_task(request, task_id)
    return participants_service.list_participants(
        task,
        kind=kind or None,
        status=status or None,
        search=search or None,
        page=page,
        per_page=per_page,
    )


@router.post(_BASE + "codes/", response={200: CodesCreatedSchema, **error_responses(400, 404, 409)})
def post_codes(request, task_id: uuid.UUID, payload: CodesIn):
    """Create access-code participants; the plaintext codes are only in this response (TS ``CodesCreated``)."""
    task = access.researcher_task(request, task_id)
    return participants_service.create_codes(task, payload.count)


@router.post(_BASE + "accounts/", response={200: AccountsCreatedSchema, **error_responses(400, 404, 409)})
def post_accounts(request, task_id: uuid.UUID, payload: AccountsIn):
    """Link existing users / create restricted evaluator accounts as participants (TS ``AccountsCreated``)."""
    task = access.researcher_task(request, task_id)
    return participants_service.add_accounts(task, request.auth, payload.model_dump())


@router.patch(_ONE, response={200: ResearcherParticipantSchema, **error_responses(400, 404)})
def patch_participant(request, task_id: uuid.UUID, participant_id: uuid.UUID, payload: ParticipantUpdateIn):
    """Exclude / include, annotate or withdraw a participant (TS ``ParticipantUpdateInput``)."""
    task = access.researcher_task(request, task_id)
    participant = participants_service.participant_or_404(task, participant_id)
    participants_service.update_participant(task, request.auth, participant, payload.model_dump(exclude_unset=True))
    return participants_service.participant_payload(task, participant)


@router.delete(_ONE, response={204: None, **error_responses(404)})
def delete_participant(request, task_id: uuid.UUID, participant_id: uuid.UUID):
    """Delete a participant and all of their data."""
    task = access.researcher_task(request, task_id)
    participants_service.delete_participant(
        participants_service.participant_or_404(task, participant_id),
        user=request.auth,
    )
    return Status(204, None)


@router.post(_ONE + "regenerate-code/", response={200: CreatedCodeSchema, **error_responses(404, 409)})
def post_regenerate_code(request, task_id: uuid.UUID, participant_id: uuid.UUID):
    """Replace a participant's access code (TS ``CreatedCode``; their cookie sessions end)."""
    task = access.researcher_task(request, task_id)
    participant = participants_service.participant_or_404(task, participant_id)
    return participants_service.regenerate_code(task, participant)


@router.post(_ONE + "setup-link/", response={200: SetupLinkSchema, **error_responses(404, 409)})
def post_setup_link(request, task_id: uuid.UUID, participant_id: uuid.UUID):
    """A new set-password link for an account you created that has never signed in (TS ``SetupLink``)."""
    task = access.researcher_task(request, task_id)
    participant = participants_service.participant_or_404(task, participant_id)
    return participants_service.issue_setup_link(task, request.auth, participant)


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


@router.get("tasks/{uuid:task_id}/invites/", response={200: list[InviteSchema], **error_responses(404)})
def get_invites(request, task_id: uuid.UUID):
    """The task's invite links, newest first (TS ``Invite[]``)."""
    return participants_service.list_invites(access.researcher_task(request, task_id))


@router.post("tasks/{uuid:task_id}/invites/", response={201: InviteSchema, **error_responses(400, 404, 409)})
def post_invite(request, task_id: uuid.UUID, payload: InviteIn):
    """Create an invite link (TS ``InviteInput`` → 201 ``Invite``)."""
    task = access.researcher_task(request, task_id)
    invite = participants_service.create_invite(task, request.auth, payload.model_dump(exclude_unset=True))
    return Status(201, participants_service.serialize_invite(invite))


@router.patch(
    "tasks/{uuid:task_id}/invites/{uuid:invite_id}/",
    response={200: InviteSchema, **error_responses(400, 404)},
)
def patch_invite(request, task_id: uuid.UUID, invite_id: uuid.UUID, payload: InviteIn):
    """Edit an invite link (label, max uses, expiry, active)."""
    task = access.researcher_task(request, task_id)
    invite = participants_service.invite_or_404(task, invite_id)
    participants_service.update_invite(invite, payload.model_dump(exclude_unset=True))
    return participants_service.serialize_invite(invite)


@router.delete("tasks/{uuid:task_id}/invites/{uuid:invite_id}/", response={204: None, **error_responses(404)})
def delete_invite(request, task_id: uuid.UUID, invite_id: uuid.UUID):
    """Delete an invite link (its participants keep their data)."""
    task = access.researcher_task(request, task_id)
    participants_service.delete_invite(participants_service.invite_or_404(task, invite_id))
    return Status(204, None)
