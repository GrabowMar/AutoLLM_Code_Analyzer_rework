"""Run endpoints: list, ad-hoc submit, preview, detail, cancel, retry, delete,
findings, issues, files (+ content), artifacts and exports.

Runs are owner-scoped (staff may read and manage all). File *content*
follows the same rule as findings and their snippets (owner or staff) and
is limited to paths in the run's sample manifest. Execution never
happens inside a request: submission creates the run and its pending tool
results, and the executor picks it up after the transaction commits.
Static paths (``/runs/preview/``) are registered before ``{uuid}`` ones.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from typing import Any
from uuid import UUID

from django.contrib.postgres.aggregates import ArrayAgg
from django.db import transaction
from django.db.models import Count
from django.db.models import F
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.http import JsonResponse
from ninja import Query
from ninja import Status
from ninja.errors import HttpError

from backend.analysis.api import common
from backend.analysis.api import exports
from backend.analysis.api.schema import FindingPageSchema
from backend.analysis.api.schema import IssuePageSchema
from backend.analysis.api.schema import RunCreateSchema
from backend.analysis.api.schema import RunDetailSchema
from backend.analysis.api.schema import RunPageSchema
from backend.analysis.api.schema import RunRetrySchema
from backend.analysis.api.serializers import finding_payload
from backend.analysis.api.serializers import run_detail
from backend.analysis.api.serializers import run_list_item
from backend.analysis.api.views._router import router
from backend.analysis.engine import artifacts
from backend.analysis.engine import executor
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import preflight as preflight_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.types import SEVERITIES
from backend.analysis.engine.types import SampleUnavailable
from backend.analysis.models import AnalysisRun
from backend.analysis.models import FileOrigin
from backend.analysis.models import Finding
from backend.analysis.models import FindingCategory
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult
from backend.analysis.research import study as study_mod
from backend.analysis.research.study import StudyError
from backend.credentials.services.resolver import SETTINGS_LINK_REMEDIATION

logger = logging.getLogger(__name__)

MAX_INLINE_FILES = 500
MAX_INLINE_BYTES = 5 * 1024 * 1024
MAX_CONTENT_BYTES = 2 * 1024 * 1024

_LINE = F("line_number").asc(nulls_last=True)
FINDING_ORDERINGS: dict[str, tuple[Any, ...]] = {
    "severity": ("severity_rank", "file_path", _LINE, "id"),
    "-severity": ("-severity_rank", "file_path", _LINE, "id"),
    "file": ("file_path", _LINE, "severity_rank", "id"),
    "-file": ("-file_path", _LINE, "severity_rank", "id"),
    "tool": ("tool_slug", "severity_rank", "file_path", _LINE, "id"),
    "rule": ("rule_id", "severity_rank", "file_path", _LINE, "id"),
    "category": ("category", "severity_rank", "file_path", _LINE, "id"),
    "created": ("created_at", "id"),
    "-created": ("-created_at", "id"),
}
RUN_ORDERINGS: dict[str, tuple[Any, ...]] = {
    "-created_at": ("-created_at", "-id"),
    "created_at": ("created_at", "id"),
    "status": ("status", "-created_at", "-id"),
    "name": ("name", "-created_at", "-id"),
    "-completed_at": (F("completed_at").desc(nulls_last=True), "-created_at", "-id"),
    "completed_at": (F("completed_at").asc(nulls_last=True), "created_at", "id"),
    "-duration": (F("duration_seconds").desc(nulls_last=True), "-created_at", "-id"),
    "duration": (F("duration_seconds").asc(nulls_last=True), "-created_at", "-id"),
}


class _Reject(Exception):  # noqa: N818 - control flow carrying a response
    def __init__(self, response) -> None:
        super().__init__("rejected")
        self.response = response


def _runs_qs():
    return AnalysisRun.objects.select_related("profile").prefetch_related("results")


def _runs_list_qs():
    # List items never need the big JSON columns (an inline source can be MBs).
    return _runs_qs().defer("plan", "sample_manifest", "measurement", "source_code")


def _detail(run: AnalysisRun) -> dict[str, Any]:
    return run_detail(_runs_qs().get(pk=run.pk))


def _check_choices(param: str, values: list[str], allowed) -> None:
    if bad := [v for v in values if v not in allowed]:
        raise _Reject(common.query_error(param, f"unknown value(s): {', '.join(bad)}"))


# --------------------------------------------------------------------------
# List / submit / preview
# --------------------------------------------------------------------------


@router.get("/runs/", response=RunPageSchema)
def list_runs(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: str = Query("", description="comma-separated run statuses"),
    purpose: str = Query("", description="comma-separated purposes (default: all but canary)"),
    study_id: UUID | None = None,
    generation_job_id: UUID | None = None,
    profile_id: UUID | None = None,
    plan_hash: str = Query(""),
    is_complete: bool | None = None,
    q: str = Query("", max_length=200),
    ordering: str = Query("-created_at"),
):
    """Your runs (staff: all), filtered and paginated; also nudges stale-run recovery (≤ once a minute)."""
    common.maybe_reconcile()
    statuses = common.csv_param(status)
    purposes = common.csv_param(purpose)
    try:
        _check_choices("status", statuses, AnalysisRun.Status.values)
        _check_choices("purpose", purposes, AnalysisRun.Purpose.values)
        _check_choices("ordering", [ordering], RUN_ORDERINGS)
    except _Reject as exc:
        return exc.response
    qs = common.visible_runs(request.auth, _runs_list_qs())
    if statuses:
        qs = qs.filter(status__in=statuses)
    if purposes:
        qs = qs.filter(purpose__in=purposes)
    else:
        qs = qs.exclude(purpose=AnalysisRun.Purpose.CANARY)
    if study_id:
        qs = qs.filter(study_id=study_id)
    if generation_job_id:
        qs = qs.filter(generation_job_id=generation_job_id)
    if profile_id:
        qs = qs.filter(profile_id=profile_id)
    if plan_hash:
        qs = qs.filter(plan_hash=plan_hash)
    if is_complete is not None:
        qs = qs.filter(is_complete=is_complete)
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(target_meta__model_slug__icontains=q)
            | Q(target_meta__app_slug__icontains=q)
            | Q(target_meta__harness__icontains=q),
        )
    items, meta = common.paginate(qs.order_by(*RUN_ORDERINGS[ordering]), page, per_page)
    return common.page_payload(items, meta, run_list_item)


def _validate_source(source: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not source:
        errors["source_code"] = "provide at least one file"
    elif len(source) > MAX_INLINE_FILES:
        errors["source_code"] = f"at most {MAX_INLINE_FILES} files"
    elif sum(len(v.encode("utf-8", "surrogatepass")) for v in source.values()) > MAX_INLINE_BYTES:
        errors["source_code"] = f"at most {MAX_INLINE_BYTES // (1024 * 1024)} MB of source"
    else:
        for path in source:
            parts = path.split("/")
            if not path or path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts):
                errors["source_code"] = f"invalid path {path!r}: use relative POSIX paths without '.' or '..'"
                break
    return errors


def _target_and_plan(user, payload: RunCreateSchema):
    """``(job, source_code, profile, plan)`` for an ad-hoc run, or raise ``_Reject``/HttpError."""
    from backend.generation.models import GenerationJob

    has_job = payload.generation_job_id is not None
    has_source = payload.source_code is not None
    if has_job == has_source:
        msg = "provide exactly one of generation_job_id or source_code"
        raise _Reject(common.validation_error({"generation_job_id": msg}))
    if payload.profile_id is not None and payload.config is not None:
        raise _Reject(common.validation_error({"config": "provide a profile_id or an inline config, not both"}))
    job = None
    if has_job:
        job = common.visible_jobs(user, study_mod.job_queryset()).filter(id=payload.generation_job_id).first()
        if job is None:
            raise HttpError(404, "Generation job not found")
        if job.status != GenerationJob.Status.COMPLETED:
            raise HttpError(400, f"Only completed generation jobs can be analysed (this job is {job.status}).")
    elif errors := _validate_source(payload.source_code or {}):
        raise _Reject(common.validation_error(errors))
    profile = None
    config = payload.config
    if payload.profile_id is not None:
        profile = common.visible_profiles(user).filter(id=payload.profile_id).first()
        if profile is None:
            raise _Reject(common.validation_error({"profile_id": "profile not found"}))
    elif config is None:
        profile = common.default_profile()
        if profile is None:
            config = plan_mod.default_profile_config()
    try:
        if profile is not None:
            plan = plan_mod.freeze_plan(profile=profile)
        else:
            plan = plan_mod.freeze_plan(config=plan_mod.validate_profile_config(config))
    except ConfigError as exc:
        raise _Reject(common.config_error(exc, prefix="" if profile is not None else "config")) from exc
    except sandbox.SandboxError as exc:
        raise HttpError(503, f"The analyzer image is unavailable: {exc}") from exc
    return job, (payload.source_code if has_source else None), profile, plan


def _require_key(user, plan: dict[str, Any]) -> None:
    if ((plan or {}).get("reviewer") or {}).get("enabled") and not common.key_present(user):
        raise _Reject(
            JsonResponse(
                {
                    "detail": "The AI reviewer is enabled but no OpenRouter API key is available.",
                    "remediation": SETTINGS_LINK_REMEDIATION,
                },
                status=400,
            ),
        )


@router.post("/runs/preview/", response=dict)
def preview_run(request, payload: RunCreateSchema):
    """Preflight for one target under the resolved plan (nothing is created)."""
    try:
        job, source, _profile, plan = _target_and_plan(request.auth, payload)
    except _Reject as exc:
        return exc.response
    result = preflight_mod.preflight(
        user=request.auth,
        plan=plan,
        jobs=[job] if job is not None else None,
        inline=source,
    )
    return study_mod.json_safe({**result, "plan": plan})


@router.post("/runs/", response={201: RunDetailSchema})
def create_run(request, payload: RunCreateSchema):
    """Ad-hoc run of one job (yours; staff: any) or inline source, under a profile or an inline config."""
    try:
        job, source, profile, plan = _target_and_plan(request.auth, payload)
        _require_key(request.auth, plan)
    except _Reject as exc:
        return exc.response
    name = payload.name or ("Ad-hoc analysis · " + (str(job.id)[:8] if job is not None else "inline source"))
    try:
        with transaction.atomic():
            run = executor.submit_run(
                user=request.auth,
                plan=plan,
                profile=profile,
                generation_job=job,
                source_code=source,
                purpose=AnalysisRun.Purpose.ADHOC,
                name=name[:200],
            )
    except SampleUnavailable as exc:
        raise HttpError(400, f"The target cannot be analysed: {exc}") from exc
    except PermissionError as exc:
        raise HttpError(404, "Generation job not found") from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return Status(201, _detail(run))


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


@router.get("/runs/{uuid:run_id}/", response=RunDetailSchema)
def get_run(request, run_id: UUID):
    """Detail: plan, target_meta, manifest totals, tool results (diagnostics, artifacts), review samples,
    measurement + outcomes, summary."""
    return run_detail(common.get_run(request.auth, run_id, qs=_runs_qs()))


@router.post("/runs/{uuid:run_id}/cancel/", response=RunDetailSchema)
def cancel_run(request, run_id: UUID):
    """Cooperative, final cancel: the worker stops at the next poll and the run is never overwritten."""
    run = common.get_run(request.auth, run_id, write=True)
    if run.status not in AnalysisRun.ACTIVE_STATUSES:
        return common.conflict(f"The run is already {run.status}.")
    executor.request_cancel(run)
    return _detail(run)


@router.post(
    "/runs/{uuid:run_id}/retry/",
    response=RunDetailSchema,
    openapi_extra=common.optional_body_openapi(RunRetrySchema),
)
def retry_run(request, run_id: UUID):
    """Reset failed/timed-out/cancelled tool results (or only ``{"tools": [...]}``) to pending and requeue."""
    run = common.get_run(request.auth, run_id, write=True)
    payload, error = common.optional_body(request, RunRetrySchema)
    if error is None:
        error = _retry_problem(run, payload.tools)
    if error is not None:
        return error
    try:
        if run.study_id:
            # Under the study row lock: serialized with a concurrent study cancel / retry-failed.
            study_mod.retry_run(run, tools=payload.tools)
        else:
            executor.retry(run, tools=payload.tools)
    except StudyError as exc:
        return common.study_error(exc)
    except ValueError as exc:
        return common.conflict(str(exc))
    return _detail(run)


def _retry_problem(run: AnalysisRun, tools: list[str] | None) -> JsonResponse | None:
    if run.status in AnalysisRun.ACTIVE_STATUSES:
        return common.conflict(f"The run is still {run.status}.")
    if tools is not None and not tools:
        return common.validation_error({"tools": "provide at least one tool, or omit to retry all failed"})
    if tools and (unknown := [t for t in tools if t not in run.tool_slugs]):
        return common.validation_error({"tools": f"not in this run's plan: {', '.join(unknown)}"})
    return None


@router.delete("/runs/{uuid:run_id}/", response=dict)
def delete_run(request, run_id: UUID):
    """Delete a finished ad-hoc or self-test run with its findings and artifacts."""
    run = common.get_run(request.auth, run_id, write=True)
    if run.status in AnalysisRun.ACTIVE_STATUSES:
        return common.conflict("Cancel the run before deleting it.")
    if run.study_id:
        return common.conflict("This run is part of a study; delete the study instead.", study_id=str(run.study_id))
    run_pk = str(run.pk)
    run.delete()
    transaction.on_commit(lambda: remove_run_artifacts([run_pk]))
    return {"deleted": True, "id": run_pk}


def remove_run_artifacts(run_ids: list[str]) -> None:
    """Best-effort removal of the runs' artifact directories (after the rows are gone)."""
    for run_id in run_ids:
        try:
            artifacts.delete_run(run_id)
        except Exception as exc:  # noqa: BLE001 - leftover files are harmless; the rows are gone
            logger.warning("Could not remove artifacts of run %s: %s", run_id, exc)


# --------------------------------------------------------------------------
# Findings / issues
# --------------------------------------------------------------------------


def _facets(qs) -> dict[str, dict[str, int]]:
    by_sev = dict(qs.order_by().values_list("severity").annotate(n=Count("id")))
    out = {"severity": {s: by_sev.get(s, 0) for s in SEVERITIES}}
    for name, field in (("tool", "tool_slug"), ("category", "category"), ("origin", "file_origin")):
        rows = qs.order_by().values_list(field).annotate(n=Count("id")).order_by("-n", field)
        out[name] = dict(rows)
    return out


@router.get("/runs/{uuid:run_id}/findings/", response=FindingPageSchema)
def list_findings(
    request,
    run_id: UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    severity: str = Query("", description="comma-separated severities"),
    tool: str = Query("", description="comma-separated tool slugs"),
    category: str = Query("", description="comma-separated categories"),
    origin: str = Query("", description="comma-separated file origins"),
    file: str = Query("", description="exact path, or a directory prefix ending in '/'"),
    rule: str = Query("", description="comma-separated rule ids"),
    grounding: str = Query("", description="comma-separated grounding verdicts (AI)"),
    is_ai: bool | None = None,
    counted: bool | None = None,
    consensus: bool | None = None,
    in_scope: bool | None = None,
    issue_key: str = Query(""),
    sample: int | None = Query(None, ge=0, description="AI review sample index"),
    result_id: UUID | None = None,
    q: str = Query("", max_length=200),
    ordering: str = Query("severity"),
    facets: bool = Query(default=True),
):
    """Findings, severity-rank ordered (critical first) with an id tiebreak; facet counts over the filter."""
    run = common.get_run(request.auth, run_id)
    severities = common.csv_param(severity)
    categories = common.csv_param(category)
    origins = common.csv_param(origin)
    groundings = common.csv_param(grounding)
    try:
        _check_choices("severity", severities, SEVERITIES)
        _check_choices("category", categories, FindingCategory.values)
        _check_choices("origin", origins, FileOrigin.values)
        _check_choices("grounding", groundings, [g for g in Finding.Grounding.values if g])
        _check_choices("ordering", [ordering], FINDING_ORDERINGS)
    except _Reject as exc:
        return exc.response
    qs = run.findings.select_related("review_sample")
    if severities:
        qs = qs.filter(severity__in=severities)
    if tools := common.csv_param(tool):
        qs = qs.filter(tool_slug__in=tools)
    if categories:
        qs = qs.filter(category__in=categories)
    if origins:
        qs = qs.filter(file_origin__in=origins)
    if file:
        qs = qs.filter(file_path__startswith=file) if file.endswith("/") else qs.filter(file_path=file)
    if rules := common.csv_param(rule):
        qs = qs.filter(rule_id__in=rules)
    if groundings:
        qs = qs.filter(grounding__in=groundings)
    if is_ai is not None:
        qs = qs.filter(is_ai=is_ai)
    if counted is not None:
        qs = qs.filter(counted=counted)
    if consensus is not None:
        qs = qs.filter(is_consensus=consensus)
    if in_scope is not None:
        qs = qs.filter(in_measured_scope=in_scope)
    if issue_key:
        qs = qs.filter(issue_key=issue_key)
    if sample is not None:
        qs = qs.filter(review_sample__index=sample)
    if result_id:
        qs = qs.filter(result_id=result_id)
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(rule_id__icontains=q) | Q(file_path__icontains=q),
        )
    items, meta = common.paginate(qs.order_by(*FINDING_ORDERINGS[ordering]), page, per_page)
    return {
        **common.page_payload(items, meta, finding_payload),
        "ordering": ordering,
        "facets": _facets(qs) if facets else {},
    }


@router.get("/runs/{uuid:run_id}/issues/", response=IssuePageSchema)
def list_issues(
    request,
    run_id: UUID,
    kind: str = Query("static", description="static (counted, deduplicated) or ai (consensus clusters)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    severity: str = Query(""),
    category: str = Query(""),
    tool: str = Query(""),
    file: str = Query(""),
):
    """Deduplicated issues (findings grouped by ``issue_key``) with the tools that detected each."""
    run = common.get_run(request.auth, run_id)
    severities = common.csv_param(severity)
    try:
        _check_choices("kind", [kind], ("static", "ai"))
        _check_choices("severity", severities, SEVERITIES)
    except _Reject as exc:
        return exc.response
    base = run.findings.exclude(issue_key="")
    base = base.filter(is_ai=False, counted=True) if kind == "static" else base.filter(is_ai=True, is_consensus=True)
    if categories := common.csv_param(category):
        base = base.filter(category__in=categories)
    if file:
        base = base.filter(file_path__startswith=file) if file.endswith("/") else base.filter(file_path=file)
    if tools := common.csv_param(tool):
        base = base.filter(issue_key__in=base.filter(tool_slug__in=tools).values("issue_key"))
    grouped = (
        base.order_by()
        .values("issue_key")
        .annotate(
            severity_rank=Min("severity_rank"),
            first_file=Min("file_path"),
            line_start=Min("line_number"),
            line_end=Max(Coalesce("end_line", "line_number")),
            findings=Count("id"),
            tools=ArrayAgg("tool_slug", distinct=True, default=[]),
            rule_ids=ArrayAgg("rule_id", distinct=True, default=[]),
            categories=ArrayAgg("category", distinct=True, default=[]),
            files=ArrayAgg("file_path", distinct=True, default=[]),
        )
    )
    if severities:
        grouped = grouped.filter(severity_rank__in=[SEVERITIES.index(s) for s in severities])
    grouped = grouped.order_by("severity_rank", "first_file", F("line_start").asc(nulls_last=True), "issue_key")
    rows, meta = common.paginate(grouped, page, per_page)
    keys = [r["issue_key"] for r in rows]
    details: dict[str, dict[str, Any]] = {}
    for f in (
        base.filter(issue_key__in=keys)
        .select_related("review_sample")
        .order_by("severity_rank", "file_path", _LINE, "id")
    ):
        entry = details.setdefault(
            f.issue_key,
            {"title": f.title, "category": f.category, "cwe": [], "samples": set()},
        )
        for cwe in f.cwe or []:
            if cwe not in entry["cwe"]:
                entry["cwe"].append(str(cwe))
        if f.review_sample_id is not None:
            entry["samples"].add(f.review_sample.index)
    items = []
    for r in rows:
        extra = details.get(r["issue_key"], {})
        items.append(
            {
                "issue_key": r["issue_key"],
                "kind": kind,
                "severity": SEVERITIES[r["severity_rank"]] if 0 <= r["severity_rank"] < len(SEVERITIES) else "info",
                "severity_rank": r["severity_rank"],
                "title": extra.get("title", ""),
                "category": extra.get("category", ""),
                "categories": sorted(r["categories"]),
                "cwe": extra.get("cwe", []),
                "file_path": r["first_file"],
                "files": sorted(r["files"]),
                "line_start": r["line_start"],
                "line_end": r["line_end"],
                "tools": sorted(r["tools"]),
                "rule_ids": sorted(r["rule_ids"]),
                "findings": r["findings"],
                "samples": sorted(extra.get("samples", set())),
            },
        )
    return {"items": items, "kind": kind, **meta}


# --------------------------------------------------------------------------
# Files / content / artifacts
# --------------------------------------------------------------------------


@router.get("/runs/{uuid:run_id}/files/", response=dict)
def list_files(
    request,
    run_id: UUID,
    included: bool | None = None,
    measured: bool | None = None,
    origin: str = Query(""),
    language: str = Query(""),
    kind: str = Query(""),
    q: str = Query("", max_length=200),
):
    """The sample manifest (every file with scope decision, origin, SLOC) plus finding counts per file."""
    run = common.get_run(request.auth, run_id)
    manifest = run.sample_manifest or {}
    counts = {
        row["file_path"]: row
        for row in run.findings.order_by()
        .values("file_path")
        .annotate(
            total=Count("id"),
            counted=Count("id", filter=Q(counted=True, is_ai=False)),
            ai_consensus=Count("id", filter=Q(is_ai=True, is_consensus=True)),
        )
    }
    origins = set(common.csv_param(origin))
    languages = set(common.csv_param(language))
    kinds = set(common.csv_param(kind))
    files = []
    for entry in manifest.get("files") or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path") or ""
        if included is not None and bool(entry.get("included")) != included:
            continue
        if measured is not None and bool(entry.get("measured")) != measured:
            continue
        if origins and entry.get("origin") not in origins:
            continue
        if languages and entry.get("language") not in languages:
            continue
        if kinds and entry.get("kind") not in kinds:
            continue
        if q and q.lower() not in path.lower():
            continue
        row = counts.get(path) or {}
        files.append(
            {
                **entry,
                "findings": {
                    "total": row.get("total", 0),
                    "counted": row.get("counted", 0),
                    "ai_consensus": row.get("ai_consensus", 0),
                },
            },
        )
    return {
        "source": manifest.get("source") or "",
        "resolver_version": manifest.get("resolver_version") or "",
        "sloc_version": manifest.get("sloc_version") or "",
        "sample_hash": run.sample_hash,
        "totals": manifest.get("totals") or {},
        "warnings": manifest.get("warnings") or [],
        "count": len(files),
        "files": files,
    }


@router.get("/runs/{uuid:run_id}/files/content/", response=dict)
def file_content(request, run_id: UUID, path: str = Query(..., min_length=1, max_length=1000)):
    """One file of the analysed sample, re-resolved from its source and verified against the run's hashes.

    Visible to the owner and staff (like findings and their snippets); only
    paths in the run's sample manifest can be read.
    """
    run = common.get_run(request.auth, run_id)
    entries = {e.get("path"): e for e in (run.sample_manifest or {}).get("files") or [] if isinstance(e, dict)}
    entry = entries.get(path)
    if entry is None:
        raise HttpError(404, "This path is not part of the run's sample.")
    try:
        smp = executor.load_sample(run)
    except executor.SampleChanged as exc:
        return common.conflict(str(exc))
    except SampleUnavailable as exc:
        raise HttpError(410, f"The run's source is no longer available: {exc}") from exc
    sample_file = smp.by_path().get(path)
    if sample_file is None:
        raise HttpError(410, "The file is no longer part of the run's source.")
    if entry.get("sha256") and sample_file.sha256 != entry["sha256"]:
        return common.conflict("The file changed since the run (sha256 mismatch).")
    binary = sample_file.kind == "asset" or b"\x00" in sample_file.content[:8192]
    too_large = sample_file.size > MAX_CONTENT_BYTES
    content = None if binary or too_large else sample_file.text
    return {
        "path": sample_file.path,
        "language": sample_file.language,
        "kind": sample_file.kind,
        "origin": sample_file.origin,
        "included": sample_file.included,
        "measured": sample_file.measured,
        "exclude_reason": sample_file.exclude_reason,
        "sha256": sample_file.sha256,
        "size": sample_file.size,
        "sloc": sample_file.sloc,
        "measured_sloc": sample_file.measured_sloc,
        "changed_lines": sample_file.changed_lines,
        "binary": binary,
        "too_large": too_large,
        "content": content,
        "lines": (content.count("\n") + (0 if content.endswith("\n") else 1)) if content else 0,
    }


def _artifact_owner(run: AnalysisRun, owner_id: UUID) -> dict[str, Any]:
    result = ToolResult.objects.filter(run=run, id=owner_id).only("artifacts").first()
    if result is not None:
        return result.artifacts or {}
    sample = ReviewSample.objects.filter(result__run=run, id=owner_id).only("artifacts").first()
    if sample is not None:
        return sample.artifacts or {}
    raise HttpError(404, "No such tool result or review sample in this run.")


def _artifact_content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith((".jsonl", ".ndjson")):
        return "application/x-ndjson"
    if lower.endswith(".sarif"):
        return "application/sarif+json"
    guessed, _ = mimetypes.guess_type(name)
    # Never let an artifact (tool output about untrusted code) render as HTML.
    if guessed is None or guessed.startswith("text/") or "html" in guessed or "xml" in guessed:
        return "text/plain; charset=utf-8"
    return guessed


@router.get("/runs/{uuid:run_id}/artifacts/{uuid:result_id}/{path:name}", url_name="analysis_artifact")
def download_artifact(request, run_id: UUID, result_id: UUID, name: str, download: bool = Query(default=False)):
    """A stored raw output (stdout, stderr, report files, AI responses), sha256-verified on read."""
    run = common.get_run(request.auth, run_id)
    entry = _artifact_owner(run, result_id).get(name)
    if not isinstance(entry, dict):
        raise HttpError(404, "Artifact not found")
    try:
        data = artifacts.load(entry)
    except FileNotFoundError as exc:
        raise HttpError(404, "The artifact file is missing from storage.") from exc
    except ValueError as exc:
        return common.conflict(f"Artifact integrity check failed: {exc}")
    response = HttpResponse(data, content_type=_artifact_content_type(name))
    filename = artifacts.safe_segment(name.rsplit("/", 1)[-1])
    disposition = "attachment" if download else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    if sha := entry.get("sha256"):
        response["X-Artifact-SHA256"] = sha
    return response


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------


def _attachment(body: str | bytes, content_type: str, filename: str) -> HttpResponse:
    response = HttpResponse(body, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/runs/{uuid:run_id}/export.json")
def export_run_json(request, run_id: UUID):
    """Everything about the run: detail, sample manifest and every finding."""
    run = common.get_run(request.auth, run_id, qs=_runs_qs())
    body = json.dumps(study_mod.json_safe(exports.run_json(run)), indent=2, ensure_ascii=False)
    return _attachment(body, "application/json", f"analysis-run-{str(run.id)[:8]}.json")


@router.get("/runs/{uuid:run_id}/export.csv")
def export_run_csv(request, run_id: UUID):
    """Findings as CSV (severity-rank order) with run, target and tool provenance columns."""
    run = common.get_run(request.auth, run_id)
    return _attachment(exports.run_csv(run), "text/csv; charset=utf-8", f"analysis-run-{str(run.id)[:8]}.csv")


@router.get("/runs/{uuid:run_id}/export.sarif")
def export_run_sarif(request, run_id: UUID):
    """SARIF 2.1.0: one run per tool with real versions and ``executionSuccessful`` per tool result."""
    run = common.get_run(request.auth, run_id)
    body = json.dumps(study_mod.json_safe(exports.run_sarif(run)), indent=2, ensure_ascii=False)
    return _attachment(body, "application/sarif+json", f"analysis-run-{str(run.id)[:8]}.sarif")
