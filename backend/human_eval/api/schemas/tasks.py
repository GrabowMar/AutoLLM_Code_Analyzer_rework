"""Schemas of the researcher task and app-pool endpoints (owner A).

Response schemas mirror the TS types in ``frontend/src/lib/api/types/human-eval.ts``
field for field (``TaskListItem``, ``TaskDetail``, ``Preset``, ``DesignValidation``,
``CandidateJob``, ``TaskApp``, ``Item``, ``PreviewSession``). The design and settings
documents are passed through as objects (``services/design.py`` validates them).

Input schemas are deliberately loose (optional fields, plain strings for date-times):
the services validate every field and answer 400 with the structured
``HumanEvalError`` body (``{detail, code, errors[{path, message}]}``) instead of
ninja's 422.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ninja import Schema

from backend.human_eval.api.schemas.common import FieldErrorSchema  # noqa: TC001 - pydantic resolves it at runtime

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


class ExperimentRefSchema(Schema):
    id: str
    name: str


class TaskCountsSchema(Schema):
    apps: int
    active_apps: int
    items: int
    participants: int
    participants_active: int
    participants_completed: int
    assignments_submitted: int
    assignments_in_progress: int
    assignments_skipped: int
    target_ratings: int | None = None


class FunctionalSummarySchema(Schema):
    status: str | None = None
    pass_rate: float | None = None
    deployed: bool | None = None
    source: str | None = None


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskListItemSchema(Schema):
    id: str
    title: str
    status: str
    mode: str
    counts: TaskCountsSchema
    experiment: ExperimentRefSchema | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProtocolLogEntrySchema(Schema):
    at: str
    by: str
    field: str
    old_sha256: str
    new_sha256: str


class TaskOwnerSchema(Schema):
    email: str
    name: str


class TaskDetailSchema(TaskListItemSchema):
    description: str
    instructions: str
    design: dict[str, Any]
    settings: dict[str, Any]
    seed: int
    design_hash: str
    frozen_at: datetime | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    allowed_actions: list[str]
    open_blockers: list[str]
    open_warnings: list[str]
    first_closed_at: datetime | None = None
    protocol_log: list[ProtocolLogEntrySchema]
    owner: TaskOwnerSchema | None = None


class TaskCreateIn(Schema):
    """TS ``TaskCreateInput`` (validated by ``services.tasks.create_task``)."""

    title: str = ""
    description: str | None = None
    instructions: str | None = None
    mode: str = ""
    preset: str | None = None
    design: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    experiment_id: str | None = None
    job_ids: list[str] | None = None
    opens_at: str | None = None
    closes_at: str | None = None


class TaskUpdateIn(Schema):
    """TS ``TaskUpdateInput``: only the fields sent are changed."""

    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    mode: str | None = None
    design: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    experiment_id: str | None = None
    opens_at: str | None = None
    closes_at: str | None = None


class TaskStatusIn(Schema):
    action: str = ""


class PresetSchema(Schema):
    slug: str
    name: str
    description: str
    mode: str
    design: dict[str, Any]
    settings: dict[str, Any]


class DesignValidationIn(Schema):
    mode: str = ""
    design: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class DesignValidationSchema(Schema):
    ok: bool
    errors: list[FieldErrorSchema]
    design: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class PreviewSessionSchema(Schema):
    participant_id: str
    portal_path: str


# ---------------------------------------------------------------------------
# Candidate jobs and the app pool
# ---------------------------------------------------------------------------


class CandidateConditionSchema(Schema):
    id: int
    label: str


class CandidateJobSchema(Schema):
    id: str
    model_name: str
    model_id: str
    app_name: str
    app_slug: str
    experiment: ExperimentRefSchema | None = None
    condition: CandidateConditionSchema | None = None
    repeat_index: int | None = None
    mode: str
    created_at: datetime
    functional: FunctionalSummarySchema
    running: bool


class ResearcherHostingInfoSchema(Schema):
    state: str
    url: str | None = None
    progress: float | None = None
    message: str = ""
    error: str = ""
    container_id: str | None = None
    container_name: str | None = None
    container_status: str | None = None
    started_by_task: bool = False
    pinned: bool = False
    last_activity_at: datetime | None = None


class TaskAppStatsSchema(Schema):
    submitted: int
    in_progress: int
    skipped: int
    unavailable_skips: int


class TaskAppSchema(Schema):
    id: str
    label: str
    job_id: str | None = None
    group_key: str
    position: int
    is_active: bool
    model_name: str
    model_id: str
    condition_label: str
    app_name: str
    app_slug: str
    repeat_index: int | None = None
    functional: FunctionalSummarySchema
    hosting: ResearcherHostingInfoSchema
    stats: TaskAppStatsSchema


class TaskAppsAddIn(Schema):
    """TS ``TaskAppsAddInput``."""

    job_ids: list[str] | None = None
    experiment_id: str | None = None
    current_only: bool = True


class TaskAppUpdateIn(Schema):
    is_active: bool | None = None
    label: str | None = None


class ItemAppRefSchema(Schema):
    id: str
    label: str


class ItemSchema(Schema):
    id: str
    app_a: ItemAppRefSchema
    app_b: ItemAppRefSchema | None = None
    group_key: str
    is_active: bool
    submitted: int
    in_progress: int
    skipped: int
    target: int | None = None
