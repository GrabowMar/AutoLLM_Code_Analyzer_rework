"""Django Ninja schemas for generation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from django.conf import settings
from ninja import Field
from ninja import ModelSchema
from ninja import Schema

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import CopilotIteration
from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services.sampling import MODE_DEFAULT_MAX_TOKENS
from backend.generation.services.sampling import MODE_DEFAULT_TEMPERATURE

# ── Template schemas ──────────────────────────────────────────────────


class StackSchema(Schema):
    """A stack skeleton (DB row seeded from runtime/scaffolding/)."""

    slug: str
    has_frontend: bool
    aliases: list[str] = []
    version: int = 1
    name: str = ""
    description: str = ""
    is_builtin: bool = True
    default_port: int = 8000
    patch_profile: str = "none"
    file_count: int = 0
    # Builtin stacks are always approved; a user stack needs approval unless
    # the viewer owns it (the list only returns stacks the viewer may run).
    is_approved: bool = True
    # The requester created it (only then may they edit or archive it).
    is_own: bool = False
    server_kind: str = "python"
    dockerfile_mode: str = "bundled"
    backend_base_image: str = ""
    frontend_base_image: str = ""
    backend_filename: str = "app.py"
    frontend_component: str = ""


class StackWriteSchema(Schema):
    """Create/update payload for a user-authored stack (Dockerfile is generated)."""

    slug: str = ""  # required on create, ignored on update
    name: str
    description: str = ""
    has_frontend: bool = False
    default_port: int = 8000
    patch_profile: str = "none"
    frontend_component: str = ""
    backend_filename: str = "app.py"
    backend_base_image: str
    frontend_base_image: str = ""
    server_kind: str = "python"
    files: dict = {}


class AppRequirementTemplateSchema(ModelSchema):
    class Meta:
        model = AppRequirementTemplate
        fields = [
            "id",
            "name",
            "slug",
            "category",
            "description",
            "backend_requirements",
            "frontend_requirements",
            "admin_requirements",
            "api_endpoints",
            "data_model",
            "admin_api_endpoints",
            "difficulty",
            "is_default",
            "created_at",
            "updated_at",
        ]


class AppRequirementCreateSchema(Schema):
    """POST/PUT body for an app spec. PUT replaces every field except the slug."""

    name: str
    slug: str
    category: str = ""
    description: str = ""
    difficulty: Literal["", "basic", "standard", "advanced"] = ""
    backend_requirements: list = []
    frontend_requirements: list = []
    admin_requirements: list = []
    api_endpoints: list = []
    data_model: dict = {}
    admin_api_endpoints: list = []


class ContentBlockSchema(ModelSchema):
    class Meta:
        model = ContentBlock
        fields = [
            "id",
            "block_type",
            "slug",
            "version",
            "name",
            "description",
            "content",
            "metadata",
            "is_system",
            "created_at",
            "updated_at",
        ]


class ContentBlockCreateSchema(Schema):
    block_type: str
    slug: str
    version: int = 1
    name: str
    description: str = ""
    content: str
    metadata: dict = {}


class ContentBlockNewVersionSchema(Schema):
    """Payload for the next immutable version of an existing block slug."""

    content: str
    name: str = ""
    description: str = ""
    metadata: dict | None = None


class BlockRefSchema(Schema):
    type: str
    slug: str
    version: int = 1


class ProfileDraftPreviewSchema(Schema):
    """Unsaved profile editor state for live preview."""

    block_refs: list[BlockRefSchema] = []
    app_slug: str = ""
    llm_config: dict = {}


class LLMParamsSchema(Schema):
    """OpenRouter sampling params; every field optional (None = inherit).

    Validated by ``services.llm_params.validate_llm_params`` in the views —
    this schema only shapes the payload.
    """

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None
    stop: list[str] | None = None
    response_format: dict | None = None
    provider: dict | None = None
    reasoning: dict | None = None

    def as_params(self) -> dict:
        """Plain dict with unset fields dropped."""
        return {k: v for k, v in self.dict().items() if v is not None}


class AgentConfigSchema(Schema):
    """Agent-run budget / network overrides; every field optional (None = default).

    Validated by ``services.agent.config.validate_agent_config`` in the views.
    """

    harness: str | None = None
    sampling_policy: str | None = None
    wall_clock_seconds: int | None = None
    max_llm_requests: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    network_policy: str | None = None

    def as_config(self) -> dict:
        return {k: v for k, v in self.dict().items() if v is not None}


class GenerationProfileSchema(ModelSchema):
    class Meta:
        model = GenerationProfile
        fields = [
            "id",
            "name",
            "slug",
            "version",
            "is_archived",
            "description",
            "scaffolding_slug",
            "block_refs",
            "llm_config",
            "is_system",
            "is_default",
            "created_at",
            "updated_at",
        ]


class GenerationProfileCreateSchema(Schema):
    name: str
    slug: str
    description: str = ""
    scaffolding_slug: str = Field(default_factory=lambda: settings.GENERATION_DEFAULT_STACK)
    block_refs: list[BlockRefSchema] = []
    llm_config: dict = {}
    is_default: bool = False


class BundleImportSchema(Schema):
    package_text: str
    conflict_strategy: str = "rename"


class PresetSlugRefSchema(Schema):
    kind: str
    slug: str


class TemplatePackageExportSchema(Schema):
    """Profiles bring their blocks and their user stack along; presets export at their latest version."""

    app_template_slugs: list[str] = []
    bundle_slugs: list[str] = []
    block_refs: list[BlockRefSchema] = []
    stack_slugs: list[str] = []
    preset_refs: list[PresetSlugRefSchema] = []


class TemplatePackageImportSchema(Schema):
    package_text: str
    conflict_strategy: str = "rename"


class StarterTemplatePackageSchema(Schema):
    slug: str
    name: str
    description: str
    app_spec_count: int
    block_count: int
    profile_count: int
    stack_count: int
    preset_count: int
    # Pre-v3 names of app_spec_count / profile_count.
    app_template_count: int
    bundle_count: int


class StarterTemplatePackageImportSchema(Schema):
    conflict_strategy: str = "rename"


# ── Run preset schemas ────────────────────────────────────────────────


class RunPresetSchema(Schema):
    id: int
    kind: str
    slug: str
    version: int
    name: str
    description: str
    config: dict
    content_hash: str
    is_system: bool
    is_archived: bool
    is_default: bool
    latest_version: int
    created_at: datetime
    updated_at: datetime


class RunPresetCreateSchema(Schema):
    kind: str
    slug: str
    name: str
    description: str = ""
    config: dict = {}


class RunPresetUpdateSchema(Schema):
    name: str
    description: str = ""
    # None keeps the current config (a metadata-only edit).
    config: dict | None = None


class RunPresetValidateSchema(Schema):
    kind: str
    config: dict = {}


# ── Job schemas ───────────────────────────────────────────────────────


class NamedPresetRefSchema(Schema):
    id: int
    slug: str
    version: int
    name: str


class PresetRefSchema(Schema):
    id: int
    slug: str
    version: int


def _named_preset_ref(preset) -> dict | None:
    if preset is None:
        return None
    return {"id": preset.id, "slug": preset.slug, "version": preset.version, "name": preset.name}


class GenerationJobSchema(ModelSchema):
    model_name: str | None = None
    model_id_str: str | None = None
    # LLMModel pk (what job creation takes as model_id / model_ids).
    model_pk: int | None = None
    batch_id: UUID | None = None
    batch_name: str | None = None
    template_name: str | None = None
    app_requirement_id: int | None = None
    app_slug: str | None = None
    experiment_id: UUID | None = None
    condition_id: int | None = None
    bundle_name: str | None = None
    bundle_slug: str | None = None
    created_by_email: str | None = None
    # Effective temperature; None for agent jobs that sampled at the provider default.
    temperature: float | None = None
    harness_preset: NamedPresetRefSchema | None = None
    sampling_preset: NamedPresetRefSchema | None = None
    budget_preset: NamedPresetRefSchema | None = None
    config_fingerprint: str | None = None

    class Meta:
        model = GenerationJob
        fields = [
            "id",
            "mode",
            "status",
            "phase",
            "stack_slug",
            "max_tokens",
            "llm_params",
            "custom_system_prompt",
            "custom_user_prompt",
            "task_description",
            "agent_config",
            "harness_options",
            "copilot_description",
            "copilot_max_iterations",
            "copilot_current_iteration",
            "copilot_use_open_source",
            "app_directory",
            "started_at",
            "completed_at",
            "duration_seconds",
            "error_message",
            "result_data",
            "metrics",
            "experiment_seed",
            "repeat_index",
            "prompt_hash",
            "bundle_key",
            "resolved_bundle",
            "profile",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_model_name(obj: GenerationJob) -> str | None:
        if obj.model:
            return obj.model.model_name
        return None

    @staticmethod
    def resolve_model_id_str(obj: GenerationJob) -> str | None:
        if obj.model:
            return obj.model.model_id
        return None

    @staticmethod
    def resolve_model_pk(obj: GenerationJob) -> int | None:
        return obj.model_id

    @staticmethod
    def resolve_temperature(obj: GenerationJob) -> float | None:
        """Effective value: the frozen snapshot wins over the legacy column.

        Agent runs only send a temperature some layer set explicitly (the
        column default 0.3 never reaches the model), so without one it is
        None: the provider default.
        """
        resolved = obj.resolved_bundle if isinstance(obj.resolved_bundle, dict) else {}
        value = (resolved.get("llm") or {}).get("temperature")
        if value is not None:
            return value
        if obj.mode == GenerationJob.Mode.AGENT:
            return (obj.llm_params or {}).get("temperature")
        return obj.temperature

    @staticmethod
    def resolve_max_tokens(obj: GenerationJob) -> int:
        """Effective value: the frozen snapshot wins over the legacy column."""
        resolved = obj.resolved_bundle if isinstance(obj.resolved_bundle, dict) else {}
        value = (resolved.get("llm") or {}).get("max_tokens")
        return value if value is not None else obj.max_tokens

    @staticmethod
    def resolve_batch_id(obj: GenerationJob) -> UUID | None:
        if obj.batch_id:
            return obj.batch_id
        return None

    @staticmethod
    def resolve_batch_name(obj: GenerationJob) -> str | None:
        if obj.batch:
            return obj.batch.name
        return None

    @staticmethod
    def resolve_template_name(obj: GenerationJob) -> str | None:
        if obj.app_requirement:
            return obj.app_requirement.name
        return None

    @staticmethod
    def resolve_app_requirement_id(obj: GenerationJob) -> int | None:
        return obj.app_requirement_id

    @staticmethod
    def resolve_app_slug(obj: GenerationJob) -> str | None:
        return obj.app_requirement.slug if obj.app_requirement else None

    @staticmethod
    def resolve_experiment_id(obj: GenerationJob) -> UUID | None:
        return obj.experiment_id

    @staticmethod
    def resolve_condition_id(obj: GenerationJob) -> int | None:
        return obj.condition_id

    @staticmethod
    def resolve_harness_preset(obj: GenerationJob) -> dict | None:
        return _named_preset_ref(obj.harness_preset)

    @staticmethod
    def resolve_sampling_preset(obj: GenerationJob) -> dict | None:
        return _named_preset_ref(obj.sampling_preset)

    @staticmethod
    def resolve_budget_preset(obj: GenerationJob) -> dict | None:
        return _named_preset_ref(obj.budget_preset)

    @staticmethod
    def resolve_config_fingerprint(obj: GenerationJob) -> str | None:
        resolved = obj.resolved_bundle if isinstance(obj.resolved_bundle, dict) else {}
        return resolved.get("config_fingerprint")

    @staticmethod
    def resolve_bundle_name(obj: GenerationJob) -> str | None:
        if obj.profile:
            return obj.profile.name
        resolved = obj.resolved_bundle if isinstance(obj.resolved_bundle, dict) else {}
        slug = resolved.get("bundle_slug")
        return slug.replace("-", " ").title() if slug else None

    @staticmethod
    def resolve_bundle_slug(obj: GenerationJob) -> str | None:
        if obj.profile:
            return obj.profile.slug
        resolved = obj.resolved_bundle if isinstance(obj.resolved_bundle, dict) else {}
        return resolved.get("bundle_slug")

    @staticmethod
    def resolve_created_by_email(obj: GenerationJob) -> str | None:
        if obj.created_by:
            return obj.created_by.email
        return None


class GenerationJobListSchema(Schema):
    """One job list row (services/job_listing.py builds it without loading the JSON blobs)."""

    id: UUID
    mode: str
    status: str
    phase: str = ""
    model_name: str | None = None
    model_id_str: str | None = None
    template_name: str | None = None
    app_slug: str | None = None
    stack_slug: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str = ""
    created_at: datetime
    batch_id: UUID | None = None
    harness: str | None = None
    harness_preset: PresetRefSchema | None = None
    condition_id: int | None = None
    # "slug@version" of the generation profile, when the job used one.
    profile_ref: str | None = None
    repeat_index: int | None = None
    experiment_seed: int | None = None
    cost_usd: float | None = None
    eval_status: str | None = None
    pass_rate: float | None = None
    termination: str | None = None
    # "valid" | "warn" | "exclude" (result_data.agent.validity); None before an agent run is assessed.
    validity: str | None = None
    config_fingerprint: str | None = None
    # Execution (spec §F.2): attempt of this row, endpoint (None = OpenRouter), frozen lane order, queue time.
    attempt: int = 0
    endpoint_slug: str | None = None
    sequence: int | None = None
    queue_seconds: float | None = None


class PaginatedJobsSchema(Schema):
    items: list[GenerationJobListSchema]
    total: int
    page: int
    per_page: int
    pages: int


class CustomJobCreateSchema(Schema):
    """Create a custom mode generation job.

    llm layers (later wins): mode default (``temperature``/``max_tokens``)
    -> sampling preset ``llm_params`` -> inline ``llm_params``.
    """

    model_id: int
    system_prompt: str
    user_prompt: str
    temperature: float = MODE_DEFAULT_TEMPERATURE
    max_tokens: int = MODE_DEFAULT_MAX_TOKENS
    sampling_preset_id: int | None = None
    llm_params: LLMParamsSchema | None = None
    seed: int | None = Field(None, ge=0, le=2_147_483_647)


class ScaffoldingJobCreateSchema(Schema):
    """Create scaffolding mode generation job(s).

    llm layers (later wins): mode default (``temperature``/``max_tokens``)
    -> profile ``llm_config`` -> sampling preset ``llm_params`` -> inline
    ``llm_params``.
    """

    # None -> the profile's stack -> settings.GENERATION_DEFAULT_STACK. An
    # explicit stack must match the profile's (prompts are written per stack).
    stack_slug: str | None = None
    app_requirement_ids: list[int]
    model_ids: list[int]
    temperature: float = MODE_DEFAULT_TEMPERATURE
    max_tokens: int = MODE_DEFAULT_MAX_TOKENS
    profile_id: int | None = None
    sampling_preset_id: int | None = None
    llm_params: LLMParamsSchema | None = None
    # Independent repetitions per (template × model) cell. Sampling variance
    # between trials is the point — comparisons need n > 1.
    trials: int = Field(1, ge=1, le=10)
    # Build-time repair of the output: full | no_deps | none (HealingMode).
    build_healing: str = Field("full", pattern="^(full|no_deps|none)$")


class LLMPreviewSchema(Schema):
    """Effective llm params a scaffolding / single-prompt job would get (``POST /jobs/llm-preview/``)."""

    mode: str = Field(..., pattern="^(scaffolding|custom)$")
    profile_id: int | None = None
    sampling_preset_id: int | None = None
    llm_params: LLMParamsSchema | None = None


class AgentJobCreateSchema(Schema):
    """Create (or preview) agent-mode job(s): apps (or one free-form task) x models x harness presets x trials.

    Precedence and rules: services/agent/request.py.
    """

    # None -> the profile's stack -> settings.GENERATION_DEFAULT_STACK.
    stack_slug: str | None = None
    app_requirement_ids: list[int] = []
    # Free-form task for a run without an app spec; with app specs it is
    # added to every brief as extra context.
    description: str = ""
    model_ids: list[int] = []
    # The comparison axis: one job per harness preset per cell.
    harness_preset_ids: list[int] = []
    # Legacy: harness names, each run with that harness's system default preset.
    harnesses: list[str] = []
    # None -> the system presets sampling/provider-default and budget/platform-default.
    sampling_preset_id: int | None = None
    budget_preset_id: int | None = None
    profile_id: int | None = None
    # Legacy lowest llm layer (per-call output cap). No temperature default:
    # agent runs sample at the provider default unless a layer sets sampling.
    max_tokens: int | None = Field(None, ge=256, le=200_000)
    llm_params: LLMParamsSchema | None = None
    # Inline overrides of the budget preset (and, without presets, the legacy harness choice).
    agent_config: AgentConfigSchema | None = None
    trials: int = Field(1, ge=1, le=10)
    # Base seed: per-job seeds are derived from it and the cell identity, so a
    # resubmitted batch reproduces the same seed matrix. None = random seeds.
    base_seed: int | None = Field(None, ge=0, le=2_147_483_647)

    def to_request(self):
        from backend.generation.services.agent.request import AgentRequest

        return AgentRequest(
            model_ids=list(self.model_ids),
            app_requirement_ids=list(self.app_requirement_ids),
            description=self.description,
            stack_slug=self.stack_slug or None,
            harness_preset_ids=list(self.harness_preset_ids),
            harnesses=list(self.harnesses),
            sampling_preset_id=self.sampling_preset_id,
            budget_preset_id=self.budget_preset_id,
            llm_params=self.llm_params.as_params() if self.llm_params else {},
            agent_config=self.agent_config.as_config() if self.agent_config else {},
            max_tokens=self.max_tokens,
            profile_id=self.profile_id,
            trials=self.trials,
            base_seed=self.base_seed,
        )


class CopilotJobCreateSchema(Schema):
    """Create a copilot mode generation job."""

    description: str
    model_id: int | None = None
    stack_slug: str | None = None
    max_iterations: int = 5
    use_open_source: bool = True


class BatchCreateResponseSchema(Schema):
    batch_id: UUID
    job_count: int
    status: str


class BatchCountsSchema(Schema):
    pending: int = 0
    # RUNNING jobs still waiting for a sandbox slot (GenerationJob.phase).
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class BatchExperimentRefSchema(Schema):
    id: UUID
    name: str


class BatchSummarySchema(Schema):
    """A batch with its status and aggregates derived from its live jobs (services/batches.py)."""

    id: UUID
    name: str
    mode: str
    status: str
    total_jobs: int
    counts: BatchCountsSchema
    is_terminal: bool
    created_at: datetime
    updated_at: datetime
    stack_slug: str | None = None
    harnesses: list[str] = []
    harness_presets: list[PresetRefSchema] = []
    model_count: int = 0
    app_count: int = 0
    trials: int | None = None
    base_seed: int | None = None
    cost_usd: float = 0.0
    graded: bool = False
    mean_pass_rate: float | None = None
    experiment: BatchExperimentRefSchema | None = None
    # GenerationBatch.request: the create body as resolved (base seed, preset
    # ids, app / model ids, inline overrides); {} for batches that predate it.
    request: dict = {}


class PaginatedBatchesSchema(Schema):
    items: list[BatchSummarySchema]
    total: int
    page: int
    per_page: int
    pages: int


class GenerationBatchSchema(ModelSchema):
    class Meta:
        model = GenerationBatch
        fields = [
            "id",
            "name",
            "mode",
            "status",
            "total_jobs",
            "completed_jobs",
            "failed_jobs",
            "created_at",
            "updated_at",
        ]


class GenerationArtifactSchema(ModelSchema):
    class Meta:
        model = GenerationArtifact
        fields = [
            "id",
            "stage",
            "request_payload",
            "response_payload",
            "prompt_tokens",
            "completion_tokens",
            "total_cost",
            "created_at",
        ]


class CopilotIterationSchema(ModelSchema):
    class Meta:
        model = CopilotIteration
        fields = [
            "id",
            "iteration_number",
            "action",
            "llm_request",
            "llm_response",
            "build_output",
            "build_success",
            "errors_detected",
            "fix_applied",
            "created_at",
        ]


# ── Experiment schemas ────────────────────────────────────────────────


class ExperimentConditionSchema(ModelSchema):
    model_name: str | None = None
    bundle_slug: str | None = None
    bundle_version: int | None = None
    # The pinned harness preset version (``harness_preset`` is its pk).
    harness_preset_ref: NamedPresetRefSchema | None = None

    class Meta:
        model = ExperimentCondition
        fields = [
            "id",
            "label",
            "profile",
            "model",
            "harness",
            "harness_preset",
            "param_overrides",
            "created_at",
        ]

    @staticmethod
    def resolve_model_name(obj: ExperimentCondition) -> str | None:
        return obj.model.model_name if obj.model_id else None

    @staticmethod
    def resolve_bundle_slug(obj: ExperimentCondition) -> str | None:
        return obj.profile.slug if obj.profile_id else None

    @staticmethod
    def resolve_bundle_version(obj: ExperimentCondition) -> int | None:
        return obj.profile.version if obj.profile_id else None

    @staticmethod
    def resolve_harness_preset_ref(obj: ExperimentCondition) -> dict | None:
        return _named_preset_ref(obj.harness_preset) if obj.harness_preset_id else None


class ExperimentConditionCreateSchema(Schema):
    """A condition: model + harness preset (agent; profile optional) or model + profile (scaffolding)."""

    profile_id: int | None = None
    model_id: int
    # Legacy harness name; with harness_preset_id it must match the preset (or be blank).
    harness: str = ""
    harness_preset_id: int | None = None
    label: str = ""
    param_overrides: dict = {}


class ExperimentSchema(ModelSchema):
    app_requirement_ids: list[int] = []
    condition_count: int = 0
    # Experiment-wide stack; null = each condition's profile stack (legacy).
    stack_slug: str | None = None
    # The pinned preset versions (``sampling_preset`` / ``budget_preset`` are their pks).
    sampling_preset_ref: NamedPresetRefSchema | None = None
    budget_preset_ref: NamedPresetRefSchema | None = None

    class Meta:
        model = Experiment
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "hypothesis",
            "status",
            "repeats",
            "base_seed",
            "mode",
            "sampling_preset",
            "budget_preset",
            "agent_config",
            "continuation_limit",
            "enable_repair",
            "build_healing",
            "llm_defaults",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_app_requirement_ids(obj: Experiment) -> list[int]:
        # .all() so a list's prefetch_related is used.
        return [app.id for app in obj.app_requirements.all()]

    @staticmethod
    def resolve_condition_count(obj: Experiment) -> int:
        return len(obj.conditions.all())

    @staticmethod
    def resolve_stack_slug(obj: Experiment) -> str | None:
        return obj.stack_slug or None

    @staticmethod
    def resolve_sampling_preset_ref(obj: Experiment) -> dict | None:
        return _named_preset_ref(obj.sampling_preset) if obj.sampling_preset_id else None

    @staticmethod
    def resolve_budget_preset_ref(obj: Experiment) -> dict | None:
        return _named_preset_ref(obj.budget_preset) if obj.budget_preset_id else None


class ExperimentCreateSchema(Schema):
    """Experiment-wide settings; ``stack_slug`` None keeps each condition profile's stack (legacy)."""

    name: str
    slug: str
    description: str = ""
    hypothesis: str = ""
    app_requirement_ids: list[int] = []
    repeats: int = Field(3, ge=1, le=20)
    base_seed: int | None = Field(None, ge=0, le=2_147_483_647)
    mode: str = Field("agent", pattern="^(agent|scaffolding)$")
    stack_slug: str | None = None
    # Pinned preset versions. Agent mode: None -> the system default preset;
    # scaffolding takes only a sampling preset's llm_params (no default).
    sampling_preset_id: int | None = None
    budget_preset_id: int | None = None
    agent_config: AgentConfigSchema | None = None
    continuation_limit: int = Field(1, ge=0, le=5)
    enable_repair: bool = True
    build_healing: str = Field("full", pattern="^(full|no_deps|none)$")
    llm_defaults: LLMParamsSchema | None = None


class ExperimentUpdateSchema(Schema):
    """Draft-only partial update; an explicit null clears base_seed, stack_slug and the preset ids."""

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
    build_healing: str | None = Field(None, pattern="^(full|no_deps|none)$")
    llm_defaults: LLMParamsSchema | None = None
