"""Schemas of the portal workbench endpoints (owner B2).

Mirrors the TS portal types (``PortalAssignment``, ``Observation``,
``ObservationAttachment``, ``SubmitResult``, ``SourceTree``, ``SourceFile``) in
``frontend/src/lib/api/types/human-eval.ts``. Free-form JSON documents (design
blocks, the frozen context, block answers) are typed as plain dicts: their
structure is validated by ``services/design.py`` and ``services/responses.py``.

Input schemas use plain strings for enum fields so that bad values are answered
by the services with the module's structured 400 (``{detail, code, errors}``)
instead of ninja's 422. Response schemas ignore extra keys, so nothing a
service adds by mistake (e.g. container details) reaches participants.

Schema class names carry a ``Work`` prefix to stay unique in the OpenAPI document
(the portal-session module defines its own ``NextResult`` etc.).
"""

from __future__ import annotations

from typing import Any

from ninja import Schema

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class WorkAnswersInput(Schema):
    """PUT ``responses/{block_id}/`` body: ``{answers}`` (validated by the service)."""

    answers: Any = None


class WorkSkipInput(Schema):
    reason: str | None = None
    note: str | None = None


class WorkObservationInput(Schema):
    """POST ``observations/``: TS ``ObservationInput`` (missing fields are reported by the service as 400)."""

    slot: str | None = None
    block_id: str | None = None
    kind: str | None = None
    title: str | None = None
    description: str | None = None
    steps: str | None = None
    location: str | None = None
    category: str | None = None
    severity: str | None = None
    cwe: str | None = None
    checklist_item_id: str | None = None


class WorkObservationPatch(Schema):
    """PATCH ``observations/{id}/``: any subset of the observation fields (``slot`` may not change)."""

    slot: str | None = None
    block_id: str | None = None
    kind: str | None = None
    title: str | None = None
    description: str | None = None
    steps: str | None = None
    location: str | None = None
    category: str | None = None
    severity: str | None = None
    cwe: str | None = None
    checklist_item_id: str | None = None


class WorkFindingVerdictInput(Schema):
    verdict: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


class WorkHostingInfo(Schema):
    """TS ``HostingInfo`` (no container ids or names)."""

    state: str
    url: str | None = None
    progress: int | float | None = None
    message: str = ""
    error: str = ""


class WorkSpecEndpoint(Schema):
    method: str
    path: str
    description: str = ""
    auth: str = ""


class WorkAppSpecView(Schema):
    name: str
    description: str
    prose: str
    frontend_requirements: list[str]
    api_endpoints: list[WorkSpecEndpoint]
    admin_api_endpoints: list[WorkSpecEndpoint]


class WorkAdminCredentials(Schema):
    username: str
    password: str


class WorkPortalApp(Schema):
    slot: str
    label: str
    hosting: WorkHostingInfo
    spec: WorkAppSpecView | None = None
    admin_credentials: WorkAdminCredentials | None = None
    # "available", or "not_measured" when the app has no runtime credentials record; None when not shown.
    admin_credentials_status: str | None = None
    has_source: bool


class WorkTaskRef(Schema):
    id: str
    title: str
    mode: str
    instructions: str


class WorkAttachment(Schema):
    """TS ``ObservationAttachment``."""

    id: str
    content_type: str
    size: int
    original_name: str
    url: str
    created_at: str


class WorkObservation(Schema):
    """TS ``Observation`` (the app is named by its display slot; ``severity`` is the tester's own)."""

    id: str
    assignment_id: str
    app_slot: str
    block_id: str
    kind: str
    title: str
    description: str
    steps: str
    location: str
    category: str
    severity: str
    cwe: str
    checklist_item_id: str
    attachments: list[WorkAttachment]
    created_at: str
    updated_at: str


class WorkResponseEntry(Schema):
    answers: dict[str, Any]
    updated_at: str


class WorkVerdictEntry(Schema):
    verdict: str
    note: str


class WorkPortalAssignment(Schema):
    """TS ``PortalAssignment``."""

    id: str
    task: WorkTaskRef
    status: str
    position: int
    started_at: str | None = None
    submitted_at: str | None = None
    active_seconds: int
    read_only: bool
    apps: list[WorkPortalApp]
    blocks: list[dict[str, Any]]
    context: dict[str, Any]
    responses: dict[str, WorkResponseEntry]
    observations: list[WorkObservation]
    finding_verdicts: dict[str, WorkVerdictEntry]
    allow_skip: bool
    http_console: bool


class WorkResponseSaved(Schema):
    block_id: str
    answers: dict[str, Any]
    updated_at: str


class WorkNextResult(Schema):
    """TS ``NextResult``."""

    assignment_id: str | None = None
    reason: str


class WorkSubmitResult(Schema):
    """TS ``SubmitResult``."""

    status: str
    next: WorkNextResult | None = None


class WorkFindingVerdict(Schema):
    finding_key: str
    verdict: str
    note: str


class WorkSourceFileEntry(Schema):
    path: str
    size: int


class WorkSourceTree(Schema):
    files: list[WorkSourceFileEntry]


class WorkSourceFile(Schema):
    path: str
    content: str
    truncated: bool
