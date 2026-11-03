"""Report generators: measurement rules (one instrument set, code-only densities, unmeasured is not zero)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.services.constants import EMPIRICAL_DENSITY_CAP
from backend.reports.services import generators
from backend.reports.tests.helpers import add_file
from backend.reports.tests.helpers import analyze
from backend.reports.tests.helpers import cell_run
from backend.reports.tests.helpers import evaluate
from backend.reports.tests.helpers import high
from backend.reports.tests.helpers import make_cell
from backend.reports.tests.helpers import make_condition
from backend.reports.tests.helpers import make_run
from backend.reports.tests.helpers import make_study
from backend.reports.tests.helpers import make_unit
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = choices.RunStatus


def _task():
    return factories.TaskSpecVersionFactory.create()


# ── dependency advisories, runtime findings, test and starter code stay out of densities ─────────


def test_condition_stats_density_counts_model_code_findings_only():
    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic")
    user = UserFactory()
    run = make_run(user, LLMModelFactory(model_id="m1"), _task())
    add_file(run, "backend/starter.py", "a = 1\n", origin=choices.FileOrigin.STARTER)
    analyze(
        run,
        {
            "bandit": [
                {"severity": "high", "file_path": "app.py"},
                # Test code is not the delivered app.
                {"severity": "critical", "file_path": "tests/test_app.py"},
                # The starter's own code is not the model's (attribution by origin).
                {"severity": "critical", "file_path": "backend/starter.py"},
            ],
            # A starter-pinned advisory is about a package, not code the model wrote.
            "pip-audit": [{"severity": "critical", "category": "dependency", "file_path": "requirements.txt"}] * 3,
            # Only a dynamic scanner saw it: the running app, not a source line.
            "zap": [{"severity": "high", "category": "security", "file_path": "/api"}],
        },
    )

    stats = generators._condition_stats(GenerationRun.objects.filter(id=run.id))

    assert stats["analyzed"] == 1
    assert stats["findings_total"]["mean"] == 7.0  # every counted finding
    assert stats["code_findings_total"]["mean"] == 2.0  # the app tree's code (starter included)
    assert stats["dependency_findings_total"]["mean"] == 3.0
    assert stats["critical_high_normalized"]["mean"] == 1.0  # the model's own file only
    assert stats["weighted_score"]["mean"] == 5.0
    assert stats["weighted_per_kloc"]["mean"] == 5.0
    assert stats["analyzed_loc"] == 1000
    assert stats["severity_basis"] == generators.NORMALIZED


def test_task_sensitivity_ignores_dependency_advisories():
    user = UserFactory()
    task = _task()
    # m-deps: few code issues but a pile of starter advisories; m-code: more code issues.
    deps = make_run(user, LLMModelFactory(model_id="m-deps"), task)
    analyze(
        deps,
        {
            "bandit": high(1),
            "pip-audit": [
                {
                    "severity": "critical",
                    "category": "dependency",
                    "rule_id": f"PYSEC-{i}",
                    "file_path": "requirements.txt",
                    "tool_specific_data": {"package": f"pkg{i}", "vuln_id": f"PYSEC-{i}"},
                }
                for i in range(20)
            ],
        },
    )
    code = make_run(user, LLMModelFactory(model_id="m-code"), task)
    analyze(code, {"bandit": high(4), "pip-audit": []})

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    ranking = [r["model_id"] for r in data["sensitivity"]["baseline_ranking"]]
    assert ranking == ["m-deps", "m-code"]
    rows = {r["model_id"]: r for r in data["models"]}
    assert rows["m-deps"]["stats"]["weighted_per_kloc"]["mean"] == 5.0
    assert rows["m-deps"]["issues_dedup"]["dependency_unique"] == 20
    assert rows["m-deps"]["issues_dedup"]["code_unique"] == 1


# ── an app nothing measured is unknown, not zero findings / a perfect score ──────────────────────


def test_unanalyzed_rows_are_unmeasured_and_never_score():
    user = UserFactory()
    task = _task()
    m1 = LLMModelFactory(model_id="m1")
    # m1's highest-success row was never analyzed; its analyzed row has an infra failure too.
    make_run(user, m1, task, treatment_hash="a" * 64)
    analyzed = make_run(user, m1, task, treatment_hash="b" * 64)
    analyze(analyzed, {"bandit": high(2)})
    make_run(user, m1, task, treatment_hash="b" * 64, status=S.FAILED)
    # m2: completed, never analyzed.
    make_run(user, LLMModelFactory(model_id="m2"), task)

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    rows = {(r["model_id"], r["treatment_hash"]): r for r in data["models"]}
    unmeasured = rows[("m2", "")]
    assert unmeasured["runs_analyzed"] == 0
    assert unmeasured["total_findings"] is None
    assert unmeasured["reported_levels"] is None
    assert unmeasured["severity_normalized_heuristic"] is None
    assert unmeasured["stats"]["findings_total"] == {"mean": None, "stdev": None, "n": 0}
    assert unmeasured["stats"]["weighted_per_kloc"]["mean"] is None
    assert unmeasured["issues_dedup"]["code_unique"] is None
    assert rows[("m1", "a" * 64)]["total_findings"] is None
    assert rows[("m1", "b" * 64)]["total_findings"] == 2
    assert data["mixed_treatments"] is True

    sensitivity = data["sensitivity"]
    # m2 gets no score at all (not 1.0); m1 is scored on its analyzed row, not dropped.
    assert sensitivity["models_evaluated"] == 1
    assert [r["model_id"] for r in sensitivity["baseline_ranking"]] == ["m1"]
    assert sensitivity["baseline_ranking"][0]["empirical_quality"] == pytest.approx(
        1 - 10 / EMPIRICAL_DENSITY_CAP,
        abs=1e-4,
    )


def test_task_sensitivity_density_uses_only_analyzed_loc():
    user = UserFactory()
    task = _task()
    model = LLMModelFactory(model_id="m1")
    analyzed = make_run(user, model, task)
    analyze(analyzed, {"bandit": high(3)})
    make_run(user, model, task, code="x = 1\n" * 9000)  # completed, never analyzed

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    row = data["models"][0]
    assert row["loc"]["total_loc"] == 10000  # generation LOC: every run
    assert row["stats"]["analyzed_loc"] == 1000
    assert row["stats"]["density_basis"]["loc"] == 1000
    # 3 high = 15 weighted over 1,000 analyzed lines: density 15, score 1 - 15/cap.
    assert data["sensitivity"]["baseline_ranking"][0]["empirical_quality"] == pytest.approx(
        1 - 15 / EMPIRICAL_DENSITY_CAP,
    )


# ── one instrument set for every compared row ────────────────────────────────────────────────────


def test_task_comparison_rows_share_one_instrument_set():
    user = UserFactory()
    task = _task()
    a = make_run(user, LLMModelFactory(model_id="m-a"), task)
    analyze(a, {"bandit": high(1)})
    b = make_run(user, LLMModelFactory(model_id="m-b"), task)
    # ruff ran on m-b's app only: its findings would make m-b look worse.
    analyze(
        b,
        {"bandit": high(1), "ruff": [{"severity": "low", "file_path": "app.py", "line_number": n} for n in range(9)]},
    )

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

    assert data["coverage"]["tools_common"] == ["bandit"]
    assert data["coverage"]["tools_partial"]["ruff"]["apps_measured"] == 1
    assert "ruff (1 of 2 runs)" in data["coverage_note"]
    rows = {r["model_id"]: r for r in data["models"]}
    for model_id in ("m-a", "m-b"):
        assert rows[model_id]["total_findings"] == 1
        assert rows[model_id]["issues_dedup"]["raw_findings"] == 1
        assert rows[model_id]["stats"]["findings_total"]["mean"] == 1.0


def _two_condition_study(user, *, repeats=1):
    study, plan = make_study(user, repeats=repeats)
    unit = make_unit(plan)
    cond_a = make_condition(plan, LLMModelFactory(model_id="m-a"), label="arm A", is_baseline=True)
    cond_b = make_condition(plan, LLMModelFactory(model_id="m-b"), label="arm B")
    return study, unit, cond_a, cond_b


def test_study_conditions_report_measures_every_condition_with_one_instrument_set():
    user = UserFactory()
    study, unit, cond_a, cond_b = _two_condition_study(user)
    run_a = cell_run(make_cell(cond_a, unit), user)
    # jscpd failed on condition A's app: it must be left out of B's row too.
    analyze(run_a, {"bandit": high(1), "jscpd": []}, statuses={"jscpd": ToolResult.Status.FAILED})
    run_b = cell_run(make_cell(cond_b, unit), user)
    analyze(
        run_b,
        {"bandit": high(1), "jscpd": [{"severity": "low", "file_path": "app.py", "line_number": n} for n in range(5)]},
    )

    data = generators.generate_study_conditions_report({"study_id": str(study.id)}, user=user)

    assert data["coverage"]["tools_common"] == ["bandit"]
    assert set(data["coverage"]["tools_partial"]) == {"jscpd"}
    rows = {r["condition_id"]: r for r in data["conditions"]}
    for cid in (cond_a.id, cond_b.id):
        assert rows[cid]["total_findings"] == 1
        assert rows[cid]["issues_dedup"]["raw_findings"] == 1
        assert rows[cid]["issues_dedup"]["code_unique"] == 1
    assert rows[cond_a.id]["label"] == "arm A"
    assert rows[cond_a.id]["is_baseline"] is True
    assert data["study"]["baseline_condition_id"] == cond_a.id
    assert any("jscpd (1 of 2 runs)" in note for note in data["notes"])


def test_study_results_are_reported_as_unavailable_not_empty(monkeypatch):
    """Until the study results land, the report says so instead of an empty (clean-looking) leaderboard."""
    user = UserFactory()
    study, _unit, _a, _b = _two_condition_study(user)

    data = generators.generate_study_conditions_report({"study_id": str(study.id)}, user=user)
    assert data["results"] is None
    assert any("not available" in note for note in data["notes"])

    monkeypatch.setattr(
        "backend.generation.study.results.study_results",
        lambda s: {"leaderboard": [], "notes": [], "interim": True},
    )
    data = generators.generate_study_conditions_report({"study_id": str(study.id)}, user=user)
    assert data["results"]["interim"] is True


def test_coverage_note_names_runs_no_tool_measured():
    user = UserFactory()
    task = _task()
    measured = make_run(user, LLMModelFactory(model_id="m-a"), task)
    analyze(measured, {"bandit": high(1)})
    # m-b's whole analysis failed: it drops out of the counts, and the note must say so.
    failed = make_run(user, LLMModelFactory(model_id="m-b"), task)
    analyze(failed, {"bandit": []}, statuses={"bandit": ToolResult.Status.FAILED})

    data = generators.generate_task_comparison({"task_slug": task.task.slug})

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


def test_study_conditions_report_uses_the_current_runs():
    """The first usable attempt of each main-stage cell; superseded, beyond-plan and pilot runs are left out."""
    user = UserFactory()
    study, plan = make_study(user, repeats=1)
    unit = make_unit(plan)
    cond = make_condition(plan, LLMModelFactory(model_id="m-a"))
    cell = make_cell(cond, unit)

    infra = cell_run(cell, user, attempt=1, status=S.FAILED)
    analyze(infra, {"bandit": high(7)})
    current = cell_run(cell, user, attempt=2)
    analyze(current, {"bandit": high(1)})
    beyond = cell_run(cell, user, attempt=3, beyond_plan=True)  # a top-up of a cell that had a usable run
    analyze(beyond, {"bandit": high(5)})
    pilot = cell_run(make_cell(cond, unit, stage=choices.Stage.PILOT, repeat_index=1, block_index=1), user)
    analyze(pilot, {"bandit": high(3)})

    data = generators.generate_study_conditions_report({"study_id": str(study.id)}, user=user)

    assert data["report_version"] == generators.REPORT_VERSION == 3
    sample = data["sample"]
    assert sample["planned_repeats"] == 1
    assert sample["runs"] == 1
    assert sample["beyond_plan_excluded"] == 1
    assert sample["pilot_excluded"] == 1
    assert sample["superseded_attempts_excluded"] == 1
    row = data["conditions"][0]
    assert row["generation"]["total_runs"] == 1
    assert row["stats"]["trials"] == 1
    assert row["total_findings"] == 1
    assert row["issues_dedup"]["runs"] == 1


# ── intention-to-treat by default; the locked plan's truncation rule wins ────────────────────────


def test_truncated_runs_are_trials_by_default_and_excluded_only_as_sensitivity():
    user = UserFactory()
    task = _task()
    model = LLMModelFactory(model_id="m1")
    make_run(user, model, task)
    make_run(user, model, task, validity={"truncated": True, "termination": "budget_exhausted"})

    itt = generators.generate_task_comparison({"task_slug": task.task.slug})["models"][0]["stats"]
    assert (itt["trials"], itt["truncated"], itt["truncated_excluded"]) == (2, 1, 0)
    sensitivity = generators.generate_task_comparison({"task_slug": task.task.slug, "exclude_truncated": True})
    stats = sensitivity["models"][0]["stats"]
    assert (stats["trials"], stats["truncated_excluded"]) == (1, 1)


def test_study_conditions_report_records_the_locked_plans_truncation_rule():
    user = UserFactory()
    study, _unit, _a, _b = _two_condition_study(user)
    study.analysis_plan = {"primary_metric": "pass_rate", "exclusions": {"truncated": False}}
    study.save(update_fields=["analysis_plan"])
    type(study).objects.filter(pk=study.pk).update(locked_at=timezone.now())
    study.refresh_from_db()

    data = generators.generate_study_conditions_report(
        {"study_id": str(study.id), "exclude_truncated": True},
        user=user,
    )

    assert data["sample"]["truncation_rule"] == "analysis_plan"
    assert data["sample"]["exclude_truncated"] is False
    assert any("locked analysis plan keeps truncated runs" in n for n in data["notes"])

    plain, _ = make_study(user)
    data = generators.generate_study_conditions_report({"study_id": str(plain.id)}, user=user)
    assert (data["sample"]["truncation_rule"], data["sample"]["exclude_truncated"]) == ("report_config", False)


def test_study_conditions_report_refuses_another_users_study():
    owner, outsider = UserFactory(), UserFactory()
    study, _ = make_study(owner)
    with pytest.raises(ValueError, match="Study not found"):
        generators.generate_study_conditions_report({"study_id": str(study.id)}, user=outsider)
    with pytest.raises(ValueError, match="Study not found"):
        generators.generate_study_conditions_report({"study_id": "not-a-uuid"}, user=owner)


# ── the tools' own levels are shown; the normalized split is labelled ────────────────────────────


def test_rows_show_the_tools_own_levels():
    user = UserFactory()
    model = LLMModelFactory(model_id="m1")
    run = make_run(user, model, _task())
    analyze(
        run,
        {
            "bandit": [{"severity": "high", "tool_severity": "HIGH"}],
            # pip-audit prints no level: unrated, whatever the normalization guessed.
            "pip-audit": [
                {"severity": "medium", "tool_severity": "", "category": "dependency", "file_path": "requirements.txt"},
            ]
            * 2,
        },
    )

    data = generators.generate_model_analysis({"model_id": "m1"})

    assert data["reported_levels"] == {"unrated": 2, "HIGH": 1}
    assert data["severity_normalized_heuristic"] == {"critical": 0, "high": 1, "medium": 2, "low": 0, "info": 0}
    assert "findings" not in data
    assert data["issues_dedup"]["code_unique"] == 1
    assert data["issues_dedup"]["dependency_unique"] == 1


# ── owner scoping (staff and no-user callers see all) ────────────────────────────────────────────


def test_generators_report_only_the_users_own_data():
    owner, outsider, staff = UserFactory(), UserFactory(), UserFactory(is_staff=True)
    model = LLMModelFactory(model_id="m1")
    task = _task()
    run = make_run(owner, model, task, metrics={"cost_usd": 0.5})
    analyze(run, {"bandit": high(2)})

    ma = generators.generate_model_analysis({"model_id": "m1"}, user=outsider)
    assert ma["generation"]["total_runs"] == 0
    assert ma["loc"]["per_run"] == []
    assert ma["loc"]["total_loc"] is None
    assert ma["total_findings"] is None
    assert generators.generate_model_analysis({"model_id": "m1"}, user=owner)["total_findings"] == 2
    assert generators.generate_model_analysis({"model_id": "m1"}, user=staff)["total_findings"] == 2
    assert generators.generate_model_analysis({"model_id": "m1"})["total_findings"] == 2  # CLI / no user

    assert generators.generate_task_comparison({"task_slug": task.task.slug}, user=outsider)["models"] == []
    assert generators.generate_tool_analysis({}, user=outsider)["total_findings"] is None
    assert generators.generate_tool_analysis({}, user=owner)["total_findings"] == 2
    assert generators.generate_generation_analytics({"days": 7}, user=outsider)["summary"]["total_runs"] == 0
    platform = generators.generate_comprehensive({"days": 7}, user=outsider)["platform"]
    # No analysis run: findings are unmeasured (as in tool_analysis), not zero.
    assert (platform["total_runs"], platform["total_analysis_runs"], platform["total_findings"]) == (0, 0, None)
    staff_platform = generators.generate_comprehensive({"days": 7}, user=staff)["platform"]
    assert (staff_platform["total_runs"], staff_platform["total_findings"]) == (1, 2)


def test_reference_runs_are_never_samples():
    user = UserFactory()
    task = _task()
    make_run(user, LLMModelFactory(model_id="m1"), task)
    factories.GenerationRunFactory.create(reference=True, created_by=user, task_version=task)

    summary = generators.generate_generation_analytics({"days": 7})["summary"]
    assert summary["total_runs"] == 1
    assert generators.generate_comprehensive({"days": 7})["platform"]["total_runs"] == 1


def test_report_service_scopes_every_type_to_the_creator():
    from backend.reports.models import Report
    from backend.reports.services import report_service

    owner, outsider = UserFactory(), UserFactory()
    make_run(owner, LLMModelFactory(model_id="m1"), _task())
    report = Report.objects.create(
        report_type="model_analysis",
        title="t",
        config={"model_id": "m1"},
        created_by=outsider,
    )
    report_service._generate(report)
    report.refresh_from_db()
    assert report.report_data["generation"]["total_runs"] == 0
    assert frozenset(generators.GENERATORS) == generators.USER_SCOPED


# ── tool analysis counts executions, so a measured zero is not a missing tool ────────────────────


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
    # Nothing applicable to read: no measurement, never a measured zero (spec 8.8).
    assert tools["hadolint"]["skipped"] == {"not_applicable": 1}
    assert (tools["hadolint"]["measured"], tools["hadolint"]["findings_per_measured_execution"]) == (0, None)
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


# ── runs still in flight have no outcome yet ─────────────────────────────────────────────────────


def test_success_rate_leaves_in_flight_runs_out():
    user = UserFactory()
    task = _task()
    model = LLMModelFactory(model_id="m1")
    for status in (S.COMPLETED, S.FAILED, S.PENDING, S.RUNNING):
        make_run(user, model, task, status=status)
    waiting = LLMModelFactory(model_id="m-waiting")
    make_run(user, waiting, task, status=S.PENDING)

    summary = generators.generate_model_analysis({"model_id": "m1"})["generation"]
    assert (summary["total_runs"], summary["in_flight_runs"], summary["success_rate"]) == (4, 2, 0.5)
    assert (summary["usable_runs"], summary["usable_rate"]) == (1, 0.5)
    assert generators.generate_model_analysis({"model_id": "m-waiting"})["generation"]["success_rate"] is None

    by_model = {r["model_id"]: r for r in generators.generate_generation_analytics({"days": 7})["by_model"]}
    assert (by_model["m1"]["in_flight"], by_model["m1"]["success_rate"]) == (2, 0.5)
    assert by_model["m-waiting"]["success_rate"] is None

    # A row with nothing finished sorts last instead of breaking the sort.
    rows = generators.generate_task_comparison({"task_slug": task.task.slug})["models"]
    assert [r["model_id"] for r in rows] == ["m1", "m-waiting"]


def test_run_summary_counts_spend_and_truncation_and_never_invents_a_zero_cost():
    user = UserFactory()
    task = _task()
    model = LLMModelFactory(model_id="m1")
    make_run(user, model, task, metrics={"cost_usd": 0.1})
    make_run(user, model, task, metrics={"cost_usd": 0.2}, validity={"truncated": True})

    summary = generators.generate_model_analysis({"model_id": "m1"})["generation"]
    assert summary["total_cost"] == pytest.approx(0.3)
    assert summary["truncated_runs"] == 1

    other = LLMModelFactory(model_id="m2")
    make_run(user, other, task)
    assert generators.generate_model_analysis({"model_id": "m2"})["generation"]["total_cost"] is None


def test_functional_pass_rate_leaves_not_measured_evaluations_out():
    user = UserFactory()
    task = _task()
    model = LLMModelFactory(model_id="m1")
    evaluate(make_run(user, model, task), passed=3, failed=1)
    evaluate(make_run(user, model, task), measured=False)

    functional = generators.generate_task_comparison({"task_slug": task.task.slug})["models"][0]["stats"]["functional"]
    assert functional["runs_measured"] == 1
    assert functional["runs_not_measured"] == 1
    assert functional["pass_rate"]["mean"] == 0.75


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


def test_loc_of_a_run_without_a_tree_is_unknown():
    user = UserFactory()
    make_run(user, LLMModelFactory(model_id="m1"), _task(), status=S.PENDING, code=None)
    assert generators.generate_model_analysis({"model_id": "m1"})["loc"]["total_loc"] is None
