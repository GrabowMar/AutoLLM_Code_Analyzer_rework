"""Django Ninja export API views."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.http import StreamingHttpResponse
from ninja import Query
from ninja import Router

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.export import services
from backend.generation.models import GenerationJob
from backend.reports.models import Report

if TYPE_CHECKING:
    from django.db.models import Model
    from django.db.models import QuerySet

router = Router(tags=["export"])

_HARD_CAP = 50_000
_DEFAULT_LIMIT = 10_000


def _auth_check(request: object) -> bool:
    return request.user.is_authenticated  # type: ignore[attr-defined]


@dataclass(frozen=True)
class ExportSpec:
    """What one exportable dataset looks like: model, scoping, and filters."""

    model: type[Model]
    owner_field: str
    filters: dict[str, str]  # query-param name -> ORM lookup
    select_related: tuple[str, ...] = field(default=())


_FINDINGS = ExportSpec(
    model=Finding,
    owner_field="result__run__created_by",
    filters={
        "task_id": "result__run_id",
        "analyzer": "result__tool_slug",
        "severity": "severity",
    },
    select_related=("result__run",),
)
_JOBS = ExportSpec(
    model=GenerationJob,
    owner_field="created_by",
    filters={"status": "status", "model_id": "model_id"},
)
_TASKS = ExportSpec(
    model=AnalysisRun,
    owner_field="created_by",
    filters={"status": "status"},
)
_REPORTS = ExportSpec(
    model=Report,
    owner_field="created_by",
    filters={"status": "status", "report_type": "report_type"},
)


def _build_qs(
    spec: ExportSpec,
    request: object,
    params: dict[str, str],
    since: datetime | None,
    limit: int,
) -> QuerySet:
    user = request.user  # type: ignore[attr-defined]
    qs = spec.model.objects.all()
    if spec.select_related:
        qs = qs.select_related(*spec.select_related)
    if not user.is_staff:
        qs = qs.filter(**{spec.owner_field: user})
    for name, lookup in spec.filters.items():
        if params.get(name):
            qs = qs.filter(**{lookup: params[name]})
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs[: min(limit, _HARD_CAP)]


def _csv_response(content: str, filename: str) -> HttpResponse:
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _json_response(data: object) -> HttpResponse:
    return HttpResponse(json.dumps(data, default=str), content_type="application/json")


# ── Findings ─────────────────────────────────────────────────────────────────


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
    params = {"task_id": task_id, "analyzer": analyzer, "severity": severity}
    qs = _build_qs(_FINDINGS, request, params, since, limit)

    if min(limit, _HARD_CAP) > _DEFAULT_LIMIT:
        resp = StreamingHttpResponse(_stream_findings_csv(qs), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="findings.csv"'
        return resp

    return _csv_response(services.findings_csv(qs), "findings.csv")


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
    params = {"task_id": task_id, "analyzer": analyzer, "severity": severity}
    qs = _build_qs(_FINDINGS, request, params, since, limit)
    return _json_response(services.findings_json(qs))


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
    params = {"task_id": task_id, "analyzer": analyzer, "severity": severity}
    qs = _build_qs(_FINDINGS, request, params, since, limit)
    resp = _json_response(services.findings_sarif(qs))
    resp["Content-Disposition"] = 'attachment; filename="findings.sarif"'
    return resp


# ── Generation jobs ───────────────────────────────────────────────────────────


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
    qs = _build_qs(_JOBS, request, {"status": status, "model_id": model_id}, since, limit)
    return _csv_response(services.generation_jobs_csv(qs), "generation-jobs.csv")


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
    qs = _build_qs(_JOBS, request, {"status": status, "model_id": model_id}, since, limit)
    return _json_response(services.generation_jobs_json(qs))


# ── Analysis tasks ────────────────────────────────────────────────────────────


@router.get("/analysis-tasks.csv", auth=None, include_in_schema=True)
def analysis_tasks_csv(
    request,
    status: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _build_qs(_TASKS, request, {"status": status}, since, limit)
    return _csv_response(services.analysis_tasks_csv(qs), "analysis-tasks.csv")


@router.get("/analysis-tasks.json", auth=None, include_in_schema=True)
def analysis_tasks_json(
    request,
    status: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = _build_qs(_TASKS, request, {"status": status}, since, limit)
    return _json_response(services.analysis_tasks_json(qs))


# ── Reports ───────────────────────────────────────────────────────────────────


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
    qs = _build_qs(_REPORTS, request, {"status": status, "report_type": report_type}, since, limit)
    return _csv_response(services.reports_csv(qs), "reports.csv")


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
    qs = _build_qs(_REPORTS, request, {"status": status, "report_type": report_type}, since, limit)
    return _json_response(services.reports_json(qs))
