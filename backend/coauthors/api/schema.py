"""Request and response shapes of the co-authors API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from ninja import Schema
from pydantic import Field


class ErrorSchema(Schema):
    detail: str
    code: str
    errors: list = []


class PersonSchema(Schema):
    id: int
    email: str
    name: str
    display_name: str


class WorkspaceSchema(Schema):
    coauthorship_id: int
    owner: PersonSchema
    owner_is_staff: bool
    joined_at: datetime
    last_active_at: datetime | None


class ContextSchema(Schema):
    actor: PersonSchema
    acting_as: WorkspaceSchema | None
    workspaces: list[WorkspaceSchema]


class SwitchInputSchema(Schema):
    coauthorship_id: int | None


class TokenInputSchema(Schema):
    # The invite token travels only in a POST body, never in a URL or a header.
    token: str = Field(max_length=64)


class PreviewSchema(Schema):
    state: Literal["valid", "already_member", "own_invite"]
    inviter_name: str
    inviter_email: str
    expires_at: datetime
    authenticated: bool
    can_sign_up: bool
    coauthorship_id: int | None


class AcceptResultSchema(Schema):
    status: Literal["joined", "already_member"]
    context: ContextSchema


class InviteSchema(Schema):
    id: int
    label: str
    token_prefix: str
    status: Literal["active", "expired", "used_up", "revoked"]
    max_uses: int
    uses: int
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None


class InviteCreatedSchema(InviteSchema):
    token: str
    path: str


class InviteCreateSchema(Schema):
    label: str = Field(default="", max_length=120)
    expires_in_days: int = Field(default=7, ge=1, le=30)
    max_uses: int = Field(default=1, ge=1, le=20)


class MemberSchema(Schema):
    coauthorship_id: int
    member: PersonSchema
    status: Literal["active", "removed", "left"]
    joined_at: datetime
    last_active_at: datetime | None
    ended_at: datetime | None
    invite_label: str


class ActionSchema(Schema):
    id: int
    method: str
    path: str
    status_code: int
    created_at: datetime
