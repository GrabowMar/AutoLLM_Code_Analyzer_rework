"""Generation job CRUD endpoints and batch endpoints.

All routes here use ``{job_id}`` / ``{batch_id}`` dynamic segments and MUST be
registered AFTER the static job-creation routes in
:mod:`llm_lab.generation.api.views.custom`. Ordering is controlled by
:mod:`llm_lab.generation.api.views.__init__`.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import Query

from llm_lab.common.pagination import paginate_queryset
from llm_lab.generation.api.schema import CopilotIterationSchema
from llm_lab.generation.api.schema import GenerationArtifactSchema
from llm_lab.generation.api.schema import GenerationBatchSchema
from llm_lab.generation.api.schema import GenerationJobListSchema
from llm_lab.generation.api.schema import GenerationJobSchema
from llm_lab.generation.api.schema import PaginatedJobsSchema
from llm_lab.generation.api.views._router import router
from llm_lab.generation.models import GenerationBatch
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.services.dispatcher import dispatch_job


@router.get("/jobs/", response=PaginatedJobsSchema)
def list_jobs(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    mode: str = Query(""),
    status: str = Query(""),
    model_id: str = Query(""),
):
    """List generation jobs with pagination and filters."""
    qs = (
        GenerationJob.objects.filter(created_by=request.auth)
        .select_related(
            "model",
            "app_requirement",
            "scaffolding_template",
        )
        .order_by("-created_at")
    )
    if mode:
        qs = qs.filter(mode=mode)
    if status:
        qs = qs.filter(status=status)
    if model_id:
        qs = qs.filter(model__model_id=model_id)

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    items = [
        GenerationJobListSchema(
            id=job.id,
            mode=job.mode,
            status=job.status,
            model_name=job.model.model_name if job.model else None,
            model_id_str=job.model.model_id if job.model else None,
            template_name=(job.app_requirement.name if job.app_requirement else None),
            scaffolding_name=(job.scaffolding_template.name if job.scaffolding_template else None),
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=job.duration_seconds,
            error_message=job.error_message,
            created_at=job.created_at,
        )
        for job in page_qs
    ]

    return PaginatedJobsSchema(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/jobs/{job_id}/", response=GenerationJobSchema)
def get_job(request, job_id: str):
    return get_object_or_404(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "scaffolding_template",
            "batch",
            "created_by",
        ),
        id=job_id,
        created_by=request.auth,
    )


@router.post("/jobs/{job_id}/cancel/")
def cancel_job(request, job_id: str):
    job = get_object_or_404(GenerationJob, id=job_id, created_by=request.auth)
    if job.status in ("pending", "running"):
        job.status = GenerationJob.Status.CANCELLED
        job.save(update_fields=["status", "updated_at"])
        return {"success": True, "status": "cancelled"}
    return {
        "success": False,
        "status": job.status,
        "message": "Job cannot be cancelled",
    }


@router.delete("/jobs/{job_id}/")
def delete_job(request, job_id: str):
    """Delete a job that is not currently running."""
    job = get_object_or_404(GenerationJob, id=job_id, created_by=request.auth)
    if job.status == "running":
        return {
            "success": False,
            "message": "Cannot delete a running job. Cancel it first.",
        }
    job.delete()
    return {"success": True}


@router.post("/jobs/{job_id}/retry/", response=GenerationJobSchema)
def retry_job(request, job_id: str):
    """Re-create a failed/cancelled job with the same parameters."""
    original = get_object_or_404(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "scaffolding_template",
            "backend_prompt_template",
            "frontend_prompt_template",
        ),
        id=job_id,
        created_by=request.auth,
    )
    if original.status not in ("failed", "cancelled"):
        return router.create_response(
            request,
            {"message": "Only failed or cancelled jobs can be retried"},
            status=400,
        )
    new_job = GenerationJob.objects.create(
        mode=original.mode,
        created_by=request.auth,
        model=original.model,
        scaffolding_template=original.scaffolding_template,
        app_requirement=original.app_requirement,
        backend_prompt_template=original.backend_prompt_template,
        frontend_prompt_template=original.frontend_prompt_template,
        custom_system_prompt=original.custom_system_prompt,
        custom_user_prompt=original.custom_user_prompt,
        temperature=original.temperature,
        max_tokens=original.max_tokens,
        copilot_description=original.copilot_description,
        copilot_max_iterations=original.copilot_max_iterations,
        copilot_use_open_source=original.copilot_use_open_source,
    )
    dispatch_job(new_job)
    return GenerationJobSchema.from_orm(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "scaffolding_template",
        ).get(
            id=new_job.id,
        ),
    )


@router.get("/jobs/{job_id}/artifacts/", response=list[GenerationArtifactSchema])
def get_job_artifacts(request, job_id: str):
    job = get_object_or_404(GenerationJob, id=job_id, created_by=request.auth)
    return job.artifacts.all()


@router.get(
    "/jobs/{job_id}/copilot-iterations/",
    response=list[CopilotIterationSchema],
)
def get_copilot_iterations(request, job_id: str):
    job = get_object_or_404(GenerationJob, id=job_id, created_by=request.auth)
    return job.copilot_iterations.all()


@router.get("/jobs/{job_id}/export/")
def export_job(request, job_id: str):
    """Export full job data as JSON (job + artifacts + iterations)."""
    job = get_object_or_404(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "scaffolding_template",
            "batch",
            "created_by",
        ),
        id=job_id,
        created_by=request.auth,
    )
    artifacts = list(
        job.artifacts.values(
            "id",
            "stage",
            "request_payload",
            "response_payload",
            "prompt_tokens",
            "completion_tokens",
            "total_cost",
            "created_at",
        ),
    )
    iterations = list(
        job.copilot_iterations.values(
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
        ),
    )
    return {
        "job": GenerationJobSchema.from_orm(job).dict(),
        "artifacts": artifacts,
        "copilot_iterations": iterations,
    }


# -- Batches ------------------------------------------------------------


@router.get("/batches/", response=list[GenerationBatchSchema])
def list_batches(request):
    return GenerationBatch.objects.filter(created_by=request.auth)


@router.get("/batches/{batch_id}/", response=GenerationBatchSchema)
def get_batch(request, batch_id: str):
    return get_object_or_404(GenerationBatch, id=batch_id, created_by=request.auth)


@router.get("/batches/{batch_id}/jobs/", response=list[GenerationJobListSchema])
def get_batch_jobs(request, batch_id: str):
    batch = get_object_or_404(
        GenerationBatch,
        id=batch_id,
        created_by=request.auth,
    )
    jobs = batch.jobs.select_related(
        "model",
        "app_requirement",
        "scaffolding_template",
    )
    return [
        GenerationJobListSchema(
            id=job.id,
            mode=job.mode,
            status=job.status,
            model_name=job.model.model_name if job.model else None,
            model_id_str=job.model.model_id if job.model else None,
            template_name=(job.app_requirement.name if job.app_requirement else None),
            scaffolding_name=(job.scaffolding_template.name if job.scaffolding_template else None),
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=job.duration_seconds,
            error_message=job.error_message,
            created_at=job.created_at,
        )
        for job in jobs
    ]
