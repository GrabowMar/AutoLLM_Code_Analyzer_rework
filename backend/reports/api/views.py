"""Reports API — Django Ninja router."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import Router

from backend.reports import services
from backend.reports.models import Report

from .schema import GenerateReportIn
from .schema import GenericResponse
from .schema import ReportDataResponse
from .schema import ReportDetailSchema
from .schema import ReportListResponse
from .schema import ReportSummarySchema

router = Router(tags=["reports"])


@router.get("/", response=ReportListResponse)
def list_reports(
    request,
    report_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    qs, total = services.list_reports(
        user=request.auth,
        report_type=report_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {
        "reports": list(qs),
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    }


@router.post("/generate/", response={201: ReportSummarySchema, 400: GenericResponse})
def generate_report(request, payload: GenerateReportIn):
    if payload.report_type not in services.GENERATORS:
        return 400, {
            "success": False,
            "message": f"Unknown report_type: {payload.report_type}",
        }
    try:
        report = services.create_and_dispatch(
            report_type=payload.report_type,
            config=payload.config,
            title=payload.title,
            description=payload.description,
            user=request.auth,
            expires_in_days=payload.expires_in_days,
        )
    except ValueError as e:
        return 400, {"success": False, "message": str(e)}
    return 201, report


@router.get("/{report_id}/", response=ReportDetailSchema)
def get_report(request, report_id: str):
    return get_object_or_404(Report, report_id=report_id, created_by=request.auth)


@router.get(
    "/{report_id}/data/",
    response={200: ReportDataResponse, 400: GenericResponse},
)
def get_report_data(request, report_id: str):
    report = get_object_or_404(Report, report_id=report_id, created_by=request.auth)
    if report.status != Report.Status.COMPLETED:
        return 400, {
            "success": False,
            "message": f"Report not ready (status: {report.status})",
        }
    return 200, {
        "report_id": report.report_id,
        "report_type": report.report_type,
        "title": report.title,
        "status": report.status,
        "progress": report.progress_percent,
        "data": report.report_data,
    }


@router.delete("/{report_id}/", response=GenericResponse)
def delete_report(request, report_id: str):
    report = get_object_or_404(Report, report_id=report_id, created_by=request.auth)
    report.delete()
    return {"success": True, "message": "deleted"}


@router.get("/{report_id}/export/")
def export_report(request, report_id: str, format: str = "md"):
    """The report as Markdown (``md``), LaTeX tables (``tex``) or raw JSON (``json``)."""
    import json

    from django.http import HttpResponse

    from backend.reports.services.renderers import render_latex
    from backend.reports.services.renderers import render_markdown

    report = get_object_or_404(Report, report_id=report_id, created_by=request.auth)
    if report.status != Report.Status.COMPLETED:
        return HttpResponse(f"Report not ready (status: {report.status})", status=400)
    if format == "md":
        body, content_type, ext = render_markdown(report), "text/markdown; charset=utf-8", "md"
    elif format == "tex":
        body, content_type, ext = render_latex(report), "application/x-tex; charset=utf-8", "tex"
    elif format == "json":
        body, content_type, ext = json.dumps(report.report_data, indent=2, default=str), "application/json", "json"
    else:
        return HttpResponse("format must be md, tex or json", status=400)
    resp = HttpResponse(body, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{report.report_id}.{ext}"'
    return resp
