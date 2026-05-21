"""Tests for statistics aggregation services and API."""

from __future__ import annotations

import pytest
from django.utils import timezone

from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.tests.factories import AnalysisResultFactory
from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.analysis.tests.factories import FindingFactory
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.llm_models.tests.factories import LLMModelFactory
from llm_lab.statistics import services
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _setup_data(user):
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    jobs = GenerationJobFactory.create_batch(
        3,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        duration_seconds=42.0,
        metrics={"total_tokens": 1500, "cost": 0.025},
        result_data={"backend": "x = 1\n", "frontend": "y = 2\n"},
    )
    GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.FAILED,
    )

    task = AnalysisTaskFactory(
        created_by=user,
        generation_job=jobs[0],
        status=AnalysisTask.Status.COMPLETED,
        duration_seconds=12.5,
    )
    task.completed_at = timezone.now()
    task.save(update_fields=["completed_at"])

    bandit = AnalysisResultFactory(
        task=task,
        analyzer_name="bandit",
        analyzer_type=AnalysisResult.AnalyzerType.STATIC,
        status=AnalysisResult.Status.COMPLETED,
    )
    eslint = AnalysisResultFactory(
        task=task,
        analyzer_name="eslint",
        analyzer_type=AnalysisResult.AnalyzerType.STATIC,
        status=AnalysisResult.Status.COMPLETED,
    )

    FindingFactory.create_batch(
        2,
        result=bandit,
        severity=Finding.Severity.CRITICAL,
        rule_id="B301",
    )
    FindingFactory.create_batch(
        3,
        result=bandit,
        severity=Finding.Severity.HIGH,
        rule_id="B105",
    )
    FindingFactory.create_batch(
        4,
        result=eslint,
        severity=Finding.Severity.MEDIUM,
        rule_id="no-unused-vars",
    )
    return model


def test_get_system_overview_aggregates_counts():
    user = UserFactory()
    _setup_data(user)

    overview = services.get_system_overview(user)

    assert overview["total_apps"] == 4
    assert overview["apps_completed"] == 3
    assert overview["apps_failed"] == 1
    assert overview["apps_success_rate"] == 75.0
    assert overview["total_analyses"] == 1
    assert overview["analyses_completed"] == 1
    assert overview["analyses_success_rate"] == 100.0
    assert overview["total_findings"] == 9
    assert overview["models_in_use"] == 1


def test_get_severity_distribution_returns_all_buckets():
    user = UserFactory()
    _setup_data(user)

    payload = services.get_severity_distribution(user)

    assert payload["total"] == 9
    severities = {b["severity"]: b["count"] for b in payload["distribution"]}
    assert severities["critical"] == 2
    assert severities["high"] == 3
    assert severities["medium"] == 4
    assert severities["low"] == 0
    assert severities["info"] == 0


def test_get_analysis_trends_includes_today():
    user = UserFactory()
    _setup_data(user)

    trends = services.get_analysis_trends(7, user)

    assert trends["days"] == 7
    assert len(trends["series"]) == 7
    assert trends["total"] >= 1
    # Last entry is today.
    last = trends["series"][-1]
    assert last["total"] >= 1


def test_get_model_comparison_scores_present():
    user = UserFactory()
    _setup_data(user)

    rows = services.get_model_comparison(user, limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "GPT-4o"
    assert row["apps"] == 4
    assert row["apps_completed"] == 3
    assert 0 <= row["security"] <= 10
    assert 0 <= row["performance"] <= 100
    assert "findings" in row
    assert row["findings"]["critical"] == 2


def test_get_tool_effectiveness_per_analyzer():
    user = UserFactory()
    _setup_data(user)

    tools = services.get_tool_effectiveness(user)

    by_name = {t["name"]: t for t in tools}
    assert by_name["bandit"]["scans"] == 1
    assert by_name["bandit"]["findings"] == 5
    assert by_name["bandit"]["avg_per_scan"] == 5.0
    assert by_name["bandit"]["top_rule"] in {"B105", "B301"}
    assert by_name["eslint"]["findings"] == 4


def test_get_top_findings_grouped_by_title():
    user = UserFactory()
    _setup_data(user)

    rows = services.get_top_findings(5, user)

    assert len(rows) <= 5
    assert all("title" in r and "count" in r for r in rows)


def test_get_recent_activity_orders_desc_and_limits():
    user = UserFactory()
    _setup_data(user)

    items = services.get_recent_activity(50, user)

    assert items
    timestamps = [i["created_at"] for i in items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_get_code_generation_stats_sums_metrics():
    user = UserFactory()
    _setup_data(user)

    stats = services.get_code_generation_stats(user)

    assert stats["total_apps"] == 4
    assert stats["completed"] == 3
    assert stats["total_tokens"] == 1500 * 3
    assert stats["total_cost_usd"] == round(0.025 * 3, 4)
    assert stats["total_lines_of_code"] >= 6


def test_get_analyzer_health_uses_registry():
    health = services.get_analyzer_health()

    assert "total" in health
    assert health["total"] >= 1
    assert health["online"] + health["offline"] == health["total"]


def test_get_dashboard_returns_all_sections():
    user = UserFactory()
    _setup_data(user)

    payload = services.get_dashboard(user)

    expected_keys = {
        "overview",
        "severity",
        "trends",
        "models",
        "tools",
        "top_findings",
        "code_generation",
        "analyzer_health",
        "recent_activity",
    }
    assert expected_keys <= set(payload.keys())


def test_get_system_overview_is_user_scoped():
    alice = UserFactory()
    bob = UserFactory()
    _setup_data(alice)

    overview_bob = services.get_system_overview(bob)
    assert overview_bob["total_apps"] == 0
    assert overview_bob["total_findings"] == 0
