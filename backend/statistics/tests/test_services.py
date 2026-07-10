"""Tests for statistics aggregation services and API."""

from __future__ import annotations

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics import services
from backend.users.tests.factories import UserFactory

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

    task = AnalysisRunFactory(
        created_by=user,
        generation_job=jobs[0],
        status=AnalysisRun.Status.COMPLETED,
        duration_seconds=12.5,
    )
    task.completed_at = timezone.now()
    task.save(update_fields=["completed_at"])

    bandit = ToolResultFactory(
        run=task,
        tool_slug="bandit",
        category="security",
        status=ToolResult.Status.COMPLETED,
    )
    eslint = ToolResultFactory(
        run=task,
        tool_slug="eslint",
        category="lint",
        status=ToolResult.Status.COMPLETED,
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


def test_get_severity_distribution_splits_ai_from_static():
    user = UserFactory()
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.COMPLETED)
    static_result = ToolResultFactory(run=run, tool_slug="bandit", category="security")
    ai_result = ToolResultFactory(run=run, tool_slug="llm_review", category="ai")
    FindingFactory.create_batch(3, result=static_result, severity=Finding.Severity.HIGH)
    FindingFactory.create_batch(2, result=ai_result, severity=Finding.Severity.HIGH)

    payload = services.get_severity_distribution(user)

    assert payload["total"] == 5
    assert payload["by_source"]["static"]["high"] == 3
    assert payload["by_source"]["ai"]["high"] == 2


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


def test_get_model_comparison_uses_shared_rankings_scoring():
    from backend.rankings.services import aggregate_rankings

    user = UserFactory()
    model = _setup_data(user)

    rows = services.get_model_comparison(user, limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "GPT-4o"
    assert row["apps"] == 4
    assert row["apps_completed"] == 3
    assert row["success_rate"] == 75.0
    assert 0 <= row["mss"] <= 1
    assert row["findings"]["critical"] == 2
    assert row["ai_findings"] == {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    # no lines_of_code in metrics → no empirical measurement, composite == mss
    assert row["empirical_quality"] is None
    assert row["composite"] == row["mss"]
    # the same model on /rankings must show the same numbers
    ranking = next(r for r in aggregate_rankings(user=user) if r["model_id"] == model.model_id)
    assert row["mss"] == ranking["mss_score"]
    assert row["composite"] == ranking["composite_score"]
    assert row["findings"] == ranking["findings"]


def test_get_model_comparison_prompt_hash_filter_scopes_apps():
    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        prompt_hash="hash-a",
    )
    GenerationJobFactory.create_batch(
        5,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        prompt_hash="hash-b",
    )

    all_rows = services.get_model_comparison(user, limit=10)
    scoped_rows = services.get_model_comparison(user, limit=10, prompt_hash="hash-a")

    assert next(r for r in all_rows if r["model_id"] == model.model_id)["apps"] == 7
    assert next(r for r in scoped_rows if r["model_id"] == model.model_id)["apps"] == 2


def test_get_model_comparison_experiment_id_filter_scopes_apps():
    from backend.generation.tests.factories import ExperimentFactory

    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    experiment = ExperimentFactory(created_by=user)
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        experiment=experiment,
    )
    GenerationJobFactory.create_batch(
        5,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
    )

    all_rows = services.get_model_comparison(user, limit=10)
    scoped_rows = services.get_model_comparison(user, limit=10, experiment_id=str(experiment.id))

    assert next(r for r in all_rows if r["model_id"] == model.model_id)["apps"] == 7
    assert next(r for r in scoped_rows if r["model_id"] == model.model_id)["apps"] == 2


def test_get_model_comparison_excludes_ai_findings():
    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    job = GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        metrics={"lines_of_code": 1000},
    )
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    static_result = ToolResultFactory(run=run, tool_slug="bandit", category="security")
    ai_result = ToolResultFactory(run=run, tool_slug="llm_review", category="ai")
    FindingFactory.create_batch(2, result=static_result, severity=Finding.Severity.HIGH)
    FindingFactory.create_batch(5, result=ai_result, severity=Finding.Severity.CRITICAL)

    rows = services.get_model_comparison(user, limit=10)

    row = rows[0]
    assert row["findings"] == {"critical": 0, "high": 2, "medium": 0, "low": 0, "info": 0}
    assert row["ai_findings"] == {"critical": 5, "high": 0, "medium": 0, "low": 0, "info": 0}
    assert row["empirical_quality"] is not None
    assert row["n_trials"] == 1


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


def test_get_analyzer_health_uses_catalog():
    from django.core.cache import cache

    from backend.analysis.models import AnalyzerTool

    cache.clear()
    # Clear the auto-seeded catalog (rolled back with the test transaction)
    # so the exact-count assertions below hold; non-seeded slugs so the
    # factory's django_get_or_create doesn't reuse seeded rows.
    AnalyzerTool.objects.all().delete()
    AnalyzerToolFactory(slug="health-a", category="security", is_enabled=True)
    AnalyzerToolFactory(slug="health-b", category="lint", is_enabled=False)

    health = services.get_analyzer_health()

    assert health["total"] == 2
    assert health["online"] == 1
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
