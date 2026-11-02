"""Legacy report generators: measurement rules (one instrument set, code-only densities, unmeasured is not zero)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.services.constants import EMPIRICAL_DENSITY_CAP
from backend.reports.services import generators
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CODE_1000 = {"backend_code": "x\n" * 1000}


def _job(user, model, template, *, status=GenerationJob.Status.COMPLETED, result_data=None, **kwargs):
    return GenerationJobFactory(
        created_by=user,
        model=model,
        app_requirement=template,
        status=status,
        result_data=result_data or CODE_1000,
        **kwargs,
    )


def _analyze(job, tools: dict[str, list[dict]], *, statuses: dict[str, str] | None = None):
    """One analysis run of *job*: per tool slug, its findings (Finding field dicts)."""
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    for slug, findings in tools.items():
        status = (statuses or {}).get(slug, ToolResult.Status.COMPLETED)
        result = ToolResultFactory(run=run, tool_slug=slug, status=status)
        for f in findings:
            Finding.objects.create(
                result=result,
                title=f.get("title", "f"),
                **{k: v for k, v in f.items() if k != "title"},
            )
    return run


def _high(n=1, **extra):
    return [{"severity": "high", **extra} for _ in range(n)]


# ── legacy-reports-1: dependency advisories (and runtime / test code) stay out of densities ─────────


def test_experiment_stats_density_counts_code_findings_only():
    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic")
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    job = _job(user, LLMModelFactory(model_id="m1"), template)
    _analyze(
        job,
        {
            "bandit": [
                {"severity": "high", "file_path": "app.py"},
                # Test code is not the delivered app.
                {"severity": "critical", "file_path": "tests/test_app.py"},
            ],
            # A scaffold-pinned advisory is about a package, not code the model wrote.
            "pip-audit": [{"severity": "critical", "category": "dependency", "file_path": "requirements.txt"}] * 3,
            # Only a dynamic scanner saw it: the running app, not a source line.
            "zap": [{"severity": "high", "category": "security"}],
        },
    )

    stats = generators._experiment_stats(GenerationJob.objects.filter(id=job.id))

    assert stats["analyzed"] == 1
    assert stats["findings_total"]["mean"] == 6.0  # every counted finding
    assert stats["code_findings_total"]["mean"] == 1.0
    assert stats["dependency_findings_total"]["mean"] == 3.0
    assert stats["critical_high_normalized"]["mean"] == 1.0
    assert stats["weighted_score"]["mean"] == 5.0  # one high (5); no criticals from deps or tests
    assert stats["weighted_per_kloc"]["mean"] == 5.0
    assert stats["severity_basis"] == generators.NORMALIZED


def test_template_sensitivity_ignores_dependency_advisories():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    # m-deps: few code issues but a pile of scaffold advisories; m-code: more code issues.
    deps = _job(user, LLMModelFactory(model_id="m-deps"), template)
    _analyze(
        deps,
        {
            "bandit": _high(1, file_path="app.py"),
            "pip-audit": [
                {
                    "severity": "critical",
                    "category": "dependency",
                    "rule_id": f"PYSEC-{i}",
                    "tool_specific_data": {"package": f"pkg{i}", "vuln_id": f"PYSEC-{i}"},
                }
                for i in range(20)
            ],
        },
    )
    code = _job(user, LLMModelFactory(model_id="m-code"), template)
    _analyze(code, {"bandit": _high(4, file_path="app.py"), "pip-audit": []})

    data = generators.generate_template_comparison({"template_slug": template.slug})

    ranking = [r["model_id"] for r in data["sensitivity"]["baseline_ranking"]]
    assert ranking == ["m-deps", "m-code"]
    rows = {r["model_id"]: r for r in data["models"]}
    assert rows["m-deps"]["stats"]["weighted_per_kloc"]["mean"] == 5.0
    assert rows["m-deps"]["issues_dedup"]["dependency_unique"] == 20
    assert rows["m-deps"]["issues_dedup"]["code_unique"] == 1


# ── legacy-reports-2: an app nothing measured is unknown, not zero findings / a perfect score ─────


def test_unanalyzed_rows_are_unmeasured_and_never_score():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    m1 = LLMModelFactory(model_id="m1")
    # m1's highest-success row was never analyzed; its analyzed row has a failed job too.
    _job(user, m1, template, bundle_key="a@1")
    analyzed = _job(user, m1, template, bundle_key="b@1")
    _analyze(analyzed, {"bandit": _high(2)})
    _job(user, m1, template, bundle_key="b@1", status=GenerationJob.Status.FAILED)
    # m2: completed, never analyzed.
    _job(user, LLMModelFactory(model_id="m2"), template)

    data = generators.generate_template_comparison({"template_slug": template.slug})

    rows = {(r["model_id"], r["bundle_key"]): r for r in data["models"]}
    unmeasured = rows[("m2", "")]
    assert unmeasured["runs_analyzed"] == 0
    assert unmeasured["total_findings"] is None
    assert unmeasured["reported_levels"] is None
    assert unmeasured["severity_normalized_heuristic"] is None
    assert unmeasured["stats"]["findings_total"] == {"mean": None, "stdev": None, "n": 0}
    assert unmeasured["stats"]["weighted_per_kloc"]["mean"] is None
    assert unmeasured["issues_dedup"]["code_unique"] is None
    assert rows[("m1", "a@1")]["total_findings"] is None
    assert rows[("m1", "b@1")]["total_findings"] == 2

    sensitivity = data["sensitivity"]
    # m2 gets no score at all (not 1.0); m1 is scored on its analyzed row, not dropped.
    assert sensitivity["models_evaluated"] == 1
    assert [r["model_id"] for r in sensitivity["baseline_ranking"]] == ["m1"]
    assert sensitivity["baseline_ranking"][0]["empirical_quality"] == pytest.approx(
        1 - 10 / EMPIRICAL_DENSITY_CAP,
        abs=1e-4,
    )


# ── legacy-reports-3: the density divides analyzed findings by analyzed LOC ─────────────────────


def test_template_sensitivity_density_uses_only_analyzed_loc():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    analyzed = _job(user, model, template)
    _analyze(analyzed, {"bandit": _high(3)})
    _job(user, model, template, result_data={"backend_code": "x\n" * 9000})  # completed, never analyzed

    data = generators.generate_template_comparison({"template_slug": template.slug})

    row = data["models"][0]
    assert row["loc"]["total_loc"] == 10000  # generation LOC: every job
    assert row["stats"]["analyzed_loc"] == 1000
    assert row["stats"]["density_basis"]["loc"] == 1000
    # 3 high = 15 weighted over 1,000 analyzed lines: density 15, score 1 - 15/cap.
    assert data["sensitivity"]["baseline_ranking"][0]["empirical_quality"] == pytest.approx(
        1 - 15 / EMPIRICAL_DENSITY_CAP,
    )


# ── legacy-reports-4 / study-report-1: one instrument set for every compared row ─────────────────


def test_template_comparison_rows_share_one_instrument_set():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    a = _job(user, LLMModelFactory(model_id="m-a"), template)
    _analyze(a, {"bandit": _high(1)})
    b = _job(user, LLMModelFactory(model_id="m-b"), template)
    # ruff ran on m-b's app only: its findings would make m-b look worse.
    _analyze(
        b,
        {"bandit": _high(1), "ruff": [{"severity": "low", "file_path": "app.py", "line_number": n} for n in range(9)]},
    )

    data = generators.generate_template_comparison({"template_slug": template.slug})

    assert data["coverage"]["tools_common"] == ["bandit"]
    assert data["coverage"]["tools_partial"]["ruff"]["apps_measured"] == 1
    assert "ruff (1 of 2 runs)" in data["coverage_note"]
    rows = {r["model_id"]: r for r in data["models"]}
    for model_id in ("m-a", "m-b"):
        assert rows[model_id]["total_findings"] == 1
        assert rows[model_id]["issues_dedup"]["raw_findings"] == 1
        assert rows[model_id]["stats"]["findings_total"]["mean"] == 1.0


def test_experiment_report_measures_every_condition_with_one_instrument_set():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    experiment = ExperimentFactory(created_by=user, repeats=1)
    experiment.app_requirements.add(template)
    # An unlabeled (legacy) condition: every table must give it the leaderboard's name.
    cond_a = ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(model_id="m-a"), label="")
    cond_b = ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(model_id="m-b"))

    job_a = _job(user, cond_a.model, template, experiment=experiment, condition=cond_a, repeat_index=0)
    # jscpd failed on condition A's app: it must be left out of B's row too.
    _analyze(job_a, {"bandit": _high(1), "jscpd": []}, statuses={"jscpd": ToolResult.Status.FAILED})
    job_b = _job(user, cond_b.model, template, experiment=experiment, condition=cond_b, repeat_index=0)
    _analyze(
        job_b,
        {"bandit": _high(1), "jscpd": [{"severity": "low", "file_path": "a.py", "line_number": n} for n in range(5)]},
    )

    data = generators.generate_experiment_report({"experiment_id": str(experiment.id)}, user=user)

    assert data["coverage"]["tools_common"] == ["bandit"]
    assert set(data["coverage"]["tools_partial"]) == {"jscpd"}
    rows = {r["condition_id"]: r for r in data["conditions"]}
    for cid in (cond_a.id, cond_b.id):
        assert rows[cid]["total_findings"] == 1
        assert rows[cid]["issues_dedup"]["raw_findings"] == 1
        assert rows[cid]["issues_dedup"]["code_unique"] == 1
    assert any("jscpd (1 of 2 runs)" in note for note in data["results"]["notes"])
    board = {r["condition_id"]: r["label"] for r in data["results"]["leaderboard"]}
    assert rows[cond_a.id]["label"] == board[cond_a.id]
    assert rows[cond_b.id]["label"] == board[cond_b.id]


def test_coverage_note_names_runs_no_tool_measured():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    measured = _job(user, LLMModelFactory(model_id="m-a"), template)
    _analyze(measured, {"bandit": _high(1)})
    # m-b's whole analysis failed: it drops out of the counts, and the note must say so.
    failed = _job(user, LLMModelFactory(model_id="m-b"), template)
    _analyze(failed, {"bandit": []}, statuses={"bandit": ToolResult.Status.FAILED})

    data = generators.generate_template_comparison({"template_slug": template.slug})

    assert data["coverage"]["tools_partial"] == {}
    assert data["coverage"]["runs_unmeasured"] == [str(failed.id)]
    assert "1 analyzed run(s) were measured by no tool" in data["coverage_note"]
    rows = {r["model_id"]: r for r in data["models"]}
    assert rows["m-b"]["total_findings"] is None
    assert rows["m-a"]["total_findings"] == 1


def test_coverage_note_without_a_common_tool():
    note = generators._coverage_note(
        {
            "apps": 2,
            "apps_unmeasured": [],
            "tools_common": [],
            "tools_partial": {"bandit": {"apps_measured": 1}, "ruff": {"apps_measured": 1}},
        },
    )
    assert note == (
        "No tool measured every analyzed run, so no findings are counted: bandit (1 of 2 runs), ruff (1 of 2 runs)."
    )
    assert generators._coverage_note({"apps": 2, "tools_common": ["bandit"], "tools_partial": {}}) is None


def test_experiment_report_uses_the_results_tab_sample():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    experiment = ExperimentFactory(created_by=user, repeats=1)
    experiment.app_requirements.add(template)
    cond = ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(model_id="m-a"))
    cell = {"experiment": experiment, "condition": cond}

    superseded = _job(user, cond.model, template, repeat_index=0, **cell)
    _analyze(superseded, {"bandit": _high(9)})
    current = _job(user, cond.model, template, repeat_index=0, **cell)
    _analyze(current, {"bandit": _high(1)})
    beyond = _job(user, cond.model, template, repeat_index=1, **cell)  # a repeat added after launch
    _analyze(beyond, {"bandit": _high(5)})

    data = generators.generate_experiment_report({"experiment_id": str(experiment.id)}, user=user)

    # Version 2: code-only definitions and one instrument set (stored v1 payloads have no key).
    assert data["report_version"] == generators.REPORT_VERSION == 2
    sample = data["sample"]
    assert sample["planned_repeats"] == 1
    assert sample["superseded_attempts_excluded"] == 1
    assert sample["beyond_plan_excluded"] == 1
    row = data["conditions"][0]
    assert row["generation"]["total_jobs"] == 1
    assert row["stats"]["trials"] == 1
    assert row["total_findings"] == 1
    assert row["issues_dedup"]["runs"] == 1


# ── frontend-6: the report records which truncation rule applied, and why ────────────────────────


def test_experiment_report_records_the_plans_truncation_rule():
    from backend.generation.services.analysis_plan import normalize_analysis_plan

    user = UserFactory()
    experiment = ExperimentFactory(created_by=user)
    experiment.analysis_plan = normalize_analysis_plan(
        {"primary_metric": "pass_rate", "exclusions": {"truncated": False}},
    )
    experiment.analysis_plan_frozen_at = timezone.now()
    experiment.save(update_fields=["analysis_plan", "analysis_plan_frozen_at"])

    data = generators.generate_experiment_report(
        {"experiment_id": str(experiment.id), "exclude_truncated": True},
        user=user,
    )

    assert data["sample"]["truncation_rule"] == "analysis_plan"
    assert data["sample"]["exclude_truncated"] is False
    assert any("frozen analysis plan keeps truncated runs" in n for n in data["results"]["notes"])

    plain = ExperimentFactory(created_by=user)
    data = generators.generate_experiment_report(
        {"experiment_id": str(plain.id), "exclude_truncated": False},
        user=user,
    )
    assert (data["sample"]["truncation_rule"], data["sample"]["exclude_truncated"]) == ("report_config", False)


# ── legacy-reports-6 / frontend-3: the tools' own levels are shown; the normalized split is labelled ──


def test_rows_show_the_tools_own_levels():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    job = _job(user, model, AppRequirementTemplateFactory())
    _analyze(
        job,
        {
            "bandit": [{"severity": "high", "tool_severity": "HIGH"}],
            # pip-audit prints no level: unrated, whatever the normalization guessed.
            "pip-audit": [{"severity": "medium", "tool_severity": "", "category": "dependency"}] * 2,
        },
    )

    data = generators.generate_model_analysis({"model_id": "m1"})

    assert data["reported_levels"] == {"unrated": 2, "HIGH": 1}
    assert data["severity_normalized_heuristic"] == {"critical": 0, "high": 1, "medium": 2, "low": 0, "info": 0}
    assert "findings" not in data
    assert data["issues_dedup"]["code_unique"] == 1
    assert data["issues_dedup"]["dependency_unique"] == 1


# ── legacy-reports-5: owner scoping (staff and no-user callers see all) ──────────────────────────


def test_generators_report_only_the_users_own_data():
    owner, outsider, staff = UserFactory(), UserFactory(), UserFactory(is_staff=True)
    model = LLMModelFactory(model_id="m1")
    template = AppRequirementTemplateFactory()
    job = _job(owner, model, template, metrics={"cost": 0.5})
    _analyze(job, {"bandit": _high(2)})

    ma = generators.generate_model_analysis({"model_id": "m1"}, user=outsider)
    assert ma["generation"]["total_jobs"] == 0
    assert ma["loc"]["per_job"] == []
    assert ma["total_findings"] is None
    assert generators.generate_model_analysis({"model_id": "m1"}, user=owner)["total_findings"] == 2
    assert generators.generate_model_analysis({"model_id": "m1"}, user=staff)["total_findings"] == 2
    assert generators.generate_model_analysis({"model_id": "m1"})["total_findings"] == 2  # CLI / no user

    assert generators.generate_template_comparison({"template_slug": template.slug}, user=outsider)["models"] == []
    assert generators.generate_tool_analysis({}, user=outsider)["total_findings"] is None
    assert generators.generate_tool_analysis({}, user=owner)["total_findings"] == 2
    assert generators.generate_generation_analytics({"days": 7}, user=outsider)["summary"]["total_jobs"] == 0
    platform = generators.generate_comprehensive({"days": 7}, user=outsider)["platform"]
    # No analysis run: findings are unmeasured (as in tool_analysis), not zero.
    assert (platform["total_jobs"], platform["total_runs"], platform["total_findings"]) == (0, 0, None)
    staff_platform = generators.generate_comprehensive({"days": 7}, user=staff)["platform"]
    assert (staff_platform["total_jobs"], staff_platform["total_findings"]) == (1, 2)


def test_report_service_scopes_every_type_to_the_creator():
    from backend.reports.models import Report
    from backend.reports.services import report_service

    owner, outsider = UserFactory(), UserFactory()
    _job(owner, LLMModelFactory(model_id="m1"), AppRequirementTemplateFactory())
    report = Report.objects.create(
        report_type="model_analysis",
        title="t",
        config={"model_id": "m1"},
        created_by=outsider,
    )
    report_service._generate(report)
    report.refresh_from_db()
    assert report.report_data["generation"]["total_jobs"] == 0
    assert frozenset(generators.GENERATORS) == generators.USER_SCOPED


# ── legacy-reports-8: tool analysis counts executions, so a measured zero is not a missing tool ────


def test_tool_analysis_lists_tools_that_found_nothing():
    user = UserFactory()
    run = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.COMPLETED)
    ToolResultFactory(run=run, tool_slug="htmlhint")  # completed, no findings
    ToolResultFactory(
        run=run,
        tool_slug="hadolint",
        status=ToolResult.Status.SKIPPED,
        summary={"skip_reason": "not_applicable"},
    )
    ToolResultFactory(run=run, tool_slug="mypy", status=ToolResult.Status.FAILED)
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    Finding.objects.create(result=bandit, severity="medium", tool_severity="MEDIUM", title="f")
    Finding.objects.create(result=bandit, severity="low", tool_severity="", title="g")
    cancelled = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.CANCELLED)
    Finding.objects.create(result=ToolResultFactory(run=cancelled, tool_slug="bandit"), severity="high", title="x")

    data = generators.generate_tool_analysis({})

    tools = {t["analyzer"]: t for t in data["tools"]}
    assert set(tools) == {"bandit", "htmlhint", "hadolint", "mypy"}
    assert (
        tools["htmlhint"]["total"],
        tools["htmlhint"]["measured"],
        tools["htmlhint"]["findings_per_measured_execution"],
    ) == (0, 1, 0.0)
    assert tools["hadolint"]["skipped"] == {"not_applicable": 1}
    assert tools["hadolint"]["measured"] == 1
    assert (tools["mypy"]["failed"], tools["mypy"]["measured"], tools["mypy"]["findings_per_measured_execution"]) == (
        1,
        0,
        None,
    )
    assert tools["bandit"]["total"] == 2  # the cancelled run is left out
    assert tools["bandit"]["reported_levels"] == {"MEDIUM": 1, "unrated": 1}
    assert tools["bandit"]["by_severity_normalized_heuristic"]["medium"] == 1
    assert data["total_runs"] == 1
    assert data["cancelled_runs_excluded"] == 1

    only_bandit = generators.generate_tool_analysis({"tool_name": "bandit"})
    assert [t["analyzer"] for t in only_bandit["tools"]] == ["bandit"]
    assert only_bandit["total_runs"] == 1


# ── legacy-reports-9: jobs still in flight have no outcome yet ───────────────────────────────────


def test_success_rate_leaves_in_flight_jobs_out():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    for status in (GenerationJob.Status.COMPLETED, GenerationJob.Status.FAILED, GenerationJob.Status.PENDING):
        _job(user, model, template, status=status)
    _job(user, model, template, status=GenerationJob.Status.RUNNING)
    waiting = LLMModelFactory(model_id="m-waiting")
    _job(user, waiting, template, status=GenerationJob.Status.PENDING)

    summary = generators.generate_model_analysis({"model_id": "m1"})["generation"]
    assert (summary["total_jobs"], summary["in_flight_jobs"], summary["success_rate"]) == (4, 2, 0.5)
    assert generators.generate_model_analysis({"model_id": "m-waiting"})["generation"]["success_rate"] is None

    by_model = {r["model_id"]: r for r in generators.generate_generation_analytics({"days": 7})["by_model"]}
    assert (by_model["m1"]["in_flight"], by_model["m1"]["success_rate"]) == (2, 0.5)
    assert by_model["m-waiting"]["success_rate"] is None

    # A row with nothing finished sorts last instead of breaking the sort.
    rows = generators.generate_template_comparison({"template_slug": template.slug})["models"]
    assert [r["model_id"] for r in rows] == ["m1", "m-waiting"]


def test_job_summary_counts_prior_attempt_spend_and_agent_truncation():
    user = UserFactory()
    template = AppRequirementTemplateFactory()
    model = LLMModelFactory(model_id="m1")
    # A requeued job: its earlier attempts' spend is part of what the job cost.
    _job(user, model, template, metrics={"cost": 0.1}, execution={"prior_attempts_cost": 0.2})
    # An agent whose final response hit the token ceiling: truncated, like a cut-off file.
    _job(user, model, template, result_data={**CODE_1000, "agent": {"final_response_truncated": True}})

    summary = generators.generate_model_analysis({"model_id": "m1"})["generation"]

    assert summary["total_cost"] == pytest.approx(0.3)
    assert summary["truncated_jobs"] == 1


def test_tool_analysis_days_window_and_comprehensive_pass_it_on():
    from datetime import timedelta

    user = UserFactory()
    recent = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.COMPLETED)
    Finding.objects.create(result=ToolResultFactory(run=recent, tool_slug="bandit"), severity="high", title="new")
    old = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.COMPLETED)
    Finding.objects.create(result=ToolResultFactory(run=old, tool_slug="ruff"), severity="low", title="old")
    AnalysisRun.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(days=10))

    windowed = generators.generate_tool_analysis({"days": 1})
    assert windowed["window_days"] == 1
    assert [t["analyzer"] for t in windowed["tools"]] == ["bandit"]
    assert (windowed["total_findings"], windowed["total_runs"]) == (1, 1)
    assert generators.generate_tool_analysis({})["total_findings"] == 2

    comprehensive = generators.generate_comprehensive({"days": 1})
    assert comprehensive["tool_analysis"]["window_days"] == 1
    assert comprehensive["tool_analysis"]["total_findings"] == 1
    # The platform totals are all-time, and labelled so.
    assert comprehensive["platform"]["total_findings"] == 2
    assert comprehensive["periods"]["platform"] == "all time"
