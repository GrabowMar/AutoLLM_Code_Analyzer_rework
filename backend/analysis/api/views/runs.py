"""Analysis run endpoints (mounted under ``/analysis/``).

Static ``runs/<word>/`` routes are registered before ``runs/{run_id}/``:
Django resolves the first matching path, so ``runs/{run_id}/`` declared first
would swallow ``runs/stats/`` and friends.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Case
from django.db.models import CharField
from django.db.models import Count
from django.db.models import F
from django.db.models import FloatField
from django.db.models import IntegerField
from django.db.models import Min
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError

from backend.analysis.api.schema import AnalysisRunSchema
from backend.analysis.api.schema import BatchCreatedSchema
from backend.analysis.api.schema import BatchCreateSchema
from backend.analysis.api.schema import BulkActionSchema
from backend.analysis.api.schema import BulkResultSchema
from backend.analysis.api.schema import FindingFacetsSchema
from backend.analysis.api.schema import PaginatedFindingsSchema
from backend.analysis.api.schema import PaginatedRunsSchema
from backend.analysis.api.schema import RerunSchema
from backend.analysis.api.schema import RunCreateSchema
from backend.analysis.api.schema import RunPreviewRequestSchema
from backend.analysis.api.schema import RunPreviewSchema
from backend.analysis.api.schema import RunStatsSchema
from backend.analysis.api.schema import ToolResultDetailSchema
from backend.analysis.api.views._router import router
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services import runner
from backend.analysis.services import workspace_service
from backend.analysis.services.serialize import iso
from backend.analysis.services.serialize import serialize_result
from backend.common.pagination import paginate_queryset
from backend.generation.models import GenerationJob

# A batch (and its preview) analyzes at most this many apps.
MAX_BATCH_APPS = 100

ACTIVE_STATUSES = (AnalysisRun.Status.PENDING, AnalysisRun.Status.RUNNING)

_RUN_SORTS = {
    "created_desc": ("-created_at",),
    "created_asc": ("created_at",),
    "name_asc": ("name", "-created_at"),
    "duration_desc": (F("duration_seconds").desc(nulls_last=True), "-created_at"),
    "findings_desc": (F("findings_total").desc(nulls_last=True), "-created_at"),
}

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
# Rated findings ordered by their (mapped) level, then those the tool did not rate.
_LEVEL_ORDER = Case(
    When(tool_severity="", then=Value(9)),
    *[When(severity=sev, then=Value(rank)) for sev, rank in _SEVERITY_RANK.items()],
    default=Value(8),
    output_field=IntegerField(),
)
_FINDING_SORTS = {
    "severity": (_LEVEL_ORDER, "file_path", "line_number"),
    "file": ("file_path", "line_number", _LEVEL_ORDER),
    "tool": ("result__tool_slug", _LEVEL_ORDER, "file_path", "line_number"),
    "rule": ("rule_id", "file_path", "line_number"),
}
# Facet lists longer than this are cut (the response says how many there were).
_FACET_LIMIT = 50


# --- Serialization -------------------------------------------------------------


def _target(run: AnalysisRun) -> dict[str, Any]:
    """What the run analyzed, for display: a generated app or pasted code."""
    job = run.generation_job if run.generation_job_id else None
    if job is None:
        files = run.source_code if isinstance(run.source_code, dict) else {}
        if not files:  # the analyzed app was deleted since (generation_job is SET_NULL)
            return {"kind": "deleted", "label": "Deleted app"}
        count = sum(1 for v in files.values() if isinstance(v, str) and v.strip())
        return {
            "kind": "code",
            "label": f"Pasted code ({count} file{'' if count == 1 else 's'})",
            "file_count": count,
        }
    model = job.model if job.model_id else None
    app = job.app_requirement if job.app_requirement_id else None
    model_name = model.model_name if model else ""
    app_name = app.name if app else ""
    parts = [app_name or job.stack_slug or "App", model_name]
    return {
        "kind": "job",
        "label": " · ".join(p for p in parts if p),
        "job_id": str(job.id),
        "job_status": job.status,
        "mode": job.mode,
        "model_name": model_name,
        "model_id": model.model_id if model else "",
        "app_name": app_name,
        "app_slug": app.slug if app else "",
        "stack_slug": job.stack_slug or "",
        "experiment_id": str(job.experiment_id) if job.experiment_id else None,
    }


def _base(run: AnalysisRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "name": run.name,
        "status": run.status,
        "tool_slugs": run.tool_slugs or [],
        "summary": run.summary or {},
        "error_message": run.error_message,
        "generation_job_id": str(run.generation_job_id) if run.generation_job_id else None,
        "batch_id": str(run.batch_id) if run.batch_id else None,
        "target": _target(run),
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "duration_seconds": run.duration_seconds,
        "phase": (run.summary or {}).get("phase", "") if run.status == AnalysisRun.Status.RUNNING else "",
    }


def _progress(runs: list[AnalysisRun]) -> dict[str, dict[str, Any]]:
    """Tools done / running and findings so far for the active runs among *runs* (two queries)."""
    active = [r for r in runs if r.status in ACTIVE_STATUSES]
    if not active:
        return {}
    ids = [r.id for r in active]
    progress: dict[str, dict[str, Any]] = {
        str(r.id): {"total": len(r.tool_slugs or []), "done": 0, "running": [], "findings": 0} for r in active
    }
    for run_id, slug, status in ToolResult.objects.filter(run_id__in=ids).values_list("run_id", "tool_slug", "status"):
        entry = progress[str(run_id)]
        if status == ToolResult.Status.RUNNING:
            entry["running"].append(slug)
        elif status != ToolResult.Status.PENDING:
            entry["done"] += 1
    for row in Finding.objects.filter(result__run_id__in=ids).values("result__run_id").annotate(n=Count("id")):
        progress[str(row["result__run_id"])]["findings"] = row["n"]
    return progress


def serialize_run_list(run: AnalysisRun, progress: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _base(run)
    # Per-tool metrics (function lists, duplication blocks) belong to the detail view.
    data["summary"] = {k: v for k, v in data["summary"].items() if k != "metrics_by_tool"}
    return {**data, "progress": progress}


def serialize_run(run: AnalysisRun) -> dict[str, Any]:
    data = _base(run)
    data["progress"] = _progress([run]).get(str(run.id))
    data["results"] = [serialize_result(r) for r in run.results.all()]
    # The files the tools see (pasted code, or the generated app's code), without contents.
    files = runner._materialize(run.get_code_for_analysis())  # noqa: SLF001
    data["source_files"] = [{"path": path, "lines": content.count("\n") + 1} for path, content in files.items()]
    return data


def _serialize_finding(f: Finding) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "severity": f.severity,
        "tool_severity": f.tool_severity,
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
        "tool_specific_data": f.tool_specific_data or {},
    }


def _runs_qs(user):
    return AnalysisRun.objects.filter(created_by=user).select_related(
        "generation_job__model",
        "generation_job__app_requirement",
    )


def _uuid(value: str, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HttpError(400, f"Invalid {what}.") from exc


def _get_run(request, run_id: str, *, related: bool = True) -> AnalysisRun:
    """The caller's run *run_id*; a malformed id is a 404 like an unknown one."""
    with contextlib.suppress(ValueError):
        qs = _runs_qs(request.auth) if related else AnalysisRun.objects.filter(created_by=request.auth)
        return get_object_or_404(qs, id=uuid.UUID(run_id))
    raise HttpError(404, "Not Found")


# --- Validation helpers --------------------------------------------------------


def _validated_tools(slugs: list[str]) -> list[str]:
    """*slugs* deduplicated in order; 400 when empty or naming unknown tools."""
    ordered = list(dict.fromkeys(s for s in slugs if s))
    if not ordered:
        raise HttpError(400, "At least one tool must be selected.")
    known = set(AnalyzerTool.objects.filter(slug__in=ordered).values_list("slug", flat=True))
    unknown = [s for s in ordered if s not in known]
    if unknown:
        raise HttpError(400, f"Unknown tool(s): {', '.join(unknown)}")
    return ordered


def _accessible_jobs(user, job_ids: list[str]) -> list[GenerationJob]:
    """The jobs *user* may analyze (their own; staff: any), in the given order; 404 if any is not."""
    ids = []
    for job_id in dict.fromkeys(job_ids):
        try:
            ids.append(uuid.UUID(str(job_id)))
        except ValueError as exc:
            raise HttpError(404, "Generation job not found.") from exc
    jobs = GenerationJob.objects.all() if user.is_staff else GenerationJob.objects.filter(created_by=user)
    found = {j.id: j for j in jobs.filter(id__in=ids).select_related("model", "app_requirement")}
    if len(found) != len(ids):
        raise HttpError(404, "Generation job not found.")
    return [found[i] for i in ids]


def _require_job_access(user, job_id: str) -> None:
    """404 unless *user* may analyze generation job *job_id* (their own; staff: any).

    Analyzing a job copies its code into the caller's workspace and stores the
    findings under the caller, so another user's job must look nonexistent.
    """
    jobs = GenerationJob.objects.all() if user.is_staff else GenerationJob.objects.filter(created_by=user)
    try:
        found = jobs.filter(id=job_id).exists()
    except (ValidationError, ValueError):
        found = False
    if not found:
        raise HttpError(404, "Generation job not found.")


def _default_name(job: GenerationJob | None) -> str:
    if job is None:
        return "Pasted code"
    run = AnalysisRun(generation_job=job)
    return _target(run)["label"] or "Analysis run"


# --- List, stats ---------------------------------------------------------------


@router.get("/runs/", response=PaginatedRunsSchema)
def list_runs(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: str = Query("", description="Comma-separated statuses; 'active' = pending + running."),
    generation_job_id: str = Query(""),
    batch_id: str = Query(""),
    tool_slug: str = Query(""),
    search: str = Query(""),
    sort: str = Query("created_desc"),
):
    """List the current user's analysis runs."""
    runner.reap_stale_runs()  # runs whose executing thread died with a reload
    qs = _runs_qs(request.auth)
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        if "active" in wanted:
            wanted = (wanted - {"active"}) | set(ACTIVE_STATUSES)
        qs = qs.filter(status__in=wanted)
    if generation_job_id:
        qs = qs.filter(generation_job_id=_uuid(generation_job_id, "generation_job_id"))
    if batch_id:
        qs = qs.filter(batch_id=_uuid(batch_id, "batch_id"))
    if tool_slug:
        qs = qs.filter(tool_slugs__contains=[tool_slug])
    if search:
        term = search.strip()
        q = (
            Q(name__icontains=term)
            | Q(generation_job__model__model_name__icontains=term)
            | Q(generation_job__model__model_id__icontains=term)
            | Q(generation_job__app_requirement__name__icontains=term)
            | Q(generation_job__stack_slug__icontains=term)
        )
        if len(term) >= 4 and all(c in "0123456789abcdef-" for c in term.lower()):
            qs = qs.annotate(id_text=Cast("id", CharField()), job_text=Cast("generation_job_id", CharField()))
            q |= Q(id_text__startswith=term.lower()) | Q(job_text__startswith=term.lower())
        qs = qs.filter(q)
    if sort == "findings_desc":
        qs = qs.annotate(findings_total=Cast(KeyTextTransform("total_findings", "summary"), FloatField()))
    qs = qs.order_by(*_RUN_SORTS.get(sort, _RUN_SORTS["created_desc"]))
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    runs = list(page_qs)
    progress = _progress(runs)
    return {
        "items": [serialize_run_list(r, progress.get(str(r.id))) for r in runs],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.get("/runs/stats/", response=RunStatsSchema)
def run_stats(request):
    """Run counts per status for the current user."""
    runner.reap_stale_runs()
    by_status = dict(
        AnalysisRun.objects.filter(created_by=request.auth)
        .values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n"),
    )
    return {
        "total": sum(by_status.values()),
        "active": sum(by_status.get(s, 0) for s in ACTIVE_STATUSES),
        "by_status": by_status,
    }


# --- Create ----------------------------------------------------------------------


@router.post("/runs/", response=AnalysisRunSchema)
def create_run(request, payload: RunCreateSchema):
    """Create an analysis run and (optionally) start it."""
    if not payload.tool_slugs:
        raise HttpError(400, "At least one tool must be selected.")
    if not payload.source_code and not payload.generation_job_id:
        raise HttpError(400, "Provide source_code or a generation_job_id.")
    tool_slugs = _validated_tools(payload.tool_slugs)

    job = None
    if payload.generation_job_id:
        _require_job_access(request.auth, payload.generation_job_id)
        job = GenerationJob.objects.select_related("model", "app_requirement").get(id=payload.generation_job_id)

    workspace = workspace_service.get_workspace(request.auth)
    run = AnalysisRun.objects.create(
        name=payload.name or _default_name(job),
        created_by=request.auth,
        workspace=workspace,
        generation_job=job,
        source_code=None if job else payload.source_code,
        tool_slugs=tool_slugs,
    )
    if payload.auto_start:
        runner.dispatch(str(run.id))
    return serialize_run(_get_run(request, str(run.id)))


@router.post("/runs/batch/", response=BatchCreatedSchema)
def create_batch(request, payload: BatchCreateSchema):
    """One run per generated app, queued to execute one after another."""
    if not payload.generation_job_ids:
        raise HttpError(400, "Select at least one app.")
    if len(payload.generation_job_ids) > MAX_BATCH_APPS:
        raise HttpError(400, f"At most {MAX_BATCH_APPS} apps per batch.")
    tool_slugs = _validated_tools(payload.tool_slugs)
    jobs = _accessible_jobs(request.auth, payload.generation_job_ids)
    workspace = workspace_service.get_workspace(request.auth)
    batch_id = uuid.uuid4()
    runs = [
        AnalysisRun.objects.create(
            name=payload.name or _default_name(job),
            created_by=request.auth,
            workspace=workspace,
            generation_job=job,
            tool_slugs=tool_slugs,
            batch_id=batch_id,
        )
        for job in jobs
    ]
    runner.dispatch_many([r.id for r in runs])
    return {"batch_id": str(batch_id), "run_ids": [str(r.id) for r in runs]}


@router.post("/runs/preview/", response=RunPreviewSchema)
def preview_runs(request, payload: RunPreviewRequestSchema):
    """What a run over these apps / this code with these tools would do (free; nothing is created).

    Per tool: on how many targets it would run, and on how many it would be
    skipped (no file it reads; app not running for dynamic tools). Per target:
    file count and whether its app container is running.
    """
    if len(payload.generation_job_ids) > MAX_BATCH_APPS:
        raise HttpError(400, f"At most {MAX_BATCH_APPS} apps per batch.")
    user = request.auth
    tools = {t.slug: t for t in AnalyzerTool.objects.filter(slug__in=payload.tool_slugs)}
    workspace = workspace_service.get_workspace(user)
    installed = {it.tool.slug: it.status for it in workspace.installed_tools.select_related("tool")}

    targets: list[dict[str, Any]] = []
    file_maps: list[dict[str, str]] = []
    running_apps: list[bool] = []
    if payload.generation_job_ids:
        jobs = _accessible_jobs(user, payload.generation_job_ids)
        running = runner.running_app_containers(user, [j.id for j in jobs])
        for job in jobs:
            files = runner._materialize(AnalysisRun(generation_job=job).get_code_for_analysis())  # noqa: SLF001
            app_running = str(job.id) in running
            file_maps.append(files)
            running_apps.append(app_running)
            targets.append(
                {
                    "job_id": str(job.id),
                    "label": _target(AnalysisRun(generation_job=job))["label"],
                    "files": len(files),
                    "lines": sum(c.count("\n") + 1 for c in files.values()),
                    "app_running": app_running,
                },
            )
    elif payload.source_code:
        files = runner._materialize(payload.source_code)  # noqa: SLF001
        file_maps.append(files)
        running_apps.append(False)
        targets.append(
            {
                "job_id": None,
                "label": "Pasted code",
                "files": len(files),
                "lines": sum(c.count("\n") + 1 for c in files.values()),
                "app_running": False,
            },
        )

    tool_rows = []
    tool_runs = 0
    for slug in dict.fromkeys(payload.tool_slugs):
        tool = tools.get(slug)
        row = {
            "slug": slug,
            "name": tool.name if tool else slug,
            "known": tool is not None,
            "installed": installed.get(slug) == "installed",
            "install_status": installed.get(slug, ""),
            "mode": tool.analysis_mode if tool else "",
            "kind": tool.kind if tool else "",
            "runs": 0,
            "not_applicable": 0,
            "app_not_running": 0,
        }
        for files, app_running in zip(file_maps, running_apps, strict=True):
            if tool is None:
                continue
            if tool.analysis_mode == "dynamic" and not app_running:
                row["app_not_running"] += 1
            elif not runner.applicable_files(tool, files):
                row["not_applicable"] += 1
            else:
                row["runs"] += 1
        tool_runs += row["runs"]
        tool_rows.append(row)

    warnings = []
    missing = [r["name"] for r in tool_rows if r["known"] and not r["installed"]]
    if missing:
        warnings.append(f"Not installed (they would fail): {', '.join(missing)}.")
    unknown = [r["slug"] for r in tool_rows if not r["known"]]
    if unknown:
        warnings.append(f"Unknown tool(s): {', '.join(unknown)}.")
    empty = [t["label"] for t in targets if t["files"] == 0]
    if empty:
        warnings.append(f"No code to analyze for: {', '.join(empty[:5])}{'…' if len(empty) > 5 else ''}.")
    return {
        "workspace_status": workspace.status,
        "targets": targets,
        "tools": tool_rows,
        "tool_runs": tool_runs,
        "warnings": warnings,
    }


@router.post("/runs/bulk/", response=BulkResultSchema)
def bulk_action(request, payload: BulkActionSchema):
    """Cancel or delete several runs. Active runs are never deleted (cancel them first)."""
    if payload.action not in ("cancel", "delete"):
        raise HttpError(400, "action must be 'cancel' or 'delete'.")
    ids = [_uuid(i) for i in payload.ids[:500]]
    runs = list(AnalysisRun.objects.filter(created_by=request.auth, id__in=ids).select_related("workspace__container"))
    affected = 0
    if payload.action == "cancel":
        for run in runs:
            affected += int(runner.cancel(run))
    else:
        deletable = [r.id for r in runs if r.status not in ACTIVE_STATUSES]
        affected = len(deletable)
        AnalysisRun.objects.filter(id__in=deletable).delete()
    return {"affected": affected, "skipped": len(runs) - affected}


# --- One run --------------------------------------------------------------------


@router.get("/runs/{run_id}/", response=AnalysisRunSchema)
def get_run(request, run_id: str):
    runner.reap_stale_runs()
    return serialize_run(_get_run(request, run_id))


@router.post("/runs/{run_id}/cancel/", response=AnalysisRunSchema)
def cancel_run(request, run_id: str):
    """Cancel a pending or running run: it stops before its next tool and the
    in-flight tool's processes are killed; ``cancelled`` is final."""
    run = _get_run(request, run_id)
    runner.cancel(run)
    return serialize_run(_get_run(request, run_id))


@router.post("/runs/{run_id}/rerun/", response=AnalysisRunSchema)
def rerun(request, run_id: str, payload: RerunSchema):
    """Start a new run on the same code: all tools, the given ones, or only those that measured nothing."""
    source = _get_run(request, run_id)
    if source.generation_job_id is None and not source.source_code:
        raise HttpError(400, "This run's app was deleted; there is nothing to re-run.")
    if payload.tool_slugs:
        slugs = payload.tool_slugs
    elif payload.only_unmeasured:
        measured = {
            r.tool_slug
            for r in source.results.all()
            if r.status == ToolResult.Status.COMPLETED
            or (r.status == ToolResult.Status.SKIPPED and (r.summary or {}).get("skip_reason") == "not_applicable")
        }
        slugs = [s for s in source.tool_slugs if s not in measured]
        if not slugs:
            raise HttpError(400, "Every tool of this run measured the code; nothing to re-run.")
    else:
        slugs = source.tool_slugs
    run = AnalysisRun.objects.create(
        name=payload.name or source.name,
        created_by=request.auth,
        workspace=workspace_service.get_workspace(request.auth),
        generation_job_id=source.generation_job_id,
        source_code=source.source_code,
        tool_slugs=_validated_tools(list(slugs)),
    )
    runner.dispatch(str(run.id))
    return serialize_run(_get_run(request, str(run.id)))


@router.delete("/runs/{run_id}/", response={200: dict})
def delete_run(request, run_id: str):
    run = _get_run(request, run_id, related=False)
    if run.status in ACTIVE_STATUSES:
        raise HttpError(409, "The run is still active; cancel it first.")
    run.delete()
    return 200, {"status": "deleted"}


@router.get("/runs/{run_id}/results/{result_id}/", response=ToolResultDetailSchema)
def get_result(request, run_id: str, result_id: str):
    """One tool's result with its raw output (command, exit code, stdout) for debugging."""
    run = _get_run(request, run_id, related=False)
    try:
        result = get_object_or_404(ToolResult, id=uuid.UUID(result_id), run=run)
    except ValueError as exc:
        raise HttpError(404, "Not Found") from exc
    tool = AnalyzerTool.objects.filter(slug=result.tool_slug).first()
    return {
        **serialize_result(result),
        "raw_output": result.raw_output or {},
        "tool_name": tool.name if tool else result.tool_slug,
        "tool_docs_url": tool.docs_url if tool else "",
        "findings_count": result.findings.count(),
    }


# --- Findings ---------------------------------------------------------------------


def _findings_qs(
    run: AnalysisRun,
    *,
    tool_slug: str = "",
    tool_severity: str = "",
    severity: str = "",
    category: str = "",
    file_path: str = "",
    rule_id: str = "",
    search: str = "",
):
    qs = Finding.objects.filter(result__run=run).select_related("result")
    if tool_slug:
        qs = qs.filter(result__tool_slug=tool_slug)
    if tool_severity == "unrated":
        qs = qs.filter(tool_severity="")
    elif tool_severity:
        qs = qs.filter(tool_severity__iexact=tool_severity)
    if severity:  # normalized level (legacy / aggregate consumers)
        qs = qs.filter(severity=severity)
    if category:
        qs = qs.filter(category=category)
    if file_path:
        qs = qs.filter(file_path=file_path)
    if rule_id:
        qs = qs.filter(rule_id=rule_id)
    if search:
        term = search.strip()
        qs = qs.filter(
            Q(title__icontains=term)
            | Q(description__icontains=term)
            | Q(file_path__icontains=term)
            | Q(rule_id__icontains=term)
            | Q(code_snippet__icontains=term),
        )
    return qs


@router.get("/runs/{run_id}/findings/", response=PaginatedFindingsSchema)
def list_findings(
    request,
    run_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    tool_slug: str = Query(""),
    tool_severity: str = Query("", description="Level as the tool printed it (case-insensitive), or 'unrated'."),
    severity: str = Query("", description="Normalized level (aggregate statistics); prefer tool_severity."),
    category: str = Query(""),
    file_path: str = Query(""),
    rule_id: str = Query(""),
    search: str = Query(""),
    sort: str = Query("severity"),
):
    run = _get_run(request, run_id, related=False)
    qs = _findings_qs(
        run,
        tool_slug=tool_slug,
        tool_severity=tool_severity,
        severity=severity,
        category=category,
        file_path=file_path,
        rule_id=rule_id,
        search=search,
    ).order_by(*_FINDING_SORTS.get(sort, _FINDING_SORTS["severity"]), "id")
    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)
    return {
        "items": [_serialize_finding(f) for f in page_qs],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.get("/runs/{run_id}/findings/facets/", response=FindingFacetsSchema)
def finding_facets(request, run_id: str, tool_slug: str = Query("")):
    """Counts for the findings explorer: per tool, reported level, category, file and rule.

    Levels are the tools' own labels (case-folded), ordered by their mapped
    rank; findings of tools that rate nothing are counted as ``unrated``.
    With ``tool_slug`` every facet but ``tools`` is limited to that tool.
    """
    run = _get_run(request, run_id, related=False)
    every = Finding.objects.filter(result__run=run)
    scoped = every.filter(result__tool_slug=tool_slug) if tool_slug else every

    def rows(qs, field, *, limit=None):
        data = list(qs.values(field).annotate(n=Count("id")).order_by("-n", field))
        return [{"value": r[field] or "", "count": r["n"]} for r in (data[:limit] if limit else data)], len(data)

    tools, _ = rows(every, "result__tool_slug")
    levels = list(
        scoped.exclude(tool_severity="")
        .annotate(label=Lower("tool_severity"))
        .values("label")
        .annotate(
            n=Count("id"),
            rank=Min(Case(*[When(severity=s, then=Value(r)) for s, r in _SEVERITY_RANK.items()], default=Value(5))),
        )
        .order_by("rank", "-n"),
    )
    categories, _ = rows(scoped.exclude(category=""), "category")
    files, files_total = rows(scoped.exclude(file_path=""), "file_path", limit=_FACET_LIMIT)
    rules, rules_total = rows(scoped.exclude(rule_id=""), "rule_id", limit=_FACET_LIMIT)
    # One representative title per rule, so the rule facet is readable.
    titles = dict(
        scoped.filter(rule_id__in=[r["value"] for r in rules])
        .values("rule_id")
        .annotate(t=Min("title"))
        .values_list("rule_id", "t"),
    )
    for r in rules:
        r["label"] = titles.get(r["value"], "")
    return {
        "total": scoped.count(),
        "tools": tools,
        "levels": [{"value": r["label"], "count": r["n"], "rank": r["rank"]} for r in levels],
        "unrated": scoped.filter(tool_severity="").count(),
        "categories": categories,
        "files": files,
        "files_total": files_total,
        "rules": rules,
        "rules_total": rules_total,
    }


# Re-exported for callers that serialized runs before the views were split.
__all__ = ["serialize_result", "serialize_run", "serialize_run_list"]
