"""Django Ninja schemas for generation."""

from datetime import datetime
from uuid import UUID

from ninja import ModelSchema
from ninja import Schema

from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import CopilotIteration
from llm_lab.generation.models import GenerationArtifact
from llm_lab.generation.models import GenerationBatch
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.models import PromptTemplate
from llm_lab.generation.models import ScaffoldingTemplate

# ── Template schemas ──────────────────────────────────────────────────


class ScaffoldingTemplateSchema(ModelSchema):
    class Meta:
        model = ScaffoldingTemplate
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "tech_stack",
            "substitution_vars",
            "is_default",
            "created_at",
            "updated_at",
        ]


class ScaffoldingTemplateCreateSchema(Schema):
    name: str
    slug: str
    description: str = ""
    tech_stack: dict = {}
    substitution_vars: list = []


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


class PromptTemplateSchema(ModelSchema):
    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "name",
            "slug",
            "stage",
            "role",
            "content",
            "description",
            "is_default",
            "version",
            "created_at",
            "updated_at",
        ]


class PromptTemplateCreateSchema(Schema):
    name: str
    slug: str
    stage: str
    role: str
    content: str
    description: str = ""


# ── Job schemas ───────────────────────────────────────────────────────


class GenerationJobSchema(ModelSchema):
    model_name: str | None = None
    model_id_str: str | None = None
    batch_id: UUID | None = None
    batch_name: str | None = None
    template_name: str | None = None
    scaffolding_name: str | None = None
    created_by_email: str | None = None

    class Meta:
        model = GenerationJob
        fields = [
            "id",
            "mode",
            "status",
            "temperature",
            "max_tokens",
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
    def resolve_scaffolding_name(obj: GenerationJob) -> str | None:
        if obj.scaffolding_template:
            return obj.scaffolding_template.name
        return None

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
    scaffolding_name: str | None = None
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


class ScaffoldingJobCreateSchema(Schema):
    """Create scaffolding mode generation job(s)."""

    scaffolding_template_id: int
    app_requirement_ids: list[int]
    model_ids: list[int]
    temperature: float = 0.3
    max_tokens: int = 32000


class CopilotJobCreateSchema(Schema):
    """Create a copilot mode generation job."""

    description: str
    model_id: int | None = None
    scaffolding_template_id: int | None = None
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
