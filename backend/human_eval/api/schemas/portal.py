"""Schemas of the portal session endpoints (owner B1).

Mirror the TS types ``JoinInfo``, ``JoinInput``, ``JoinResult``, ``ResumeInput``,
``ResumeResult``, ``PortalMe``, ``PortalParticipation``, ``PortalTask``,
``NextResult`` and ``WithdrawInput`` in ``frontend/src/lib/api/types/human-eval.ts``
field for field. Design fragments (consent, questions) pass through as the
normalized design dicts (``services/design.py``) with attention ``expected``
values already stripped. Shared pieces (errors, pagination) live in ``common.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

from ninja import Schema

NextReasonLiteral = Literal[
    "assigned",
    "resumed",
    "done",
    "limit_reached",
    "no_items_available",
    "not_accepting",
    "consent_required",
    "pre_questions_required",
]


class ConsentConfigSchema(Schema):
    enabled: bool
    text: str


# --- join / resume -------------------------------------------------------------


class AlreadyJoinedSchema(Schema):
    task_id: UUID
    participant_label: str


class JoinInfoSchema(Schema):
    task_title: str
    task_description: str
    #: open | not_open | closed | full | expired | inactive
    availability: str
    already_joined: AlreadyJoinedSchema | None
    consent: ConsentConfigSchema
    estimated_items: int | None


class JoinInputSchema(Schema):
    consent: bool = False


class JoinResultSchema(Schema):
    task_id: UUID
    participant_label: str
    access_code: str


class ResumeInputSchema(Schema):
    code: str = ""


class ResumeResultSchema(Schema):
    task_id: UUID
    participant_label: str


# --- me ------------------------------------------------------------------------------


class PortalParticipationSchema(Schema):
    task_id: UUID
    task_title: str
    task_status: str
    participant_label: str
    participant_status: str
    kind: str
    submitted: int
    target: int | None


class PortalMeParticipantSchema(Schema):
    task_id: UUID
    label: str


class PortalMeUserSchema(Schema):
    email: str
    name: str
    is_evaluator: bool


class PortalMeSchema(Schema):
    kind: Literal["participant", "account", "anonymous"]
    participant: PortalMeParticipantSchema | None
    user: PortalMeUserSchema | None
    participations: list[PortalParticipationSchema]


# --- task page ---------------------------------------------------------------------


class PortalTaskParticipantSchema(Schema):
    label: str
    kind: str
    status: str
    consent_at: datetime | None
    completion_code: str


class PortalTaskProgressSchema(Schema):
    submitted: int
    skipped: int
    limit: int | None
    remaining_available: int


class PortalTaskBlockSchema(Schema):
    id: str
    type: str
    title: str


class PortalTaskSchema(Schema):
    id: UUID
    title: str
    description: str
    instructions: str
    mode: str
    status: str
    accepting: bool
    participant: PortalTaskParticipantSchema
    consent: ConsentConfigSchema
    pre_questions: list[dict[str, Any]]
    pre_answers: dict[str, Any]
    pre_answers_locked: bool = False
    post_questions: list[dict[str, Any]]
    post_answers: dict[str, Any]
    progress: PortalTaskProgressSchema
    current_assignment_id: UUID | None
    blocks: list[PortalTaskBlockSchema]
    allow_skip: bool


class ConsentInputSchema(Schema):
    agree: bool = False


class AnswersInputSchema(Schema):
    answers: dict[str, Any] = {}


class NextResultSchema(Schema):
    assignment_id: UUID | None
    reason: NextReasonLiteral


class WithdrawInputSchema(Schema):
    delete_data: bool = False
