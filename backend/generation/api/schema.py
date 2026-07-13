"""Django Ninja schemas for generation."""

from datetime import datetime
from uuid import UUID

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

# ── Template schemas ──────────────────────────────────────────────────


class StackSchema(Schema):
    """A stack skeleton from runtime/scaffolding/manifest.json."""

    slug: str
    has_frontend: bool
    aliases: list[str] = []


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
            "is_default",
            "created_at",
            "updated_at",
        ]


class AppRequirementCreateSchema(Schema):
    name: str
    slug: str
    category: str = ""
    description: str = ""
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
    scaffolding_slug: str = "flask-react"
    block_refs: list[BlockRefSchema] = []
    llm_config: dict = {}
    is_default: bool = False


class BundleImportSchema(Schema):
    package_text: str
    conflict_strategy: str = "rename"


class TemplatePackageExportSchema(Schema):
    app_template_slugs: list[str] = []
    bundle_slugs: list[str] = []
    block_refs: list[BlockRefSchema] = []


class TemplatePackageImportSchema(Schema):
    package_text: str
    conflict_strategy: str = "rename"


class StarterTemplatePackageSchema(Schema):
    slug: str
    name: str
    description: str
    app_template_count: int
    block_count: int
    bundle_count: int


class StarterTemplatePackageImportSchema(Schema):
    conflict_strategy: str = "rename"


# ── Job schemas ───────────────────────────────────────────────────────


class GenerationJobSchema(ModelSchema):
    model_name: str | None = None
    model_id_str: str | None = None
    batch_id: UUID | None = None
    batch_name: str | None = None
    template_name: str | None = None
    bundle_name: str | None = None
    bundle_slug: str | None = None
    created_by_email: str | None = None

    class Meta:
        model = GenerationJob
        fields = [
            "id",
            "mode",
            "status",
            "stack_slug",
            "temperature",
            "max_tokens",
            "llm_params",
            "custom_system_prompt",
            "custom_user_prompt",
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
    def resolve_temperature(obj: GenerationJob) -> float:
        """Effective value: the frozen snapshot wins over the legacy column."""
        resolved = obj.resolved_bundle if isinstance(obj.resolved_bundle, dict) else {}
        value = (resolved.get("llm") or {}).get("temperature")
        return value if value is not None else obj.temperature

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
    id: UUID
    mode: str
    status: str
    model_name: str | None = None
    model_id_str: str | None = None
    template_name: str | None = None
    stack_slug: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str = ""
    created_at: datetime


class PaginatedJobsSchema(Schema):
    items: list[GenerationJobListSchema]
    total: int
    page: int
    per_page: int
    pages: int


class CustomJobCreateSchema(Schema):
    """Create a custom mode generation job."""

    model_id: int
    system_prompt: str
    user_prompt: str
    temperature: float = 0.3
    max_tokens: int = 32000
    llm_params: LLMParamsSchema | None = None
    seed: int | None = None


class ScaffoldingJobCreateSchema(Schema):
    """Create scaffolding mode generation job(s)."""

    stack_slug: str
    app_requirement_ids: list[int]
    model_ids: list[int]
    temperature: float = 0.3
    max_tokens: int = 32000
    profile_id: int | None = None
    llm_params: LLMParamsSchema | None = None
    # Independent repetitions per (template × model) cell. Sampling variance
    # between trials is the point — comparisons need n > 1.
    trials: int = Field(1, ge=1, le=10)


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

    class Meta:
        model = ExperimentCondition
        fields = [
            "id",
            "label",
            "profile",
            "model",
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


class ExperimentConditionCreateSchema(Schema):
    profile_id: int
    model_id: int
    label: str = ""
    param_overrides: dict = {}


class ExperimentSchema(ModelSchema):
    app_requirement_ids: list[int] = []
    condition_count: int = 0

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
            "continuation_limit",
            "enable_repair",
            "llm_defaults",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_app_requirement_ids(obj: Experiment) -> list[int]:
        return list(obj.app_requirements.values_list("id", flat=True))

    @staticmethod
    def resolve_condition_count(obj: Experiment) -> int:
        return obj.conditions.count()


class ExperimentCreateSchema(Schema):
    name: str
    slug: str
    description: str = ""
    hypothesis: str = ""
    app_requirement_ids: list[int] = []
    repeats: int = Field(3, ge=1, le=20)
    base_seed: int | None = None
    continuation_limit: int = Field(1, ge=0, le=5)
    enable_repair: bool = True
    llm_defaults: LLMParamsSchema | None = None


class ExperimentUpdateSchema(Schema):
    name: str | None = None
    description: str | None = None
    hypothesis: str | None = None
    app_requirement_ids: list[int] | None = None
    repeats: int | None = Field(None, ge=1, le=20)
    base_seed: int | None = None
    continuation_limit: int | None = Field(None, ge=0, le=5)
    enable_repair: bool | None = None
    llm_defaults: LLMParamsSchema | None = None
