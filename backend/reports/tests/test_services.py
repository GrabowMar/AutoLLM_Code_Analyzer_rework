"""Reports app — service tests."""

from __future__ import annotations

import time

import pytest

from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports import services
from backend.reports.models import Report
from backend.reports.services import generators
from backend.reports.services import loc as loc_mod
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_loc_from_job_counts_non_blank_non_comment():
    user = UserFactory()
    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "backend_code": "import os\n# comment\n\nprint('hi')\n",
            "frontend_code": "import React from 'react';\n// comment\n\nReact.foo();\n",
        },
    )
    out = loc_mod.loc_from_job(job)
    assert out["backend_loc"] == 2
    assert out["frontend_loc"] == 2
    assert out["total_loc"] == 4


def test_loc_for_jobs_aggregates():
    user = UserFactory()
    j1 = GenerationJobFactory(
        created_by=user,
        result_data={"backend_code": "a\nb\n", "frontend_code": "x\n"},
    )
    j2 = GenerationJobFactory(
        created_by=user,
        result_data={"backend_code": "c\nd\ne\n"},
    )
    out = loc_mod.loc_for_jobs(GenerationJob.objects.filter(id__in=[j1.id, j2.id]))
    assert out["total_loc"] == 6
    assert out["counted"] == 2
    assert len(out["per_job"]) == 2


def test_generate_model_analysis_returns_aggregates():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        duration_seconds=10.0,
        result_data={"backend_code": "x\ny\n"},
    )
    GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.FAILED,
    )
    data = generators.generate_model_analysis({"model_id": model.model_id})
    assert data["model"]["model_id"] == model.model_id
    assert data["generation"]["total_jobs"] == 3
    assert data["generation"]["completed_jobs"] == 2
    assert data["generation"]["failed_jobs"] == 1
    assert data["loc"]["total_loc"] == 4
    assert data["findings"] == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }


def test_generate_model_analysis_counts_only_latest_run_per_job():
    from backend.analysis.models import AnalysisRun
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    job = GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        result_data={"backend_code": "x\n"},
    )

    def run_with_findings(n, **kwargs):
        run = AnalysisRunFactory(
            created_by=user,
            generation_job=job,
            status=AnalysisRun.Status.COMPLETED,
            **kwargs,
        )
        result = ToolResultFactory(run=run, tool_slug="bandit", metrics={"score": n})
        from backend.analysis.models import Finding

        for _ in range(n):
            Finding.objects.create(result=result, severity="low", title="f")
        return run

    run_with_findings(10)  # stale earlier analysis of the same code
    run_with_findings(3)  # latest

    data = generators.generate_model_analysis({"model_id": model.model_id})
    # Re-analyzing a job must not double-count: only the latest run counts.
    assert data["total_findings"] == 3
    assert data["tools"] == [{"analyzer": "bandit", "tasks": 1}]
    assert data["metrics_by_tool"]["bandit"]["numeric"]["score"]["avg"] == 3


def test_job_summary_reports_truncated_jobs():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/x")
    GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        result_data={"backend_code": "x\n", "frontend_truncated": True},
    )
    GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        result_data={"backend_code": "x\n"},
    )
    data = generators.generate_model_analysis({"model_id": model.model_id})
    assert data["generation"]["truncated_jobs"] == 1


def _job_with_run_findings(user, model, template, severities, *, result_data=None, metrics=None):
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

    job = GenerationJobFactory(
        created_by=user,
        model=model,
        app_requirement=template,
        status=GenerationJob.Status.COMPLETED,
        result_data=result_data or {"backend_code": "x\n" * 100},
        metrics=metrics or {},
    )
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=run, tool_slug="bandit")
    for sev in severities:
        Finding.objects.create(result=result, severity=sev, title="f")
    return job


def test_template_comparison_stats_mean_and_stdev():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    # Two trials: 2 vs 4 findings (all high) → mean 3, stdev sqrt(2).
    _job_with_run_findings(user, model, template, ["high"] * 2, metrics={"cost": 0.01, "total_duration": 100})
    _job_with_run_findings(user, model, template, ["high"] * 4, metrics={"cost": 0.03, "total_duration": 200})

    data = generators.generate_template_comparison({"template_slug": template.slug})
    stats = data["models"][0]["stats"]
    assert stats["trials"] == 2
    assert stats["analyzed"] == 2
    assert stats["findings_total"] == {"mean": 3.0, "stdev": pytest.approx(1.4142, abs=1e-3), "n": 2}
    assert stats["critical_high"]["mean"] == 3.0
    # weighted: high=5 → 10 and 20 → mean 15
    assert stats["weighted_score"]["mean"] == 15.0
    assert stats["cost"]["total"] == pytest.approx(0.04)
    assert stats["duration"]["mean"] == 150.0


def test_template_comparison_excludes_truncated_trials_by_default():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _job_with_run_findings(user, model, template, ["high"])
    _job_with_run_findings(
        user,
        model,
        template,
        ["high"] * 9,
        result_data={"backend_code": "x\n", "frontend_truncated": True},
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})
    stats = data["models"][0]["stats"]
    assert stats["trials"] == 1
    assert stats["truncated_excluded"] == 1
    assert stats["findings_total"]["mean"] == 1.0

    included = generators.generate_template_comparison(
        {"template_slug": template.slug, "exclude_truncated": False},
    )
    assert included["models"][0]["stats"]["trials"] == 2


def test_template_comparison_functional_rollup():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _job_with_run_findings(
        user,
        model,
        template,
        [],
        metrics={"functional": {"passed": True, "pass_rate": 1.0}},
    )
    _job_with_run_findings(
        user,
        model,
        template,
        [],
        metrics={"functional": {"passed": False, "pass_rate": 0.5}},
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})
    functional = data["models"][0]["stats"]["functional"]
    assert functional["jobs_probed"] == 2
    assert functional["jobs_passed"] == 1
    assert functional["pass_rate"]["mean"] == 0.75


def test_template_comparison_splits_ai_findings_from_static():
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalyzerToolFactory
    from backend.analysis.tests.factories import ToolResultFactory

    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    job = _job_with_run_findings(user, model, template, ["high"] * 2)
    run = AnalysisRun.objects.get(generation_job=job)
    ai_result = ToolResultFactory(run=run, tool_slug="llm_review")
    for _ in range(5):
        Finding.objects.create(result=ai_result, severity="critical", title="opinion")

    data = generators.generate_template_comparison({"template_slug": template.slug})

    row = data["models"][0]
    assert row["findings"] == {"critical": 0, "high": 2, "medium": 0, "low": 0, "info": 0}
    assert row["total_findings"] == 2
    assert row["ai_findings"] == {"critical": 5, "high": 0, "medium": 0, "low": 0, "info": 0}
    assert row["ai_total"] == 5
    stats = row["stats"]
    assert stats["findings_total"]["mean"] == 2.0
    assert stats["critical_high"]["mean"] == 2.0  # AI criticals not counted
    assert stats["ai_findings_total"]["mean"] == 5.0
    assert stats["ai_critical_high"]["mean"] == 5.0


def test_template_comparison_includes_weight_sensitivity():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    # crit-heavy vs low-heavy model on 1000 LOC each: baseline densities 30 vs
    # 25 per KLOC rank low/m first; under "flat" (crit weight 10 → 1) the
    # densities become 3 vs 25 and the order flips.
    code = {"backend_code": "x\n" * 1000}
    _job_with_run_findings(user, LLMModelFactory(model_id="crit/m"), template, ["critical"] * 3, result_data=code)
    _job_with_run_findings(user, LLMModelFactory(model_id="low/m"), template, ["low"] * 25, result_data=code)

    data = generators.generate_template_comparison({"template_slug": template.slug})

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


def test_generate_template_comparison():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    m1 = LLMModelFactory(model_id="m1")
    m2 = LLMModelFactory(model_id="m2")
    GenerationJobFactory.create_batch(
        2,
        created_by=user,
        model=m1,
        app_requirement=template,
        status=GenerationJob.Status.COMPLETED,
    )
    GenerationJobFactory(
        created_by=user,
        model=m2,
        app_requirement=template,
        status=GenerationJob.Status.FAILED,
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})
    assert data["template"]["slug"] == template.slug
    assert data["total_models"] == 2


def test_generate_tool_analysis_empty_returns_zero():
    data = generators.generate_tool_analysis({})
    assert data["total_findings"] == 0
    assert data["tools"] == []
    assert data["metrics_by_tool"] == {}


def test_generate_tool_analysis_aggregates_metrics():
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

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
    GenerationJobFactory.create_batch(
        3,
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
    )
    data = generators.generate_generation_analytics({"days": 7})
    assert data["window_days"] == 7
    assert data["summary"]["total_jobs"] >= 3
    assert any(row["model_id"] == "m1" for row in data["by_model"])


def test_generate_comprehensive_returns_platform_metrics():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    GenerationJobFactory(
        created_by=user,
        model=LLMModelFactory(model_id="m2"),
    )
    data = generators.generate_comprehensive({"days": 30})
    assert "generation_analytics" in data
    assert "tool_analysis" in data
    assert data["platform"]["total_models"] >= 2


@pytest.mark.django_db(transaction=True)
def test_create_and_dispatch_generates_report():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    GenerationJobFactory(
        created_by=user,
        model=LLMModelFactory(model_id="m2"),
        status=GenerationJob.Status.COMPLETED,
    )

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
    assert report.report_data["summary"]["total_jobs"] >= 1
    assert report.summary["window_days"] == 30


def test_create_and_dispatch_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown report_type"):
        services.create_and_dispatch(report_type="nope", config={})


def test_list_reports_filters_by_user():
    u1 = UserFactory()
    u2 = UserFactory()
    Report.objects.create(
        report_type="comprehensive",
        title="A",
        config={},
        created_by=u1,
    )
    Report.objects.create(
        report_type="comprehensive",
        title="B",
        config={},
        created_by=u2,
    )
    qs, total = services.list_reports(user=u1)
    assert total == 1
    assert next(iter(qs)).title == "A"
