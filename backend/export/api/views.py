"""Django Ninja export API views.

Exports use the API's normal authentication (session or Bearer token), so
researchers' scripts can download them. Every response says how many rows
matched and whether the limit truncated it (``X-Total-Count`` /
``X-Export-Truncated``); findings are ordered by severity rank (critical
first), so a truncated export drops the least severe rows, never a random
severity bucket.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from django.http import HttpResponse
from django.http import StreamingHttpResponse
from ninja import Query
from ninja import Router
from ninja.errors import HttpError

from backend.analysis.engine import measurement as measurement_engine
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.export import services
from backend.generation.models import GenerationJob
from backend.reports.models import Report

if TYPE_CHECKING:
    from django.db.models import Model
    from django.db.models import QuerySet

router = Router(tags=["export"])

_HARD_CAP = 50_000
_DEFAULT_LIMIT = 10_000
_UUID_PARAMS = ("task_id", "job_id")


@dataclass(frozen=True)
class ExportSpec:
    """What one exportable dataset looks like: model, scoping, filters and order."""

    model: type[Model]
    owner_field: str
    filters: dict[str, str]  # query-param name -> ORM lookup
    select_related: tuple[str, ...] = field(default=())
    order_by: tuple[str, ...] = field(default=())


_FINDINGS = ExportSpec(
    model=Finding,
    owner_field="run__created_by",
    filters={
        "task_id": "run_id",
        "analyzer": "tool_slug",
        "severity": "severity",
        "job_id": "run__generation_job_id",
        "plan_hash": "run__plan_hash",
        "category": "category",
    },
    select_related=("result", "run", "run__generation_job", "run__generation_job__model"),
    # Severity rank (critical first) with a stable tiebreak: a truncated
    # export keeps the most severe rows.
    order_by=("severity_rank", "run__created_at", "file_path", "line_number", "id"),
)
_JOBS = ExportSpec(
    model=GenerationJob,
    owner_field="created_by",
    filters={"status": "status", "model_id": "model_id"},
    order_by=("-created_at", "id"),
)
_TASKS = ExportSpec(
    model=AnalysisRun,
    owner_field="created_by",
    filters={"status": "status", "purpose": "purpose", "plan_hash": "plan_hash", "job_id": "generation_job_id"},
    order_by=("-created_at", "id"),
)
_REPORTS = ExportSpec(
    model=Report,
    owner_field="created_by",
    filters={"status": "status", "report_type": "report_type"},
    order_by=("-created_at", "id"),
)


@dataclass
class _Export:
    qs: QuerySet
    total: int
    limit: int

    @property
    def truncated(self) -> bool:
        return self.total > self.limit


def _owned(spec: ExportSpec, user: Any) -> QuerySet:
    qs = spec.model.objects.all()
    if not getattr(user, "is_staff", False):
        qs = qs.filter(**{spec.owner_field: user})
    return qs


def _build(
    spec: ExportSpec,
    request: Any,
    params: dict[str, Any],
    since: datetime | None,
    limit: int,
    *,
    extra: dict[str, Any] | None = None,
) -> _Export:
    for name in _UUID_PARAMS:
        if params.get(name):
            try:
                uuid.UUID(str(params[name]))
            except ValueError as exc:
                raise HttpError(422, f"{name} must be a UUID") from exc
    qs = _owned(spec, request.auth)
    if spec.select_related:
        qs = qs.select_related(*spec.select_related)
    for name, lookup in spec.filters.items():
        if params.get(name):
            qs = qs.filter(**{lookup: params[name]})
    if extra:
        qs = qs.filter(**extra)
    if since:
        qs = qs.filter(created_at__gte=since)
    if spec.order_by:
        qs = qs.order_by(*spec.order_by)
    limit = max(1, min(limit, _HARD_CAP))
    return _Export(qs=qs[:limit], total=qs.count(), limit=limit)


def _annotate(resp: HttpResponse | StreamingHttpResponse, export: _Export) -> HttpResponse | StreamingHttpResponse:
    resp["X-Total-Count"] = str(export.total)
    resp["X-Export-Limit"] = str(export.limit)
    resp["X-Export-Truncated"] = "true" if export.truncated else "false"
    return resp


def _csv_response(content: str, filename: str, export: _Export) -> HttpResponse:
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return _annotate(resp, export)


def _json_response(data: object, export: _Export) -> HttpResponse:
    return _annotate(HttpResponse(json.dumps(data, default=str), content_type="application/json"), export)


# ── Findings ─────────────────────────────────────────────────────────────────


def _finding_filters(
    request: Any,
    *,
    canonical: bool,
    counted: bool | None,
    is_ai: bool | None,
    plan_hash: str = "",
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if counted is not None:
        extra["counted"] = counted
    if is_ai is not None:
        extra["is_ai"] = is_ai
    if canonical:
        # Only each job's canonical (latest complete) run: one measurement per
        # job, no double counting from re-analyses. With a plan_hash the
        # canonical run is chosen *under that plan* (as rankings and reports
        # choose it), so a newer run of the job under another plan never
        # drops the job from the export.
        jobs = GenerationJob.objects.all()
        if not getattr(request.auth, "is_staff", False):
            jobs = jobs.filter(created_by=request.auth)
        extra["run_id__in"] = measurement_engine.canonical_run_ids(
            list(jobs.values_list("id", flat=True)),
            plan_hash=plan_hash or None,
            complete_only=True,
        )
    return extra


def _annotate_canonical(resp: HttpResponse | StreamingHttpResponse, extra: dict[str, Any]):
    """Say which canonical runs a ``canonical=true`` export covers (runs, distinct plans)."""
    run_ids = extra.get("run_id__in")
    if run_ids is None:
        return resp
    resp["X-Canonical-Runs"] = str(len(run_ids))
    plans = AnalysisRun.objects.filter(id__in=run_ids).values_list("plan_hash", flat=True).distinct()
    # More than one plan means the rows are not one comparable dataset; pass
    # plan_hash to pin one.
    resp["X-Canonical-Plans"] = str(plans.count())
    return resp


def _stream_findings_csv(qs: Any):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(services.FINDING_HEADERS)
    yield buf.getvalue()
    for f in qs.iterator(chunk_size=2000):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(services.finding_row(f))
        yield buf.getvalue()


def _finding_params(task_id: str, analyzer: str, severity: str, job_id: str, plan_hash: str, category: str) -> dict:
    return {
        "task_id": task_id,
        "analyzer": analyzer,
        "severity": severity,
        "job_id": job_id,
        "plan_hash": plan_hash,
        "category": category,
    }


@router.get("/findings.csv", include_in_schema=True)
def findings_csv(
    request,
    task_id: str = Query(""),
    analyzer: str = Query(""),
    severity: str = Query(""),
    job_id: str = Query(""),
    plan_hash: str = Query(""),
    category: str = Query(""),
    canonical: bool = False,
    counted: bool | None = None,
    is_ai: bool | None = None,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    params = _finding_params(task_id, analyzer, severity, job_id, plan_hash, category)
    extra = _finding_filters(request, canonical=canonical, counted=counted, is_ai=is_ai, plan_hash=plan_hash)
    export = _build(_FINDINGS, request, params, since, limit, extra=extra)

    if export.limit > _DEFAULT_LIMIT:
        resp = StreamingHttpResponse(_stream_findings_csv(export.qs), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="findings.csv"'
        return _annotate_canonical(_annotate(resp, export), extra)

    return _annotate_canonical(_csv_response(services.findings_csv(export.qs), "findings.csv", export), extra)


@router.get("/findings.json", include_in_schema=True)
def findings_json(
    request,
    task_id: str = Query(""),
    analyzer: str = Query(""),
    severity: str = Query(""),
    job_id: str = Query(""),
    plan_hash: str = Query(""),
    category: str = Query(""),
    canonical: bool = False,
    counted: bool | None = None,
    is_ai: bool | None = None,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    params = _finding_params(task_id, analyzer, severity, job_id, plan_hash, category)
    extra = _finding_filters(request, canonical=canonical, counted=counted, is_ai=is_ai, plan_hash=plan_hash)
    export = _build(_FINDINGS, request, params, since, limit, extra=extra)
    return _annotate_canonical(_json_response(services.findings_json(export.qs), export), extra)


def _sarif_tool_results(request: Any, params: dict[str, Any], extra: dict[str, Any], since: datetime | None):
    """Every tool execution in the exported scope, with or without findings."""
    results = ToolResult.objects.all()
    if not getattr(request.auth, "is_staff", False):
        results = results.filter(run__created_by=request.auth)
    lookups = {
        "task_id": "run_id",
        "analyzer": "tool_slug",
        "job_id": "run__generation_job_id",
        "plan_hash": "run__plan_hash",
    }
    for name, lookup in lookups.items():
        if params.get(name):
            results = results.filter(**{lookup: params[name]})
    if "run_id__in" in extra:
        results = results.filter(run_id__in=extra["run_id__in"])
    if not params.get("task_id"):
        results = results.exclude(run__purpose=AnalysisRun.Purpose.CANARY)
    if since:
        results = results.filter(run__created_at__gte=since)
    return results.order_by("run__created_at", "position", "tool_slug")[:_HARD_CAP]


@router.get("/findings.sarif", include_in_schema=True)
def findings_sarif(
    request,
    task_id: str = Query(""),
    analyzer: str = Query(""),
    severity: str = Query(""),
    job_id: str = Query(""),
    plan_hash: str = Query(""),
    category: str = Query(""),
    canonical: bool = False,
    counted: bool | None = None,
    is_ai: bool | None = None,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    params = _finding_params(task_id, analyzer, severity, job_id, plan_hash, category)
    extra = _finding_filters(request, canonical=canonical, counted=counted, is_ai=is_ai, plan_hash=plan_hash)
    export = _build(_FINDINGS, request, params, since, limit, extra=extra)
    tool_results = _sarif_tool_results(request, params, extra, since)
    resp = _json_response(services.findings_sarif(export.qs, tool_results=tool_results), export)
    resp["Content-Disposition"] = 'attachment; filename="findings.sarif"'
    return _annotate_canonical(resp, extra)


# ── Generation jobs ───────────────────────────────────────────────────────────


@router.get("/generation-jobs.csv", include_in_schema=True)
def generation_jobs_csv(
    request,
    status: str = Query(""),
    model_id: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    export = _build(_JOBS, request, {"status": status, "model_id": model_id}, since, limit)
    return _csv_response(services.generation_jobs_csv(export.qs), "generation-jobs.csv", export)


@router.get("/generation-jobs.json", include_in_schema=True)
def generation_jobs_json(
    request,
    status: str = Query(""),
    model_id: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    export = _build(_JOBS, request, {"status": status, "model_id": model_id}, since, limit)
    return _json_response(services.generation_jobs_json(export.qs), export)


# ── Analysis tasks (runs) ─────────────────────────────────────────────────────


def _task_params(status: str, purpose: str, plan_hash: str, job_id: str) -> dict[str, str]:
    return {"status": status, "purpose": purpose, "plan_hash": plan_hash, "job_id": job_id}


@router.get("/analysis-tasks.csv", include_in_schema=True)
def analysis_tasks_csv(
    request,
    status: str = Query(""),
    purpose: str = Query(""),
    plan_hash: str = Query(""),
    job_id: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    export = _build(_TASKS, request, _task_params(status, purpose, plan_hash, job_id), since, limit)
    return _csv_response(services.analysis_tasks_csv(export.qs), "analysis-tasks.csv", export)


@router.get("/analysis-tasks.json", include_in_schema=True)
def analysis_tasks_json(
    request,
    status: str = Query(""),
    purpose: str = Query(""),
    plan_hash: str = Query(""),
    job_id: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    export = _build(_TASKS, request, _task_params(status, purpose, plan_hash, job_id), since, limit)
    return _json_response(services.analysis_tasks_json(export.qs), export)


# ── Reports ───────────────────────────────────────────────────────────────────


@router.get("/reports.csv", include_in_schema=True)
def reports_csv(
    request,
    status: str = Query(""),
    report_type: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    export = _build(_REPORTS, request, {"status": status, "report_type": report_type}, since, limit)
    return _csv_response(services.reports_csv(export.qs), "reports.csv", export)


@router.get("/reports.json", include_in_schema=True)
def reports_json(
    request,
    status: str = Query(""),
    report_type: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT),
):
    export = _build(_REPORTS, request, {"status": status, "report_type": report_type}, since, limit)
    return _json_response(services.reports_json(export.qs), export)
