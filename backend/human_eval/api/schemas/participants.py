"""Schemas of the researcher participant, code, account and invite endpoints (owner A).

Response schemas mirror the TS types (``ResearcherParticipant``, ``CodesCreated``,
``CreatedCode``, ``AccountsCreated``, ``AccountResult``, ``SetupLink``, ``Invite``).
Input schemas are loose; ``services/participants.py`` validates every field and answers
400 with the structured ``HumanEvalError`` body.
"""

from __future__ import annotations

from datetime import datetime

from ninja import Schema


class ParticipantInviteRefSchema(Schema):
    id: str
    label: str


class ResearcherParticipantSchema(Schema):
    id: str
    label: str
    kind: str
    status: str
    email: str | None = None
    invite: ParticipantInviteRefSchema | None = None
    access_code_hint: str
    consent_at: datetime | None = None
    joined_at: datetime | None = None
    last_seen_at: datetime | None = None
    completed_at: datetime | None = None
    submitted: int
    skipped: int
    in_progress: int
    excluded: bool
    exclusion_reason: str
    excluded_at: datetime | None = None
    note: str
    quality_flags: list[str]
    created_at: datetime


class ParticipantUpdateIn(Schema):
    """TS ``ParticipantUpdateInput``: only the fields sent are changed."""

    excluded: bool | None = None
    exclusion_reason: str | None = None
    note: str | None = None
    status: str | None = None


class CodesIn(Schema):
    count: int = 0


class CreatedCodeSchema(Schema):
    participant_id: str
    label: str
    code: str


class CodesCreatedSchema(Schema):
    codes: list[CreatedCodeSchema]


class AccountsIn(Schema):
    """TS ``AccountsInput``."""

    emails: list[str] = []
    restricted: bool = True
    send_email: bool = False


class AccountResultSchema(Schema):
    email: str
    participant: ResearcherParticipantSchema | None = None
    user_created: bool
    setup_path: str | None = None
    error: str | None = None


class AccountsCreatedSchema(Schema):
    results: list[AccountResultSchema]


class SetupLinkSchema(Schema):
    setup_path: str
    expires_at: datetime


class InviteSchema(Schema):
    id: str
    token: str
    path: str
    label: str
    max_uses: int | None = None
    uses: int
    expires_at: datetime | None = None
    is_active: bool
    created_at: datetime


class InviteIn(Schema):
    """TS ``InviteInput``: only the fields sent are changed on PATCH."""

    label: str | None = None
    max_uses: int | None = None
    expires_at: str | None = None
    is_active: bool | None = None
