"""Ninja schemas for /api/execution/ (spec §F.4). Foundation → Track A.

Mirrors ``frontend/src/lib/api/types/execution.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from ninja import Field
from ninja import Schema

BlockedReason = Literal[
    "sandbox_full",
    "llm_threads_full",
    "endpoint_offline",
    "endpoint_full",
    "model_not_served",
    "residency_wait",
    "warming_model",
    "experiment_cap",
    "circuit_open",
    "draining",
    "retry_backoff",
]


class UsedTotal(Schema):
    used: int = 0
    total: int = 0


class Lanes(Schema):
    sandbox: UsedTotal = UsedTotal()
    eval: UsedTotal = UsedTotal()
    llm: UsedTotal = UsedTotal()
    post: UsedTotal = UsedTotal()


class ExecutorLeader(Schema):
    id: str
    started_at: datetime
    last_seen_at: datetime
    code_version: str


class DrainingRemaining(Schema):
    jobs: int = 0
    post_runs: int = 0


class ExecutorStatus(Schema):
    online: bool = False
    leader: ExecutorLeader | None = None
    code_stale: bool = False
    draining: bool = False
    draining_remaining: DrainingRemaining = DrainingRemaining()


class SlotSettings(Schema):
    sandbox_slots: int
    eval_slots: int
    llm_job_threads: int
    post_run_slots: int


class CapacityLimits(Schema):
    apps_dind_mem: str
    max_sandbox_slots_allowed: int
    warnings: list[str] = []


class EndpointCapacity(Schema):
    """Per visible endpoint; other tenants' models appear only as counts."""

    id: int
    slug: str
    name: str
    kind: str
    connection: str
    online: bool = False
    status: Literal["online", "offline", "degraded", "disabled"] = "offline"
    slots: UsedTotal = UsedTotal()  # in-flight upstream requests / max_concurrency
    jobs: UsedTotal = UsedTotal()  # active jobs / job cap
    held_by_others: int = 0
    queued_requests: int = 0
    resident_models: list[str] = []  # caller's own models
    resident_others: int = 0
    resident_limit: int | None = None
    loaded_count: int = 0
    circuit: Literal["closed", "open"] = "closed"
    blocked_reason: str | None = None


class ExperimentCapacityRow(Schema):
    id: UUID
    name: str
    status: str
    priority: int = 0
    running: int = 0
    pending: int = 0
    eta_seconds: float | None = None


class CapacityOut(Schema):
    executor: ExecutorStatus
    settings: SlotSettings
    limits: CapacityLimits
    lanes: Lanes
    endpoints: list[EndpointCapacity] = []
    experiments: list[ExperimentCapacityRow] = []


class QueueItem(Schema):
    job_id: UUID
    experiment_id: UUID | None = None
    condition_id: int | None = None
    model_name: str = ""
    endpoint_slug: str
    lane: Literal["sandbox", "llm"]
    sequence: int | None = None
    priority: int = 0
    queued_at: datetime
    run_after: datetime | None = None
    attempt: int = 0
    blocked_reason: BlockedReason | None = None


class QueueOut(Schema):
    items: list[QueueItem] = []
    others_ahead: dict[str, int] = {}  # endpoint slug -> count of other users' jobs ahead


class ExecutorSettingsOut(Schema):
    sandbox_slots: int
    eval_slots: int
    llm_job_threads: int
    post_run_slots: int
    draining: bool


class ExecutorSettingsPatch(Schema):
    sandbox_slots: int | None = Field(None, ge=0, le=8)
    eval_slots: int | None = Field(None, ge=0, le=4)
    llm_job_threads: int | None = Field(None, ge=1, le=32)
    post_run_slots: int | None = Field(None, ge=0, le=1)
    draining: bool | None = None


class MemoryRuleError(Schema):
    """400 body when the memory rule (§C.10) is violated."""

    detail: str
    arithmetic: str
    mem_limit: str
    required: str
