"""Composite dashboard payload combining all statistics modules."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from llm_lab.statistics.services.aggregations import get_analyzer_health
from llm_lab.statistics.services.aggregations import get_code_generation_stats
from llm_lab.statistics.services.aggregations import get_model_comparison
from llm_lab.statistics.services.aggregations import get_severity_distribution
from llm_lab.statistics.services.aggregations import get_system_overview
from llm_lab.statistics.services.aggregations import get_tool_effectiveness
from llm_lab.statistics.services.aggregations import get_top_findings
from llm_lab.statistics.services.trend import get_analysis_trends
from llm_lab.statistics.services.trend import get_recent_activity

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def get_dashboard(user: AbstractBaseUser | None = None) -> dict[str, Any]:
    """Single payload powering the Statistics page in one round-trip."""

    return {
        "overview": get_system_overview(user),
        "severity": get_severity_distribution(user),
        "trends": get_analysis_trends(7, user),
        "models": get_model_comparison(user, limit=10),
        "tools": get_tool_effectiveness(user),
        "top_findings": get_top_findings(5, user),
        "code_generation": get_code_generation_stats(user),
        "analyzer_health": get_analyzer_health(),
        "recent_activity": get_recent_activity(15, user),
    }
