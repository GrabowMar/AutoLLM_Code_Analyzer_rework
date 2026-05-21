"""Django Ninja API for platform statistics."""

from __future__ import annotations

from ninja import Query
from ninja import Router

from llm_lab.statistics import services
from llm_lab.statistics.api.schema import ActivityItemSchema
from llm_lab.statistics.api.schema import AnalyzerHealthSchema
from llm_lab.statistics.api.schema import CodeGenerationSchema
from llm_lab.statistics.api.schema import DashboardSchema
from llm_lab.statistics.api.schema import ModelComparisonRowSchema
from llm_lab.statistics.api.schema import OverviewSchema
from llm_lab.statistics.api.schema import SeverityDistributionSchema
from llm_lab.statistics.api.schema import ToolRowSchema
from llm_lab.statistics.api.schema import TopFindingSchema
from llm_lab.statistics.api.schema import TrendsSchema

router = Router(tags=["statistics"])


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
def get_models(request, limit: int = Query(25, ge=1, le=100)):
    return services.get_model_comparison(request.auth, limit=limit)


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
