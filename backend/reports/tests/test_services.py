"""Reports app — service tests (generators read canonical measured runs)."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.tests.measured import PLAN_A
from backend.rankings.tests.measured import PLAN_B
from backend.rankings.tests.measured import make_run
from backend.rankings.tests.measured import measurement_json
from backend.rankings.tests.measured import sev
from backend.reports import services
from backend.reports.models import Report
from backend.reports.services import generators
from backend.reports.services import loc as loc_mod
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _job(user, model=None, template=None, **kwargs):
    kwargs.setdefault("status", GenerationJob.Status.COMPLETED)
    kwargs.setdefault("result_data", {"backend_code": "x = 1\n" * 100})
    return GenerationJobFactory(created_by=user, model=model, app_requirement=template, **kwargs)


def _measured_job(user, model, template=None, *, job_kwargs=None, **measure):
    job = _job(user, model, template, **(job_kwargs or {}))
    make_run(job, **measure)
    return job


# ── loc ───────────────────────────────────────────────────────────────────────


def test_loc_from_job_uses_the_sample_resolver_and_skips_lockfiles():
    user = UserFactory()
    job = _job(
        user,
        result_data={
            "files": {
                "app.py": "import os\n\nprint('hi')\n",
                "frontend/src/App.jsx": "export default function App() {}\nconst x = 1;\n",
                "frontend/package-lock.json": "{\n" * 3000,
            },
        },
    )

    out = loc_mod.loc_from_job(job)

    assert out == {"backend_loc": 2, "frontend_loc": 2, "total_loc": 4, "measured_loc": 4}


def test_loc_from_job_unresolvable_is_missing_not_zero():
    user = UserFactory()
    job = _job(user, status=GenerationJob.Status.FAILED, result_data={})

    out = loc_mod.loc_from_job(job)

    assert out["total_loc"] is None
    assert out["unavailable"] == "job_not_completed"


def test_loc_for_jobs_aggregates_resolvable_jobs_only():
    user = UserFactory()
    j1 = _job(user, result_data={"backend_code": "a\nb\n", "frontend_code": "x\n"})
    j2 = _job(user, result_data={"backend_code": "c\nd\ne\n"})
    j3 = _job(user, status=GenerationJob.Status.FAILED, result_data={})

    out = loc_mod.loc_for_jobs(GenerationJob.objects.filter(id__in=[j1.id, j2.id, j3.id]))

    assert out["total_loc"] == 6
    assert out["counted"] == 2
    assert out["unavailable"] == 1
    assert len(out["per_job"]) == 3


def test_loc_for_jobs_with_nothing_resolvable_is_none():
    user = UserFactory()
    job = _job(user, status=GenerationJob.Status.FAILED, result_data={})

    out = loc_mod.loc_for_jobs([job])

    assert out["total_loc"] is None
    assert out["counted"] == 0


# ── model_analysis ────────────────────────────────────────────────────────────


def test_generate_model_analysis_without_analyses_reports_missing_not_zero():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o", model_name="GPT-4o")
    for _ in range(2):
        _job(user, model, duration_seconds=10.0)
    _job(user, model, status=GenerationJob.Status.FAILED)

    data = generators.generate_model_analysis({"model_id": model.model_id})

    assert data["schema_version"] == generators.REPORT_SCHEMA_VERSION
    assert data["model"]["model_id"] == model.model_id
    assert data["generation"]["total_jobs"] == 3
    assert data["generation"]["completed_jobs"] == 2
    assert data["generation"]["failed_jobs"] == 1
    assert data["generation"]["success_rate"] == pytest.approx(0.6667, abs=1e-4)
    assert data["findings"] is None
    assert data["total_findings"] is None
    assert data["loc"]["measured_loc"] is None
    assert data["analysis_coverage"]["not_analysed"] == 2
    assert data["stats"]["findings_total"] == {"mean": None, "median": None, "stdev": None, "n": 0, "n_missing": 2}


def test_generate_model_analysis_counts_only_the_canonical_run_per_job():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    job = _job(user, model)
    stale = make_run(job, findings={"low": 10}, measured_sloc=500)
    AnalysisRun.objects.filter(id=stale.id).update(created_at=timezone.now() - timedelta(hours=1))
    fresh = make_run(job, findings={"low": 3}, measured_sloc=500, total_sloc=700)
    ToolResult.objects.filter(run=fresh).update(metrics={"score": 3})

    data = generators.generate_model_analysis({"model_id": model.model_id})

    # Re-analysing a job must not double-count: only the canonical run counts.
    assert data["total_findings"] == 3
    assert data["findings"] == sev(low=3)
    assert data["loc"]["measured_loc"] == 500
    assert data["loc"]["total_loc"] == 700
    assert data["tools"][0]["analyzer"] == "bandit"
    assert data["tools"][0]["tasks"] == 1
    assert data["tools"][0]["versions"] == ["1.2.3"]
    assert data["metrics_by_tool"]["bandit"]["numeric"]["score"]["avg"] == 3
    assert data["provenance"]["run_ids"] == [str(fresh.id)]
    assert data["provenance"]["plan_hash"] == PLAN_A


def test_failed_tool_metrics_and_statuses_are_not_averaged_in():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/tools")
    job = _job(user, model)
    run = make_run(job, tools={"radon": ToolResult.Status.SUCCEEDED, "jscpd": ToolResult.Status.FAILED})
    ToolResult.objects.filter(run=run).update(metrics={"duplication_percentage": 7.0})

    data = generators.generate_model_analysis({"model_id": model.model_id})

    assert set(data["metrics_by_tool"]) == {"radon"}
    tools = {t["analyzer"]: t for t in data["tools"]}
    assert tools["jscpd"]["succeeded"] == 0
    assert tools["jscpd"]["statuses"] == {"failed": 1}


def test_job_summary_reports_truncated_jobs():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/x")
    _job(user, model, result_data={"backend_code": "x\n", "frontend_truncated": True})
    _job(user, model)

    data = generators.generate_model_analysis({"model_id": model.model_id})

    assert data["generation"]["truncated_jobs"] == 1


def test_generate_model_analysis_is_scoped_to_the_user():
    me, other = UserFactory(), UserFactory()
    model = LLMModelFactory(model_id="m/scoped")
    _measured_job(me, model, findings={"high": 1})
    _measured_job(other, model, findings={"high": 9})

    mine = generators.generate_model_analysis({"model_id": model.model_id}, user=me)
    everyone = generators.generate_model_analysis({"model_id": model.model_id})

    assert mine["total_findings"] == 1
    assert everyone["total_findings"] == 10


# ── template_comparison ───────────────────────────────────────────────────────


def test_template_comparison_stats_mean_and_stdev():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    # Two trials: 2 vs 4 counted static findings (all high) -> mean 3, stdev sqrt(2).
    _measured_job(
        user,
        model,
        template,
        job_kwargs={"metrics": {"cost": 0.01, "total_duration": 100}},
        findings={"high": 2},
    )
    _measured_job(
        user,
        model,
        template,
        job_kwargs={"metrics": {"cost": 0.03, "total_duration": 200}},
        findings={"high": 4},
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})

    stats = data["models"][0]["stats"]
    assert stats["trials"] == 2
    assert stats["analyzed"] == 2
    assert stats["findings_total"]["mean"] == 3.0
    assert stats["findings_total"]["stdev"] == pytest.approx(1.4142, abs=1e-3)
    assert stats["findings_total"]["n"] == 2
    assert stats["critical_high"]["mean"] == 3.0
    # weighted issues: high=5 -> 10 and 20 -> mean 15; per KLOC on 1000 SLOC -> 15
    assert stats["weighted_score"]["mean"] == 15.0
    assert stats["weighted_per_kloc"]["mean"] == 15.0
    assert stats["outcomes"]["static_issues_per_kloc"]["mean"] == 3.0
    assert stats["cost"]["total"] == pytest.approx(0.04)
    assert stats["duration"]["mean"] == 150.0


def test_single_trial_has_no_stdev():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    _measured_job(user, LLMModelFactory(model_id="m1"), template, findings={"high": 2})

    stats = generators.generate_template_comparison({"template_slug": template.slug})["models"][0]["stats"]

    assert stats["findings_total"]["n"] == 1
    assert stats["findings_total"]["stdev"] is None


def test_unanalysed_trials_are_missing_not_zero():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _measured_job(user, model, template, findings={"high": 4})
    _job(user, model, template)  # completed, never analysed

    stats = generators.generate_template_comparison({"template_slug": template.slug})["models"][0]["stats"]

    assert stats["trials"] == 2
    assert stats["analyzed"] == 1
    assert stats["findings_total"]["mean"] == 4.0
    assert stats["findings_total"]["n_missing"] == 1
    assert stats["analysis"]["not_analysed"] == 1


def test_template_comparison_excludes_truncated_trials_by_default():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _measured_job(user, model, template, findings={"high": 1})
    _measured_job(
        user,
        model,
        template,
        job_kwargs={"result_data": {"backend_code": "x\n", "frontend_truncated": True}},
        findings={"high": 9},
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})
    stats = data["models"][0]["stats"]
    assert stats["trials"] == 1
    assert stats["truncated_excluded"] == 1
    assert stats["excluded_reasons"] == {"truncated": 1}
    assert stats["findings_total"]["mean"] == 1.0

    included = generators.generate_template_comparison({"template_slug": template.slug, "exclude_truncated": False})
    assert included["models"][0]["stats"]["trials"] == 2
    assert included["models"][0]["stats"]["findings_total"]["mean"] == 5.0


def test_template_comparison_groups_by_model_and_bundle_key():
    """Jobs from different prompt/bundle versions must not be pooled into one row."""
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _measured_job(user, model, template, job_kwargs={"bundle_key": "bundle-a@1"}, findings={"high": 1})
    _measured_job(user, model, template, job_kwargs={"bundle_key": "bundle-a@2"}, findings={"high": 3})

    data = generators.generate_template_comparison({"template_slug": template.slug})

    assert data["total_models"] == 2
    assert set(data["bundle_keys"]) == {"bundle-a@1", "bundle-a@2"}
    assert data["mixed_bundle_versions"] is True
    assert {row["bundle_key"] for row in data["models"]} == {"bundle-a@1", "bundle-a@2"}


def test_template_comparison_bundle_and_prompt_filters_isolate_one_version():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _measured_job(
        user,
        model,
        template,
        job_kwargs={"bundle_key": "b@1", "prompt_hash": "hash-a"},
        findings={"high": 1},
    )
    _measured_job(
        user,
        model,
        template,
        job_kwargs={"bundle_key": "b@2", "prompt_hash": "hash-b"},
        findings={"high": 3},
    )

    by_bundle = generators.generate_template_comparison({"template_slug": template.slug, "bundle_key": "b@1"})
    by_prompt = generators.generate_template_comparison({"template_slug": template.slug, "prompt_hash": "hash-b"})

    assert by_bundle["total_models"] == 1
    assert by_bundle["models"][0]["bundle_key"] == "b@1"
    assert by_bundle["mixed_bundle_versions"] is False
    assert by_prompt["models"][0]["total_findings"] == 3


def test_experiment_stats_reports_mixed_prompt_hashes():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    job_a = _measured_job(user, model, job_kwargs={"prompt_hash": "hash-a"})
    job_b = _measured_job(user, model, job_kwargs={"prompt_hash": "hash-b"})

    stats = generators._experiment_stats(GenerationJob.objects.filter(id__in=[job_a.id, job_b.id]))

    assert stats["prompt_hashes"] == ["hash-a", "hash-b"]
    assert stats["mixed_prompt_versions"] is True


def _conformance(status, pass_rate, **extra):
    """``metrics.functional`` as the conformance evaluator writes it."""
    return {
        "source": "conformance",
        "status": status,
        "passed": status == "passed",
        "pass_rate": pass_rate,
        "evaluator_version": 3,
        **extra,
    }


def test_functional_rollup_is_none_safe():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    for functional in (
        _conformance("passed", 1.0),
        _conformance("failed", 0.5),
        _conformance("infra_error", None),
    ):
        _measured_job(
            user,
            model,
            template,
            job_kwargs={"metrics": {"functional": functional}},
            functional_pass_rate=functional["pass_rate"],
            functional_status=functional["status"],
        )

    data = generators.generate_template_comparison({"template_slug": template.slug})
    functional = data["models"][0]["stats"]["functional"]

    assert functional["jobs_probed"] == 2
    assert functional["jobs_passed"] == 1
    assert functional["jobs_unknown"] == 1
    assert functional["pass_rate"]["mean"] == 0.75
    assert functional["pass_rate"]["n_missing"] == 1
    assert functional["source"] == "conformance"
    outcome = data["models"][0]["stats"]["outcomes"]["functional_pass_rate"]
    assert outcome["n"] == 2
    assert outcome["n_missing"] == 1


def test_functional_not_applicable_is_missing_not_zero():
    """A job without an app spec stores pass_rate 0.0 with status not_applicable: missing data."""
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    na = _conformance("not_applicable", 0.0)
    _measured_job(
        user,
        model,
        job_kwargs={"metrics": {"functional": na}},
        functional_pass_rate=0.0,
        functional_status="not_applicable",
    )
    _measured_job(
        user,
        model,
        job_kwargs={"metrics": {"functional": _conformance("passed", 1.0)}},
        functional_pass_rate=1.0,
        functional_status="passed",
    )
    # The same two cases on trials that have no measured run (live job record).
    _job(user, model, metrics={"functional": na})
    _job(user, model, metrics={"functional": _conformance("passed", 1.0)})

    data = generators.generate_model_analysis({"model_id": "m1"})
    functional = data["stats"]["functional"]

    assert functional["pass_rate"]["mean"] == 1.0
    assert functional["pass_rate"]["n"] == 2
    assert functional["pass_rate"]["n_missing"] == 2
    assert functional["jobs_unknown"] == 2
    assert functional["by_status"] == {"not_applicable": 2, "passed": 2}
    assert functional["from_measurement"] == 2
    assert functional["from_job"] == 2
    # The measured trials agree with the empirical block (= /rankings).
    assert data["empirical"]["functional_pass_rate"] == 1.0
    assert data["empirical"]["functional_n"] == 1


def test_functional_never_mixes_the_smoke_probe_with_conformance():
    """A smoke-probe result (no source/status) overwriting metrics.functional is another instrument."""
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    smoke = {"passed": True, "health_ok": True, "endpoints_checked": 0, "endpoints_ok": 0, "pass_rate": 1.0}
    evaluation = {
        "status": "failed",
        "summary": {"total": 8, "passed": 2, "failed": 6, "pass_rate": 0.25},
        "evaluator_version": 3,
    }
    # Unmeasured trial: the evaluator's own record survives in result_data.evaluation.
    _job(
        user,
        model,
        metrics={"functional": smoke},
        result_data={"backend_code": "x = 1\n" * 100, "evaluation": evaluation},
    )
    # Unmeasured trial probed only by the smoke test: no conformance result at all.
    _job(user, model, metrics={"functional": smoke})

    data = generators.generate_model_analysis({"model_id": "m1"})
    functional = data["stats"]["functional"]

    assert functional["pass_rate"]["mean"] == 0.25
    assert functional["pass_rate"]["n"] == 1
    assert functional["pass_rate"]["n_missing"] == 1
    assert functional["other_instruments"] == {"smoke": 2}
    assert functional["evaluator_versions"] == ["3"]


def test_measured_trials_read_the_snapshot_like_rankings():
    """A conformance result written after the analysis snapshot does not change the measured trial."""
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    job = _measured_job(user, model)  # snapshot: no functional result yet
    job.metrics = {"functional": _conformance("passed", 1.0)}
    job.save(update_fields=["metrics"])

    data = generators.generate_model_analysis({"model_id": "m1"})

    assert data["stats"]["functional"]["pass_rate"]["mean"] is None
    assert data["stats"]["functional"]["pass_rate"]["n_missing"] == 1
    assert data["empirical"]["functional_pass_rate"] is None
    assert data["provenance"]["functional"]["source"] == "conformance"


def test_functional_snapshot_of_another_instrument_is_not_counted():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    job = _job(user, model)
    measurement = measurement_json(functional_pass_rate=1.0, functional_status="passed")
    measurement["functional"]["source"] = "smoke"
    make_run(job, measurement=measurement)

    data = generators.generate_model_analysis({"model_id": "m1"})

    assert data["stats"]["functional"]["pass_rate"]["n"] == 0
    assert data["stats"]["functional"]["other_instruments"] == {"smoke": 1}
    assert data["empirical"]["functional_pass_rate"] is None


def test_template_comparison_splits_ai_findings_from_static():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    _measured_job(user, model, template, findings={"high": 2}, ai={"critical": 5})

    row = generators.generate_template_comparison({"template_slug": template.slug})["models"][0]

    assert row["findings"] == sev(high=2)
    assert row["total_findings"] == 2
    assert row["ai_findings"] == sev(critical=5)
    assert row["ai_total"] == 5
    stats = row["stats"]
    assert stats["critical_high"]["mean"] == 2.0  # AI criticals not counted
    assert stats["ai_findings_total"]["mean"] == 5.0
    assert stats["ai_critical_high"]["mean"] == 5.0


def test_template_comparison_includes_weight_sensitivity():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    # crit-heavy vs low-heavy model on 1000 measured SLOC each: baseline
    # densities 30 vs 25 rank low/m first; under "flat" they become 3 vs 25.
    _measured_job(user, LLMModelFactory(model_id="crit/m"), template, issues={"critical": 3})
    _measured_job(user, LLMModelFactory(model_id="low/m"), template, issues={"low": 25})

    sensitivity = generators.generate_template_comparison({"template_slug": template.slug})["sensitivity"]

    assert sensitivity["models_evaluated"] == 2
    assert [r["model_id"] for r in sensitivity["baseline_ranking"]] == ["low/m", "crit/m"]
    schemes = {s["scheme"]: s for s in sensitivity["schemes"]}
    assert set(schemes) == {"security_heavy", "flat", "info_included"}
    assert schemes["flat"]["kendall_tau"] == -1.0
    assert schemes["flat"]["adjacent_swaps"] == [("low/m", "crit/m")]


def test_rows_share_one_plan_and_other_plans_are_counted_not_mixed():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    m1, m2 = LLMModelFactory(model_id="m1"), LLMModelFactory(model_id="m2")
    _measured_job(user, m1, template, findings={"high": 1}, plan_hash=PLAN_A)
    _measured_job(user, m1, template, findings={"high": 1}, plan_hash=PLAN_A)
    _measured_job(user, m2, template, findings={"high": 1}, plan_hash=PLAN_A)
    _measured_job(user, m2, template, findings={"high": 50}, plan_hash=PLAN_B)

    data = generators.generate_template_comparison({"template_slug": template.slug})

    rows = {r["model_id"]: r for r in data["models"]}
    assert data["analysis_coverage"]["plan_hash"] == PLAN_A
    assert data["analysis_coverage"]["other_plan"] == 1
    assert data["provenance"]["mixed_plans"] is True
    assert rows["m2"]["total_findings"] == 1
    assert rows["m2"]["stats"]["analysis"]["other_plan"] == 1


def test_generate_model_analysis_missing_id_raises():
    with pytest.raises(ValueError, match="model_id"):
        generators.generate_model_analysis({})


def test_generate_model_analysis_unknown_model_raises():
    with pytest.raises(ValueError, match="not found"):
        generators.generate_model_analysis({"model_id": "x/none"})


def test_generate_template_comparison_rows_for_failed_only_models():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    m1 = LLMModelFactory(model_id="m1")
    m2 = LLMModelFactory(model_id="m2")
    _job(user, m1, template)
    _job(user, m1, template)
    _job(user, m2, template, status=GenerationJob.Status.FAILED)

    data = generators.generate_template_comparison({"template_slug": template.slug})

    assert data["template"]["slug"] == template.slug
    assert data["total_models"] == 2
    failed_row = next(r for r in data["models"] if r["model_id"] == "m2")
    assert failed_row["generation"]["success_rate"] == 0.0
    assert failed_row["stats"]["trials"] == 0


# ── tool_analysis ─────────────────────────────────────────────────────────────


def test_generate_tool_analysis_empty_returns_zero():
    data = generators.generate_tool_analysis({})
    assert data["total_findings"] == 0
    assert data["tools"] == []
    assert data["metrics_by_tool"] == {}


def test_tool_analysis_counts_only_succeeded_executions_as_scans():
    user = UserFactory()
    ok = ToolResultFactory(run=AnalysisRunFactory(created_by=user), tool_slug="bandit", duration_seconds=2.0)
    FindingFactory.create_batch(3, result=ok, severity="high")
    FindingFactory(result=ok, severity="low", counted=False)
    ToolResultFactory(
        run=AnalysisRunFactory(created_by=user),
        tool_slug="bandit",
        status=ToolResult.Status.FAILED,
        failure_reason=ToolResult.FailureReason.PARSE_ERROR,
    )
    ToolResultFactory(
        run=AnalysisRunFactory(created_by=user),
        tool_slug="bandit",
        status=ToolResult.Status.NOT_APPLICABLE,
    )

    data = generators.generate_tool_analysis({})

    bandit = data["tools"][0]
    assert bandit["executions"] == 3
    assert bandit["scans"] == 1
    assert bandit["failed"] == 1
    assert bandit["not_applicable"] == 1
    assert bandit["failure_reasons"] == {"parse_error": 1}
    assert bandit["success_rate"] == 0.5  # 1 of the 2 executions that ran on files
    assert bandit["total"] == 4
    assert bandit["counted"] == 3
    assert bandit["findings_per_scan"] == 4.0
    assert bandit["by_severity"] == sev(high=3, low=1)
    assert bandit["mean_duration_s"] == 2.0


def test_tool_analysis_ignores_canary_runs_and_other_users():
    me, other = UserFactory(), UserFactory()
    ToolResultFactory(run=AnalysisRunFactory(created_by=me, purpose=AnalysisRun.Purpose.CANARY), tool_slug="ruff")
    ToolResultFactory(run=AnalysisRunFactory(created_by=other), tool_slug="ruff")

    data = generators.generate_tool_analysis({}, user=me)

    assert data["tools"] == []
    assert data["total_tasks"] == 0


def test_generate_tool_analysis_aggregates_metrics_of_succeeded_results():
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
    ToolResultFactory(
        run=AnalysisRunFactory(created_by=user),
        tool_slug="radon",
        status=ToolResult.Status.FAILED,
        metrics={"average_complexity": 100.0},
    )

    data = generators.generate_tool_analysis({})
    agg = data["metrics_by_tool"]["radon"]
    assert agg["results_with_metrics"] == 2
    assert agg["numeric"]["average_complexity"] == {"avg": 4.0, "min": 3.0, "max": 5.0}
    assert agg["distributions"]["rank_distribution"] == {"A": 6, "B": 1, "F": 1}
    # Lists such as top_functions don't aggregate meaningfully and are skipped.
    assert "top_functions" not in agg["numeric"]


# ── generation_analytics / comprehensive ──────────────────────────────────────


def test_generate_generation_analytics_includes_summary_and_by_model():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    for _ in range(3):
        _job(user, model)
    _job(user, model, status=GenerationJob.Status.RUNNING)

    data = generators.generate_generation_analytics({"days": 7})

    assert data["window_days"] == 7
    assert data["summary"]["total_jobs"] >= 4
    row = next(row for row in data["by_model"] if row["model_id"] == "m1")
    assert row["jobs"] == 4
    assert row["success_rate"] == 1.0  # the running job is not a failure
    assert data["loc"]["counted"] >= 3


def test_generate_comprehensive_returns_platform_metrics():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    model = LLMModelFactory(model_id="m2")
    _measured_job(user, model, findings={"high": 2}, ai={"low": 1})
    _job(user, model)

    data = generators.generate_comprehensive({"days": 30}, user=user)

    assert "generation_analytics" in data
    assert "tool_analysis" in data
    assert data["platform"]["total_models"] >= 2
    assert data["platform"]["measured_jobs"] == 1
    assert data["platform"]["total_findings"] == 2
    assert data["platform"]["total_ai_findings"] == 1
    assert data["rankings_sensitivity"]["comparability"]["measured"] == 1


# ── experiment_report ─────────────────────────────────────────────────────────


def test_generate_experiment_report_missing_id_raises():
    with pytest.raises(ValueError, match="experiment_id required"):
        generators.generate_experiment_report({})


def test_generate_experiment_report_unknown_id_raises():
    with pytest.raises(ValueError, match="Experiment not found"):
        generators.generate_experiment_report({"experiment_id": "00000000-0000-0000-0000-000000000000"})


def test_generate_experiment_report_malformed_id_raises():
    with pytest.raises(ValueError, match="Experiment not found"):
        generators.generate_experiment_report({"experiment_id": "not-a-uuid"})


def _experiment_with_conditions(user, n_conditions=2):
    from backend.generation.tests.factories import ExperimentConditionFactory
    from backend.generation.tests.factories import ExperimentFactory
    from backend.generation.tests.factories import GenerationProfileFactory

    experiment = ExperimentFactory(created_by=user)
    bundle = GenerationProfileFactory()
    conditions = [
        ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(model_id=f"m-{i}"), profile=bundle)
        for i in range(n_conditions)
    ]
    return experiment, conditions


def test_generate_experiment_report_per_condition_stats_and_deltas():
    user = UserFactory()
    experiment, (cond_a, cond_b) = _experiment_with_conditions(user)
    _measured_job(
        user,
        cond_a.model,
        job_kwargs={"experiment": experiment, "condition": cond_a},
        findings={"high": 1},
    )
    _measured_job(
        user,
        cond_b.model,
        job_kwargs={"experiment": experiment, "condition": cond_b},
        findings={"high": 3},
    )

    data = generators.generate_experiment_report({"experiment_id": str(experiment.id)})

    assert data["experiment"]["id"] == str(experiment.id)
    assert data["total_conditions"] == 2
    by_condition = {row["condition_id"]: row for row in data["conditions"]}
    assert by_condition[cond_a.id]["stats"]["trials"] == 1
    assert by_condition[cond_b.id]["stats"]["trials"] == 1
    assert len(data["comparisons"]) == 1
    delta = data["comparisons"][0]["deltas"]["findings_total"]
    assert delta["a"] == 1.0
    assert delta["b"] == 3.0
    assert delta["delta"] == 2.0
    assert delta["n_a"] == delta["n_b"] == 1


def test_experiment_report_has_no_delta_against_an_unmeasured_condition():
    user = UserFactory()
    experiment, (cond_a, cond_b) = _experiment_with_conditions(user)
    _measured_job(
        user,
        cond_a.model,
        job_kwargs={"experiment": experiment, "condition": cond_a},
        findings={"high": 2},
    )
    # condition B generated but was never analysed: n=0, not "0 findings"
    _job(user, cond_b.model, experiment=experiment, condition=cond_b, metrics={"cost": 0.5})

    data = generators.generate_experiment_report({"experiment_id": str(experiment.id)})

    deltas = data["comparisons"][0]["deltas"]
    assert deltas["findings_total"] is None
    assert deltas["weighted_per_kloc"] is None
    assert deltas["cost"] is None  # side A has no cost data
    by_condition = {row["condition_id"]: row for row in data["conditions"]}
    assert by_condition[cond_b.id]["stats"]["findings_total"]["mean"] is None


def test_experiment_report_is_owner_scoped():
    owner, stranger = UserFactory(), UserFactory()
    experiment, _ = _experiment_with_conditions(owner)

    with pytest.raises(ValueError, match="Experiment not found"):
        generators.generate_experiment_report({"experiment_id": str(experiment.id)}, user=stranger)
    assert generators.generate_experiment_report({"experiment_id": str(experiment.id)}, user=owner)


def test_generate_experiment_report_registered_in_generators():
    assert generators.GENERATORS["experiment_report"] is generators.generate_experiment_report


# ── report service ────────────────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_create_and_dispatch_generates_report():
    user = UserFactory()
    LLMModelFactory(model_id="m1")
    _job(user, LLMModelFactory(model_id="m2"))

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


def test_generate_passes_the_report_owner_to_the_generator():
    from backend.reports.services.report_service import _generate

    owner = UserFactory()
    model = LLMModelFactory(model_id="m/own")
    _measured_job(owner, model, findings={"high": 1})
    _measured_job(UserFactory(), model, findings={"high": 5})
    report = Report.objects.create(
        report_type="model_analysis",
        title="x",
        config={"model_id": model.model_id},
        created_by=owner,
    )

    _generate(report)

    report.refresh_from_db()
    assert report.report_data["total_findings"] == 1
    assert report.summary["total_findings"] == 1
    assert report.summary["measured_jobs"] == 1


def test_create_and_dispatch_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown report_type"):
        services.create_and_dispatch(report_type="nope", config={})


def test_create_and_dispatch_validates_config_before_dispatch():
    with pytest.raises(ValueError, match="experiment_id"):
        services.create_and_dispatch(report_type="experiment_report", config={})
    with pytest.raises(ValueError, match="plan_hash"):
        services.create_and_dispatch(report_type="comprehensive", config={"plan_hash": "short"})
    with pytest.raises(ValueError, match="exclude_truncated"):
        services.create_and_dispatch(report_type="comprehensive", config={"exclude_truncated": "yes"})


def test_fail_stuck_reports_only_touches_old_unfinished_reports():
    stuck = Report.objects.create(report_type="comprehensive", title="A", status=Report.Status.GENERATING)
    fresh = Report.objects.create(report_type="comprehensive", title="B", status=Report.Status.GENERATING)
    done = Report.objects.create(report_type="comprehensive", title="C", status=Report.Status.COMPLETED)
    old = timezone.now() - timedelta(hours=2)
    Report.objects.filter(id__in=[stuck.id, done.id]).update(created_at=old)

    assert services.fail_stuck_reports(60) == 1

    stuck.refresh_from_db()
    fresh.refresh_from_db()
    done.refresh_from_db()
    assert stuck.status == Report.Status.FAILED
    assert fresh.status == Report.Status.GENERATING
    assert done.status == Report.Status.COMPLETED


def _in_flight(title, *, age_minutes=0, status=Report.Status.GENERATING):
    report = Report.objects.create(report_type="comprehensive", title=title, status=status)
    if age_minutes:
        Report.objects.filter(id=report.id).update(created_at=timezone.now() - timedelta(minutes=age_minutes))
    return report


def test_reaper_fails_reports_whose_generating_thread_is_gone():
    from backend.reports.services import report_service

    dead = _in_flight("dead", age_minutes=5)
    dead_pending = _in_flight("never started", age_minutes=5, status=Report.Status.PENDING)
    fresh = _in_flight("just dispatched")  # inside the grace period
    live = _in_flight("still generating", age_minutes=5)
    report_service._hold_liveness_lock(live.id)  # what the generating thread holds
    try:
        assert report_service.is_generating_live(live.id) is True
        assert report_service.is_generating_live(dead.id) is False
        assert services.reap_dead_reports() == 2
    finally:
        report_service._release_liveness_lock(live.id)

    for report in (dead, dead_pending, fresh, live):
        report.refresh_from_db()
    assert dead.status == Report.Status.FAILED
    assert "exited" in dead.error_message
    assert dead_pending.status == Report.Status.FAILED
    assert fresh.status == Report.Status.GENERATING
    assert live.status == Report.Status.GENERATING


def test_maintenance_reaper_never_fails_a_live_report():
    from backend.reports.services import report_service

    live = _in_flight("slow comprehensive", age_minutes=120)
    report_service._hold_liveness_lock(live.id)
    try:
        assert services.fail_stuck_reports(60) == 0
    finally:
        report_service._release_liveness_lock(live.id)
    live.refresh_from_db()
    assert live.status == Report.Status.GENERATING


def test_a_failed_report_is_never_flipped_back_to_completed():
    from backend.reports.services.report_service import _generate

    report = _in_flight("reaped meanwhile")
    Report.objects.filter(id=report.id).update(status=Report.Status.FAILED, error_message="reaped")

    _generate(report)

    report.refresh_from_db()
    assert report.status == Report.Status.FAILED
    assert report.error_message == "reaped"
    assert report.report_data == {}


def test_listing_and_opening_reports_reap_dead_ones(client, monkeypatch):
    from backend.reports.services import report_service

    user = UserFactory()
    client.force_login(user)
    dead = Report.objects.create(
        report_type="comprehensive",
        title="dead",
        status=Report.Status.GENERATING,
        created_by=user,
    )
    Report.objects.filter(id=dead.id).update(created_at=timezone.now() - timedelta(minutes=10))
    monkeypatch.setattr(report_service, "_last_reap", 0.0)

    listed = client.get("/api/reports/").json()

    assert listed["reports"][0]["status"] == Report.Status.FAILED
    # Throttled: a second dead report is left for the next interval...
    other = Report.objects.create(
        report_type="comprehensive",
        title="dead too",
        status=Report.Status.GENERATING,
        created_by=user,
    )
    Report.objects.filter(id=other.id).update(created_at=timezone.now() - timedelta(minutes=10))
    assert client.get(f"/api/reports/{other.report_id}/").json()["status"] == Report.Status.GENERATING
    # ...and reaped once it has passed (the detail page polls).
    monkeypatch.setattr(report_service, "_last_reap", 0.0)
    assert client.get(f"/api/reports/{other.report_id}/").json()["status"] == Report.Status.FAILED


def test_list_reports_filters_by_user():
    u1 = UserFactory()
    u2 = UserFactory()
    Report.objects.create(report_type="comprehensive", title="A", config={}, created_by=u1)
    Report.objects.create(report_type="comprehensive", title="B", config={}, created_by=u2)

    qs, total = services.list_reports(user=u1)

    assert total == 1
    assert next(iter(qs)).title == "A"


def test_unusable_measurement_keeps_the_job_a_trial_but_unmeasured():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    job = _job(user, model, template)
    make_run(job, measurement={}, is_complete=True)

    data = generators.generate_template_comparison({"template_slug": template.slug})

    stats = data["models"][0]["stats"]
    assert stats["trials"] == 1
    assert stats["analyzed"] == 0
    assert stats["excluded"] == 0
    assert stats["analysis"]["excluded_reasons"] == {"no_measurement": 1}
    assert stats["findings_total"]["n_missing"] == 1


def test_every_generator_output_is_json_serializable():
    """report_data is a JSONField: no UUID/set/datetime objects may leak into it."""
    import json

    user = UserFactory()
    template = AppRequirementTemplateFactory()
    experiment, (cond_a, _) = _experiment_with_conditions(user)
    job = _measured_job(
        user,
        cond_a.model,
        template,
        job_kwargs={"experiment": experiment, "condition": cond_a},
        findings={"high": 1},
        ai={"low": 2},
    )
    ToolResult.objects.filter(run__generation_job=job).update(metrics={"score": 1, "dist": {"A": 1}})
    configs = {
        "model_analysis": {"model_id": cond_a.model.model_id},
        "template_comparison": {"template_slug": template.slug},
        "tool_analysis": {},
        "generation_analytics": {"days": 7},
        "comprehensive": {"days": 7},
        "experiment_report": {"experiment_id": str(experiment.id)},
    }

    for report_type, generator in generators.GENERATORS.items():
        data = generator(configs[report_type], user=user)
        assert json.loads(json.dumps(data)) == data, report_type
