"""Generation job CRUD endpoints and batch endpoints.

All routes here use ``{job_id}`` / ``{batch_id}`` dynamic segments and MUST be
registered AFTER the static job-creation routes in
:mod:`backend.generation.api.views.custom`. Ordering is controlled by
:mod:`backend.generation.api.views.__init__`.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import FloatField
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query
from ninja.errors import HttpError

from backend.common.pagination import paginate_queryset
from backend.execution.transitions import cancel_jobs
from backend.generation.api.schema import BatchSummarySchema
from backend.generation.api.schema import CopilotIterationSchema
from backend.generation.api.schema import GenerationArtifactSchema
from backend.generation.api.schema import GenerationJobListSchema
from backend.generation.api.schema import GenerationJobSchema
from backend.generation.api.schema import PaginatedBatchesSchema
from backend.generation.api.schema import PaginatedJobsSchema
from backend.generation.api.views._router import router
from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.batches import batch_summaries
from backend.generation.services.batches import batch_summary
from backend.generation.services.batches import cancel_batch
from backend.generation.services.batches import experiment_batch_q
from backend.generation.services.batches import refresh_batch
from backend.generation.services.dispatcher import dispatch_evaluation
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.job_cloning import CellTakenError
from backend.generation.services.job_cloning import clone_job
from backend.generation.services.job_listing import job_list_item
from backend.generation.services.job_listing import with_list_fields
from backend.runtime.models import ContainerInstance

# Detail reads: every FK the detail schema resolves, in one query.
_DETAIL_RELATED = (
    "model",
    "app_requirement",
    "profile",
    "batch",
    "created_by",
    "harness_preset",
    "sampling_preset",
    "budget_preset",
)

_CONTAINER_STATUS_FILTERS = {
    "running": Q(status="running"),
    "stopped": Q(status="stopped"),
    "building": Q(status__in=["building", "pending"]),
}


_QUEUED_PHASES = (GenerationJob.Phase.QUEUED, GenerationJob.Phase.EVAL_QUEUED)
_SORTS = {
    "created_desc": "-created_at",
    "created_asc": "created_at",
    "duration_desc": "-duration_seconds",
    "duration_asc": "duration_seconds",
    "model_asc": "model__model_name",
    "sequence": "sequence",
    "-completed_at": "-completed_at",
}


@router.get("/jobs/stats/", response=dict)
def job_stats(request):
    """Count jobs by status (one query). ``running`` includes the ``queued``
    ones: running jobs waiting for an evaluation slot (phase ``eval_queued``;
    legacy rows: ``queued``).
    """
    status = GenerationJob.Status
    return GenerationJob.objects.filter(created_by=request.auth).aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=status.COMPLETED)),
        running=Count("id", filter=Q(status=status.RUNNING)),
        queued=Count("id", filter=Q(status=status.RUNNING, phase__in=_QUEUED_PHASES)),
        failed=Count("id", filter=Q(status=status.FAILED)),
        pending=Count("id", filter=Q(status=status.PENDING)),
        cancelled=Count("id", filter=Q(status=status.CANCELLED)),
    )


def _uuid_param(value: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HttpError(400, f"{name} must be a UUID") from exc


def _current_attempts(qs):
    """Only the latest job of each experiment cell (``DISTINCT ON (condition, app, repeat)``); ad-hoc jobs stay."""
    latest = (
        GenerationJob.objects.filter(condition__isnull=False)
        .order_by("condition_id", "app_requirement_id", "repeat_index", "-created_at")
        .distinct("condition_id", "app_requirement_id", "repeat_index")
        .values("id")
    )
    return qs.filter(Q(condition__isnull=True) | Q(id__in=Subquery(latest)))


def _execution_fields(job: GenerationJob) -> dict:
    queue_seconds = getattr(job, "queue_seconds_value", None)
    return {
        "attempt": job.attempt,
        "endpoint_slug": getattr(job, "endpoint_slug_value", None),
        "sequence": job.sequence,
        "queue_seconds": float(queue_seconds) if queue_seconds is not None else None,
    }


@router.get("/jobs/", response=PaginatedJobsSchema)
def list_jobs(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=500),
    mode: str = Query(""),
    status: str = Query(""),
    model_id: str = Query(""),
    search: str = Query(""),
    sort_by: str = Query(""),
    container_status: str = Query(""),
    batch_id: str = Query(""),
    harness: str = Query(""),
    experiment_id: str = Query(""),
    condition_id: int | None = Query(None),
    endpoint_id: int | None = Query(None),
    app_requirement_id: int | None = Query(None),
    repeat_index: int | None = Query(None),
    phase: str = Query(""),
    current: bool = Query(default=False),
    attempt_gt: int | None = Query(None),
):
    """List generation jobs with pagination and filters.

    ``per_page`` may go up to 500 only together with ``experiment_id`` (else 100).
    ``phase`` takes a comma-separated list; ``current=true`` keeps only the latest
    job per experiment cell; ``endpoint_id`` of the system (OpenRouter) endpoint
    matches jobs without one.
    """
    if per_page > 100 and not experiment_id:
        raise HttpError(400, "per_page above 100 requires experiment_id")
    qs = GenerationJob.objects.filter(created_by=request.auth)
    if mode:
        qs = qs.filter(mode=mode)
    if status:
        qs = qs.filter(status=status)
    if model_id:
        qs = qs.filter(model__model_id=model_id)
    if batch_id:
        qs = qs.filter(batch_id=_uuid_param(batch_id, "batch_id"))
    if experiment_id:
        qs = qs.filter(experiment_id=_uuid_param(experiment_id, "experiment_id"))
    if condition_id is not None:
        qs = qs.filter(condition_id=condition_id)
    if app_requirement_id is not None:
        qs = qs.filter(app_requirement_id=app_requirement_id)
    if repeat_index is not None:
        qs = qs.filter(repeat_index=repeat_index)
    if endpoint_id is not None:
        from backend.inference.models import LLMEndpoint

        if LLMEndpoint.objects.filter(pk=endpoint_id, is_system=True).exists():
            qs = qs.filter(Q(endpoint_id__isnull=True) | Q(endpoint_id=endpoint_id))
        else:
            qs = qs.filter(endpoint_id=endpoint_id)
    if phase:
        qs = qs.filter(phase__in=[p.strip() for p in phase.split(",")])
    if attempt_gt is not None:
        qs = qs.filter(attempt__gt=attempt_gt)
    if current:
        qs = _current_attempts(qs)

    # Container filters are EXISTS subqueries, not joins: a job with several
    # containers must still be one row (and counted once).
    if container_status:
        containers = ContainerInstance.objects.filter(generation_job=OuterRef("pk"))
        if container_status in _CONTAINER_STATUS_FILTERS:
            qs = qs.filter(Exists(containers.filter(_CONTAINER_STATUS_FILTERS[container_status])))
        elif container_status == "none":
            qs = qs.filter(~Exists(containers.exclude(status="removed")))

    if search:
        uuid_q = Q()
        with contextlib.suppress(ValueError):
            uuid_q = Q(id=uuid.UUID(search))
        qs = qs.filter(
            uuid_q
            | Q(model__model_name__icontains=search)
            | Q(model__model_id__icontains=search)
            | Q(app_requirement__name__icontains=search)
            | Q(stack_slug__icontains=search),
        )

    qs = with_list_fields(qs).annotate(
        endpoint_slug_value=F("endpoint__slug"),
        queue_seconds_value=Cast(KeyTextTransform("queue_seconds", "execution"), FloatField()),
    )
    if harness:
        qs = qs.filter(harness=harness)

    order_field = _SORTS.get(sort_by, "-created_at")
    if order_field == "sequence":
        qs = qs.order_by(F("sequence").asc(nulls_last=True), "queued_at", "-id")
    elif order_field == "-completed_at":
        qs = qs.order_by(F("completed_at").desc(nulls_last=True), "-id")
    else:
        qs = qs.order_by(order_field, "-id")

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    return PaginatedJobsSchema(
        items=[GenerationJobListSchema(**job_list_item(job), **_execution_fields(job)) for job in page_qs],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


def _job_uuid(job_id: str) -> uuid.UUID:
    """A malformed job id is a JSON 404 (clients show "not found"), not a 500 from the UUID field."""
    try:
        return uuid.UUID(job_id)
    except ValueError as exc:
        raise HttpError(404, "Not Found") from exc


@router.get("/jobs/{job_id}/", response=GenerationJobSchema)
def get_job(request, job_id: str):
    return get_object_or_404(
        GenerationJob.objects.select_related(*_DETAIL_RELATED),
        id=_job_uuid(job_id),
        created_by=request.auth,
    )


@router.post("/jobs/{job_id}/cancel/")
def cancel_job(request, job_id: str):
    job = get_object_or_404(
        GenerationJob.objects.select_related("batch"),
        id=_job_uuid(job_id),
        created_by=request.auth,
    )
    if job.status in ("pending", "running"):
        counts = cancel_jobs(GenerationJob.objects.filter(id=job.id), scope="all")
        if counts["finishing_evaluations"]:
            return {
                "success": False,
                "status": job.status,
                "message": "The run is done and is being evaluated; it finishes on its own.",
            }
        if counts["cancelled_pending"] or counts["signalled_running"]:
            return {"success": True, "status": "cancelled"}
    return {
        "success": False,
        "status": job.status,
        "message": "Job cannot be cancelled",
    }


@router.delete("/jobs/{job_id}/")
def delete_job(request, job_id: str):
    """Delete a job that is not currently running."""
    job = get_object_or_404(
        GenerationJob.objects.select_related("batch"),
        id=_job_uuid(job_id),
        created_by=request.auth,
    )
    if job.status == "running":
        return {
            "success": False,
            "message": "Cannot delete a running job. Cancel it first.",
        }
    batch = job.batch
    job.delete()
    if batch is not None:
        refresh_batch(batch)
    return {"success": True}


def _load_job_for_cloning(request, job_id: str) -> GenerationJob:
    return get_object_or_404(
        GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "profile",
        ),
        id=_job_uuid(job_id),
        created_by=request.auth,
    )


def _dispatch_and_serialize(new_job: GenerationJob) -> GenerationJobSchema:
    if new_job.batch_id:
        # The clone joined its batch: total_jobs counts it, and the batch is
        # active again until the clone finishes.
        refresh_batch(new_job.batch)
    dispatch_job(new_job)
    return GenerationJobSchema.from_orm(
        GenerationJob.objects.select_related(*_DETAIL_RELATED).get(id=new_job.id),
    )


def _reopen_experiment(job: GenerationJob) -> None:
    """Reopen the finished experiment a retried cell belongs to (as a top-up does, spec §C.8).

    The executor only claims jobs of RUNNING experiments, so without this a
    retry of a cell of a completed experiment would stay pending forever (and
    one of a cancelled experiment would be cancelled by the reconciler). A
    paused experiment stays paused.
    """
    if job.experiment_id is None:
        return
    reopened = Experiment.objects.filter(
        pk=job.experiment_id,
        status__in=[Experiment.Status.COMPLETED, Experiment.Status.CANCELLED],
    ).update(status=Experiment.Status.RUNNING, completed_at=None, cancelled_at=None, updated_at=timezone.now())
    if reopened:
        from backend.realtime.events import publish_experiment_event

        experiment_id = job.experiment_id
        event = {"type": "state", "status": "running", "pause_reason": ""}
        transaction.on_commit(lambda: publish_experiment_event(experiment_id, event))


@router.post("/jobs/{job_id}/retry/", response={200: GenerationJobSchema, 400: dict})
def retry_job(request, job_id: str):
    """Re-create a failed/cancelled job, re-resolving templates.

    The retry replaces the failed trial: it takes over the job's experiment
    cell and batch and runs under the cell's seed (common random numbers).
    """
    original = _load_job_for_cloning(request, job_id)
    if original.status not in ("failed", "cancelled"):
        return 400, {"message": "Only failed or cancelled jobs can be retried"}
    try:
        # The cell check runs inside the clone's transaction, under a row lock.
        new_job = clone_job(original, request.auth, reuse_snapshot=False, new_seed=True, keep_cell=True)
    except CellTakenError as exc:
        return 400, {"message": str(exc)}
    _reopen_experiment(new_job)
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
        # Replays verify reproducibility; keep them out of the trial counts
        # (no batch, no experiment cell).
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
    # An extra trial grows the batch rather than displacing a failure. It
    # joins no experiment cell: its fresh seed is unpaired, and one extra
    # trial of one condition would unbalance the design (raise the
    # experiment's repeats and relaunch instead).
    new_job = clone_job(original, request.auth, reuse_snapshot=True, new_seed=True)
    return _dispatch_and_serialize(new_job)


@router.get("/jobs/{job_id}/artifacts/", response=list[GenerationArtifactSchema])
def get_job_artifacts(request, job_id: str):
    job = get_object_or_404(GenerationJob, id=_job_uuid(job_id), created_by=request.auth)
    return job.artifacts.all()


@router.post("/jobs/{job_id}/evaluate/", response={200: dict, 400: dict})
def evaluate(request, job_id: str):
    """Re-run the platform evaluation (build, run, hidden conformance suite) for a finished job."""
    job = get_object_or_404(GenerationJob, id=_job_uuid(job_id), created_by=request.auth)
    if job.status != GenerationJob.Status.COMPLETED:
        return 400, {"detail": "Only completed jobs can be evaluated."}
    if job.app_requirement_id is None and not (job.resolved_bundle or {}).get("app_requirement"):
        return 400, {"detail": "This job has no app spec to evaluate against."}
    if not dispatch_evaluation(job):
        return 400, {"detail": "The job is already queued or running."}
    return {"status": "queued"}


@router.get("/jobs/{job_id}/run-record/{name}")
def download_run_record(request, job_id: str, name: str):
    """One file of an agent run's evidence (LLM call log, harness events, patch, workspace).

    Only names listed in the job's own run record are served, and the file
    must still match the sha256 recorded when the run finished.
    """
    job = get_object_or_404(GenerationJob, id=_job_uuid(job_id), created_by=request.auth)
    record = ((job.result_data or {}).get("agent") or {}).get("run_record") or {}
    entry = record.get(name)
    if name.startswith("_") or not isinstance(entry, dict):
        raise HttpError(404, "No such run-record file")
    root = Path(settings.GENERATION_RUNS_ROOT) / str(job.id)
    recorded = Path(record.get("_dir") or root)
    # Retried attempts keep their evidence under <job_id>/a<attempt>/ (never outside the job's directory).
    if recorded != root and root not in recorded.parents:
        raise HttpError(404, "No such run-record file")
    path = recorded / name
    if not path.is_file():
        raise HttpError(404, "Run-record file is no longer on disk")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise HttpError(409, "Run-record file does not match its recorded sha256")
    return FileResponse(path.open("rb"), as_attachment=True, filename=f"{str(job.id)[:8]}-{name}")


@router.get(
    "/jobs/{job_id}/copilot-iterations/",
    response=list[CopilotIterationSchema],
)
def get_copilot_iterations(request, job_id: str):
    job = get_object_or_404(GenerationJob, id=_job_uuid(job_id), created_by=request.auth)
    return job.copilot_iterations.all()


@router.get("/jobs/{job_id}/export/")
def export_job(request, job_id: str):
    """Export full job data as JSON (job + artifacts + iterations)."""
    job = get_object_or_404(
        GenerationJob.objects.select_related(*_DETAIL_RELATED),
        id=_job_uuid(job_id),
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
        "config_fingerprint": resolved.get("config_fingerprint"),
        "presets": resolved.get("presets"),
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


def _owned_batch(request, batch_id: uuid.UUID) -> GenerationBatch:
    return get_object_or_404(GenerationBatch, id=batch_id, created_by=request.auth)


@router.get("/batches/", response=PaginatedBatchesSchema)
def list_batches(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    mode: str = Query(""),
    experiment: bool | None = Query(None),
):
    """The user's batches, newest first. ``experiment``: true = experiment launches only, false = ad-hoc only."""
    qs = GenerationBatch.objects.filter(created_by=request.auth)
    if mode:
        qs = qs.filter(mode=mode)
    if experiment is not None:
        qs = qs.filter(experiment_batch_q()) if experiment else qs.exclude(experiment_batch_q())
    page_qs, total, page, pages = paginate_queryset(qs.order_by("-created_at", "-id"), page, per_page)
    return PaginatedBatchesSchema(
        items=[BatchSummarySchema(**row) for row in batch_summaries(page_qs)],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/batches/{uuid:batch_id}/", response=BatchSummarySchema)
def get_batch(request, batch_id: uuid.UUID):
    return batch_summary(_owned_batch(request, batch_id))


@router.get("/batches/{uuid:batch_id}/jobs/", response=list[GenerationJobListSchema])
def get_batch_jobs(request, batch_id: uuid.UUID):
    """Every job of the batch, in creation order (task, trial, model, harness preset)."""
    batch = _owned_batch(request, batch_id)
    jobs = with_list_fields(batch.jobs.all()).order_by("created_at", "id")
    return [GenerationJobListSchema(**job_list_item(job)) for job in jobs]


@router.post("/batches/{uuid:batch_id}/cancel/", response=dict)
def cancel_generation_batch(request, batch_id: uuid.UUID):
    """Cancel every pending, queued and running job of the batch."""
    batch = _owned_batch(request, batch_id)
    # Status events go out after commit (execution.transitions.cancel_jobs).
    cancelled = cancel_batch(batch)
    return {"cancelled": len(cancelled)}
