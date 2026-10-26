"""Tests for statistics aggregation services (deduplicated to canonical measured runs)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.tests.measured import make_run
from backend.rankings.tests.measured import sev
from backend.statistics import services
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _setup_data(user):
    """4 jobs (3 completed, 1 failed); job 0 analysed twice (stale + canonical run)."""
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    jobs = GenerationJobFactory.create_batch(
        3,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        duration_seconds=42.0,
        metrics={"total_tokens": 1500, "cost": 0.025, "lines_of_code": 99999},
        result_data={"backend": "x = 1\n", "frontend": "y = 2\n"},
    )
    GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.FAILED)

    stale = make_run(jobs[0], findings={"critical": 50}, tools={"bandit": "succeeded", "eslint": "succeeded"})
    AnalysisRun.objects.filter(id=stale.id).update(created_at=timezone.now() - timedelta(hours=1))
    run = make_run(
        jobs[0],
        findings={"critical": 2, "high": 3, "medium": 4},
        ai={"high": 1},
        measured_sloc=800,
        total_sloc=1200,
        tools={"bandit": "succeeded", "eslint": "succeeded", "semgrep": "failed"},
    )
    AnalysisRun.objects.filter(id=run.id).update(duration_seconds=12.5)
    bandit = run.results.get(tool_slug="bandit")
    eslint = run.results.get(tool_slug="eslint")
    FindingFactory.create_batch(2, result=bandit, severity=Finding.Severity.CRITICAL, rule_id="B301")
    FindingFactory.create_batch(3, result=bandit, severity=Finding.Severity.HIGH, rule_id="B105")
    FindingFactory.create_batch(4, result=eslint, severity=Finding.Severity.MEDIUM, rule_id="no-unused-vars")
    # Findings of the stale run never reach the dashboard.
    FindingFactory.create_batch(5, result=stale.results.get(tool_slug="bandit"), rule_id="STALE")
    return model, run


def test_get_system_overview_aggregates_counts():
    user = UserFactory()
    _setup_data(user)

    overview = services.get_system_overview(user)

    assert overview["total_apps"] == 4
    assert overview["apps_completed"] == 3
    assert overview["apps_failed"] == 1
    assert overview["apps_success_rate"] == 75.0
    assert overview["total_analyses"] == 2
    assert overview["analyses_completed"] == 2
    assert overview["analyses_success_rate"] == 100.0
    # canonical run only (2 + 3 + 4), the stale run's 50 are not added
    assert overview["total_findings"] == 9
    assert overview["total_ai_findings"] == 1
    assert overview["measured_jobs"] == 1
    assert overview["models_in_use"] == 1


def test_overview_success_rate_ignores_unfinished_and_canary_runs():
    user = UserFactory()
    make_run(None, user=user)
    make_run(None, user=user, status=AnalysisRun.Status.PARTIAL, is_complete=False)
    make_run(None, user=user, status=AnalysisRun.Status.RUNNING)
    make_run(None, user=user, purpose=AnalysisRun.Purpose.CANARY)

    overview = services.get_system_overview(user)

    assert overview["total_analyses"] == 3
    assert overview["analyses_partial"] == 1
    assert overview["analyses_running"] == 1
    assert overview["analyses_success_rate"] == 50.0


def test_get_severity_distribution_reads_canonical_measurements():
    user = UserFactory()
    _setup_data(user)

    payload = services.get_severity_distribution(user)

    assert payload["total"] == 10  # 9 static + 1 AI consensus
    severities = {b["severity"]: b["count"] for b in payload["distribution"]}
    assert severities == {"critical": 2, "high": 4, "medium": 4, "low": 0, "info": 0}
    assert payload["by_source"]["static"] == sev(critical=2, high=3, medium=4)
    assert payload["by_source"]["ai"] == sev(high=1)


def test_get_analysis_trends_includes_today():
    user = UserFactory()
    _setup_data(user)

    trends = services.get_analysis_trends(7, user)

    assert trends["days"] == 7
    assert len(trends["series"]) == 7
    assert trends["total"] >= 1
    last = trends["series"][-1]
    assert last["total"] >= 1


def test_get_model_comparison_uses_shared_rankings_scoring():
    from backend.rankings.services import aggregate_rankings

    user = UserFactory()
    model, _ = _setup_data(user)

    rows = services.get_model_comparison(user, limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "GPT-4o"
    assert row["apps"] == 4
    assert row["apps_completed"] == 3
    assert row["success_rate"] == 75.0
    assert 0 <= row["mss"] <= 1
    assert row["findings"] == sev(critical=2, high=3, medium=4)
    assert row["ai_findings"] == sev(high=1)
    assert row["n_trials"] == 1
    assert row["measured_sloc"] == 800
    assert row["empirical_quality"] is not None
    # the same model on /rankings must show the same numbers
    ranking = next(r for r in aggregate_rankings(user=user) if r["model_id"] == model.model_id)
    assert row["mss"] == ranking["mss_score"]
    assert row["composite"] == ranking["composite_score"]
    assert row["findings"] == ranking["findings"]


def test_get_model_comparison_unmeasured_model_has_no_empirical_score():
    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)

    row = services.get_model_comparison(user, limit=10)[0]

    assert row["empirical_quality"] is None
    assert row["composite"] == row["mss"]
    assert row["n_trials"] == 0


def test_get_model_comparison_filters_scope_apps():
    from backend.generation.tests.factories import ExperimentFactory

    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    experiment = ExperimentFactory(created_by=user)
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        prompt_hash="hash-a",
        experiment=experiment,
    )
    GenerationJobFactory.create_batch(
        5,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        prompt_hash="hash-b",
    )

    def apps(**filters):
        return next(r for r in services.get_model_comparison(user, limit=10, **filters))["apps"]

    assert apps() == 7
    assert apps(prompt_hash="hash-a") == 2
    assert apps(experiment_id=str(experiment.id)) == 2


def test_get_tool_effectiveness_counts_only_succeeded_scans():
    user = UserFactory()
    _setup_data(user)

    tools = {t["name"]: t for t in services.get_tool_effectiveness(user)}

    assert tools["bandit"]["scans"] == 1  # canonical run only
    assert tools["bandit"]["findings"] == 5
    assert tools["bandit"]["avg_per_scan"] == 5.0
    assert tools["bandit"]["top_rule"] == "B105"
    assert tools["bandit"]["versions"] == ["1.2.3"]
    assert tools["eslint"]["findings"] == 4
    # a failed tool is reported as failed, not as a clean scan
    assert tools["semgrep"]["scans"] == 0
    assert tools["semgrep"]["failed"] == 1
    assert tools["semgrep"]["success_rate"] == 0.0
    # never scanned: no average, not "finds nothing"
    assert tools["semgrep"]["avg_per_scan"] is None
    assert tools["semgrep"]["findings"] == 0


def test_get_tool_effectiveness_is_user_scoped():
    me, other = UserFactory(), UserFactory()
    _setup_data(other)

    assert services.get_tool_effectiveness(me) == []


def test_get_top_findings_grouped_by_rule_in_canonical_runs():
    user = UserFactory()
    _setup_data(user)

    rows = services.get_top_findings(5, user)

    assert rows[0] == {
        "title": rows[0]["title"],
        "severity": "medium",
        "count": 4,
        "rule_id": "no-unused-vars",
        "tool": "eslint",
        "is_ai": False,
    }
    assert "STALE" not in {r["rule_id"] for r in rows}


def test_get_recent_activity_orders_desc_and_limits():
    user = UserFactory()
    _setup_data(user)

    items = services.get_recent_activity(50, user)

    assert items
    timestamps = [i["created_at"] for i in items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_get_code_generation_stats_uses_measured_sloc_not_raw_lines():
    user = UserFactory()
    _setup_data(user)

    stats = services.get_code_generation_stats(user)

    assert stats["total_apps"] == 4
    assert stats["completed"] == 3
    assert stats["total_tokens"] == 1500 * 3
    assert stats["total_cost_usd"] == round(0.025 * 3, 4)
    # raw metrics.lines_of_code (lockfiles included) is ignored
    assert stats["total_lines_of_code"] == 1200
    assert stats["measured_lines_of_code"] == 800
    assert stats["loc_jobs_covered"] == 1


def _health_catalog():
    from backend.analysis.models import AnalyzerTool

    # Clear the auto-seeded catalog (rolled back with the test transaction);
    # non-seeded slugs so the factory's django_get_or_create doesn't reuse rows.
    AnalyzerTool.objects.all().delete()
    AnalyzerToolFactory(slug="health-a", category="security", is_enabled=True)
    AnalyzerToolFactory(slug="health-b", category="lint", is_enabled=True)
    AnalyzerToolFactory(slug="health-c", category="lint", is_enabled=False)


def test_analyzer_health_without_selftest_reports_nothing_online():
    _health_catalog()

    health = services.get_analyzer_health()

    assert health["total"] == 3
    assert health["online"] == 0
    assert health["selftest"] is None
    statuses = {a["name"]: a["status"] for a in health["analyzers"]}
    assert statuses == {"health-a": "untested", "health-b": "untested", "health-c": "disabled"}


def test_analyzer_health_follows_the_latest_canary(monkeypatch):
    from backend.analysis.engine import canary

    _health_catalog()
    asked = []
    verdicts = {
        "health-a": {"passed": True, "status": "succeeded"},
        "health-b": {"passed": False, "status": "succeeded", "missing": [{"rule": "X1"}]},
    }

    def latest(image_tag=None):
        asked.append(image_tag)
        return {"passed": False, "run_id": "r-1", "image_tag": image_tag, "tools": {}}

    monkeypatch.setattr(canary, "latest", latest)
    # each tool's newest verdict, which may come from an older, partial self-test
    monkeypatch.setattr(canary, "latest_per_tool", lambda image_tag=None: verdicts, raising=False)

    health = services.get_analyzer_health()

    assert health["online"] == 1
    assert health["offline"] == 2
    rows = {a["name"]: a for a in health["analyzers"]}
    assert rows["health-a"]["available"] is True
    assert rows["health-b"]["status"] == "failed"
    assert "missed 1 expected" in rows["health-b"]["message"]
    # only self-tests of the current analyzer image vouch for it
    assert asked == ["llmlab/analyzer:test"]
    assert health["selftest"]["image_tag"] == "llmlab/analyzer:test"
    assert health["by_type"]["lint"] == {"online": 0, "total": 2}


def test_analyzer_health_survives_an_unreadable_selftest(monkeypatch):
    from backend.analysis.engine import canary

    _health_catalog()

    def boom(image_tag=None):
        msg = "db down"
        raise RuntimeError(msg)

    monkeypatch.setattr(canary, "latest", boom)

    health = services.get_analyzer_health()

    assert health["online"] == 0
    assert health["selftest"] is None


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
    assert overview_bob["total_findings"] is None  # nothing measured: no data


def test_inline_runs_and_failed_tools_never_inflate_findings():
    user = UserFactory()
    # An ad-hoc inline run is not a job measurement; its findings stay out.
    inline = AnalysisRunFactory(created_by=user)
    FindingFactory.create_batch(
        7,
        result=inline.results.create(tool_slug="bandit", status=ToolResult.Status.SUCCEEDED),
    )

    assert services.get_system_overview(user)["total_findings"] is None
    assert services.get_severity_distribution(user)["total"] == 0
    assert services.get_severity_distribution(user)["static_measured_jobs"] == 0


def test_tool_effectiveness_counts_failures_of_runs_that_failed_as_a_whole():
    """A run is FAILED exactly when no tool succeeded: its tool failures must still count."""
    user = UserFactory()
    model = LLMModelFactory()
    ok = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    make_run(ok, findings={"high": 1})
    for _ in range(3):
        job = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
        make_run(job, status=AnalysisRun.Status.FAILED, is_complete=False, tools={"bandit": "failed"})
    # A cancelled run says nothing about the tool; it is not counted.
    cancelled = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    make_run(cancelled, status=AnalysisRun.Status.CANCELLED, is_complete=False, tools={"bandit": "cancelled"})

    bandit = {t["name"]: t for t in services.get_tool_effectiveness(user)}["bandit"]

    assert bandit["scans"] == 1
    assert bandit["failed"] == 3
    assert bandit["runs"] == 4
    assert bandit["success_rate"] == 0.25


def test_tool_effectiveness_uses_each_jobs_latest_finished_or_failed_run():
    user = UserFactory()
    job = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    earlier = make_run(job, findings={"high": 2})
    AnalysisRun.objects.filter(id=earlier.id).update(created_at=timezone.now() - timedelta(hours=1))
    make_run(job, status=AnalysisRun.Status.FAILED, is_complete=False, tools={"bandit": "failed"})

    bandit = {t["name"]: t for t in services.get_tool_effectiveness(user)}["bandit"]

    assert bandit["runs"] == 1  # one run per job, never double counted
    assert bandit["failed"] == 1
    assert bandit["success_rate"] == 0.0


def test_ai_only_re_run_never_hides_the_full_static_measurement():
    """A later narrower re-analysis (AI-only review) must not zero the dashboard totals."""
    user = UserFactory()
    job = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    full = make_run(job, findings={"high": 5})
    bandit = full.results.get(tool_slug="bandit")
    FindingFactory.create_batch(5, result=bandit, severity=Finding.Severity.HIGH, rule_id="B105")
    AnalysisRun.objects.filter(id=full.id).update(created_at=timezone.now() - timedelta(hours=1))
    review = make_run(
        job,
        plan_hash="c" * 64,
        tools={"ai_review": "succeeded"},
        static_available=False,
        ai={"medium": 2},
    )
    FindingFactory.create_batch(
        2,
        result=review.results.get(tool_slug="ai_review"),
        severity=Finding.Severity.MEDIUM,
        rule_id="ai-sqli",
        is_ai=True,
    )

    overview = services.get_system_overview(user)
    severity = services.get_severity_distribution(user)
    top = {(r["tool"], r["rule_id"]): r["count"] for r in services.get_top_findings(10, user)}

    assert overview["total_findings"] == 5
    assert overview["total_ai_findings"] == 2
    assert overview["measured_jobs"] == 1
    assert overview["static_measured_jobs"] == 1
    assert overview["ai_reviewed_jobs"] == 1
    assert severity["by_source"]["static"] == sev(high=5)
    assert severity["by_source"]["ai"] == sev(medium=2)
    assert top[("bandit", "B105")] == 5
    assert top[("ai_review", "ai-sqli")] == 2


def test_no_static_measurement_is_visible_in_the_overview():
    user = UserFactory()
    job = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    make_run(job, tools={"ai_review": "succeeded"}, static_available=False, ai={"low": 1})

    overview = services.get_system_overview(user)

    assert overview["measured_jobs"] == 1
    assert overview["static_measured_jobs"] == 0  # 0 findings over 0 jobs: no data, not clean
    assert overview["total_findings"] is None
    assert overview["total_ai_findings"] == 1
    assert overview["ai_reviewed_jobs"] == 1


def test_overview_totals_are_null_when_nothing_was_measured():
    """No measured job: both totals are missing data (None), never a clean-looking 0."""
    user = UserFactory()
    GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)

    overview = services.get_system_overview(user)

    assert overview["measured_jobs"] == 0
    assert overview["total_findings"] is None
    assert overview["total_ai_findings"] is None


def test_overview_ai_total_is_null_without_a_successful_review():
    """A static-only plan, or a failed AI review, is no AI data; a reviewed job with 0 opinions is 0."""
    user = UserFactory()
    static_only = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    make_run(static_only, findings={"high": 2})
    failed_review = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    make_run(failed_review, findings={"low": 1}, ai={}, ai_available=False)

    overview = services.get_system_overview(user)
    severity = services.get_severity_distribution(user)

    assert overview["total_findings"] == 3
    assert overview["ai_reviewed_jobs"] == 0
    assert overview["total_ai_findings"] is None
    assert severity["static_measured_jobs"] == 2
    assert severity["ai_reviewed_jobs"] == 0

    reviewed = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    make_run(reviewed, findings={}, ai={})
    from django.core.cache import cache

    cache.clear()
    overview = services.get_system_overview(user)
    assert overview["ai_reviewed_jobs"] == 1
    assert overview["total_ai_findings"] == 0  # reviewed, no consensus opinion: a measured zero


def test_model_rows_carry_their_denominators_like_rankings_rows():
    """n_static / n_ai say which trials the zero-filled severities cover (as on /rankings)."""
    from backend.rankings.services import aggregate_rankings

    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    measured = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    make_run(measured, findings={"high": 2}, ai={"low": 1}, functional_pass_rate=0.5)
    no_static = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    make_run(no_static, tools={"bandit": "failed"}, static_available=False)

    row = services.get_model_comparison(user, limit=10)[0]
    ranking = next(r for r in aggregate_rankings(user=user) if r["model_id"] == model.model_id)

    assert row["n_trials"] == 2
    assert row["n_static"] == 1
    assert row["n_ai"] == 1
    assert (row["n_trials"], row["n_static"], row["n_ai"]) == (
        ranking["n_trials"],
        ranking["n_static"],
        ranking["n_ai"],
    )
    assert row["findings"] == sev(high=2)
    assert row["functional_pass_rate"] == row["smoke_pass_rate"] == 0.5
    assert row["functional_n"] == 1


def test_model_rows_without_static_measurement_report_n_static_zero():
    user = UserFactory()
    model = LLMModelFactory(provider="OpenAI", model_name="GPT-4o")
    job = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    make_run(job, tools={"bandit": "failed"}, static_available=False)

    row = services.get_model_comparison(user, limit=10)[0]

    assert row["n_trials"] == 1
    assert row["n_static"] == 0  # the zero-filled findings are not a measurement
    assert row["n_ai"] == 0
    assert row["empirical_quality"] is None


def test_tool_card_counts_raw_and_counted_findings_of_scans_only():
    user = UserFactory()
    job = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    run = make_run(job, findings={"high": 1}, tools={"bandit": "succeeded", "hadolint": "not_applicable"})
    bandit = run.results.get(tool_slug="bandit")
    FindingFactory(result=bandit, severity=Finding.Severity.HIGH, rule_id="B105", counted=True)
    FindingFactory.create_batch(2, result=bandit, severity=Finding.Severity.LOW, rule_id="B101", counted=False)

    tools = {t["name"]: t for t in services.get_tool_effectiveness(user)}

    assert tools["bandit"]["scans"] == 1
    assert tools["bandit"]["findings"] == 3  # raw tool output
    assert tools["bandit"]["counted_findings"] == 1
    assert tools["bandit"]["avg_per_scan"] == 3.0
    assert tools["bandit"]["top_rule"] == "B101"
    # nothing in scope: neither a scan nor a failure, and no average
    assert tools["hadolint"]["scans"] == 0
    assert tools["hadolint"]["not_applicable"] == 1
    assert tools["hadolint"]["avg_per_scan"] is None
    assert tools["hadolint"]["success_rate"] is None
