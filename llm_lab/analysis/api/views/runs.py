"""Analysis run endpoints (mounted under ``/analysis/``)."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError

from llm_lab.analysis.api.schema import AnalysisRunSchema
from llm_lab.analysis.api.schema import PaginatedFindingsSchema
from llm_lab.analysis.api.schema import PaginatedRunsSchema
from llm_lab.analysis.api.schema import RunCreateSchema
from llm_lab.analysis.api.views._router import router
from llm_lab.analysis.models import AnalysisRun
from llm_lab.analysis.models import Finding
from llm_lab.analysis.services import runner
from llm_lab.analysis.services import workspace_service
from llm_lab.common.pagination import paginate_queryset


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def serialize_result(result) -> dict[str, Any]:
    return {
        "id": str(result.id),
        "tool_slug": result.tool_slug,
        "category": result.category,
        "status": result.status,
        "summary": result.summary or {},
        "error_message": result.error_message,
    }


def serialize_run(run: AnalysisRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "name": run.name,
        "status": run.status,
        "tool_slugs": run.tool_slugs or [],
        "summary": run.summary or {},
        "error_message": run.error_message,
        "generation_job_id": str(run.generation_job_id) if run.generation_job_id else None,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "created_at": _iso(run.created_at),
        "results": [serialize_result(r) for r in run.results.all()],
    }


def serialize_run_list(run: AnalysisRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "name": run.name,
        "status": run.status,
        "tool_slugs": run.tool_slugs or [],
        "summary": run.summary or {},
        "created_at": _iso(run.created_at),
    }


@router.get("/runs/", response=PaginatedRunsSchema)
def list_runs(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: str = Query(""),
    generation_job_id: str = Query(""),
):
    """List the current user's analysis runs."""
    qs = AnalysisRun.objects.filter(created_by=request.auth)
    if status:
        qs = qs.filter(status=status)
    if generation_job_id:
        qs = qs.filter(generation_job_id=generation_job_id)
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    return {
        "items": [serialize_run_list(r) for r in page_qs],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.post("/runs/", response=AnalysisRunSchema)
def create_run(request, payload: RunCreateSchema):
    """Create an analysis run and (optionally) start it."""
    if not payload.tool_slugs:
        raise HttpError(400, "At least one tool must be selected.")
    if not payload.source_code and not payload.generation_job_id:
        raise HttpError(400, "Provide source_code or a generation_job_id.")

    workspace = workspace_service.get_workspace(request.auth)
    run = AnalysisRun.objects.create(
        name=payload.name or "Analysis run",
        created_by=request.auth,
        workspace=workspace,
        generation_job_id=payload.generation_job_id or None,
        source_code=payload.source_code,
        tool_slugs=payload.tool_slugs,
    )
    if payload.auto_start:
        runner.dispatch(str(run.id))
    return serialize_run(run)


@router.get("/runs/{run_id}/", response=AnalysisRunSchema)
def get_run(request, run_id: str):
    run = get_object_or_404(
        AnalysisRun.objects.prefetch_related("results"),
        id=run_id,
        created_by=request.auth,
    )
    return serialize_run(run)


@router.post("/runs/{run_id}/cancel/", response=AnalysisRunSchema)
def cancel_run(request, run_id: str):
    run = get_object_or_404(AnalysisRun, id=run_id, created_by=request.auth)
    if run.status in (AnalysisRun.Status.PENDING, AnalysisRun.Status.RUNNING):
        run.status = AnalysisRun.Status.CANCELLED
        run.save(update_fields=["status"])
    return serialize_run(run)


@router.delete("/runs/{run_id}/", response={200: dict})
def delete_run(request, run_id: str):
    run = get_object_or_404(AnalysisRun, id=run_id, created_by=request.auth)
    run.delete()
    return 200, {"status": "deleted"}


@router.get("/runs/{run_id}/findings/", response=PaginatedFindingsSchema)
def list_findings(
    request,
    run_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    severity: str = Query(""),
    tool_slug: str = Query(""),
):
    run = get_object_or_404(AnalysisRun, id=run_id, created_by=request.auth)
    qs = Finding.objects.filter(result__run=run).select_related("result")
    if severity:
        qs = qs.filter(severity=severity)
    if tool_slug:
        qs = qs.filter(result__tool_slug=tool_slug)
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    items = [
        {
            "id": str(f.id),
            "severity": f.severity,
            "category": f.category,
            "confidence": f.confidence,
            "title": f.title,
            "description": f.description,
            "suggestion": f.suggestion,
            "file_path": f.file_path,
            "line_number": f.line_number,
            "column_number": f.column_number,
            "code_snippet": f.code_snippet,
            "rule_id": f.rule_id,
            "tool_slug": f.result.tool_slug,
        }
        for f in page_qs
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }
