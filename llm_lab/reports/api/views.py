"""Reports API — Django Ninja router."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import Router

from llm_lab.reports import services
from llm_lab.reports.models import Report

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
        user=request.user,
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
            user=request.user,
            expires_in_days=payload.expires_in_days,
        )
    except ValueError as e:
        return 400, {"success": False, "message": str(e)}
    return 201, report


@router.get("/{report_id}/", response=ReportDetailSchema)
def get_report(request, report_id: str):
    return get_object_or_404(Report, report_id=report_id)


@router.get(
    "/{report_id}/data/",
    response={200: ReportDataResponse, 400: GenericResponse},
)
def get_report_data(request, report_id: str):
    report = get_object_or_404(Report, report_id=report_id)
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
    report = get_object_or_404(Report, report_id=report_id)
    report.delete()
    return {"success": True, "message": "deleted"}
