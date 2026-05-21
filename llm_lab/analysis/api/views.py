"""Django Ninja API views for analysis."""

from __future__ import annotations

from django.db.models import Count
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja import Router

from llm_lab.analysis.api.schema import ActionResponseSchema
from llm_lab.analysis.api.schema import AnalysisResultSchema
from llm_lab.analysis.api.schema import AnalysisStatsSchema
from llm_lab.analysis.api.schema import AnalysisTaskCreateSchema
from llm_lab.analysis.api.schema import AnalysisTaskListSchema
from llm_lab.analysis.api.schema import AnalysisTaskSchema
from llm_lab.analysis.api.schema import AnalyzerInfoSchema
from llm_lab.analysis.api.schema import PaginatedAnalysisTasksSchema
from llm_lab.analysis.api.schema import PaginatedFindingsSchema
from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.services.base import AnalyzerRegistry
from llm_lab.common.pagination import paginate_queryset
from llm_lab.generation.models import GenerationJob

router = Router(tags=["analysis"])


def _dispatch_task(task: AnalysisTask) -> None:
    """Run an analysis task in a background thread."""
    import logging

    from llm_lab.analysis.services.analysis_service import AnalysisService
    from llm_lab.common.threading import dispatch_in_thread

    _logger = logging.getLogger(__name__)
    task_id = task.id

    def _run() -> None:
        try:
            service = AnalysisService()
            service.execute(
                AnalysisTask.objects.select_related(
                    "generation_job",
                    "created_by",
                ).get(id=task_id),
            )
        except AnalysisTask.DoesNotExist:
            _logger.warning("Analysis task %s no longer exists", task_id)
        except Exception:
            _logger.exception(
                "Unhandled error dispatching analysis task %s",
                task_id,
            )

    dispatch_in_thread(_run)


# -- Tasks -----------------------------------------------------------------


@router.post("/tasks/", response=AnalysisTaskSchema)
def create_task(request, payload: AnalysisTaskCreateSchema):
    """Create an analysis task."""
    from config.api import api

    known = {a["name"] for a in AnalyzerRegistry.list_available()}
    unknown = set(payload.analyzers) - known
    if unknown:
        return api.create_response(
            request,
            {"detail": f"Unknown analyzers: {', '.join(sorted(unknown))}"},
            status=400,
        )

    generation_job = None
    if payload.generation_job_id:
        generation_job = get_object_or_404(
            GenerationJob,
            id=payload.generation_job_id,
            created_by=request.auth,
        )

    task = AnalysisTask.objects.create(
        name=payload.name,
        generation_job=generation_job,
        source_code=payload.source_code,
        configuration={
            "analyzers": payload.analyzers,
            "settings": payload.settings,
            "live_target": payload.live_target,
            "generation_job_id": (str(payload.generation_job_id) if payload.generation_job_id else None),
        },
        created_by=request.auth,
    )

    if payload.auto_start:
        _dispatch_task(task)

    return AnalysisTask.objects.select_related(
        "generation_job",
        "created_by",
    ).get(id=task.id)


@router.get("/tasks/", response=PaginatedAnalysisTasksSchema)
def list_tasks(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    status: str = Query(""),
    search: str = Query(""),
    generation_job_id: str = Query(""),
):
    """List analysis tasks with pagination and filters."""
    qs = (
        AnalysisTask.objects.filter(
            created_by=request.auth,
        )
        .select_related("created_by")
        .order_by("-created_at")
    )

    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(name__icontains=search)
    if generation_job_id:
        qs = qs.filter(generation_job_id=generation_job_id)

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    items = [
        AnalysisTaskListSchema(
            id=task.id,
            name=task.name,
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
            generation_job_id=(str(task.generation_job_id) if task.generation_job_id else None),
            created_by_email=task.created_by.email if task.created_by else "",
            results_summary=task.results_summary,
            started_at=task.started_at,
            completed_at=task.completed_at,
            duration_seconds=task.duration_seconds,
            container_instance_id=task.configuration.get("container_instance_id") or None,
            target_url=task.configuration.get("target_url") or None,
        )
        for task in page_qs
    ]

    return PaginatedAnalysisTasksSchema(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/tasks/{task_id}/", response=AnalysisTaskSchema)
def get_task(request, task_id: str):
    """Get analysis task detail."""
    return get_object_or_404(
        AnalysisTask.objects.select_related(
            "generation_job",
            "created_by",
        ).prefetch_related("results"),
        id=task_id,
        created_by=request.auth,
    )


@router.post("/tasks/{task_id}/cancel/", response=ActionResponseSchema)
def cancel_task(request, task_id: str):
    """Cancel a pending or running analysis task."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    if task.status in ("pending", "running"):
        task.status = AnalysisTask.Status.CANCELLED
        task.save(update_fields=["status", "updated_at"])
        return {"success": True, "status": "cancelled"}
    return {
        "success": False,
        "status": task.status,
        "message": "Task cannot be cancelled",
    }


@router.delete("/tasks/{task_id}/", response=ActionResponseSchema)
def delete_task(request, task_id: str):
    """Delete an analysis task and all related data."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    task.delete()
    return {"success": True}


# -- Results ---------------------------------------------------------------


@router.get("/tasks/{task_id}/results/", response=list[AnalysisResultSchema])
def list_results(request, task_id: str):
    """List all results for an analysis task."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    return task.results.annotate(
        _findings_count=Count("findings"),
    ).all()


@router.get(
    "/tasks/{task_id}/results/{result_id}/",
    response=AnalysisResultSchema,
)
def get_result(request, task_id: str, result_id: int):
    """Get a single analysis result."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    return get_object_or_404(AnalysisResult, id=result_id, task=task)


# -- Findings --------------------------------------------------------------


@router.get("/tasks/{task_id}/findings/", response=PaginatedFindingsSchema)
def list_findings(
    request,
    task_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    severity: str = Query(""),
    category: str = Query(""),
    analyzer: str = Query(""),
):
    """List all findings for an analysis task with filters."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    qs = Finding.objects.filter(
        result__task=task,
    ).select_related("result")

    if severity:
        qs = qs.filter(severity=severity)
    if category:
        qs = qs.filter(category=category)
    if analyzer:
        qs = qs.filter(result__analyzer_name=analyzer)

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    items = list(page_qs)

    return PaginatedFindingsSchema(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# -- Analyzers -------------------------------------------------------------


@router.get("/analyzers/", response=list[AnalyzerInfoSchema])
def list_analyzers(request):
    """List all available analyzers."""
    return AnalyzerRegistry.list_available()


# -- Stats -----------------------------------------------------------------


@router.get("/stats/", response=AnalysisStatsSchema)
def get_stats(request):
    """Get aggregated analysis stats for the current user."""
    tasks = AnalysisTask.objects.filter(created_by=request.auth)
    findings = Finding.objects.filter(result__task__created_by=request.auth)

    task_counts = tasks.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
    )

    severity_counts = dict(
        findings.values_list("severity").annotate(count=Count("id")).values_list("severity", "count"),
    )

    category_counts = dict(
        findings.values_list("category").annotate(count=Count("id")).values_list("category", "count"),
    )

    most_common = list(
        findings.values("title").annotate(count=Count("id")).order_by("-count")[:10],
    )

    return AnalysisStatsSchema(
        total_tasks=task_counts["total"],
        completed_tasks=task_counts["completed"],
        failed_tasks=task_counts["failed"],
        running_tasks=task_counts["running"],
        total_findings=findings.count(),
        findings_by_severity=severity_counts,
        findings_by_category=category_counts,
        most_common_issues=most_common,
    )
