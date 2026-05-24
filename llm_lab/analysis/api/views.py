"""Django Ninja API views for analysis."""

from __future__ import annotations

from typing import Any

from django.db.models import Count
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja import Router

from llm_lab.analysis.api.schema import ActionResponseSchema
from llm_lab.analysis.api.schema import AnalysisProfileCreateSchema
from llm_lab.analysis.api.schema import AnalysisProfileSchema
from llm_lab.analysis.api.schema import AnalysisResultSchema
from llm_lab.analysis.api.schema import AnalysisStatsSchema
from llm_lab.analysis.api.schema import AnalysisTaskCreateSchema
from llm_lab.analysis.api.schema import AnalysisTaskListSchema
from llm_lab.analysis.api.schema import AnalysisTaskSchema
from llm_lab.analysis.api.schema import AnalyzerInfoSchema
from llm_lab.analysis.api.schema import FindingSchema
from llm_lab.analysis.api.schema import PaginatedAnalysisTasksSchema
from llm_lab.analysis.api.schema import PaginatedFindingsSchema
from llm_lab.analysis.api.schema import SuppressSchema
from llm_lab.analysis.models import AnalysisProfile
from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.services.base import AnalyzerRegistry
from llm_lab.common.pagination import paginate_queryset
from llm_lab.generation.models import GenerationJob

router = Router(tags=["analysis"])


def _validate_analyzer_settings(analyzer_name: str, settings: dict[str, Any]) -> list[str]:
    """Return validation error messages for analyzer settings against its config_schema."""
    analyzer_cls = AnalyzerRegistry.get(analyzer_name)
    if not analyzer_cls:
        return []
    errors: list[str] = []
    for cf in analyzer_cls.config_schema:
        val = settings.get(cf.name, cf.default)
        if cf.required and val is None:
            errors.append(f"{analyzer_name}.{cf.name}: required")
        if cf.type == "number" and val is not None:
            try:
                num = float(val)
            except (TypeError, ValueError):
                errors.append(f"{analyzer_name}.{cf.name}: must be a number")
                continue
            if cf.min is not None and num < cf.min:
                errors.append(f"{analyzer_name}.{cf.name}: must be >= {cf.min}")
            if cf.max is not None and num > cf.max:
                errors.append(f"{analyzer_name}.{cf.name}: must be <= {cf.max}")
        if cf.type in ("select", "multiselect") and cf.options and val is not None:
            valid = {o["value"] for o in cf.options}
            if cf.type == "select" and val not in valid:
                errors.append(f"{analyzer_name}.{cf.name}: invalid option '{val}'")
    return errors


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

    # Resolve profile and merge analyzers + settings
    profile = None
    analyzers = list(payload.analyzers)
    merged_settings: dict[str, Any] = dict(payload.settings)

    if payload.profile_id:
        try:
            profile = AnalysisProfile.objects.get(id=payload.profile_id, created_by=request.auth)
        except AnalysisProfile.DoesNotExist:
            return api.create_response(
                request,
                {"detail": "Profile not found"},
                status=404,
            )
        if not analyzers:
            analyzers = list(profile.analyzers)
        # Profile settings are the base; payload settings override per-analyzer
        base = {k: dict(v) for k, v in profile.settings.items()}
        for name, overrides in payload.settings.items():
            base.setdefault(name, {}).update(overrides)
        merged_settings = base

    # Validate that all requested analyzers are known
    known = {a["name"] for a in AnalyzerRegistry.list_available()}
    unknown = set(analyzers) - known
    if unknown:
        return api.create_response(
            request,
            {"detail": f"Unknown analyzers: {', '.join(sorted(unknown))}"},
            status=400,
        )

    # Validate per-analyzer settings against config_schema
    validation_errors: list[str] = []
    for name in analyzers:
        ana_settings = merged_settings.get(name, {})
        validation_errors.extend(_validate_analyzer_settings(name, ana_settings))
    if validation_errors:
        return api.create_response(
            request,
            {"detail": validation_errors},
            status=422,
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
        profile=profile,
        configuration={
            "analyzers": analyzers,
            "settings": merged_settings,
            "live_target": payload.live_target,
            "generation_job_id": (str(payload.generation_job_id) if payload.generation_job_id else None),
            "thresholds": payload.thresholds,
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
            threshold_status=task.threshold_status,
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
            profile_id=task.profile_id,
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
    file_path: str = Query(""),
    include_suppressed: bool = Query(default=False),
):
    """List all findings for an analysis task with filters."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    qs = Finding.objects.filter(
        result__task=task,
    ).select_related("result", "suppressed_by")

    if not include_suppressed:
        qs = qs.filter(suppressed=False)
    if severity:
        qs = qs.filter(severity=severity)
    if category:
        qs = qs.filter(category=category)
    if analyzer:
        qs = qs.filter(result__analyzer_name=analyzer)
    if file_path:
        qs = qs.filter(file_path__icontains=file_path)

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    items = list(page_qs)

    return PaginatedFindingsSchema(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.post("/tasks/{task_id}/findings/{finding_id}/suppress/", response=FindingSchema)
def suppress_finding(request, task_id: str, finding_id: int, payload: SuppressSchema):
    """Mark a finding as suppressed (false positive)."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    finding = get_object_or_404(Finding, id=finding_id, result__task=task)
    finding.suppressed = True
    finding.suppression_reason = payload.reason
    finding.suppressed_by = request.auth
    finding.save(update_fields=["suppressed", "suppression_reason", "suppressed_by"])
    return Finding.objects.select_related("result", "suppressed_by").get(id=finding.id)


@router.post("/tasks/{task_id}/findings/{finding_id}/unsuppress/", response=FindingSchema)
def unsuppress_finding(request, task_id: str, finding_id: int):
    """Remove suppression from a finding."""
    task = get_object_or_404(AnalysisTask, id=task_id, created_by=request.auth)
    finding = get_object_or_404(Finding, id=finding_id, result__task=task)
    finding.suppressed = False
    finding.suppression_reason = ""
    finding.suppressed_by = None
    finding.save(update_fields=["suppressed", "suppression_reason", "suppressed_by"])
    return Finding.objects.select_related("result", "suppressed_by").get(id=finding.id)


# -- Analyzers -------------------------------------------------------------


@router.get("/analyzers/", response=list[AnalyzerInfoSchema])
def list_analyzers(request):
    """List all available analyzers."""
    return AnalyzerRegistry.list_available()


# -- Profiles --------------------------------------------------------------


@router.get("/profiles/", response=list[AnalysisProfileSchema])
def list_profiles(request):
    """List analysis profiles for the current user."""
    return AnalysisProfile.objects.filter(created_by=request.auth).order_by("-is_default", "name")


@router.post("/profiles/", response=AnalysisProfileSchema)
def create_profile(request, payload: AnalysisProfileCreateSchema):
    """Create a new analysis profile."""
    if payload.is_default:
        AnalysisProfile.objects.filter(created_by=request.auth, is_default=True).update(is_default=False)
    return AnalysisProfile.objects.create(
        name=payload.name,
        description=payload.description,
        analyzers=payload.analyzers,
        settings=payload.settings,
        is_default=payload.is_default,
        created_by=request.auth,
    )


@router.get("/profiles/{profile_id}/", response=AnalysisProfileSchema)
def get_profile(request, profile_id: int):
    """Get an analysis profile."""
    return get_object_or_404(AnalysisProfile, id=profile_id, created_by=request.auth)


@router.put("/profiles/{profile_id}/", response=AnalysisProfileSchema)
def update_profile(request, profile_id: int, payload: AnalysisProfileCreateSchema):
    """Update an analysis profile."""
    profile = get_object_or_404(AnalysisProfile, id=profile_id, created_by=request.auth)
    if payload.is_default and not profile.is_default:
        AnalysisProfile.objects.filter(created_by=request.auth, is_default=True).update(is_default=False)
    profile.name = payload.name
    profile.description = payload.description
    profile.analyzers = payload.analyzers
    profile.settings = payload.settings
    profile.is_default = payload.is_default
    profile.save()
    return profile


@router.delete("/profiles/{profile_id}/", response=ActionResponseSchema)
def delete_profile(request, profile_id: int):
    """Delete an analysis profile."""
    profile = get_object_or_404(AnalysisProfile, id=profile_id, created_by=request.auth)
    profile.delete()
    return {"success": True}


# -- Stats -----------------------------------------------------------------


@router.get("/stats/", response=AnalysisStatsSchema)
def get_stats(request):
    """Get aggregated analysis stats for the current user."""
    tasks = AnalysisTask.objects.filter(created_by=request.auth)
    findings = Finding.objects.filter(result__task__created_by=request.auth, suppressed=False)

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
