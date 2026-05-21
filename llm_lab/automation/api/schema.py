"""Django Ninja schemas for automation."""

from typing import Any
from uuid import UUID

from ninja import ModelSchema
from ninja import Schema

from llm_lab.automation.models import Batch
from llm_lab.automation.models import BatchItem
from llm_lab.automation.models import Pipeline
from llm_lab.automation.models import PipelineRun
from llm_lab.automation.models import PipelineStep
from llm_lab.automation.models import PipelineStepRun
from llm_lab.automation.models import Schedule


class PipelineStepSchema(ModelSchema):
    id: UUID
    pipeline_id: UUID

    class Meta:
        model = PipelineStep
        fields = ["id", "pipeline", "order", "name", "kind", "config", "depends_on"]


class PipelineSchema(ModelSchema):
    id: UUID
    owner_id: int
    steps: list[PipelineStepSchema] = []

    class Meta:
        model = Pipeline
        fields = [
            "id",
            "owner",
            "name",
            "description",
            "version",
            "status",
            "config",
            "tags",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_steps(obj: Pipeline) -> list[PipelineStep]:
        return list(obj.steps.order_by("order"))


class PipelineListSchema(ModelSchema):
    id: UUID
    owner_id: int

    class Meta:
        model = Pipeline
        fields = [
            "id",
            "owner",
            "name",
            "description",
            "version",
            "status",
            "tags",
            "created_at",
            "updated_at",
        ]


class PipelineCreateSchema(Schema):
    name: str
    description: str = ""
    status: str = "draft"
    config: dict[str, Any] = {}
    tags: list[str] = []


class PipelineUpdateSchema(Schema):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    config: dict[str, Any] | None = None
    tags: list[str] | None = None
    version: int | None = None


class ClonePipelineSchema(Schema):
    new_name: str


class TriggerRunSchema(Schema):
    params: dict[str, Any] = {}


class PipelineStepRunSchema(ModelSchema):
    id: UUID
    run_id: UUID
    step_id: UUID | None

    class Meta:
        model = PipelineStepRun
        fields = [
            "id",
            "run",
            "step",
            "status",
            "started_at",
            "completed_at",
            "output",
            "error",
            "attempt",
            "retries_remaining",
            "created_at",
        ]


class PipelineRunSchema(ModelSchema):
    id: UUID
    pipeline_id: UUID
    triggered_by_id: int | None
    step_runs: list[PipelineStepRunSchema] = []

    class Meta:
        model = PipelineRun
        fields = [
            "id",
            "pipeline",
            "triggered_by",
            "status",
            "started_at",
            "completed_at",
            "error",
            "result_summary",
            "params",
            "created_at",
        ]

    @staticmethod
    def resolve_step_runs(obj: PipelineRun) -> list[PipelineStepRun]:
        return list(obj.step_runs.order_by("created_at"))


class PipelineRunListSchema(ModelSchema):
    id: UUID
    pipeline_id: UUID
    triggered_by_id: int | None

    class Meta:
        model = PipelineRun
        fields = [
            "id",
            "pipeline",
            "triggered_by",
            "status",
            "started_at",
            "completed_at",
            "error",
            "params",
            "created_at",
        ]


class BatchSchema(ModelSchema):
    id: UUID
    owner_id: int

    class Meta:
        model = Batch
        fields = [
            "id",
            "owner",
            "name",
            "description",
            "config",
            "status",
            "created_at",
        ]


class BatchCreateSchema(Schema):
    pipeline_id: UUID
    name: str
    description: str = ""
    matrix: dict[str, Any] = {}


class BatchItemSchema(ModelSchema):
    id: UUID
    batch_id: UUID
    pipeline_run_id: UUID | None

    class Meta:
        model = BatchItem
        fields = ["id", "batch", "pipeline_run", "status", "params", "created_at"]


class BatchDetailSchema(ModelSchema):
    id: UUID
    owner_id: int
    items: list[BatchItemSchema] = []

    class Meta:
        model = Batch
        fields = [
            "id",
            "owner",
            "name",
            "description",
            "config",
            "status",
            "created_at",
        ]

    @staticmethod
    def resolve_items(obj: Batch) -> list[BatchItem]:
        return list(obj.items.order_by("created_at"))


class ScheduleSchema(ModelSchema):
    id: UUID
    pipeline_id: UUID
    owner_id: int

    class Meta:
        model = Schedule
        fields = [
            "id",
            "pipeline",
            "owner",
            "cron_expression",
            "enabled",
            "next_run_at",
            "last_run_at",
            "created_at",
        ]


class ScheduleCreateSchema(Schema):
    pipeline_id: UUID
    cron_expression: str
    enabled: bool = True


class PaginatedPipelinesSchema(Schema):
    items: list[PipelineListSchema]
    total: int
    page: int
    pages: int


class PaginatedRunsSchema(Schema):
    items: list[PipelineRunListSchema]
    total: int
    page: int
    pages: int


class PaginatedBatchesSchema(Schema):
    items: list[BatchSchema]
    total: int
    page: int
    pages: int


class PaginatedSchedulesSchema(Schema):
    items: list[ScheduleSchema]
    total: int
    page: int
    pages: int


class DslValidationResult(Schema):
    valid: bool
    errors: list[str]


class StepRunLogSchema(Schema):
    step_run_id: UUID
    step_name: str | None
    step_kind: str | None
    status: str
    started_at: Any
    completed_at: Any
    attempt: int
    retries_remaining: int
    output: dict[str, Any]
    error: str


class RunLogsSchema(Schema):
    run_id: UUID
    run_status: str
    step_logs: list[StepRunLogSchema]


class RetryRunSchema(Schema):
    new_run_id: UUID
