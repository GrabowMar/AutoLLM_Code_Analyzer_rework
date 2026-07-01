"""Statistics service layer (split into aggregations, trend, export)."""

from backend.statistics.services.aggregations import get_analyzer_health
from backend.statistics.services.aggregations import get_code_generation_stats
from backend.statistics.services.aggregations import get_model_comparison
from backend.statistics.services.aggregations import get_severity_distribution
from backend.statistics.services.aggregations import get_system_overview
from backend.statistics.services.aggregations import get_tool_effectiveness
from backend.statistics.services.aggregations import get_top_findings
from backend.statistics.services.export import get_dashboard
from backend.statistics.services.trend import get_analysis_trends
from backend.statistics.services.trend import get_recent_activity

__all__ = [
    "get_analysis_trends",
    "get_analyzer_health",
    "get_code_generation_stats",
    "get_dashboard",
    "get_model_comparison",
    "get_recent_activity",
    "get_severity_distribution",
    "get_system_overview",
    "get_tool_effectiveness",
    "get_top_findings",
]
