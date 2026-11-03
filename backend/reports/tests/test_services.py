"""Reports app: service tests (generators, report service, summaries)."""

from __future__ import annotations

import time

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports import services
from backend.reports.models import Report
from backend.reports.services import generators
from backend.reports.tests.helpers import cell_run
from backend.reports.tests.helpers import evaluate
from backend.reports.tests.helpers import make_cell
from backend.reports.tests.helpers import make_condition
from backend.reports.tests.helpers import make_run
from backend.reports.tests.helpers import make_study
from backend.reports.tests.helpers import make_unit
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = choices.RunStatus


def _run_with_findings(user, model, task, severities, *, code="x = 1\n" * 100, **kwargs):
    run = make_run(user, model, task, code=code, **kwargs)
    analysis = AnalysisRunFactory(
        created_by=user,
        generation_job=run,
        source_code={},
        status=AnalysisRun.Status.COMPLETED,
    )
    result = ToolResultFactory(run=analysis, tool_slug="bandit")
    for sev in severities:
        Finding.objects.create(result=result, severity=sev, title="f", file_path="app.py")
    return run


def test_generate_model_analysis_returns_aggregates():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    task = factories.TaskSpecVersionFactory.create()
    for _ in range(2):
        make_run(user, model, task, duration_seconds=10.0, code="x = 1\ny = 2\n")
    make_run(user, model, task, status=S.FAILED, code=None)
    data = generators.generate_model_analysis({"model_id": model.model_id})
    assert data["model"]["model_id"] == model.model_id
    assert data["generation"]["total_runs"] == 3
    assert data["generation"]["completed_runs"] == 2
    assert data["generation"]["failed_runs"] == 1
    assert data["generation"]["by_outcome"] == {"tested": 2, "infra_error": 1}
    assert data["loc"]["total_loc"] == 4
    # No run was analyzed: the counts are unknown, not zero findings.
    assert data["runs_analyzed"] == 0
    assert data["total_findings"] is None
    assert data["reported_levels"] is None
    assert data["severity_normalized_heuristic"] is None
    assert data["issues_dedup"]["runs"] == 0
    assert data["issues_dedup"]["code_unique"] is None


def test_generate_model_analysis_counts_only_the_latest_result_per_run():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    run = make_run(user, model, factories.TaskSpecVersionFactory.create())

    def analysis_with_findings(n):
        analysis = AnalysisRunFactory(
            created_by=user,
            generation_job=run,
            source_code={},
            status=AnalysisRun.Status.COMPLETED,
        )
        result = ToolResultFactory(run=analysis, tool_slug="bandit", metrics={"score": n})
        for _ in range(n):
            Finding.objects.create(result=result, severity="low", title="f", file_path="app.py")
        return analysis

    analysis_with_findings(10)  # stale earlier analysis of the same code
    analysis_with_findings(3)  # latest

    data = generators.generate_model_analysis({"model_id": model.model_id})
    # Re-analyzing a run must not double-count: only the latest result per tool counts.
    assert data["total_findings"] == 3
    assert data["tools"] == [{"analyzer": "bandit", "tasks": 1}]
    assert data["metrics_by_tool"]["bandit"]["numeric"]["score"]["avg"] == 3


def test_task_comparison_stats_mean_and_stdev():
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    model = LLMModelFactory(model_id="m1")
    # Two trials: 2 vs 4 findings (all high) -> mean 3, stdev sqrt(2).
    _run_with_findings(user, model, task, ["high"] * 2, metrics={"cost_usd": 0.01}, duration_seconds=100)
    _run_with_findings(user, model, task, ["high"] * 4, metrics={"cost_usd": 0.03}, duration_seconds=200)

    data = generators.generate_task_comparison({"task_slug": task.task.slug})
    stats = data["models"][0]["stats"]
    assert stats["trials"] == 2
    assert stats["analyzed"] == 2
    assert stats["findings_total"] == {"mean": 3.0, "stdev": pytest.approx(1.4142, abs=1e-3), "n": 2}
    assert stats["critical_high_normalized"]["mean"] == 3.0
    # weighted: high=5 -> 10 and 20 -> mean 15
    assert stats["weighted_score"]["mean"] == 15.0
    assert stats["cost"]["total"] == pytest.approx(0.04)
    assert stats["duration"]["mean"] == 150.0


def test_task_comparison_groups_by_model_and_treatment():
    """Runs under different treatments (stack, prompts, harness) must not be pooled into one row."""
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    model = LLMModelFactory(model_id="m1")
    _run_with_findings(user, model, task, ["high"], treatment_hash="a" * 64)
    _run_with_findings(user, model, task, ["high"] * 3, treatment_hash="b" * 64)

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    assert data["total_models"] == 2
    assert set(data["treatment_hashes"]) == {"a" * 64, "b" * 64}
    assert data["mixed_treatments"] is True
    assert {row["treatment_hash"] for row in data["models"]} == {"a" * 64, "b" * 64}
    assert all(row["model_id"] == "m1" for row in data["models"])

    one = generators.generate_task_comparison({"task_slug": task.task.slug, "treatment_hash": "a" * 64})
    assert one["total_models"] == 1
    assert one["models"][0]["treatment_hash"] == "a" * 64
    assert one["mixed_treatments"] is False


def test_task_comparison_prompt_hash_filter_isolates_one_version():
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    model = LLMModelFactory(model_id="m1")
    _run_with_findings(user, model, task, ["high"], prompt_hash="a" * 64)
    _run_with_findings(user, model, task, ["high"] * 3, prompt_hash="b" * 64)

    data = generators.generate_task_comparison({"task_slug": task.task.slug, "prompt_hash": "a" * 64})

    assert data["total_models"] == 1
    assert data["models"][0]["stats"]["trials"] == 1


def test_task_comparison_spans_task_versions_unless_one_is_chosen():
    user = UserFactory()
    v1 = factories.TaskSpecVersionFactory.create()
    v2 = factories.TaskSpecVersionFactory.create(task=v1.task)
    model = LLMModelFactory(model_id="m1")
    _run_with_findings(user, model, v1, ["high"])
    _run_with_findings(user, model, v2, ["high"])

    data = generators.generate_task_comparison({"task_slug": v1.task.slug})
    assert data["task"]["versions"] == [v1.version, v2.version]
    assert data["models"][0]["stats"]["trials"] == 2
    only = generators.generate_task_comparison({"task_slug": v1.task.slug, "task_version": v2.version})
    assert only["models"][0]["stats"]["trials"] == 1


def test_condition_stats_reports_mixed_prompt_hashes():
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    model = LLMModelFactory(model_id="m1")
    a = _run_with_findings(user, model, task, ["high"], prompt_hash="a" * 64)
    b = _run_with_findings(user, model, task, ["high"], prompt_hash="b" * 64)

    stats = generators._condition_stats(GenerationRun.objects.filter(id__in=[a.id, b.id]))
    assert stats["prompt_hashes"] == ["a" * 64, "b" * 64]
    assert stats["mixed_prompt_versions"] is True

    single = generators._condition_stats(GenerationRun.objects.filter(id=a.id))
    assert single["prompt_hashes"] == ["a" * 64]
    assert single["mixed_prompt_versions"] is False


def test_task_comparison_functional_rollup():
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    model = LLMModelFactory(model_id="m1")
    evaluate(_run_with_findings(user, model, task, []), passed=4, failed=0)
    evaluate(_run_with_findings(user, model, task, []), passed=2, failed=2)

    data = generators.generate_task_comparison({"task_slug": task.task.slug})
    functional = data["models"][0]["stats"]["functional"]
    assert functional["runs_measured"] == 2
    assert functional["runs_all_passed"] == 1
    assert functional["pass_rate"]["mean"] == 0.75


def test_task_comparison_splits_ai_findings_from_static():
    from backend.analysis.tests.factories import AnalyzerToolFactory

    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    model = LLMModelFactory(model_id="m1")
    run = _run_with_findings(user, model, task, ["high"] * 2)
    analysis = AnalysisRun.objects.get(generation_job=run)
    ai_result = ToolResultFactory(run=analysis, tool_slug="llm_review")
    for _ in range(5):
        Finding.objects.create(result=ai_result, severity="critical", title="opinion", file_path="app.py")

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    row = data["models"][0]
    # The normalized split is labelled as such; the tools printed no level (unrated).
    assert row["severity_normalized_heuristic"] == {"critical": 0, "high": 2, "medium": 0, "low": 0, "info": 0}
    assert row["reported_levels"] == {"unrated": 2}
    assert row["total_findings"] == 2
    assert row["ai_severity_normalized_heuristic"] == {"critical": 5, "high": 0, "medium": 0, "low": 0, "info": 0}
    assert row["ai_reported_levels"] == {"unrated": 5}
    assert row["ai_total"] == 5
    assert "findings" not in row
    stats = row["stats"]
    assert stats["findings_total"]["mean"] == 2.0
    assert stats["critical_high_normalized"]["mean"] == 2.0  # AI criticals not counted
    assert stats["ai_findings_total"]["mean"] == 5.0
    assert stats["ai_critical_high_normalized"]["mean"] == 5.0


def test_task_comparison_includes_weight_sensitivity():
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    # crit-heavy vs low-heavy model on 1000 LOC each: baseline densities 30 vs
    # 25 per KLOC rank low/m first; under "flat" (crit weight 10 -> 1) the
    # densities become 3 vs 25 and the order flips.
    code = "x = 1\n" * 1000
    _run_with_findings(user, LLMModelFactory(model_id="crit/m"), task, ["critical"] * 3, code=code)
    _run_with_findings(user, LLMModelFactory(model_id="low/m"), task, ["low"] * 25, code=code)

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    sensitivity = data["sensitivity"]
    assert sensitivity["models_evaluated"] == 2
    assert [r["model_id"] for r in sensitivity["baseline_ranking"]] == ["low/m", "crit/m"]
    schemes = {s["scheme"]: s for s in sensitivity["schemes"]}
    assert set(schemes) == {"security_heavy", "flat", "info_included"}
    for s in schemes.values():
        assert -1.0 <= s["kendall_tau"] <= 1.0
    assert schemes["flat"]["kendall_tau"] == -1.0
    assert schemes["flat"]["adjacent_swaps"] == [("low/m", "crit/m")]


def test_generate_model_analysis_missing_id_raises():
    with pytest.raises(ValueError, match="model_id"):
        generators.generate_model_analysis({})


def test_generate_model_analysis_unknown_model_raises():
    with pytest.raises(ValueError, match="not found"):
        generators.generate_model_analysis({"model_id": "x/none"})


def test_generate_task_comparison():
    user = UserFactory()
    task = factories.TaskSpecVersionFactory.create()
    m1 = LLMModelFactory(model_id="m1")
    for _ in range(2):
        make_run(user, m1, task)
    make_run(user, LLMModelFactory(model_id="m2"), task, status=S.FAILED)

    data = generators.generate_task_comparison({"task_slug": task.task.slug})
    assert data["task"]["slug"] == task.task.slug
    assert data["total_models"] == 2
    with pytest.raises(ValueError, match="task_slug required"):
        generators.generate_task_comparison({})
    with pytest.raises(ValueError, match="Task not found"):
        generators.generate_task_comparison({"task_slug": "no-such-task"})


def test_generate_tool_analysis_empty_is_unmeasured():
    data = generators.generate_tool_analysis({})
    # Nothing executed: unmeasured, not zero findings.
    assert data["total_findings"] is None
    assert data["tools"] == []
    assert data["metrics_by_tool"] == {}


def test_generate_tool_analysis_aggregates_metrics():
    user = UserFactory()
    for avg, dist in ((3.0, {"A": 4, "B": 1}), (5.0, {"A": 2, "F": 1})):
        ToolResultFactory(
            run=AnalysisRunFactory(created_by=user),
            tool_slug="radon",
            metrics={
                "average_complexity": avg,
                "rank_distribution": dist,
                "top_functions": [{"name": "f", "complexity": 9}],
            },
        )

    data = generators.generate_tool_analysis({})
    agg = data["metrics_by_tool"]["radon"]
    assert agg["results_with_metrics"] == 2
    assert agg["numeric"]["average_complexity"] == {"avg": 4.0, "min": 3.0, "max": 5.0}
    assert agg["distributions"]["rank_distribution"] == {"A": 6, "B": 1, "F": 1}
    # Lists such as top_functions don't aggregate meaningfully and are skipped.
    assert "top_functions" not in agg["numeric"]


def test_generate_generation_analytics_includes_summary_and_by_model():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    for _ in range(3):
        make_run(user, model)
    data = generators.generate_generation_analytics({"days": 7})
    assert data["window_days"] == 7
    assert data["summary"]["total_runs"] >= 3
    assert data["by_kind"] == {"app_agentic": 3}
    assert any(row["model_id"] == "m1" for row in data["by_model"])


def test_generate_comprehensive_returns_platform_metrics():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    make_run(user, LLMModelFactory(model_id="m2"), status=S.PENDING, code=None)
    data = generators.generate_comprehensive({"days": 30})
    assert "generation_analytics" in data
    assert "tool_analysis" in data
    assert data["platform"]["catalog_models"] >= 2
    assert data["platform"]["models_with_runs"] == 1


def test_study_conditions_report_requires_an_owned_study():
    owner, stranger = UserFactory(), UserFactory()
    study, _plan = make_study(owner)
    config = {"study_id": str(study.id)}

    with pytest.raises(ValueError, match="study_id required"):
        generators.generate_study_conditions_report({})
    with pytest.raises(ValueError, match="Study not found"):
        generators.generate_study_conditions_report({"study_id": "00000000-0000-0000-0000-000000000000"}, user=owner)
    with pytest.raises(ValueError, match="Study not found"):
        generators.generate_study_conditions_report(config, user=stranger)
    with pytest.raises(ValueError, match="Study not found"):
        generators.generate_study_conditions_report(config)  # no user: nothing is visible
    assert generators.generate_study_conditions_report(config, user=owner)["study"]["id"] == str(study.id)
    staff = UserFactory(is_staff=True)
    assert generators.generate_study_conditions_report(config, user=staff)["study"]["id"] == str(study.id)


def test_study_conditions_report_per_condition_stats():
    user = UserFactory()
    study, plan = make_study(user)
    unit = make_unit(plan)
    cond_a = make_condition(plan, LLMModelFactory(model_id="m-a"), is_baseline=True)
    cond_b = make_condition(plan, LLMModelFactory(model_id="m-b"))
    cell_run(make_cell(cond_a, unit), user)
    cell_run(make_cell(cond_b, unit), user)

    data = generators.generate_study_conditions_report({"study_id": str(study.id)}, user=user)

    assert data["study"]["id"] == str(study.id)
    assert data["total_conditions"] == 2
    by_condition = {row["condition_id"]: row for row in data["conditions"]}
    assert by_condition[cond_a.id]["model_id"] == "m-a"
    assert by_condition[cond_a.id]["stats"]["trials"] == 1
    assert by_condition[cond_b.id]["model_id"] == "m-b"
    assert by_condition[cond_b.id]["stats"]["trials"] == 1
    assert "comparisons" not in data


def test_create_and_dispatch_refuses_another_users_study_report():
    study, _plan = make_study(UserFactory())
    with pytest.raises(ValueError, match="Study not found"):
        services.create_and_dispatch(
            report_type="experiment_report",
            config={"study_id": str(study.id)},
            user=UserFactory(),
        )
    with pytest.raises(ValueError, match="Study not found"):
        services.create_and_dispatch(
            report_type="study_report",
            config={"study_id": str(study.id)},
            user=UserFactory(),
        )
    assert not Report.objects.exists()


def test_create_and_run_generates_synchronously_with_the_owner():
    owner = UserFactory()
    study, _plan = make_study(owner)
    report = services.report_service.create_and_run(
        report_type="experiment_report",
        config={"study_id": str(study.id)},
        user=owner,
    )
    assert report.status == Report.Status.COMPLETED
    assert report.report_data["study"]["id"] == str(study.id)
    assert report.summary["study_id"] == str(study.id)
    assert report.title == f"Study conditions: {study.id}"


def test_study_conditions_report_registered_in_generators():
    assert generators.GENERATORS["experiment_report"] is generators.generate_study_conditions_report
    assert generators.GENERATORS["template_comparison"] is generators.generate_task_comparison


@pytest.mark.django_db(transaction=True)
def test_create_and_dispatch_generates_report():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    make_run(user, LLMModelFactory(model_id="m2"))

    report = services.create_and_dispatch(
        report_type="generation_analytics",
        config={"days": 30},
        user=user,
    )
    # Wait for the daemon thread to finish (max ~5s).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        report.refresh_from_db()
        if report.status in (Report.Status.COMPLETED, Report.Status.FAILED):
            break
        time.sleep(0.1)

    assert report.status == Report.Status.COMPLETED, report.error_message
    assert report.report_data["summary"]["total_runs"] >= 1
    assert report.summary["window_days"] == 30


def test_create_and_dispatch_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown report_type"):
        services.create_and_dispatch(report_type="nope", config={})


def test_list_reports_filters_by_user():
    u1 = UserFactory()
    u2 = UserFactory()
    Report.objects.create(report_type="comprehensive", title="A", config={}, created_by=u1)
    Report.objects.create(report_type="comprehensive", title="B", config={}, created_by=u2)
    qs, total = services.list_reports(user=u1)
    assert total == 1
    assert next(iter(qs)).title == "A"


def test_source_loc_ignores_lockfiles_manifests_and_docs():
    from backend.reports.services.loc import loc_from_files

    files = {
        "app.py": "import os\n\n# comment\nprint(1)\n",
        "requirements.txt": "flask\n",
        "README.md": "# App\ntext\n",
        "frontend/package.json": '{\n "name": "x"\n}\n',
        "frontend/package-lock.json": "{\n" * 5000,
        "frontend/src/App.jsx": "export default 1;\n// c\n",
        "static/app.js": "let a = 1;\n",
        "frontend/dist/vendor.min.js": "x\n" * 100,
    }
    assert loc_from_files(files) == {"backend_loc": 2, "frontend_loc": 2, "total_loc": 4, "source_files": 3}


def test_study_conditions_markdown_keeps_paired_rows_and_notes():
    from backend.reports.services.renderers import render_markdown

    data = {
        "study": {"name": "E", "status": "completed", "repeats": 2},
        "conditions": [
            {
                "condition_id": 1,
                "label": "Base",
                "is_baseline": True,
                "issues_dedup": {"apps": 2, "code_unique": 3},
                "generation": {},
            },
            {"condition_id": 2, "label": "Other", "issues_dedup": {"apps": 2, "code_unique": 5}, "generation": {}},
        ],
        "results": {
            "leaderboard": [{"rank": 1, "label": "Base", "pass_rate_mean": 0.9}],
            "paired": {
                "status": "confirmatory",
                "correction": "holm",
                "alpha": 0.05,
                "rows": [
                    {"condition_id": 2, "n_pairs": 6, "mean_delta": -0.1, "wilcoxon_p": 0.03, "p_adjusted": 0.06},
                ],
            },
            "notes": ["Every graded cell scored 1"],
        },
        "notes": ["Finding counts use only the 1 tool(s) that measured every analyzed run"],
    }
    report = Report(report_type=Report.Type.STUDY_CONDITIONS, title="T", report_data=data)
    md = render_markdown(report)
    assert "## Paired vs baseline (confirmatory, two-sided)" in md
    assert "| Other | 6 | -0.1 |" in md
    assert "p (holm)" in md
    assert "Every graded cell scored 1" in md
    assert "Finding counts use only" in md
    assert "| Baseline | Base |" in md


def test_create_and_dispatch_starts_the_thread_only_after_commit(django_capture_on_commit_callbacks):
    from unittest import mock

    user = UserFactory()
    with mock.patch("backend.reports.services.report_service._dispatch") as dispatch:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            report = services.create_and_dispatch(report_type="generation_analytics", config={}, user=user)
        # Started before the commit, the thread could not see the row and the report would stay pending.
        dispatch.assert_not_called()
        assert len(callbacks) == 1
        callbacks[0]()
        dispatch.assert_called_once_with(report.id)


def test_reap_stale_reports_fails_reports_whose_thread_died():
    from datetime import timedelta

    from django.utils import timezone

    from backend.reports.services import report_service

    now = timezone.now()

    def report(status, age):
        r = Report.objects.create(report_type="comprehensive", title=f"{status} {age}", config={}, status=status)
        Report.objects.filter(id=r.id).update(created_at=now - age)
        return r

    stuck_pending = report(Report.Status.PENDING, timedelta(minutes=11))
    fresh_pending = report(Report.Status.PENDING, timedelta(minutes=1))
    stuck_generating = report(Report.Status.GENERATING, timedelta(hours=2))
    busy_generating = report(Report.Status.GENERATING, timedelta(minutes=20))
    done = report(Report.Status.COMPLETED, timedelta(days=3))

    assert report_service.reap_stale_reports(now) == 2
    statuses = {r.id: Report.objects.get(id=r.id).status for r in (stuck_pending, fresh_pending, stuck_generating)}
    assert statuses[stuck_pending.id] == Report.Status.FAILED
    assert statuses[stuck_generating.id] == Report.Status.FAILED
    assert statuses[fresh_pending.id] == Report.Status.PENDING
    assert Report.objects.get(id=busy_generating.id).status == Report.Status.GENERATING
    assert Report.objects.get(id=done.id).status == Report.Status.COMPLETED
    assert Report.objects.get(id=stuck_pending.id).error_message.startswith("Interrupted")


def _summary(leaderboard):
    from backend.reports.services.report_service import _make_summary

    return _make_summary("experiment_report", {"results": {"leaderboard": leaderboard}, "total_conditions": 3})


def test_study_conditions_summary_names_no_leader_on_a_tie():
    tied = _summary(
        [
            {"label": "A", "pass_rate_mean": 1.0},
            {"label": "B", "pass_rate_mean": 1.0},
            {"label": "C", "pass_rate_mean": 0.5},
        ],
    )
    assert (tied["leader"], tied["leader_pass_rate"], tied["tied_leaders"]) == (None, 1.0, 2)

    clear = _summary([{"label": "A", "pass_rate_mean": 0.9}, {"label": "B", "pass_rate_mean": 0.5}])
    assert (clear["leader"], clear["leader_pass_rate"], clear["tied_leaders"]) == ("A", 0.9, 0)

    ungraded = _summary([{"label": "A", "pass_rate_mean": None}, {"label": "B", "pass_rate_mean": None}])
    assert (ungraded["leader"], ungraded["leader_pass_rate"]) == (None, None)


def test_tool_analysis_rejects_an_unknown_tool():
    from backend.analysis.tests.factories import AnalyzerToolFactory

    with pytest.raises(ValueError, match="Unknown tool"):
        services.create_and_dispatch(report_type="tool_analysis", config={"tool_name": "osv"}, user=UserFactory())
    AnalyzerToolFactory(slug="osv-scanner")
    report = services.create_and_dispatch(
        report_type="tool_analysis",
        config={"tool_name": "osv-scanner"},
        user=UserFactory(),
    )
    assert report.title == "Tool analysis: osv-scanner"


def test_list_reports_ends_reports_whose_thread_died():
    from datetime import timedelta

    from django.utils import timezone

    user = UserFactory()
    stuck = Report.objects.create(report_type="comprehensive", title="stuck", config={}, created_by=user)
    Report.objects.filter(id=stuck.id).update(created_at=timezone.now() - timedelta(minutes=11))

    qs, total = services.list_reports(user=user)

    assert total == 1
    listed = next(iter(qs))
    assert listed.status == Report.Status.FAILED
    assert listed.error_message.startswith("Interrupted")


def test_model_analysis_summary_keeps_unmeasured_values_unset():
    from backend.reports.services.report_service import _make_summary

    model = LLMModelFactory(model_id="openai/never-analyzed")
    make_run(UserFactory(), model, status=S.PENDING, code=None)

    summary = _make_summary("model_analysis", generators.generate_model_analysis({"model_id": model.model_id}))

    assert summary["total_runs"] == 1
    # Nothing finished, nothing analyzed, no tree: unknown, not 0% / zero findings / zero LOC.
    assert (summary["success_rate"], summary["runs_analyzed"], summary["total_findings"]) == (None, 0, None)
    assert summary["total_loc"] is None


def _study_summary(ranking):
    from backend.reports.services.report_service import _make_summary

    return _make_summary("study_report", {"ranking": ranking, "funnel": {"overall": {"runs": 6}}})


def test_study_report_summary_names_no_leader_on_a_tie():
    # Competition ranks from mcdm.rank_models: a three-way ceiling tie all rank 1.
    tied = _study_summary(
        {
            "models_tied": True,
            "ranking": [
                {"model": "deepseek", "topsis_rank": 1},
                {"model": "glm", "topsis_rank": 1},
                {"model": "qwen", "topsis_rank": 1},
            ],
        },
    )
    assert (tied["leader"], tied["tied_leaders"], tied["runs"]) == (None, 3, 6)

    # A tie below the top does not take the leader away.
    clear = _study_summary(
        {
            "models_tied": True,
            "ranking": [
                {"model": "deepseek", "topsis_rank": 1},
                {"model": "glm", "topsis_rank": 2},
                {"model": "qwen", "topsis_rank": 2},
            ],
        },
    )
    assert (clear["leader"], clear["tied_leaders"]) == ("deepseek", 0)

    empty = _study_summary({"ranking": []})
    assert (empty["leader"], empty["tied_leaders"], empty["unique_issues"]) == (None, 0, None)


def test_study_report_summary_from_rank_models_ties():
    from backend.statistics.services.mcdm import Criterion
    from backend.statistics.services.mcdm import rank_models

    criteria = (
        Criterion("deploy_rate", "Deploy rate", "benefit", 0.5),
        Criterion("pass_rate", "Pass rate", "benefit", 0.5),
    )
    ceiling = {m: {"deploy_rate": 1.0, "pass_rate": 1.0} for m in ("deepseek", "glm", "qwen")}

    summary = _study_summary(rank_models(ceiling, criteria, draws=10))

    assert (summary["leader"], summary["tied_leaders"]) == (None, 3)
