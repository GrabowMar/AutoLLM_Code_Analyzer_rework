"""Django Ninja API views for automation."""

from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from backend.automation import services
from backend.automation.api.schema import BatchCreateSchema
from backend.automation.api.schema import BatchDetailSchema
from backend.automation.api.schema import ClonePipelineSchema
from backend.automation.api.schema import DslValidationResult
from backend.automation.api.schema import PaginatedBatchesSchema
from backend.automation.api.schema import PaginatedPipelinesSchema
from backend.automation.api.schema import PaginatedRunsSchema
from backend.automation.api.schema import PaginatedSchedulesSchema
from backend.automation.api.schema import PipelineCreateSchema
from backend.automation.api.schema import PipelineRunSchema
from backend.automation.api.schema import PipelineSchema
from backend.automation.api.schema import PipelineUpdateSchema
from backend.automation.api.schema import RetryRunSchema
from backend.automation.api.schema import RunLogsSchema
from backend.automation.api.schema import ScheduleCreateSchema
from backend.automation.api.schema import ScheduleSchema
from backend.automation.api.schema import StepRunLogSchema
from backend.automation.api.schema import TriggerRunSchema
from backend.automation.models import Batch
from backend.automation.models import Pipeline
from backend.automation.models import PipelineRun
from backend.automation.models import PipelineStep
from backend.automation.models import Schedule
from backend.common.ownership import get_owned_or_staff_or_403
from backend.common.pagination import paginate_queryset

router = Router(tags=["automation"])


def _run_mutable_or_403(user: Any, run_id: str) -> PipelineRun:
    """Fetch a run for mutation: allowed for its triggerer, pipeline owner, or staff."""
    run = get_object_or_404(
        PipelineRun.objects.select_related("pipeline"),
        id=run_id,
    )
    user_id = getattr(user, "id", None)
    if run.triggered_by_id == user_id:
        return run
    if run.pipeline.owner_id == user_id or getattr(user, "is_staff", False):
        return run
    raise HttpError(403, "You can only modify runs you triggered or own.")


def _reconcile_steps(pipeline: Pipeline, steps_data: list[dict[str, Any]]) -> None:
    """Replace pipeline steps from DSL config steps list."""
    pipeline.steps.all().delete()
    for i, s in enumerate(steps_data):
        PipelineStep.objects.create(
            pipeline=pipeline,
            order=s.get("order", i),
            name=s.get("name", ""),
            kind=s.get("kind", "script"),
            config=s.get("config", {}),
            depends_on=s.get("depends_on", []),
        )


@router.get("/pipelines/", response=PaginatedPipelinesSchema)
def list_pipelines(
    request: HttpRequest,
    page: int = 1,
    per_page: int = 20,
    status: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    owner_me: bool = False,
) -> Any:
    qs = Pipeline.objects.all()
    if owner_me:
        qs = qs.filter(owner=request.auth)
    if status:
        qs = qs.filter(status=status)
    if tag:
        qs = qs.filter(tags__contains=[tag])
    if search:
        qs = qs.filter(name__icontains=search)
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    return {"items": list(page_qs), "total": total, "page": page, "pages": pages}


@router.post("/pipelines/", response={201: PipelineSchema, 400: DslValidationResult})
def create_pipeline(request: HttpRequest, payload: PipelineCreateSchema) -> Any:
    errors = services.validate_pipeline_dsl(payload.config)
    if errors:
        return 400, {"valid": False, "errors": errors}
    pipeline = Pipeline.objects.create(
        owner=request.auth,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        config=payload.config,
        tags=payload.tags,
    )
    steps_data = payload.config.get("steps", [])
    _reconcile_steps(pipeline, steps_data)
    return 201, pipeline


@router.get("/pipelines/{pipeline_id}/", response=PipelineSchema)
def get_pipeline(request: HttpRequest, pipeline_id: str) -> Pipeline:
    return get_object_or_404(Pipeline, id=pipeline_id)


@router.put(
    "/pipelines/{pipeline_id}/",
    response={200: PipelineSchema, 400: DslValidationResult},
)
def update_pipeline(
    request: HttpRequest,
    pipeline_id: str,
    payload: PipelineUpdateSchema,
) -> Any:
    pipeline = get_owned_or_staff_or_403(Pipeline, user=request.auth, id=pipeline_id)
    update_data = payload.dict(exclude_none=True)
    new_config = update_data.get("config", pipeline.config)
    errors = services.validate_pipeline_dsl(new_config)
    if errors:
        return 400, {"valid": False, "errors": errors}
    for field, value in update_data.items():
        setattr(pipeline, field, value)
    pipeline.save()
    if "config" in update_data:
        _reconcile_steps(pipeline, new_config.get("steps", []))
    return 200, pipeline


@router.delete("/pipelines/{pipeline_id}/", response={204: None})
def delete_pipeline(request: HttpRequest, pipeline_id: str) -> tuple[int, None]:
    pipeline = get_owned_or_staff_or_403(Pipeline, user=request.auth, id=pipeline_id)
    pipeline.delete()
    return 204, None


@router.post("/pipelines/{pipeline_id}/clone/", response={201: PipelineSchema})
def clone_pipeline(
    request: HttpRequest,
    pipeline_id: str,
    payload: ClonePipelineSchema,
) -> Any:
    pipeline = get_owned_or_staff_or_403(Pipeline, user=request.auth, id=pipeline_id)
    new_pipeline = services.clone_pipeline(pipeline, payload.new_name)
    return 201, new_pipeline


@router.post("/pipelines/{pipeline_id}/runs/", response={202: PipelineRunSchema})
def trigger_run(
    request: HttpRequest,
    pipeline_id: str,
    payload: TriggerRunSchema,
) -> Any:
    pipeline = get_owned_or_staff_or_403(Pipeline, user=request.auth, id=pipeline_id)
    run = services.trigger_run(pipeline, payload.params, request.auth)
    return 202, run


@router.get("/pipelines/{pipeline_id}/runs/", response=PaginatedRunsSchema)
def list_pipeline_runs(
    request: HttpRequest,
    pipeline_id: str,
    page: int = 1,
    per_page: int = 20,
) -> Any:
    pipeline = get_object_or_404(Pipeline, id=pipeline_id)
    qs = pipeline.runs.all()
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    return {"items": list(page_qs), "total": total, "page": page, "pages": pages}


@router.get("/runs/{run_id}/", response=PipelineRunSchema)
def get_run(request: HttpRequest, run_id: str) -> PipelineRun:
    return get_object_or_404(
        PipelineRun.objects.prefetch_related("step_runs"),
        id=run_id,
    )


@router.post("/runs/{run_id}/cancel/", response={200: PipelineRunSchema})
def cancel_run(request: HttpRequest, run_id: str) -> Any:
    run = _run_mutable_or_403(request.auth, run_id)
    if run.status in ("pending", "running"):
        run.status = "cancelled"
        run.save(update_fields=["status"])
    return 200, run


@router.get("/runs/{run_id}/logs/", response=RunLogsSchema)
def get_run_logs(request: HttpRequest, run_id: str) -> Any:
    """Return aggregated log: per step_run status, output, error."""
    run = get_object_or_404(
        PipelineRun.objects.prefetch_related("step_runs__step"),
        id=run_id,
    )
    step_logs = [
        StepRunLogSchema(
            step_run_id=sr.id,
            step_name=sr.step.name if sr.step else None,
            step_kind=sr.step.kind if sr.step else None,
            status=sr.status,
            started_at=sr.started_at,
            completed_at=sr.completed_at,
            attempt=sr.attempt,
            retries_remaining=sr.retries_remaining,
            output=sr.output or {},
            error=sr.error or "",
        )
        for sr in run.step_runs.order_by("created_at")
    ]
    return RunLogsSchema(
        run_id=run.id,
        run_status=run.status,
        step_logs=step_logs,
    )


@router.post("/runs/{run_id}/retry/", response={202: RetryRunSchema})
def retry_run(request: HttpRequest, run_id: str) -> Any:
    """Create a new PipelineRun reusing the same params as the original."""
    original = _run_mutable_or_403(request.auth, run_id)
    new_run = services.trigger_run(original.pipeline, original.params, request.auth)
    return 202, RetryRunSchema(new_run_id=new_run.id)


@router.get("/batches/", response=PaginatedBatchesSchema)
def list_batches(request: HttpRequest, page: int = 1, per_page: int = 20) -> Any:
    qs = Batch.objects.filter(owner=request.auth)
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    return {"items": list(page_qs), "total": total, "page": page, "pages": pages}


@router.post("/batches/", response={201: BatchDetailSchema})
def create_batch(request: HttpRequest, payload: BatchCreateSchema) -> Any:
    pipeline = get_owned_or_staff_or_403(
        Pipeline,
        user=request.auth,
        id=payload.pipeline_id,
    )
    batch = Batch.objects.create(
        owner=request.auth,
        name=payload.name,
        description=payload.description,
        config={
            "pipeline_id": str(pipeline.id),
            "matrix": payload.matrix,
            "repeats": payload.repeats,
        },
    )
    return 201, batch


@router.get("/batches/{batch_id}/", response=BatchDetailSchema)
def get_batch(request: HttpRequest, batch_id: str) -> Batch:
    return get_object_or_404(Batch, id=batch_id, owner=request.auth)


@router.post("/batches/{batch_id}/cancel/", response={200: BatchDetailSchema})
def cancel_batch(request: HttpRequest, batch_id: str) -> Any:
    """Cancel all in-flight (pending or running) runs in the batch."""
    batch = get_object_or_404(Batch, id=batch_id, owner=request.auth)
    in_flight_run_ids = list(
        batch.items.filter(
            pipeline_run__status__in=["pending", "running"],
        ).values_list("pipeline_run_id", flat=True),
    )
    PipelineRun.objects.filter(id__in=in_flight_run_ids).update(status="cancelled")
    batch.status = "cancelled"
    batch.save(update_fields=["status"])
    return 200, batch


@router.get("/schedules/", response=PaginatedSchedulesSchema)
def list_schedules(request: HttpRequest, page: int = 1, per_page: int = 20) -> Any:
    qs = Schedule.objects.filter(owner=request.auth)
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    return {"items": list(page_qs), "total": total, "page": page, "pages": pages}


@router.post("/schedules/", response={201: ScheduleSchema, 400: DslValidationResult})
def create_schedule(request: HttpRequest, payload: ScheduleCreateSchema) -> Any:
    from croniter import croniter

    if not croniter.is_valid(payload.cron_expression):
        return 400, {"valid": False, "errors": ["Invalid cron expression"]}
    pipeline = get_owned_or_staff_or_403(
        Pipeline,
        user=request.auth,
        id=payload.pipeline_id,
    )
    next_run = services.next_cron_time(payload.cron_expression)
    schedule = Schedule.objects.create(
        pipeline=pipeline,
        owner=request.auth,
        cron_expression=payload.cron_expression,
        enabled=payload.enabled,
        next_run_at=next_run,
    )
    return 201, schedule


@router.patch("/schedules/{schedule_id}/enabled/", response=ScheduleSchema)
def toggle_schedule(request: HttpRequest, schedule_id: str, enabled: bool) -> Any:
    schedule = get_object_or_404(Schedule, id=schedule_id, owner=request.auth)
    schedule.enabled = enabled
    schedule.save(update_fields=["enabled"])
    return schedule


@router.delete("/schedules/{schedule_id}/", response={204: None})
def delete_schedule(request: HttpRequest, schedule_id: str) -> tuple[int, None]:
    schedule = get_object_or_404(Schedule, id=schedule_id, owner=request.auth)
    schedule.delete()
    return 204, None
