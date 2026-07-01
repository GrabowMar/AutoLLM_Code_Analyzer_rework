"""Django Ninja export API views."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from django.http import HttpResponse
from django.http import StreamingHttpResponse
from ninja import Query
from ninja import Router

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.export import services
from backend.generation.models import GenerationJob
from backend.reports.models import Report

router = Router(tags=["export"])

_HARD_CAP = 50_000
_DEFAULT_LIMIT = 10_000


def _auth_check(request: object) -> bool:
    return request.user.is_authenticated  # type: ignore[attr-defined]


# ── Findings ─────────────────────────────────────────────────────────────────


def _findings_qs(
    request: object,
    task_id: str,
    analyzer: str,
    severity: str,
    since: datetime | None,
    limit: int,
) -> object:
    user = request.user  # type: ignore[attr-defined]
    qs = Finding.objects.select_related("result__run")
    if not user.is_staff:
        qs = qs.filter(result__run__created_by=user)
    if task_id:
        qs = qs.filter(result__run_id=task_id)
    if analyzer:
        qs = qs.filter(result__tool_slug=analyzer)
    if severity:
        qs = qs.filter(severity=severity)
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs[: min(limit, _HARD_CAP)]


def _finding_row(f: object) -> list[object]:
    return [
        str(f.id),  # type: ignore[attr-defined]
        str(f.result.run_id),  # type: ignore[attr-defined]
        f.result.tool_slug,  # type: ignore[attr-defined]
        f.severity,  # type: ignore[attr-defined]
        f.file_path,  # type: ignore[attr-defined]
        f.line_number or "",  # type: ignore[attr-defined]
        f.rule_id,  # type: ignore[attr-defined]
        f.title,  # type: ignore[attr-defined]
        f.tool_specific_data.get("cwe", "") if f.tool_specific_data else "",  # type: ignore[attr-defined]
        f.created_at.isoformat(),  # type: ignore[attr-defined]
    ]


def _stream_findings_csv(qs: object):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(services.FINDING_HEADERS)
    yield buf.getvalue()
    for f in qs:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_finding_row(f))
        yield buf.getvalue()


@router.get("/findings.csv", auth=None, include_in_schema=True)
def findings_csv(
    request,
    task_id: str = Query(""),
    analyzer: str = Query(""),
    severity: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _findings_qs(request, task_id, analyzer, severity, since, limit)

    if min(limit, _HARD_CAP) > _DEFAULT_LIMIT:
        resp = StreamingHttpResponse(_stream_findings_csv(qs), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="findings.csv"'
        return resp

    content = services.findings_csv(qs)
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="findings.csv"'
    return resp


@router.get("/findings.json", auth=None, include_in_schema=True)
def findings_json(
    request,
    task_id: str = Query(""),
    analyzer: str = Query(""),
    severity: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _findings_qs(request, task_id, analyzer, severity, since, limit)
    data = services.findings_json(qs)
    return HttpResponse(json.dumps(data, default=str), content_type="application/json")


@router.get("/findings.sarif", auth=None, include_in_schema=True)
def findings_sarif(
    request,
    task_id: str = Query(""),
    analyzer: str = Query(""),
    severity: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _findings_qs(request, task_id, analyzer, severity, since, limit)
    data = services.findings_sarif(qs)
    resp = HttpResponse(json.dumps(data, default=str), content_type="application/json")
    resp["Content-Disposition"] = 'attachment; filename="findings.sarif"'
    return resp


# ── Generation jobs ───────────────────────────────────────────────────────────


def _jobs_qs(
    request: object,
    status: str,
    model_id: str,
    since: datetime | None,
    limit: int,
) -> object:
    user = request.user  # type: ignore[attr-defined]
    qs = GenerationJob.objects.all()
    if not user.is_staff:
        qs = qs.filter(created_by=user)
    if status:
        qs = qs.filter(status=status)
    if model_id:
        qs = qs.filter(model_id=model_id)
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs[: min(limit, _HARD_CAP)]


@router.get("/generation-jobs.csv", auth=None, include_in_schema=True)
def generation_jobs_csv(
    request,
    status: str = Query(""),
    model_id: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _jobs_qs(request, status, model_id, since, limit)
    content = services.generation_jobs_csv(qs)
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="generation-jobs.csv"'
    return resp


@router.get("/generation-jobs.json", auth=None, include_in_schema=True)
def generation_jobs_json(
    request,
    status: str = Query(""),
    model_id: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _jobs_qs(request, status, model_id, since, limit)
    data = services.generation_jobs_json(qs)
    return HttpResponse(json.dumps(data, default=str), content_type="application/json")


# ── Analysis tasks ────────────────────────────────────────────────────────────


def _tasks_qs(
    request: object,
    status: str,
    since: datetime | None,
    limit: int,
) -> object:
    user = request.user  # type: ignore[attr-defined]
    qs = AnalysisRun.objects.all()
    if not user.is_staff:
        qs = qs.filter(created_by=user)
    if status:
        qs = qs.filter(status=status)
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs[: min(limit, _HARD_CAP)]


@router.get("/analysis-tasks.csv", auth=None, include_in_schema=True)
def analysis_tasks_csv(
    request,
    status: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _tasks_qs(request, status, since, limit)
    content = services.analysis_tasks_csv(qs)
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="analysis-tasks.csv"'
    return resp


@router.get("/analysis-tasks.json", auth=None, include_in_schema=True)
def analysis_tasks_json(
    request,
    status: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _tasks_qs(request, status, since, limit)
    data = services.analysis_tasks_json(qs)
    return HttpResponse(json.dumps(data, default=str), content_type="application/json")


# ── Reports ───────────────────────────────────────────────────────────────────


def _reports_qs(
    request: object,
    status: str,
    report_type: str,
    since: datetime | None,
    limit: int,
) -> object:
    user = request.user  # type: ignore[attr-defined]
    qs = Report.objects.all()
    if not user.is_staff:
        qs = qs.filter(created_by=user)
    if status:
        qs = qs.filter(status=status)
    if report_type:
        qs = qs.filter(report_type=report_type)
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs[: min(limit, _HARD_CAP)]


@router.get("/reports.csv", auth=None, include_in_schema=True)
def reports_csv(
    request,
    status: str = Query(""),
    report_type: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _reports_qs(request, status, report_type, since, limit)
    content = services.reports_csv(qs)
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="reports.csv"'
    return resp


@router.get("/reports.json", auth=None, include_in_schema=True)
def reports_json(
    request,
    status: str = Query(""),
    report_type: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _reports_qs(request, status, report_type, since, limit)
    data = services.reports_json(qs)
    return HttpResponse(json.dumps(data, default=str), content_type="application/json")
