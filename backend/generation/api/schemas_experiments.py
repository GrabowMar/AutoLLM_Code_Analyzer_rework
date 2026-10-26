"""Ninja schemas for the experiments API v2 (spec §F.1). Foundation → Track C.

Mirrors ``frontend/src/lib/api/types/experiments.ts``. The old experiment
schemas in ``generation/api/schema.py`` stay for compatibility.

Payload shapes that are rollups computed by services (status, cells,
results, preview additions) are typed loosely where the spec leaves the inner
shape to the computing service; the field names here are the wire contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

from ninja import Field
from ninja import Schema

from backend.generation.api.schema import AgentConfigSchema
from backend.generation.api.schema import ExperimentConditionSchema
from backend.generation.api.schema import LLMParamsSchema
from backend.generation.api.schema import NamedPresetRefSchema

SchedulingPolicy = Literal["auto", "interleaved", "blocked", "grouped"]
ExperimentStatusV2 = Literal["draft", "running", "paused", "completed", "cancelled", "archived"]
PauseReason = Literal["", "user", "digest_changed", "endpoint_removed"]
ScheduleAction = Literal["top_up", "add_replicates", "clone_and_run"]


# ── Shared ─────────────────────────────────────────────────────────────


class IssueSchema(Schema):
    """A preview / launch issue (codes in §F.1; ``blocking`` ones stop a launch)."""

    code: str
    detail: str = ""
    blocking: bool = False
    model_id: int | None = None
    endpoint: str | None = None
    condition_id: int | None = None


class ErrorResponseSchema(Schema):
    """400 body of create / launch: ``{detail, code, errors[]}`` (+ ``experiment_id`` when a draft was kept)."""

    detail: str
    code: str = ""
    errors: list[IssueSchema] = []
    experiment_id: UUID | None = None


class SuccessSchema(Schema):
    success: bool = True


# ── Design inputs ──────────────────────────────────────────────────────


class ConditionCrossSchema(Schema):
    """Agent: models x harness choices; scaffolding: models x profiles (explicit pks, no "Auto")."""

    model_ids: list[int]
    harness_preset_ids: list[int] | None = None
    harnesses: list[str] | None = None
    profile_ids: list[int] | None = None


class ConditionExtraSchema(Schema):
    """An ablation condition (badge "ablation"); ``param_overrides`` always lands in ``differs_on``."""

    model_id: int
    harness_preset_id: int | None = None
    harness: str | None = None
    profile_id: int | None = None
    label: str | None = None
    param_overrides: LLMParamsSchema | None = None


class ConditionBaselineSchema(Schema):
    model_id: int
    harness_preset_id: int | None = None
    harness: str | None = None
    profile_id: int | None = None


class ConditionSetSpec(Schema):
    cross: ConditionCrossSchema
    extra: list[ConditionExtraSchema] | None = None
    # Pre-registered baseline; default: the first cross condition.
    baseline: ConditionBaselineSchema | None = None


class ExecutionOptionsSchema(Schema):
    warmup: bool | None = None
    max_resident_models: int | None = Field(None, ge=1)


class ExecutionSpecSchema(Schema):
    scheduling_policy: SchedulingPolicy | None = None
    priority: int | None = Field(None, ge=-10, le=10)
    max_concurrency: int | None = Field(None, ge=1, le=64)
    infra_retries: int | None = Field(None, ge=0, le=5)
    options: ExecutionOptionsSchema | None = None


class PostRunAnalysisSchema(Schema):
    enabled: bool = False
    tool_slugs: list[str] = []
    only_passing: bool = False


class PostRunReportSchema(Schema):
    enabled: bool = False
    exclude_truncated: bool = True


class PostRunSpec(Schema):
    evaluate: bool = True
    analysis: PostRunAnalysisSchema = PostRunAnalysisSchema()
    report: PostRunReportSchema = PostRunReportSchema()


class ScheduleParamsSchema(Schema):
    replicates: int | None = Field(None, ge=1, le=5)
    name_template: str | None = None


class ScheduleSpec(Schema):
    action: ScheduleAction
    cron: str
    timezone: str = "UTC"
    params: ScheduleParamsSchema | None = None
    skip_if_active: bool = True
    enabled: bool = True


class ScheduleUpdateSchema(Schema):
    action: ScheduleAction | None = None
    cron: str | None = None
    timezone: str | None = None
    params: ScheduleParamsSchema | None = None
    skip_if_active: bool | None = None
    enabled: bool | None = None


class ScheduleOut(Schema):
    id: int
    action: ScheduleAction
    cron: str
    timezone: str
    params: dict = {}
    skip_if_active: bool
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_result: dict = {}


class PostRunOut(Schema):
    id: int
    trigger: Literal["completed", "manual", "schedule"]
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    progress: dict = {}
    analysis_run_ids: list[Any] = []
    report_id: UUID | None = None
    error: str = ""
    created_at: datetime
    completed_at: datetime | None = None


class PostRunConfigOut(Schema):
    config: PostRunSpec
    runs: list[PostRunOut] = []


class ExperimentCreateV2(Schema):
    """One call that creates the draft and its conditions (``?launch=true`` also launches)."""

    # Existing create fields (schema.py ExperimentCreateSchema)
    name: str
    slug: str
    description: str = ""
    hypothesis: str = ""
    app_requirement_ids: list[int] = []
    repeats: int = Field(3, ge=1, le=20)
    base_seed: int | None = Field(None, ge=0, le=2_147_483_647)
    mode: str = Field("agent", pattern="^(agent|scaffolding)$")
    stack_slug: str | None = None
    sampling_preset_id: int | None = None
    budget_preset_id: int | None = None
    agent_config: AgentConfigSchema | None = None
    continuation_limit: int = Field(1, ge=0, le=5)
    enable_repair: bool = True
    build_healing: Literal["full", "no_deps", "none"] = "full"
    llm_defaults: LLMParamsSchema | None = None
    # v2 (a draft may start without conditions; a launch needs at least one)
    conditions: ConditionSetSpec | None = None
    execution: ExecutionSpecSchema | None = None
    post_run: PostRunSpec | None = None
    schedules: list[ScheduleSpec] | None = None
    # Pre-registered analysis plan (services/analysis_plan.py); frozen at launch.
    analysis_plan: dict[str, Any] | None = None


class ExperimentUpdateV2(Schema):
    """Draft: every design field. Launched: only name, description, hypothesis, priority,
    max_concurrency, infra_retries, execution_options, post_run, repeats (increase only) and
    scheduling_policy (paused only). ``baseline_condition_id`` is frozen after launch."""

    name: str | None = None
    description: str | None = None
    hypothesis: str | None = None
    app_requirement_ids: list[int] | None = None
    repeats: int | None = Field(None, ge=1, le=20)
    base_seed: int | None = Field(None, ge=0, le=2_147_483_647)
    mode: str | None = Field(None, pattern="^(agent|scaffolding)$")
    stack_slug: str | None = None
    sampling_preset_id: int | None = None
    budget_preset_id: int | None = None
    agent_config: AgentConfigSchema | None = None
    continuation_limit: int | None = Field(None, ge=0, le=5)
    enable_repair: bool | None = None
    build_healing: Literal["full", "no_deps", "none"] | None = None
    llm_defaults: LLMParamsSchema | None = None
    baseline_condition_id: int | None = None
    scheduling_policy: SchedulingPolicy | None = None
    priority: int | None = Field(None, ge=-10, le=10)
    max_concurrency: int | None = Field(None, ge=1, le=64)
    infra_retries: int | None = Field(None, ge=0, le=5)
    execution_options: ExecutionOptionsSchema | None = None
    post_run: PostRunSpec | None = None
    analysis_plan: dict[str, Any] | None = None


# ── Lifecycle bodies ───────────────────────────────────────────────────


class LaunchBody(Schema):
    mode: Literal["launch", "top_up"] = "launch"
    condition_ids: list[int] | None = None


class ResumeBody(Schema):
    accept_digest_drift: bool = False


class CancelBody(Schema):
    scope: Literal["all", "pending"] = "all"


class CloneBody(Schema):
    name: str | None = None
    slug: str | None = None
    include_schedules: bool = False
    include_post_run: bool = True
    overrides: dict | None = None  # Partial<ExperimentCreateV2>
    launch: bool = False


class ExperimentState(Schema):
    status: ExperimentStatusV2
    pause_reason: str = ""
    paused_at: datetime | None = None
    running: int = 0
    pending: int = 0


class CancelResult(ExperimentState):
    cancelled_pending: int = 0
    signalled_running: int = 0
    finishing_evaluations: int = 0


class ConditionBulkSkipped(Schema):
    key: str
    reason: Literal["duplicate", "ineligible"]
    detail: str = ""


class ConditionBulkResult(Schema):
    created: list[ExperimentConditionSchema] = []
    skipped: list[ConditionBulkSkipped] = []


class RequeueLegacyResult(Schema):
    requeued: int
    batches_refreshed: int


class ScheduleRunNowResult(Schema):
    outcome: str
    batch_id: UUID | None = None
    experiment_id: UUID | None = None
    jobs_created: int = 0


class CronPreviewOut(Schema):
    valid: bool
    detail: str = ""
    next_runs: list[datetime] = []


# ── List / detail ──────────────────────────────────────────────────────


class ProgressCounts(Schema):
    pending: int = 0
    running: int = 0
    eval_queued: int = 0
    evaluating: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class EndpointRefSchema(Schema):
    id: int
    slug: str
    name: str
    kind: str
    online: bool = False


class ExperimentListItem(Schema):
    id: UUID
    name: str
    slug: str
    status: ExperimentStatusV2
    pause_reason: str = ""
    mode: str
    scheduling_policy: SchedulingPolicy
    priority: int = 0
    created_at: datetime
    updated_at: datetime
    launched_at: datetime | None = None
    completed_at: datetime | None = None
    condition_count: int = 0
    model_count: int = 0
    app_count: int = 0
    repeats: int
    total_cells: int = 0
    progress: ProgressCounts = ProgressCounts()  # current attempt per cell
    cost_usd: float = 0.0
    eta_seconds: float | None = None
    endpoints: list[EndpointRefSchema] = []
    has_schedule: bool = False
    next_run_at: datetime | None = None


class PaginatedExperiments(Schema):
    items: list[ExperimentListItem]
    total: int
    page: int
    pages: int


class IdNameRef(Schema):
    id: UUID
    name: str


class ScheduleRef(Schema):
    id: int
    action: ScheduleAction


class BatchRef(Schema):
    id: UUID
    kind: str
    created_at: datetime
    total_jobs: int = 0


class ConditionEndpointRef(Schema):
    slug: str
    kind: str


class ExperimentConditionV2(ExperimentConditionSchema):
    """The existing condition schema plus where it runs (``endpoint`` null = OpenRouter)."""

    endpoint: ConditionEndpointRef | None = None
    served_model_name: str = ""
    digest: str = ""
    pinned_digest: str = ""
    display_label: str = ""

    @staticmethod
    def resolve_endpoint(obj) -> dict | None:
        endpoint = obj.model.endpoint if obj.model.endpoint_id else None
        if endpoint is None or endpoint.is_system:
            return None
        return {"slug": endpoint.slug, "kind": endpoint.kind}

    @staticmethod
    def resolve_served_model_name(obj) -> str:
        return obj.model.served_model_name or ""

    @staticmethod
    def resolve_digest(obj) -> str:
        return obj.model.digest or ""

    @staticmethod
    def resolve_display_label(obj) -> str:
        from backend.generation.services.experiments import condition_label

        return condition_label(obj)


class ExperimentDetail(ExperimentListItem):
    description: str = ""
    hypothesis: str = ""
    base_seed: int | None = None
    agent_config: dict = {}
    llm_defaults: dict = {}
    stack_slug: str | None = None
    sampling_preset_ref: NamedPresetRefSchema | None = None
    budget_preset_ref: NamedPresetRefSchema | None = None
    # The pinned preset pks (v1 field names, kept for compatibility).
    sampling_preset: int | None = None
    budget_preset: int | None = None
    app_requirement_ids: list[int] = []
    max_concurrency: int | None = None
    infra_retries: int = 2
    execution_options: dict = {}
    post_run: dict = {}
    cloned_from: IdNameRef | None = None
    created_by_schedule: ScheduleRef | None = None
    baseline_condition_id: int | None = None
    conditions: list[ExperimentConditionV2] = []
    batches: list[BatchRef] = []
    schedules: list[ScheduleOut] = []
    latest_post_run: PostRunOut | None = None


class LaunchResult(Schema):
    experiment: ExperimentDetail
    batch: BatchRef | None = None
    jobs_created: int = 0
    policy_effective_by_endpoint: dict[str, str] = {}
    warnings: list[IssueSchema] = []


# ── Preview ────────────────────────────────────────────────────────────


class PreviewScheduleSchema(Schema):
    policy_effective_by_endpoint: dict[str, str] = {}
    reason: str = ""
    order_seed: int | None = None
    estimated_swaps: dict[str, int] = {}


class PreviewEndpointModel(Schema):
    id: int
    name: str
    digest: str = ""
    num_ctx: int | None = None
    tools: bool = False


class PreviewEndpoint(Schema):
    id: int
    slug: str
    name: str
    kind: str
    connection: str
    online: bool = False
    sessions: int = 0
    max_concurrency: int
    job_cap: int
    resident_limit: int | None = None
    models: list[PreviewEndpointModel] = []
    cells: int = 0
    est_run_s_p50: float | None = None
    est_hours: float | None = None
    issues: list[IssueSchema] = []


class PreviewCapacity(Schema):
    sandbox_slots: int
    eval_slots: int
    llm_job_threads: int
    est_parallel_runs: int


class PreviewEstimate(Schema):
    total_hours: float | None = None
    bottleneck: str | None = None  # "sandbox" | "eval" | "endpoint:<slug>"


class PreviewCost(Schema):
    openrouter_upper_bound_usd: float = 0.0
    local_cells: int = 0
    kind: Literal["budget_upper_bound", "heuristic", "local_zero", "mixed"]


class ExperimentPreviewV2(Schema):
    """The existing preview payload (``services/experiments.preview_experiment``) plus the v2 sections."""

    # existing
    total_jobs: int
    pending_jobs: int | None = None
    conditions: int
    apps: int
    repeats: int
    estimated_cost: float
    cost_estimate_kind: str | None = None
    mode: str | None = None
    stack_slug: str | None = None
    stacks: list[str] = []
    presets: dict | None = None
    agent_config: dict | None = None
    per_condition: list[dict] = []
    comparability: dict | None = None
    launch_errors: list[dict] = []
    # v2
    issues: list[IssueSchema] = []
    schedule: PreviewScheduleSchema = PreviewScheduleSchema()
    endpoints: list[PreviewEndpoint] = []
    capacity: PreviewCapacity | None = None
    estimate: PreviewEstimate = PreviewEstimate()
    cost: PreviewCost | None = None


# ── Status / cells / results ───────────────────────────────────────────


class StatusCells(Schema):
    total: int = 0
    done: int = 0
    failed_final: int = 0
    in_flight: int = 0
    retried: int = 0


class StatusThroughput(Schema):
    completed_last_15m: int = 0
    completed_last_60m: int = 0
    per_hour_ewma: float = 0.0


class StatusConditionModel(Schema):
    id: int
    name: str
    endpoint_slug: str | None = None
    kind: str = "openrouter"


class StatusCondition(Schema):
    condition_id: int
    label: str
    model: StatusConditionModel
    harness: str = ""
    counts: ProgressCounts = ProgressCounts()
    k_passed: int = 0
    n_graded: int = 0
    pass_rate: float | None = None
    median_s: float | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    last_activity_at: datetime | None = None


class UsedTotal(Schema):
    used: int = 0
    total: int = 0


class StatusEndpointJobs(Schema):
    active: int = 0
    cap: int = 0
    held_by_others: int = 0


class StatusEndpointResident(Schema):
    models: list[str] = []  # this user's models only
    others: int = 0
    limit: int | None = None


class StatusEndpointLoaded(Schema):
    name: str
    size_vram_gb: float | None = None
    cpu_spill: bool = False


class StatusEndpoint(Schema):
    id: int
    slug: str
    name: str
    kind: str
    connection: str
    online: bool = False
    sessions: int = 0
    health: Literal["ok", "degraded", "offline"] = "offline"
    slots: UsedTotal = UsedTotal()
    jobs: StatusEndpointJobs = StatusEndpointJobs()
    resident: StatusEndpointResident = StatusEndpointResident()
    loaded: list[StatusEndpointLoaded] = []
    loaded_others: int = 0
    queued_requests: int = 0
    queued_jobs_this_experiment: int = 0
    tokens_per_s_5m: float | None = None
    p50_request_s_15m: float | None = None
    circuit: Literal["closed", "open"] = "closed"
    blocked_reason: str | None = None
    last_error: str | None = None  # owner/staff only


class StatusLanes(Schema):
    sandbox: UsedTotal = UsedTotal()
    eval: UsedTotal = UsedTotal()
    llm: UsedTotal = UsedTotal()
    post: UsedTotal = UsedTotal()


class StatusExecutor(Schema):
    online: bool = False
    draining: bool = False
    code_stale: bool = False


class ExperimentStatusV2Schema(Schema):
    status: ExperimentStatusV2
    pause_reason: str = ""
    policy_effective: dict[str, str] | str | None = None
    launched_at: datetime | None = None
    updated_at: datetime
    counts: ProgressCounts = ProgressCounts()  # current attempt per cell
    cells: StatusCells = StatusCells()
    attempts_total: int = 0
    throughput: StatusThroughput = StatusThroughput()
    eta_seconds: float | None = None
    bottleneck: str | None = None
    conditions: list[StatusCondition] = []
    endpoints: list[StatusEndpoint] = []
    lanes: StatusLanes = StatusLanes()
    executor: StatusExecutor = StatusExecutor()
    latest_post_run: PostRunOut | None = None
    legacy_pending: int = 0


class CellsCondition(Schema):
    id: int
    label: str
    model_id: int
    endpoint_slug: str | None = None


class CellsApp(Schema):
    id: int
    slug: str
    name: str


class CellsPayload(Schema):
    """``cells`` rows: ``[ci, ai, r, s, job_id, pass_rate|null, attempt]``.

    ``s``: p pending, b backoff, w waiting for model prefetch, r running,
    q eval_queued, e evaluating, c completed-pass, f completed-fail,
    u completed-ungraded, x failed, k cancelled.
    """

    conditions: list[CellsCondition] = []
    apps: list[CellsApp] = []
    repeats: int = 0
    cells: list[list[Any]] = []


class ExperimentResults(Schema):
    """Computed by ``generation.services.experiment_results.compute`` (inner shapes in §F.1)."""

    metric: str = "pass_rate"
    exclude_invalid: bool = False
    exclude_truncated: bool = True
    common_cells: bool = True
    fingerprint_filter: str = "all"
    baseline_condition_id: int | None = None
    baseline_source: Literal["preregistered", "post_hoc"] = "preregistered"
    interim: bool = True
    balanced: bool = False
    leaderboard: list[dict] = []
    matrix: dict = {}
    paired: dict = {}
    notes: list[str] = []
    generated_at: datetime
