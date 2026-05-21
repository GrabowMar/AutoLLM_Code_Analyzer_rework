"""Statistics service layer (split into aggregations, trend, export)."""

from llm_lab.statistics.services.aggregations import get_analyzer_health
from llm_lab.statistics.services.aggregations import get_code_generation_stats
from llm_lab.statistics.services.aggregations import get_model_comparison
from llm_lab.statistics.services.aggregations import get_severity_distribution
from llm_lab.statistics.services.aggregations import get_system_overview
from llm_lab.statistics.services.aggregations import get_tool_effectiveness
from llm_lab.statistics.services.aggregations import get_top_findings
from llm_lab.statistics.services.export import get_dashboard
from llm_lab.statistics.services.trend import get_analysis_trends
from llm_lab.statistics.services.trend import get_recent_activity

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
