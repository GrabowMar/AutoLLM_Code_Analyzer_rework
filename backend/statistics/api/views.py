"""Django Ninja API for platform statistics."""

from __future__ import annotations

import uuid

from ninja import Query
from ninja import Router
from ninja.errors import HttpError

from backend.statistics import services
from backend.statistics.api.schema import ActivityItemSchema
from backend.statistics.api.schema import AnalyzerHealthSchema
from backend.statistics.api.schema import CodeGenerationSchema
from backend.statistics.api.schema import DashboardSchema
from backend.statistics.api.schema import ModelComparisonRowSchema
from backend.statistics.api.schema import OverviewSchema
from backend.statistics.api.schema import SeverityDistributionSchema
from backend.statistics.api.schema import ToolRowSchema
from backend.statistics.api.schema import TopFindingSchema
from backend.statistics.api.schema import TrendsSchema

router = Router(tags=["statistics"])


def _uuid_param(value: str | None, name: str) -> str | None:
    """*value* as a canonical UUID string; a malformed id is the caller's error (400), not a server error."""
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HttpError(400, f"{name} must be a UUID") from exc


@router.get("/dashboard/", response=DashboardSchema)
def get_dashboard(request):
    """Composite payload for the Statistics page."""
    return services.get_dashboard(request.auth)


@router.get("/overview/", response=OverviewSchema)
def get_overview(request):
    return services.get_system_overview(request.auth)


@router.get("/severity/", response=SeverityDistributionSchema)
def get_severity(request):
    return services.get_severity_distribution(request.auth)


@router.get("/trends/", response=TrendsSchema)
def get_trends(request, days: int = Query(7, ge=1, le=90)):
    return services.get_analysis_trends(days, request.auth)


@router.get("/models/", response=list[ModelComparisonRowSchema])
def get_models(
    request,
    limit: int = Query(25, ge=1, le=100),
    prompt_hash: str | None = None,
    config_fingerprint: str | None = None,
    study_id: str | None = None,
):
    return services.get_model_comparison(
        request.auth,
        limit=limit,
        prompt_hash=prompt_hash,
        config_fingerprint=config_fingerprint,
        study_id=_uuid_param(study_id, "study_id"),
    )


@router.get("/tools/", response=list[ToolRowSchema])
def get_tools(request):
    return services.get_tool_effectiveness(request.auth)


@router.get("/top-findings/", response=list[TopFindingSchema])
def get_top_findings(request, limit: int = Query(10, ge=1, le=100)):
    return services.get_top_findings(limit, request.auth)


@router.get("/recent-activity/", response=list[ActivityItemSchema])
def get_recent_activity(request, limit: int = Query(20, ge=1, le=100)):
    return services.get_recent_activity(limit, request.auth)


@router.get("/code-generation/", response=CodeGenerationSchema)
def get_code_generation(request):
    return services.get_code_generation_stats(request.auth)


@router.get("/analyzer-health/", response=AnalyzerHealthSchema)
def get_analyzer_health(request):
    return services.get_analyzer_health()


# ── study tables (paper revision: dedup, tool accounting) ─────────────────


def _study_scope(request, study_id: str | None):
    from backend.statistics.services.runs import study_runs

    return study_runs(request.auth, study_id=_uuid_param(study_id, "study_id"))


@router.get("/study/breakdowns/")
def get_study_breakdowns(
    request,
    study_id: str | None = None,
    include_ai: bool = False,
    line_tolerance: int = Query(2, ge=0, le=20),
    include_issues: bool = False,
    common_tools_only: bool = True,
):
    """Raw vs cross-tool-deduplicated issues per app, model, task and tool, plus tool overlap.

    Scope: the study's current runs (first usable attempt per cell), else every run the caller owns. By
    default only tools that measured every app in scope are counted (see ``coverage``).
    """
    from backend.statistics.services.study import issue_breakdowns

    data = issue_breakdowns(
        _study_scope(request, study_id),
        include_ai=include_ai,
        line_tolerance=line_tolerance,
        common_tools_only=common_tools_only,
    )
    if not include_issues:
        data.pop("issues")
    return data


@router.get("/study/tools/")
def get_study_tools(request):
    """The tool inventory: catalog analyzers plus the platform's runtime evaluators."""
    from backend.statistics.services.accounting import tool_inventory

    return tool_inventory()


@router.get("/study/accounting/")
def get_study_accounting(request, study_id: str | None = None):
    """Runs, analyses, per-tool executions and evaluations actually recorded (never tools x apps)."""
    from backend.statistics.services.accounting import run_accounting

    return run_accounting(_study_scope(request, study_id))
