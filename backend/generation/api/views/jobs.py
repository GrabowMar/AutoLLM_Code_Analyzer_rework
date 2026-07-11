"""Generation job CRUD endpoints and batch endpoints.

All routes here use ``{job_id}`` / ``{batch_id}`` dynamic segments and MUST be
registered AFTER the static job-creation routes in
:mod:`backend.generation.api.views.custom`. Ordering is controlled by
:mod:`backend.generation.api.views.__init__`.
"""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Query

from backend.common.pagination import paginate_queryset
from backend.generation.api.schema import CopilotIterationSchema
from backend.generation.api.schema import GenerationArtifactSchema
from backend.generation.api.schema import GenerationBatchSchema
from backend.generation.api.schema import GenerationJobListSchema
from backend.generation.api.schema import GenerationJobSchema
from backend.generation.api.schema import PaginatedJobsSchema
from backend.generation.api.views._router import router
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.job_cloning import clone_job


@router.get("/jobs/stats/", response=dict)
def job_stats(request):
    """Get count of jobs by status."""
    qs = GenerationJob.objects.filter(created_by=request.auth)
    return {
        "total": qs.count(),
        "completed": qs.filter(status="completed").count(),
        "running": qs.filter(status="running").count(),
        "failed": qs.filter(status="failed").count(),
        "pending": qs.filter(status="pending").count(),
        "cancelled": qs.filter(status="cancelled").count(),
    }


@router.get("/jobs/", response=PaginatedJobsSchema)
def list_jobs(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    mode: str = Query(""),
    status: str = Query(""),
    model_id: str = Query(""),
    search: str = Query(""),
    sort_by: str = Query(""),
    container_status: str = Query(""),
):
    """List generation jobs with pagination and filters."""
    qs = GenerationJob.objects.filter(created_by=request.auth).select_related(
        "model",
        "app_requirement",
        "profile",
    )
    if mode:
        qs = qs.filter(mode=mode)
    if status:
        qs = qs.filter(status=status)
    if model_id:
        qs = qs.filter(model__model_id=model_id)

    # Filter by container status
    if container_status:
        if container_status == "running":
            qs = qs.filter(container_instances__status="running")
        elif container_status == "stopped":
            qs = qs.filter(container_instances__status="stopped")
        elif container_status == "building":
            qs = qs.filter(container_instances__status__in=["building", "pending"])
        elif container_status == "none":
            qs = qs.filter(Q(container_instances__isnull=True) | Q(container_instances__status="removed"))

    if search:
        import uuid

        uuid_q = Q()
        try:
            val = uuid.UUID(search)
            uuid_q = Q(id=val)
        except ValueError:
            pass
        qs = qs.filter(
            uuid_q
            | Q(model__model_name__icontains=search)
            | Q(model__model_id__icontains=search)
            | Q(app_requirement__name__icontains=search)
            | Q(stack_slug__icontains=search),
        )

    # Sorting
    allowed_sorts = {
        "created_desc": "-created_at",
        "created_asc": "created_at",
        "duration_desc": "-duration_seconds",
        "duration_asc": "duration_seconds",
        "model_asc": "model__model_name",
    }
    order_field = allowed_sorts.get(sort_by, "-created_at")
    qs = qs.order_by(order_field)

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    items = [
        GenerationJobListSchema(
            id=job.id,
            mode=job.mode,
            status=job.status,
            model_name=job.model.model_name if job.model else None,
            model_id_str=job.model.model_id if job.model else None,
            template_name=(job.app_requirement.name if job.app_requirement else None),
            stack_slug=job.stack_slug,
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
            "profile",
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


def _load_job_for_cloning(request, job_id: str) -> GenerationJob:
    return get_object_or_404(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "profile",
        ),
        id=job_id,
        created_by=request.auth,
    )


def _dispatch_and_serialize(new_job: GenerationJob) -> GenerationJobSchema:
    dispatch_job(new_job)
    return GenerationJobSchema.from_orm(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
        ).get(
            id=new_job.id,
        ),
    )


@router.post("/jobs/{job_id}/retry/", response={200: GenerationJobSchema, 400: dict})
def retry_job(request, job_id: str):
    """Re-create a failed/cancelled job, re-resolving templates."""
    original = _load_job_for_cloning(request, job_id)
    if original.status not in ("failed", "cancelled"):
        return 400, {"message": "Only failed or cancelled jobs can be retried"}
    new_job = clone_job(original, request.auth, reuse_snapshot=False, new_seed=True)
    return _dispatch_and_serialize(new_job)


@router.post("/jobs/{job_id}/replay/", response={200: GenerationJobSchema, 400: dict})
def replay_job(request, job_id: str):
    """Exact replay: identical prompt snapshot and sampling seed."""
    original = _load_job_for_cloning(request, job_id)
    if original.status in ("pending", "running"):
        return 400, {"message": "Job is still in progress"}
    if not isinstance(original.resolved_bundle, dict) or not original.resolved_bundle:
        return 400, {"message": "Job has no resolved bundle snapshot to replay"}
    new_job = clone_job(
        original,
        request.auth,
        reuse_snapshot=True,
        new_seed=False,
        # Replays verify reproducibility; keep them out of the trial counts.
        keep_batch=False,
    )
    return _dispatch_and_serialize(new_job)


@router.post("/jobs/{job_id}/rerun/", response={200: GenerationJobSchema, 400: dict})
def rerun_job(request, job_id: str):
    """One more trial: identical prompt snapshot, fresh sampling seed."""
    original = _load_job_for_cloning(request, job_id)
    if original.status in ("pending", "running"):
        return 400, {"message": "Job is still in progress"}
    if not isinstance(original.resolved_bundle, dict) or not original.resolved_bundle:
        return 400, {"message": "Job has no resolved bundle snapshot to rerun"}
    new_job = clone_job(original, request.auth, reuse_snapshot=True, new_seed=True)
    if new_job.batch:
        # An extra trial grows the batch rather than displacing a failure.
        GenerationBatch.objects.filter(id=new_job.batch_id).update(
            total_jobs=new_job.batch.total_jobs + 1,
        )
    return _dispatch_and_serialize(new_job)


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
            "profile",
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
    resolved = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    experiment = {
        "bundle_schema_version": resolved.get("bundle_schema_version"),
        "bundle_slug": resolved.get("bundle_slug"),
        "scaffolding_slug": resolved.get("scaffolding_slug"),
        "seed": resolved.get("seed", job.experiment_seed),
        "prompt_hash": job.prompt_hash or resolved.get("prompt_hash"),
        "run_fingerprint": resolved.get("run_fingerprint"),
        "bundle_key": job.bundle_key,
        "llm": resolved.get("llm"),
        "block_count": len(resolved.get("blocks", [])),
        "app_requirement_slug": (resolved.get("app_requirement") or {}).get("slug"),
        "profile_id": job.profile_id,
        "profile_slug": (job.profile.slug if job.profile else None),
    }
    return {
        "experiment": experiment,
        "resolved_bundle": resolved,
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
    )
    return [
        GenerationJobListSchema(
            id=job.id,
            mode=job.mode,
            status=job.status,
            model_name=job.model.model_name if job.model else None,
            model_id_str=job.model.model_id if job.model else None,
            template_name=(job.app_requirement.name if job.app_requirement else None),
            stack_slug=job.stack_slug,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=job.duration_seconds,
            error_message=job.error_message,
            created_at=job.created_at,
        )
        for job in jobs
    ]
