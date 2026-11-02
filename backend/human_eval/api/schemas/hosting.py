"""Schemas of the hosting endpoints, researcher and portal (owner C).

Mirrors the TS types ``HostingInfo``, ``ResearcherHostingInfo``, ``TaskHosting``,
``HumanEvalEnvironment``, ``HeartbeatInput``/``HeartbeatResult`` and
``ConsoleRequest``/``ConsoleResponse`` (``frontend/src/lib/api/types/human-eval.ts``).
Other modules that embed hosting info (task apps, portal assignments) should reuse
:class:`HeHostingInfoSchema` / :class:`HeResearcherHostingInfoSchema` so OpenAPI has
one definition. Shared pieces (errors, pagination) live in ``common.py``.
"""

from __future__ import annotations

from typing import Any
from typing import Literal

from ninja import Schema
from pydantic import Field

HostingStateLiteral = Literal["idle", "queued", "building", "starting", "running", "stopping", "failed", "unavailable"]
SlotLiteral = Literal["A", "B", "single"]


class HeHostingInfoSchema(Schema):
    """TS ``HostingInfo`` (participants never get container ids or names)."""

    state: HostingStateLiteral
    url: str | None = None
    progress: int | None = None
    message: str = ""
    error: str = ""


class HeResearcherHostingInfoSchema(HeHostingInfoSchema):
    """TS ``ResearcherHostingInfo`` (``container_id`` is the ContainerInstance id)."""

    container_id: str | None = None
    container_name: str | None = None
    container_status: str | None = None
    started_by_task: bool = False
    pinned: bool = False
    last_activity_at: str | None = None


class HeTaskHostingAppSchema(HeResearcherHostingInfoSchema):
    app_id: str
    label: str


class HeTaskHostingSchema(Schema):
    """TS ``TaskHosting``."""

    apps: list[HeTaskHostingAppSchema]
    task_max_running: int
    global_max_running: int
    global_running: int
    running: int
    queued: int
    apps_reachable: bool


class HeTaskHostingActionSchema(Schema):
    """Body of ``POST tasks/{task_id}/hosting/`` (TS ``TaskHostingAction``)."""

    action: Literal["start_all", "stop_all"]


class HeAppHostingActionSchema(Schema):
    """Body of ``POST tasks/{task_id}/apps/{app_id}/hosting/`` (TS ``AppHostingAction``)."""

    action: Literal["start", "stop", "pin", "unpin", "rebuild"]


class HeEnvironmentSchema(Schema):
    """TS ``HumanEvalEnvironment``."""

    apps_reachable: bool
    warnings: list[str]
    routing_mode: Literal["subdomain", "traefik", "path", "direct"]
    global_max_running_apps: int
    global_running_apps: int
    max_concurrent_builds: int
    default_settings: dict[str, Any]
    default_design: dict[str, Any]
    security_checklist_default: list[dict[str, Any]]
    instruments: dict[str, Any]


class HeHeartbeatInputSchema(Schema):
    """TS ``HeartbeatInput``: visible-tab seconds since the last beat (capped at 60 server-side)."""

    active_seconds: float = 0
    visible: bool = True
    viewing: list[SlotLiteral] = Field(default_factory=list, max_length=3)


class HeSlotHostingSchema(Schema):
    slot: SlotLiteral
    hosting: HeHostingInfoSchema


class HeHeartbeatResultSchema(Schema):
    """TS ``HeartbeatResult``."""

    status: str
    active_seconds: int
    apps: list[HeSlotHostingSchema]


class HeConsoleRequestSchema(Schema):
    """TS ``ConsoleRequest``; method, path, headers and body are validated by ``services/console.py`` (400)."""

    slot: str
    method: str
    path: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""


class HeConsoleResponseSchema(Schema):
    """TS ``ConsoleResponse``."""

    id: str
    status_code: int | None = None
    reason: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    truncated: bool = False
    elapsed_ms: int | None = None
    error: str = ""
