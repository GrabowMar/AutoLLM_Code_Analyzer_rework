"""The study report end to end: sections, validity checks, planned comparisons, Markdown/LaTeX export."""

from __future__ import annotations

from unittest import mock

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.models import Report
from backend.reports.services import renderers
from backend.reports.services import study_report
from backend.reports.tests.helpers import analyze
from backend.reports.tests.helpers import cell_run
from backend.reports.tests.helpers import evaluate
from backend.reports.tests.helpers import make_cell
from backend.reports.tests.helpers import make_condition
from backend.reports.tests.helpers import make_run
from backend.reports.tests.helpers import make_study
from backend.reports.tests.helpers import make_unit
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

STATUSES = ["passed", "failed", "build_failed", "passed", "start_failed", "passed"]
_OUTCOME = {
    "passed": choices.Outcome.TESTED,
    "failed": choices.Outcome.TESTED,
    "build_failed": choices.Outcome.BUILD_FAIL,
    "start_failed": choices.Outcome.START_FAIL,
    "failed_all": choices.Outcome.TESTED,
}
_CHECKS = {"passed": (4, 0), "failed": (2, 2), "build_failed": (0, 1), "start_failed": (0, 1), "failed_all": (0, 2)}


def _lock(study, plan):
    """Launch: the plan is locked (pre-registered) and the study finished."""
    Study.objects.filter(pk=study.pk).update(analysis_plan=plan, locked_at=timezone.now())
    study.refresh_from_db()


def _set_status(study, status):
    Study.objects.filter(pk=study.pk).update(status=status)
    study.refresh_from_db()


def _evaluated_run(cell_or_none, user, status, *, model=None, task=None, stack=None, code=None, **kwargs):
    passed, failed = _CHECKS[status]
    extra = {"outcome": _OUTCOME[status], "stack_profile": stack, **kwargs}
    if code is not None:
        extra["code"] = code
    if cell_or_none is None:
        run = make_run(user, model, task, **extra)
    else:
        run = cell_run(cell_or_none, user, **extra)
    evaluate(run, passed=passed, failed=failed)
    return run


@pytest.fixture
def study():
    user = UserFactory()
    models = [
        LLMModelFactory(
            model_id=f"vendor/m{i}",
            context_window=8_000 * (i + 1),
            capabilities_json={"context_length": 128_000},
            max_output_tokens=4096 * (i + 1),
            input_price_per_token=1e-6 * (i + 1),
            output_price_per_token=2e-6 * (i + 1),
        )
        for i in range(4)
    ]
    study, plan = make_study(user, repeats=1)
    stack = factories.StackProfileFactory.create()
    units = [make_unit(plan) for _ in range(3)]
    conditions = [
        make_condition(plan, m, label=f"cond-{m.model_id}", is_baseline=(i == 0)) for i, m in enumerate(models)
    ]
    _lock(study, {"primary_metric": "deploy_rate", "secondary_metrics": ["pass_rate"], "correction": "holm"})
    n = 0
    for c in conditions:
        for u in units:
            status = STATUSES[n % len(STATUSES)]
            n += 1
            run = _evaluated_run(
                make_cell(c, u),
                user,
                status,
                stack=stack,
                prompt_hash=f"{n % 3:064d}",
                metrics={"cost_usd": 0.01 * (n % 3 + 1)},
                code="x = 1\n" * (50 + n * 10),
            )
            analysis = analyze(run, {})
            bandit = ToolResultFactory(run=analysis, tool_slug="bandit")
            ruff = ToolResultFactory(run=analysis, tool_slug="ruff", category="quality")
            ToolResultFactory(
                run=analysis,
                tool_slug="radon",
                category="quality",
                metrics={"average_complexity": 2.0 + n % 4, "max_complexity": 9},
            )
            FindingFactory(result=bandit, rule_id="B105", line_number=3, severity="low", file_path="app.py")
            FindingFactory(
                result=ruff,
                rule_id="S105",
                line_number=3,
                severity="low",
                file_path="app.py",
                category="quality",
            )
            for k in range(n % 3):
                FindingFactory(
                    result=ruff,
                    rule_id="F401",
                    line_number=10 + k,
                    severity="medium",
                    file_path="app.py",
                    category="quality",
                )
    return user, study


def _md(data):
    return renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )


def test_study_report_sections(study):
    user, study = study
    data = study_report.generate_study_report({"study_id": str(study.id), "seed": 1}, user=user)

    assert set(data) >= {
        "protocol",
        "definitions",
        "funnel",
        "issues",
        "security_bounds",
        "code_metrics",
        "correlations",
        "ranking",
        "planned_comparisons",
        "validity",
        "per_app",
    }
    assert not [k for k in data if k.startswith("heal")]  # the v2 build-repair ablation is gone
    overall = data["funnel"]["overall"]
    assert (overall["runs"], overall["evaluated"]) == (12, 12)
    assert overall["deployed"] == sum(1 for i in range(12) if STATUSES[i % 6] in ("passed", "failed"))
    assert overall["deploy_rate"]["ci95"][0] < overall["deploy_rate"]["rate"] < overall["deploy_rate"]["ci95"][1]

    # Bandit B105 + Ruff S105 on the same line are one issue.
    totals = data["issues"]["totals"]
    assert totals["duplicate_findings"] == 12

    sev = {r["severity"]: r for r in data["security_bounds"]["overall"]}
    assert sev["critical"]["issues"] == 0
    assert sev["critical"]["zero_rate_upper95"] == pytest.approx(1 - 0.05 ** (1 / 12), abs=1e-4)

    corr = data["correlations"]
    assert corr["status"] == "exploratory"
    assert corr["family_size"] > 0
    assert set(corr["effective_context_differs"]) == {f"vendor/m{i}" for i in range(4)}
    test = next(
        t for t in corr["tests"] if t["predictor"] == "context_window_effective" and t["outcome"] == "deploy_rate"
    )
    assert test["n_models"] == 4
    assert "p_holm" in test

    assert data["ranking"]["models_ranked"] == 4
    assert data["ranking"]["data_uncertainty"]["draws"] > 0

    planned = data["planned_comparisons"]
    assert [f["metric"] for f in planned["families"]] == ["deploy_rate", "pass_rate"]
    assert all(len(f["comparisons"]) == 3 for f in planned["families"])
    assert planned["families"][0]["comparisons"][0]["n_pairs"] == 3

    codes = {v["code"] for v in data["validity"]}
    assert {"small_n_models", "zero_severe", "effective_context", "single_stack", "duplicates"} <= codes
    assert not [c for c in codes if c.startswith("heal")]
    assert "no_plan" not in codes

    assert data["code_metrics"]["by_model"][0]["avg_complexity"]["n"] == 3
    assert len(data["protocol"]["data_fingerprint"]) == 64
    row = data["per_app"][0]
    assert row["evaluation_status"] == "measured"
    assert {"run_id", "task", "unit_key", "kind", "code_unique_model", "outcome", "usable"} <= set(row)


def test_report_is_reproducible(study):
    user, study = study
    config = {"study_id": str(study.id), "seed": 4}
    a = study_report.generate_study_report(config, user=user)
    b = study_report.generate_study_report(config, user=user)
    for key in ("ranking", "correlations", "funnel", "issues"):
        assert a[key] == b[key]


def test_other_users_study_is_refused(study):
    _user, study = study
    with pytest.raises(ValueError, match="not found"):
        study_report.generate_study_report({"study_id": str(study.id)}, user=UserFactory())


def test_markdown_and_latex_render(study):
    user, study = study
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    report = Report.objects.create(
        report_type=Report.Type.STUDY_REPORT,
        title="Study 50% & more_",
        report_data=data,
        status=Report.Status.COMPLETED,
        created_by=user,
    )
    md = renderers.render_markdown(report)
    assert "## Outcome funnel by model" in md
    assert "| vendor/m0 |" in md
    assert "## Threats to validity detected in this data" in md
    assert "Build heal" not in md
    tex = renderers.render_latex(report)
    assert "\\begin{longtable}" in tex
    assert "Study 50\\% \\& more\\_" in tex
    assert "vendor/m0" in tex


def test_generic_render_for_other_types():
    report = Report(
        report_type="tool_analysis",
        title="Tools",
        report_data={"total_findings": 3, "tools": [{"analyzer": "bandit", "total": 3, "by_severity": {"high": 3}}]},
        status=Report.Status.COMPLETED,
    )
    md = renderers.render_markdown(report)
    assert "| analyzer | total |" in md
    assert "| bandit | 3 |" in md


def test_api_generate_and_export(client, study):
    user, study = study
    client.force_login(user)
    # Generation runs in a background thread in the app; run it inline here.
    with mock.patch("backend.reports.services.report_service._dispatch"):
        res = client.post(
            "/api/reports/generate/",
            data={"report_type": "study_report", "config": {"study_id": str(study.id)}},
            content_type="application/json",
        )
    assert res.status_code == 201, res.content
    report = Report.objects.get(report_id=res.json()["report_id"])
    from backend.reports.services.report_service import _generate

    _generate(report)
    for fmt, marker in (("md", "# "), ("tex", "longtable"), ("json", "protocol")):
        out = client.get(f"/api/reports/{report.report_id}/export/?format={fmt}")
        assert out.status_code == 200, (fmt, out.content[:200])
        assert marker in out.content.decode()
    assert client.get(f"/api/reports/{report.report_id}/export/?format=docx").status_code == 400
    report.refresh_from_db()
    assert report.summary["models"] == 4


def test_bad_ranking_criteria_rejected_up_front(client, study):
    user, study = study
    client.force_login(user)
    res = client.post(
        "/api/reports/generate/",
        data={
            "report_type": "study_report",
            "config": {"study_id": str(study.id), "ranking_criteria": [{"key": "x", "direction": "up"}]},
        },
        content_type="application/json",
    )
    assert res.status_code == 400


def _set_plan(study, **plan):
    """Swap the pre-registered plan of a locked study (the model refuses that edit, so raw SQL: test setup only)."""
    import json

    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE generation_study SET analysis_plan = %s::jsonb WHERE id = %s",
            [json.dumps({"primary_metric": "deploy_rate", **plan}), str(study.pk)],
        )
    study.refresh_from_db()


def _unlock(study):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("UPDATE generation_study SET locked_at = NULL WHERE id = %s", [str(study.pk)])
    study.refresh_from_db()


def test_planned_comparisons_include_research_question_metrics(study):
    user, study = study
    _set_plan(
        study,
        secondary_metrics=["pass_rate"],
        research_questions=[
            {"id": "RQ1", "question": "Fewer issues?", "metric": "issues_per_kloc", "direction": "less"},
        ],
    )
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    families = {f["metric"]: f for f in data["planned_comparisons"]["families"]}
    assert list(families) == ["deploy_rate", "pass_rate", "issues_per_kloc"]
    rq = families["issues_per_kloc"]
    assert (rq["role"], rq["status"]) == ("research_question", "confirmatory")
    assert rq["research_questions"] == [{"id": "RQ1", "direction": "less"}]
    assert all("p_adjusted" in c for c in rq["comparisons"])


def test_conditions_below_min_apps_are_not_tested(study):
    user, study = study
    _set_plan(study, min_apps_per_condition=4)  # every condition has 3 apps
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    planned = data["planned_comparisons"]
    assert planned["min_apps_per_condition"] == 4
    comparisons = planned["families"][0]["comparisons"]
    assert len(comparisons) == 3
    assert all(c["skipped"] == "below_min_apps" and c["p_adjusted"] is None for c in comparisons)
    assert "below_min_apps" in {v["code"] for v in data["validity"]}
    assert "Not tested (fewer than 4 apps)" in _md(data)

    _set_plan(study, min_apps_per_condition=3)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert not any(c.get("skipped") for c in data["planned_comparisons"]["families"][0]["comparisons"])


def test_truncated_runs_are_kept_by_default_and_the_plan_governs_exclusion(study):
    """Intention-to-treat by default; a plan's exclusions.truncated wins over the report setting."""
    user, study = study
    run = GenerationRun.objects.filter(study=study).first()
    run.validity = {"truncated": True, "termination": "budget_exhausted"}
    run.save(update_fields=["validity"])

    _set_plan(study)  # no exclusions: intention-to-treat
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["funnel"]["overall"]["runs"] == 12
    assert data["funnel"]["overall"]["truncated"] == 1

    _set_plan(study, exclusions={"truncated": False})
    data = study_report.generate_study_report({"study_id": str(study.id), "exclude_truncated": True}, user=user)
    assert data["funnel"]["overall"]["runs"] == 12
    assert data["protocol"]["settings"]["exclude_truncated"] is False
    assert data["protocol"]["settings"]["exclusions_source"] == "analysis_plan"
    assert "plan_exclusions" in {v["code"] for v in data["validity"]}

    _set_plan(study, exclusions={"truncated": True})
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["funnel"]["overall"]["runs"] == 11
    assert data["protocol"]["truncated_excluded"] == 1
    assert "plan_exclusions" not in {v["code"] for v in data["validity"]}


def _tested(planned):
    return [c for f in planned["families"] for c in f["comparisons"] if not c.get("skipped")]


def test_one_correction_covers_every_declared_metric(study):
    user, study = study
    _set_status(study, choices.StudyStatus.COMPLETED)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    planned = data["planned_comparisons"]
    tested = _tested(planned)
    assert (planned["multiplicity"], planned["comparisons_tested"]) == ("all", 6)  # 2 metrics x 3 conditions
    # The family size is the m Holm corrected over: comparisons with no non-zero delta have no p and are not in it.
    with_p = [c for c in tested if c["p_wilcoxon"] is not None]
    assert planned["family_size"] == len(with_p)
    smallest = min(with_p, key=lambda c: c["p_wilcoxon"])
    assert smallest["p_adjusted"] == pytest.approx(min(1.0, smallest["p_wilcoxon"] * len(with_p)), rel=1e-3)
    assert f"Adjusted over all {len(with_p)} planned tests" in _md(data)
    assert "multiplicity_per_metric" not in {v["code"] for v in data["validity"]}

    _set_plan(study, secondary_metrics=["pass_rate"], multiplicity="per_metric")
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["planned_comparisons"]["comparisons_tested"] == 6
    assert "multiplicity_per_metric" in {v["code"] for v in data["validity"]}


def test_stopped_cells_make_the_family_exploratory(study):
    user, study = study
    unit = make_unit(study.active_plan)
    make_cell(
        study.conditions.first(),
        unit,
        state=choices.CellState.EXCLUDED,
        exclusion_reason=choices.CellExclusionReason.BUDGET_STOP,
    )
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    planned = data["planned_comparisons"]
    assert planned["stopped_early"] is True
    assert {f["status"] for f in planned["families"]} == {"exploratory"}
    assert "stopped_early" in {v["code"] for v in data["validity"]}


def test_beyond_plan_pilot_and_unusable_runs_are_never_in_the_sample(study):
    """Runs beyond the plan and pilot runs are excluded; a cell without a usable run is attrition, not a failure."""
    user, study = study
    first = GenerationRun.objects.filter(study=study).order_by("created_at").first()
    cell_run(first.cell, user, attempt=2, beyond_plan=True)
    condition = study.conditions.first()
    pilot_cell = make_cell(condition, make_unit(study.active_plan), stage=choices.Stage.PILOT, block_index=9)
    _evaluated_run(pilot_cell, user, "passed")
    infra_cell = make_cell(condition, make_unit(study.active_plan), block_index=10)
    cell_run(infra_cell, user, status=choices.RunStatus.FAILED)

    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)

    assert data["funnel"]["overall"]["runs"] == 12
    protocol = data["protocol"]
    assert (protocol["beyond_plan_excluded"], protocol["pilot_excluded"]) == (1, 1)
    assert protocol["attrition_excluded"] == {"infra_error": 1}
    codes = {v["code"]: v for v in data["validity"]}
    assert codes["attrition"]["level"] == "warning"
    assert "design_extended" in codes
    md = _md(data)
    assert "| Attrition (no usable run; excluded) | infra_error: 1 |" in md


# ── code-only severities, instrument coverage, the app as the unit, condition arms ──


def _app_run(user, cell, status, *, model=None, task=None, cost=0.01):
    """One analyzed run: a 100-line app with one bandit (security) finding, evaluated with *status*."""
    run = _evaluated_run(cell, user, status, model=model, task=task, metrics={"cost_usd": cost}, code="x = 1\n" * 100)
    analysis = analyze(run, {})
    bandit = ToolResultFactory(run=analysis, tool_slug="bandit")
    FindingFactory(result=bandit, rule_id="B105", line_number=3, severity="low", file_path="app.py")
    return run, analysis


def test_security_bounds_count_code_security_issues_only(study):
    """Advisories, quality findings and test code never count as code security issues (P2-P4)."""
    user, study = study
    analyses = AnalysisRun.objects.filter(generation_job__study=study).order_by("created_at")
    for analysis in analyses:
        audit = ToolResultFactory(run=analysis, tool_slug="pip-audit", category="dependency")
        FindingFactory(
            result=audit,
            rule_id="PYSEC-2026-1",
            severity="critical",
            category="dependency",
            file_path="requirements.txt",
            line_number=None,
        )
    first = analyses.first()
    ruff = first.results.get(tool_slug="ruff")
    FindingFactory(
        result=ruff,
        rule_id="F999",
        line_number=40,
        severity="high",
        file_path="app.py",
        category="quality",
    )
    bandit = first.results.get(tool_slug="bandit")
    FindingFactory(result=bandit, rule_id="B101", line_number=2, severity="high", file_path="tests/test_app.py")

    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    per_app = data["per_app"]
    # Every kind, normalized (the advisories), under a name that cannot pass for the tool's own label.
    assert sum(r["unique_normalized_critical"] for r in per_app) == 12
    assert "unique_critical" not in per_app[0]
    assert sum(r["code_unique_tests"] for r in per_app) == 1
    security = data["security_bounds"]
    assert (security["population"], security["severity"]) == ("code_security", "normalized")
    sev = {r["severity"]: r for r in security["overall"]}
    assert (sev["critical"]["issues"], sev["high"]["issues"]) == (0, 0)
    zero = next(v for v in data["validity"] if v["code"] == "zero_severe")
    assert "No critical or high code security issues" in zero["message"]
    assert data["issues"]["totals"]["dependency_unique"] == 12
    starter = next(v for v in data["validity"] if v["code"] == "starter_dependencies")
    assert starter["section"] == "issues, security_bounds"
    inherited = data["issues"]["totals"]["dependency_inherited"]
    assert starter["message"].startswith(f"{inherited} of 12 dependency advisories")

    md = _md(data)
    assert "## Code security issues by normalized severity (heuristic), with bounds" in md
    assert "## Dependency advisories by model (package vulnerabilities, not code issues)" in md
    assert "Levels the tool printed" in md
    assert "Max severity |" not in md


def test_tools_that_missed_apps_are_named_and_left_out(study):
    user, study = study
    analyses = list(AnalysisRun.objects.filter(generation_job__study=study).order_by("created_at"))
    for analysis in analyses[1:]:
        eslint = ToolResultFactory(run=analysis, tool_slug="eslint", category="quality")
        FindingFactory(result=eslint, rule_id="no-unused-vars", severity="low", file_path="app.py")
    ToolResultFactory(run=analyses[0], tool_slug="eslint", category="quality", status=ToolResult.Status.FAILED)

    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    coverage = data["protocol"]["instrument_coverage"]
    assert "eslint" in coverage["tools_partial"]
    assert "eslint" not in {t["tool"] for t in data["issues"]["by_tool"]}
    codes = {v["code"]: v["message"] for v in data["validity"]}
    assert "eslint (11 of 12 runs measured; 1 failed)" in codes["tools_dropped"]
    md = _md(data)
    assert "| Tools left out (did not measure every run) | eslint (11 of 12 runs: 1 failed) |" in md
    assert "| Tools counted (measured every analyzed run) | bandit, radon, ruff |" in md


@pytest.fixture
def repeats_study():
    """One model, two apps: app 0 passes twice; app 1 ran once and fails every check."""
    user = UserFactory()
    study, plan = make_study(user, repeats=2)
    _set_status(study, choices.StudyStatus.COMPLETED)
    condition = make_condition(plan, LLMModelFactory(model_id="vendor/rep"), is_baseline=True)
    units = [make_unit(plan) for _ in range(2)]
    _app_run(user, make_cell(condition, units[0], repeat_index=0), "passed")
    _app_run(user, make_cell(condition, units[0], repeat_index=1, block_index=1), "passed")
    _app_run(user, make_cell(condition, units[1], repeat_index=0, block_index=2), "failed_all")
    return user, study


def test_repeats_are_averaged_per_app(repeats_study):
    """The unit is the app: an app's repeats count once (P5), in means, intervals, bounds and few_apps."""
    user, study = repeats_study
    data = study_report.generate_study_report({"study_id": str(study.id), "seed": 1}, user=user)
    (outcome,) = data["model_outcomes"]
    # Mean of app means (1.0, 0.0), not the pooled 2/3 of three runs.
    assert (outcome["apps"], outcome["runs"], outcome["pass_rate"]) == (2, 3, 0.5)
    assert outcome["functional_passed"] == 0.5
    overall = data["funnel"]["overall"]
    assert (overall["apps"], overall["runs"], overall["evaluated"]) == (2, 3, 3)
    deploy = overall["deploy_rate"]
    assert (deploy["k"], deploy["n"], deploy["apps"], deploy["ci_method"]) == (3, 3, 2, "wilson_apps")
    assert tuple(deploy["ci95"]) == pytest.approx((0.3424, 1.0), abs=1e-4)  # Wilson on 2 apps, not 3 runs
    functional = overall["functional_rate"]
    assert (functional["rate"], tuple(functional["ci95"])) == (0.5, pytest.approx((0.0945, 0.9055), abs=1e-4))
    sev = {r["severity"]: r for r in data["security_bounds"]["overall"]}
    assert sev["critical"]["apps"] == 2
    assert sev["critical"]["zero_rate_upper95"] == pytest.approx(1 - 0.05 ** (1 / 2), abs=1e-4)
    few = next(v for v in data["validity"] if v["code"] == "few_apps")
    assert "vendor/rep" in few["message"]
    assert data["code_metrics"]["by_model"][0]["apps"] == 2
    assert "100.0% (3/3 runs, 2 apps) [34.2, 100.0]" in _md(data)


def test_the_latest_evaluation_counts_and_not_measured_is_not_evaluated(repeats_study):
    """Re-evaluating appends a row; the latest counts. A tester error is not an evaluation (never a 0)."""
    user, study = repeats_study
    run = GenerationRun.objects.filter(study=study).order_by("created_at").first()
    evaluate(run, measured=False)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    row = next(r for r in data["per_app"] if r["run_id"] == str(run.id))
    assert (row["evaluation_status"], row["evaluated"], row["pass_rate"]) == ("not_measured", False, None)
    assert data["funnel"]["overall"]["evaluated"] == 2
    assert "infra_errors" in {v["code"] for v in data["validity"]}


def test_mixed_repeats_use_a_cluster_bootstrap():
    user = UserFactory()
    study, plan = make_study(user, repeats=2)
    condition = make_condition(plan, LLMModelFactory(model_id="vendor/mix"))
    block = 0
    for i in range(3):
        unit = make_unit(plan)
        _app_run(user, make_cell(condition, unit, repeat_index=0, block_index=block), "passed")
        second = "failed" if i == 0 else "passed"  # 0.5: deployed, half the checks fail
        _app_run(user, make_cell(condition, unit, repeat_index=1, block_index=block + 1), second)
        block += 2
    data = study_report.generate_study_report({"study_id": str(study.id), "seed": 3}, user=user)
    functional = data["funnel"]["overall"]["functional_rate"]
    assert functional["ci_method"] == "cluster_bootstrap"
    assert functional["rate"] == pytest.approx((0.5 + 1 + 1) / 3, abs=1e-4)
    lo, hi = functional["ci95"]
    assert lo < functional["rate"] <= hi


def test_arms_sharing_a_model_are_grouped_by_condition():
    """Two conditions on one model (prompt, stack or harness arms) are never pooled (P6)."""
    user = UserFactory()
    study, plan = make_study(user, repeats=1)
    _set_status(study, choices.StudyStatus.COMPLETED)
    shared, other = LLMModelFactory(model_id="vendor/shared"), LLMModelFactory(model_id="vendor/other")
    arms = [
        make_condition(plan, shared, label="Shared A", is_baseline=True),
        make_condition(plan, shared, label="Shared B"),
        make_condition(plan, other, label="Other"),
    ]
    units = [make_unit(plan) for _ in range(2)]
    block = 0
    for arm, status in zip(arms, ("passed", "build_failed", "passed"), strict=True):
        for unit in units:
            _app_run(user, make_cell(arm, unit, block_index=block), status)
            block += 1
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["protocol"]["grouped_by"] == "condition"
    assert data["protocol"]["models"] == ["vendor/other", "vendor/shared"]
    outcomes = {o["model"]: o for o in data["model_outcomes"]}
    assert set(outcomes) == {"Shared A", "Shared B", "Other"}
    assert (outcomes["Shared A"]["pass_rate"], outcomes["Shared B"]["pass_rate"]) == (1.0, 0.0)
    assert outcomes["Shared B"]["model_id"] == "vendor/shared"
    assert {r["model"] for r in data["funnel"]["by_model"]} == {"Shared A", "Shared B", "Other"}
    assert {r["model"] for r in data["ranking"]["ranking"]} == {"Shared A", "Shared B", "Other"}
    md = _md(data)
    assert "## Outcome funnel by condition" in md
    assert "## Issues by condition (deduplicated)" in md
    assert "| Shared B |" in md


def test_comparisons_without_pairs_are_not_tested(study):
    """A baseline without values is not a null result: no test, no 'not significant', no place in the family."""
    user, study = study
    _set_status(study, choices.StudyStatus.COMPLETED)
    _set_plan(study, secondary_metrics=["issues_per_kloc"])
    baseline = study.conditions.get(is_baseline=True)
    AnalysisRun.objects.filter(generation_job__condition=baseline).delete()
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    planned = data["planned_comparisons"]
    family = next(f for f in planned["families"] if f["metric"] == "issues_per_kloc")
    assert family["common_cells"] == 0
    assert all(c["skipped"] == "no_baseline_data" for c in family["comparisons"])
    assert all(c["significant"] is None and c["p_adjusted"] is None for c in family["comparisons"])
    assert planned["comparisons_tested"] == 3  # deploy_rate only
    message = next(v["message"] for v in data["validity"] if v["code"] == "untested_comparisons")
    assert "issues_per_kloc: cond-vendor/m1" in message
    not_analyzed = next(v["message"] for v in data["validity"] if v["code"] == "not_analyzed")
    assert not_analyzed.startswith("3 generated run(s) have no finished static analysis")
    md = _md(data)
    assert "Not tested (the baseline has no values for this metric)" in md
    assert "fewer than 0 apps" not in md


def test_significance_is_decided_on_unrounded_p():
    """10 apps, 25 tests, every delta positive: exact Holm 0.0488 < 0.05; rounding p to 0.002 first gave 0.05."""
    user = UserFactory()
    study, plan = make_study(user)
    conditions = [make_condition(plan, LLMModelFactory(), is_baseline=(i == 0)) for i in range(6)]
    metrics = ["pass_rate", "unique_issues_per_app", "issues_per_kloc", "security_per_app", "cost_per_app"]
    analysis_plan = {"primary_metric": metrics[0], "secondary_metrics": metrics[1:], "multiplicity": "all"}
    rows = []
    for c in conditions:
        better = c != conditions[0]
        for app in range(10):
            rows.append(
                {
                    "run_id": f"{c.id}-{app}",
                    "condition_id": c.id,
                    "model": "m",
                    "kind": "app_agentic",
                    "unit_key": f"task:t{app}@1",
                    "repeat_index": 0,
                    "status": "completed",
                    "generated": True,
                    "analyzed": True,
                    "evaluated": True,
                    "deployed": True,
                    "functional_passed": better,
                    "pass_rate": 0.6 if better else 0.5,
                    "unique_issues": 2 if better else 1,
                    "code_unique_model": 2 if better else 1,
                    "loc": 1000,
                    "security_unique_model": 1 if better else 0,
                    "cost": 0.2 if better else 0.1,
                },
            )
    design = {
        "planned_repeats": 1,
        "beyond_plan_excluded": 0,
        "stopped_early": False,
        "interim": False,
        "plan_locked": True,
    }
    planned = study_report.planned_comparisons_section(rows, study, analysis_plan, design)
    tested = _tested(planned)
    assert planned["family_size"] == len(tested) == 25
    assert all(c["p_wilcoxon"] == pytest.approx(2 / 2**10, rel=1e-3) for c in tested)
    assert all(c["p_adjusted"] == pytest.approx(25 * 2 / 2**10, rel=1e-3) for c in tested)
    assert all(c["significant"] is True for c in tested)


def test_confirmatory_labels_need_the_plans_analysis_on_the_planned_sample(study):
    user, study = study
    _set_plan(
        study,
        secondary_metrics=["pass_rate"],
        ranking_criteria=[{"key": "deploy_rate", "direction": "benefit", "weight": 1}],
    )
    _set_status(study, choices.StudyStatus.COMPLETED)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["protocol"]["confirmatory"] == {"design": True, "reasons": []}
    assert data["ranking"]["status"] == "confirmatory"
    assert data["model_outcomes"][0]["status"]["deploy_rate"] == "confirmatory"
    small = next(v["message"] for v in data["validity"] if v["code"] == "small_n_models")
    assert "pre-registered ranking describes these 4 models only" in small

    _set_status(study, choices.StudyStatus.RUNNING)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["ranking"]["status"] == "exploratory"
    assert set(data["model_outcomes"][0]["status"].values()) == {"exploratory"}
    assert data["protocol"]["confirmatory"]["reasons"] == ["interim: the study has not finished"]

    _set_status(study, choices.StudyStatus.STOPPED)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["planned_comparisons"]["stopped_early"] is True
    assert data["ranking"]["status"] == "exploratory"
    assert "| Confirmatory analysis | no: stopped early: planned cells were cancelled |" in _md(data)

    _set_status(study, choices.StudyStatus.COMPLETED)
    _unlock(study)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert {f["status"] for f in data["planned_comparisons"]["families"]} == {"exploratory"}
    assert "plan_not_locked" in {v["code"] for v in data["validity"]}


def test_protocol_names_the_study_baseline_and_plan(study):
    user, study = study
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    report = Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed")
    md = renderers.render_markdown(report)
    baseline = data["planned_comparisons"]["baseline_condition"]
    assert baseline == data["protocol"]["study"]["baseline_condition"] == "cond-vendor/m0"
    assert f"| Baseline condition | {baseline} |" in md
    assert f"| Study | {study.name} |" in md
    assert "| Primary metric | deploy_rate |" in md
    assert f"vs baseline {baseline}" in md
    assert "## Plan metrics by model" in md
    tex = renderers.render_latex(report)
    assert "Correct \\textbar{} deployed" in tex
    assert "\\textless{} 0.05" in tex


def test_an_empty_scope_reports_unmeasured_not_zero():
    data = study_report.generate_study_report({"seed": 0}, user=UserFactory())
    assert data["funnel"]["overall"]["runs"] == 0
    assert data["funnel"]["overall"]["deploy_rate"]["rate"] is None
    assert all(r["issues"] is None for r in data["security_bounds"]["overall"])
    report = Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed")
    assert "Generated before" not in renderers.render_markdown(report)
    assert "\\begin{longtable}" in renderers.render_latex(report)


def test_mixed_configuration_fingerprints_within_a_cell_group_are_flagged(repeats_study):
    """Repeats of one (condition, unit) must share a configuration; a mix is pooled and said so."""
    user, study = repeats_study
    runs = list(GenerationRun.objects.filter(study=study).order_by("created_at"))
    GenerationRun.objects.filter(pk=runs[0].pk).update(config_fingerprint="a" * 64)
    GenerationRun.objects.filter(pk=runs[1].pk).update(config_fingerprint="b" * 64)
    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    assert data["protocol"]["mixed_fingerprint_cells"] == 1
    assert "mixed_fingerprints" in {v["code"] for v in data["validity"]}


# ── recorded instruments, ranking ties, one label per group, model-level correlations ──


def test_mixed_commands_and_parsers_of_counted_tools_are_flagged(study):
    """The command and parser behind each counted result are recorded; a mix within a counted tool is a threat."""
    from backend.analysis.services.runner import command_fingerprint

    user, study = study
    results = ToolResult.objects.filter(run__generation_job__study=study)
    for i, result in enumerate(results.filter(tool_slug="bandit").order_by("id")):
        result.summary = {"command_sha": "aaa" if i < 6 else "bbb", "parser_sha": "p1"}
        result.save(update_fields=["summary"])
    # Stored before the command hash existed: the recorded command is hashed instead.
    results.filter(tool_slug="ruff").update(raw_output={"command": "ruff check /work", "stdout": "[]"})
    # Same command and parser, but the file patterns that decide applicability changed between analyses.
    for i, result in enumerate(results.filter(tool_slug="radon").order_by("id")):
        result.summary = {"patterns_sha": "f1" if i < 6 else "f2"}
        result.save(update_fields=["summary"])
    # A tool left out of every count (it missed a run) pools nothing, whatever its commands.
    analyses = list(AnalysisRun.objects.filter(generation_job__study=study).order_by("created_at"))
    for i, analysis in enumerate(analyses[1:]):
        ToolResultFactory(run=analysis, tool_slug="eslint", category="quality", summary={"command_sha": f"e{i % 2}"})

    data = study_report.generate_study_report({"study_id": str(study.id)}, user=user)
    tools = {t["slug"]: t for t in data["protocol"]["tools"]}
    assert (tools["bandit"]["commands"], tools["bandit"]["parsers"]) == ({"aaa": 6, "bbb": 6}, {"p1": 12})
    assert tools["ruff"]["commands"] == {command_fingerprint("ruff check /work"): 12}
    assert tools["radon"]["commands"] == {}  # no command recorded: not counted, never guessed
    assert len(tools["eslint"]["commands"]) == 2
    message = next(v["message"] for v in data["validity"] if v["code"] == "mixed_tool_commands")
    assert "bandit (commands)" in message
    assert tools["radon"]["patterns"] == {"f1": 6, "f2": 6}
    assert "radon (file patterns)" in message
    assert "ruff" not in message
    assert "eslint" not in message


@pytest.fixture
def arms_and_adhoc():
    """One model under two conditions that share a label, plus runs outside the study for two models.

    Both arms deploy and pass; the runs outside the study never build.
    """
    user = UserFactory()
    shared = LLMModelFactory(model_id="vendor/shared", context_window=64_000)
    other = LLMModelFactory(model_id="vendor/other", context_window=32_000)
    study, plan = make_study(user, repeats=1)
    _set_status(study, choices.StudyStatus.COMPLETED)
    arms = [make_condition(plan, shared, label="Same") for _ in range(2)]
    units = [make_unit(plan) for _ in range(2)]
    block = 0
    for arm in arms:
        for unit in units:
            _app_run(user, make_cell(arm, unit, block_index=block), "passed")
            block += 1
    for model in (shared, other):
        for unit in units:
            _app_run(user, None, "build_failed", model=model, task=unit.task_version)
    return user, arms


def test_every_table_names_a_group_alike(arms_and_adhoc):
    """Issue tables use the funnel's groups: colliding labels get their id, ad-hoc runs stay per model."""
    user, arms = arms_and_adhoc
    data = study_report.generate_study_report({"seed": 0}, user=user)
    assert data["protocol"]["grouped_by"] == "condition"
    a, b = (f"Same (#{arm.id})" for arm in arms)
    expected = {a, b, "vendor/other", "vendor/shared"}
    assert {r["model"] for r in data["funnel"]["by_model"]} == expected
    by_group = {r["model"]: r for r in data["issues"]["by_group"]}
    assert set(by_group) == expected
    # by_condition pools every run outside the study, whatever its model, in one row; by_group does not.
    assert (by_group["vendor/other"]["apps"], by_group["vendor/other"]["runs"]) == (2, 2)
    assert by_group[a]["condition_id"] == arms[0].id

    issues = _md(data).split("## Issues by condition (deduplicated)")[1].split("\n## ")[0]
    assert f"| {a} |" in issues
    assert f"| {b} |" in issues
    assert "| vendor/other |" in issues
    assert "\n| - |" not in issues


def test_model_parameter_correlations_count_models_not_arms(arms_and_adhoc):
    """Arms of one model share its parameters: the model is one point (its arms averaged), so n counts models."""
    user, _arms = arms_and_adhoc
    data = study_report.generate_study_report({"seed": 0}, user=user)
    corr = data["correlations"]
    assert (corr["n_models"], corr["arms_averaged"]) == (2, True)
    assert [m["model"] for m in corr["model_parameters"]] == ["vendor/other", "vendor/shared"]
    test = next(t for t in corr["tests"] if t["predictor"] == "context_window_nominal" and t["outcome"] == "pass_rate")
    assert test["n_models"] == 2
    assert "is one point: the mean of its conditions' values" in " ".join(corr["notes"])
    # Each arm weighs the same; an arm without a value does not count as 0.
    level = study_report.model_level_criteria(
        {
            "A1": {"pass_rate": 1.0, "cost_per_app": None},
            "A2": {"pass_rate": 0.5, "cost_per_app": 0.2},
            "B": {"pass_rate": 0.0, "cost_per_app": 0.1},
        },
        {"A1": [{"model": "vendor/a"}], "A2": [{"model": "vendor/a"}], "B": [{"model": "vendor/b"}]},
    )
    assert level == {
        "vendor/a": {"pass_rate": 0.75, "cost_per_app": 0.2},
        "vendor/b": {"pass_rate": 0.0, "cost_per_app": 0.1},
    }


def test_ranking_ties_are_a_validity_warning(arms_and_adhoc):
    user, arms = arms_and_adhoc
    criteria = [
        {"key": "deploy_rate", "direction": "benefit", "weight": 1},
        {"key": "pass_rate", "direction": "benefit", "weight": 1},
    ]
    data = study_report.generate_study_report({"seed": 0, "ranking_criteria": criteria}, user=user)
    ranking = data["ranking"]
    assert ranking["models_tied"] is True
    top = " = ".join(sorted(f"Same (#{arm.id})" for arm in arms))
    tie = next(v for v in data["validity"] if v["code"] == "ranking_tied")
    assert tie["section"] == "ranking"
    assert f"{top}; vendor/other = vendor/shared" in tie["message"]
    assert f"Tied: {top}; vendor/other = vendor/shared." in _md(data)


def test_uncovered_ai_findings_are_not_counted(study):
    user, study = study
    data = study_report.generate_study_report({"study_id": str(study.id), "include_ai": True}, user=user)
    assert data["issues"]["params"]["ai_counted"] == []
    excluded = next(v["message"] for v in data["validity"] if v["code"] == "ai_excluded")
    assert excluded.startswith("AI-reviewer findings were requested, but no AI reviewer reviewed every analyzed app")
    assert (
        "| AI-reviewer findings | requested, but none counted (no AI reviewer reviewed every analyzed run) |"
        in _md(data)
    )
