"""Django Ninja export API views."""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.http import StreamingHttpResponse
from ninja import File
from ninja import Query
from ninja import Router
from ninja.files import UploadedFile  # noqa: TC002 - ninja resolves the annotation at runtime

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.export import services
from backend.export.services.findings import UNRATED
from backend.export.services.findings import ExportContext
from backend.export.services.findings import export_context
from backend.export.services.findings import finding_csv_row
from backend.export.services.findings import finding_rows_queryset
from backend.generation.models import GenerationRun
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
        "generation_run_id": "result__run__generation_job_id",
        "study_id": "result__run__generation_job__study_id",
        "analyzer": "result__tool_slug",
        "normalized_severity": "severity",
    },
)
_RUNS = ExportSpec(
    model=GenerationRun,
    owner_field="created_by",
    # The model slug (e.g. "deepseek/deepseek-v4.1-flash"), as everywhere else in the API.
    filters={
        "status": "status",
        "model_id": "model__model_id",
        "study_id": "study_id",
        "kind": "kind",
        "outcome": "outcome",
    },
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
    params: dict[str, object],
    since: datetime | None,
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
    # Newest first with a unique tiebreak: a cut by ``limit`` drops the oldest
    # rows, never one severity bucket (the model's default ordering is alphabetical).
    return qs.order_by("-created_at", "-pk")


@dataclass(frozen=True)
class Window:
    """The rows one export returns, and how many matched before the limit."""

    qs: QuerySet
    total: int
    cap: int

    @property
    def truncated(self) -> bool:
        return self.total > self.cap


def _window(qs: QuerySet, limit: int) -> Window:
    cap = min(limit, _HARD_CAP)
    return Window(qs=qs[:cap], total=qs.count(), cap=cap)


def _mark(resp: HttpResponse | StreamingHttpResponse, window: Window):
    """Say whether ``limit`` cut the export: rows are never dropped silently."""
    resp["X-Total-Count"] = str(window.total)
    resp["X-Truncated"] = "true" if window.truncated else "false"
    return resp


def _csv_response(content: str, filename: str, window: Window) -> HttpResponse:
    resp = HttpResponse(content, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return _mark(resp, window)


def _json_response(data: object, window: Window) -> HttpResponse:
    return _mark(HttpResponse(json.dumps(data, default=str), content_type="application/json"), window)


# ── Findings ─────────────────────────────────────────────────────────────────


def _findings_window(
    request: object,
    *,
    task_id: uuid.UUID | None,
    generation_run_id: uuid.UUID | None,
    study_id: uuid.UUID | None,
    analyzer: str,
    tool_severity: str,
    normalized_severity: str,
    latest_only: bool,
    since: datetime | None,
    limit: int,
) -> tuple[Window, ExportContext]:
    """The findings one export returns, and what its rows share (counted results, the apps they describe)."""
    params = {
        "task_id": task_id,
        "generation_run_id": generation_run_id,
        "study_id": study_id,
        "analyzer": analyzer,
        "normalized_severity": normalized_severity,
    }
    qs = _build_qs(_FINDINGS, request, params, since)
    label = tool_severity.strip()
    if label.lower() == UNRATED:
        qs = qs.filter(tool_severity="")
    elif label:
        qs = qs.filter(tool_severity__iexact=label)
    run_ids = qs.order_by().values_list("result__run__generation_job_id", flat=True).distinct()
    ctx = export_context(run_ids)
    if latest_only:
        qs = qs.filter(result_id__in=ctx.latest_ids)
    return _window(qs, limit), ctx


def _stream_findings_csv(qs: QuerySet, ctx: ExportContext, *, spreadsheet_safe: bool):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(services.FINDING_HEADERS)
    yield buf.getvalue()
    for f in finding_rows_queryset(qs).iterator(chunk_size=2000):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(finding_csv_row(f, ctx, spreadsheet_safe=spreadsheet_safe))
        yield buf.getvalue()


@router.get("/findings.csv", auth=None, include_in_schema=True)
def findings_csv(
    request,
    task_id: uuid.UUID | None = None,
    generation_run_id: uuid.UUID | None = None,
    study_id: uuid.UUID | None = None,
    analyzer: str = Query(""),
    tool_severity: str = Query(""),
    normalized_severity: str = Query(""),
    severity: str = Query("", deprecated=True, description="Alias of normalized_severity."),
    latest_only: bool = False,
    spreadsheet_safe: bool = True,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window, ctx = _findings_window(
        request,
        task_id=task_id,
        generation_run_id=generation_run_id,
        study_id=study_id,
        analyzer=analyzer,
        tool_severity=tool_severity,
        normalized_severity=normalized_severity or severity,
        latest_only=latest_only,
        since=since,
        limit=limit,
    )

    if window.cap > _DEFAULT_LIMIT:
        resp = StreamingHttpResponse(
            _stream_findings_csv(window.qs, ctx, spreadsheet_safe=spreadsheet_safe),
            content_type="text/csv",
        )
        resp["Content-Disposition"] = 'attachment; filename="findings.csv"'
        return _mark(resp, window)

    content = services.findings_csv(window.qs, ctx, spreadsheet_safe=spreadsheet_safe)
    return _csv_response(content, "findings.csv", window)


@router.get("/findings.json", auth=None, include_in_schema=True)
def findings_json(
    request,
    task_id: uuid.UUID | None = None,
    generation_run_id: uuid.UUID | None = None,
    study_id: uuid.UUID | None = None,
    analyzer: str = Query(""),
    tool_severity: str = Query(""),
    normalized_severity: str = Query(""),
    severity: str = Query("", deprecated=True, description="Alias of normalized_severity."),
    latest_only: bool = False,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window, ctx = _findings_window(
        request,
        task_id=task_id,
        generation_run_id=generation_run_id,
        study_id=study_id,
        analyzer=analyzer,
        tool_severity=tool_severity,
        normalized_severity=normalized_severity or severity,
        latest_only=latest_only,
        since=since,
        limit=limit,
    )
    return _json_response(services.findings_json(window.qs, ctx), window)


@router.get("/findings.sarif", auth=None, include_in_schema=True)
def findings_sarif(
    request,
    task_id: uuid.UUID | None = None,
    generation_run_id: uuid.UUID | None = None,
    study_id: uuid.UUID | None = None,
    analyzer: str = Query(""),
    tool_severity: str = Query(""),
    normalized_severity: str = Query(""),
    severity: str = Query("", deprecated=True, description="Alias of normalized_severity."),
    latest_only: bool = False,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window, ctx = _findings_window(
        request,
        task_id=task_id,
        generation_run_id=generation_run_id,
        study_id=study_id,
        analyzer=analyzer,
        tool_severity=tool_severity,
        normalized_severity=normalized_severity or severity,
        latest_only=latest_only,
        since=since,
        limit=limit,
    )
    resp = _json_response(services.findings_sarif(window.qs, ctx, total=window.total), window)
    resp["Content-Disposition"] = 'attachment; filename="findings.sarif"'
    return resp


# ── Generation runs ───────────────────────────────────────────────────────────


def _runs_params(status: str, model_id: str, study_id: uuid.UUID | None, kind: str, outcome: str) -> dict[str, object]:
    return {"status": status, "model_id": model_id, "study_id": study_id, "kind": kind, "outcome": outcome}


@router.get("/generation-runs.csv", auth=None, include_in_schema=True)
def generation_runs_csv(
    request,
    status: str = Query(""),
    model_id: str = Query("", description="Model slug, e.g. deepseek/deepseek-v4.1-flash."),
    study_id: uuid.UUID | None = None,
    kind: str = Query(""),
    outcome: str = Query(""),
    spreadsheet_safe: bool = True,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    params = _runs_params(status, model_id, study_id, kind, outcome)
    window = _window(_build_qs(_RUNS, request, params, since), limit)
    content = services.generation_runs_csv(window.qs, spreadsheet_safe=spreadsheet_safe)
    return _csv_response(content, "generation-runs.csv", window)


@router.get("/generation-runs.json", auth=None, include_in_schema=True)
def generation_runs_json(
    request,
    status: str = Query(""),
    model_id: str = Query("", description="Model slug, e.g. deepseek/deepseek-v4.1-flash."),
    study_id: uuid.UUID | None = None,
    kind: str = Query(""),
    outcome: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    params = _runs_params(status, model_id, study_id, kind, outcome)
    window = _window(_build_qs(_RUNS, request, params, since), limit)
    return _json_response(services.generation_runs_json(window.qs), window)


@router.get("/runs/{run_id}/bundle.zip", auth=None, include_in_schema=True)
def run_bundle_zip(request, run_id: uuid.UUID):
    """One run's evidence: manifest.json, files.csv (path, origin, sha, loc), resolved_lock.json, task and code."""
    if not _auth_check(request):
        return HttpResponse(status=401)
    qs = GenerationRun.objects.all()
    if not request.user.is_staff:
        qs = qs.filter(created_by=request.user)
    run = qs.filter(id=run_id).first()
    if run is None:
        return HttpResponse(status=404)
    resp = HttpResponse(services.run_bundle(run), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="run-{run_id}.zip"'
    return resp


# ── Analysis tasks ────────────────────────────────────────────────────────────


@router.get("/analysis-tasks.csv", auth=None, include_in_schema=True)
def analysis_tasks_csv(
    request,
    status: str = Query(""),
    spreadsheet_safe: bool = True,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window = _window(_build_qs(_TASKS, request, {"status": status}, since), limit)
    content = services.analysis_tasks_csv(window.qs, spreadsheet_safe=spreadsheet_safe)
    return _csv_response(content, "analysis-tasks.csv", window)


@router.get("/analysis-tasks.json", auth=None, include_in_schema=True)
def analysis_tasks_json(
    request,
    status: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window = _window(_build_qs(_TASKS, request, {"status": status}, since), limit)
    return _json_response(services.analysis_tasks_json(window.qs), window)


# ── Reports ───────────────────────────────────────────────────────────────────


@router.get("/reports.csv", auth=None, include_in_schema=True)
def reports_csv(
    request,
    status: str = Query(""),
    report_type: str = Query(""),
    spreadsheet_safe: bool = True,
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window = _window(_build_qs(_REPORTS, request, {"status": status, "report_type": report_type}, since), limit)
    content = services.reports_csv(window.qs, spreadsheet_safe=spreadsheet_safe)
    return _csv_response(content, "reports.csv", window)


@router.get("/reports.json", auth=None, include_in_schema=True)
def reports_json(
    request,
    status: str = Query(""),
    report_type: str = Query(""),
    since: datetime | None = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=0),
):
    if not _auth_check(request):
        return HttpResponse(status=401)
    window = _window(_build_qs(_REPORTS, request, {"status": status, "report_type": report_type}, since), limit)
    return _json_response(services.reports_json(window.qs), window)


# ── Manual review sample ─────────────────────────────────────────────────────


@router.get("/review-sample.zip")
def review_sample_zip(
    request,
    study_id: uuid.UUID | None = None,
    size: int = Query(30, ge=1, le=500),
    per_stratum: int | None = Query(None, ge=1, le=100),
    stratify: str = Query("model", pattern="^(|model|task|model,task)$"),
    seed: int = Query(0, ge=0),
    blind: bool = True,
):
    """A seeded, stratified sample of apps with blank label sheets for manual review.

    With ``study_id``: the study's current runs (404 for another user's study); otherwise every sample run
    the caller owns (staff: everyone's).
    """
    from backend.export.services.review_sample import build_review_sample
    from backend.export.services.review_sample import review_population

    try:
        runs = review_population(request.auth, study_id=str(study_id) if study_id else None)
    except ValueError:
        return HttpResponse("Study not found", status=404)
    data, _manifest = build_review_sample(
        runs,
        size=size,
        per_stratum=per_stratum,
        stratify=tuple(k for k in stratify.split(",") if k),
        seed=seed,
        blind=blind,
    )
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="review-sample-seed{seed}.zip"'
    return resp


@router.post("/review-sample/score/")
def review_sample_score(request, file: UploadedFile = File(...)):  # noqa: B008
    """Score a filled-in review sample ZIP: precision, Cohen's kappa, Spearman rho."""
    import zipfile

    from backend.export.services.review_sample import score_review_labels

    try:
        return score_review_labels(file.read())
    except zipfile.BadZipFile:
        return HttpResponse("Upload the review sample as a .zip", status=400)
