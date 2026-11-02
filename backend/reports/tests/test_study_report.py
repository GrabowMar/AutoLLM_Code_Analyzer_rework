"""The study report end to end: sections, validity checks, planned comparisons, Markdown/LaTeX export."""

from __future__ import annotations

from unittest import mock

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.analysis_plan import normalize_analysis_plan
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.models import Report
from backend.reports.services import renderers
from backend.reports.services import study_report
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

STATUSES = ["passed", "failed", "build_failed", "passed", "start_failed", "passed"]


@pytest.fixture
def study():
    user = UserFactory()
    templates = [AppRequirementTemplateFactory(slug=f"tpl-{i}") for i in range(3)]
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
    experiment = ExperimentFactory(created_by=user, mode="scaffolding")
    conditions = [ExperimentConditionFactory(experiment=experiment, model=m) for m in models]
    experiment.baseline_condition = conditions[0]
    experiment.analysis_plan = normalize_analysis_plan(
        {"primary_metric": "deploy_rate", "secondary_metrics": ["pass_rate"], "correction": "holm"},
    )
    # Launch freezes the plan: only a frozen plan is pre-registered.
    experiment.analysis_plan_frozen_at = timezone.now()
    experiment.save()
    n = 0
    for c in conditions:
        for t in templates:
            status = STATUSES[n % len(STATUSES)]
            n += 1
            job = GenerationJobFactory(
                created_by=user,
                mode="scaffolding",
                status=GenerationJob.Status.COMPLETED,
                model=c.model,
                app_requirement=t,
                experiment=experiment,
                condition=c,
                stack_slug="flask-react",
                prompt_hash=f"h-{t.slug}",
                result_data={
                    "files": {"app.py": "x = 1\n" * (50 + n * 10), "frontend/src/App.jsx": "let a\n" * 30},
                    "evaluation": {
                        "status": status,
                        "summary": {"pass_rate": 1.0 if status == "passed" else 0.5 if status == "failed" else 0.0},
                        "build": {
                            "healing": {"mode": "full", "applicable": True, "changed": n % 2 == 0, "patches": []},
                        },
                    },
                },
                metrics={"cost": 0.01 * (n % 3 + 1), "functional": {"deployed": status in ("passed", "failed")}},
            )
            run = AnalysisRunFactory(
                created_by=user,
                generation_job=job,
                status=AnalysisRun.Status.COMPLETED,
                source_code=None,
            )
            bandit = ToolResultFactory(run=run, tool_slug="bandit")
            ruff = ToolResultFactory(run=run, tool_slug="ruff", category="quality")
            ToolResultFactory(
                run=run,
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
    return user, experiment


def test_study_report_sections(study):
    user, experiment = study
    data = study_report.generate_study_report({"experiment_id": str(experiment.id), "seed": 1}, user=user)

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
    overall = data["funnel"]["overall"]
    assert (overall["jobs"], overall["evaluated"]) == (12, 12)
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
    assert {"small_n_models", "zero_severe", "effective_context", "single_stack", "healing", "duplicates"} <= codes
    assert "no_plan" not in codes

    assert data["code_metrics"]["by_model"][0]["avg_complexity"]["n"] == 3
    assert len(data["protocol"]["data_fingerprint"]) == 64


def test_report_is_reproducible(study):
    user, experiment = study
    config = {"experiment_id": str(experiment.id), "seed": 4}
    a = study_report.generate_study_report(config, user=user)
    b = study_report.generate_study_report(config, user=user)
    for key in ("ranking", "correlations", "funnel", "issues"):
        assert a[key] == b[key]


def test_other_users_experiment_is_refused(study):
    _user, experiment = study
    with pytest.raises(ValueError, match="not found"):
        study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=UserFactory())


def test_markdown_and_latex_render(study):
    user, experiment = study
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
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
    user, experiment = study
    client.force_login(user)
    # Generation runs in a background thread in the app; run it inline here.
    with mock.patch("backend.reports.services.report_service._dispatch"):
        res = client.post(
            "/api/reports/generate/",
            data={"report_type": "study_report", "config": {"experiment_id": str(experiment.id)}},
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
    user, experiment = study
    client.force_login(user)
    res = client.post(
        "/api/reports/generate/",
        data={
            "report_type": "study_report",
            "config": {"experiment_id": str(experiment.id), "ranking_criteria": [{"key": "x", "direction": "up"}]},
        },
        content_type="application/json",
    )
    assert res.status_code == 400


def _set_plan(experiment, **plan):
    experiment.analysis_plan = normalize_analysis_plan({"primary_metric": "deploy_rate", **plan})
    experiment.save(update_fields=["analysis_plan"])


def test_planned_comparisons_include_research_question_metrics(study):
    user, experiment = study
    _set_plan(
        experiment,
        secondary_metrics=["pass_rate"],
        research_questions=[
            {"id": "RQ1", "question": "Fewer issues?", "metric": "issues_per_kloc", "direction": "less"},
        ],
    )
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    families = {f["metric"]: f for f in data["planned_comparisons"]["families"]}
    assert list(families) == ["deploy_rate", "pass_rate", "issues_per_kloc"]
    rq = families["issues_per_kloc"]
    assert (rq["role"], rq["status"]) == ("research_question", "confirmatory")
    assert rq["research_questions"] == [{"id": "RQ1", "direction": "less"}]
    assert all("p_adjusted" in c for c in rq["comparisons"])


def test_conditions_below_min_apps_are_not_tested(study):
    user, experiment = study
    _set_plan(experiment, min_apps_per_condition=4)  # every condition has 3 apps
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    planned = data["planned_comparisons"]
    assert planned["min_apps_per_condition"] == 4
    comparisons = planned["families"][0]["comparisons"]
    assert len(comparisons) == 3
    assert all(c["skipped"] == "below_min_apps" and c["p_adjusted"] is None for c in comparisons)
    assert "below_min_apps" in {v["code"] for v in data["validity"]}
    report = Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed")
    assert "Not tested (fewer than 4 apps)" in renderers.render_markdown(report)

    _set_plan(experiment, min_apps_per_condition=3)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert not any(c.get("skipped") for c in data["planned_comparisons"]["families"][0]["comparisons"])


def test_plan_exclusions_govern_truncation(study):
    user, experiment = study
    job = GenerationJob.objects.filter(experiment=experiment).first()
    job.result_data = {**job.result_data, "backend_truncated": True}
    job.save(update_fields=["result_data"])
    config = {"experiment_id": str(experiment.id), "exclude_truncated": True}

    _set_plan(experiment, exclusions={"truncated": False})
    data = study_report.generate_study_report(config, user=user)
    assert data["funnel"]["overall"]["jobs"] == 12
    assert data["protocol"]["settings"]["exclude_truncated"] is False
    assert data["protocol"]["settings"]["exclusions_source"] == "analysis_plan"
    assert "plan_exclusions" in {v["code"] for v in data["validity"]}

    _set_plan(experiment)  # default plan exclusions: truncated excluded
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert data["funnel"]["overall"]["jobs"] == 11
    assert "plan_exclusions" not in {v["code"] for v in data["validity"]}


def _tested(planned):
    return [c for f in planned["families"] for c in f["comparisons"] if not c.get("skipped")]


def test_one_correction_covers_every_declared_metric(study):
    user, experiment = study
    Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    planned = data["planned_comparisons"]
    tested = _tested(planned)
    assert (planned["multiplicity"], planned["comparisons_tested"]) == ("all", 6)  # 2 metrics x 3 conditions
    # The family size is the m Holm corrected over: comparisons with no non-zero delta have no p and are not in it.
    with_p = [c for c in tested if c["p_wilcoxon"] is not None]
    assert planned["family_size"] == len(with_p) == 4
    smallest = min(with_p, key=lambda c: c["p_wilcoxon"])
    assert smallest["p_adjusted"] == pytest.approx(min(1.0, smallest["p_wilcoxon"] * 4), rel=1e-3)
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "Adjusted over all 4 planned tests" in md
    assert sum(1 for c in tested if c["in_family"] is False) == 2
    assert "comparison(s) here have no p-value" in md
    assert "multiplicity_per_metric" not in {v["code"] for v in data["validity"]}

    _set_plan(experiment, secondary_metrics=["pass_rate"], multiplicity="per_metric")
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert (data["planned_comparisons"]["comparisons_tested"], data["planned_comparisons"]["family_size"]) == (6, 4)
    assert "multiplicity_per_metric" in {v["code"] for v in data["validity"]}


def test_stopped_early_and_extended_designs(study):
    user, experiment = study
    job = GenerationJob.objects.filter(experiment=experiment).first()
    job.status = GenerationJob.Status.CANCELLED
    job.save(update_fields=["status"])
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    planned = data["planned_comparisons"]
    assert planned["stopped_early"] is True
    assert {f["status"] for f in planned["families"]} == {"exploratory"}
    assert "stopped_early" in {v["code"] for v in data["validity"]}

    job.status = GenerationJob.Status.COMPLETED
    job.save(update_fields=["status"])
    GenerationBatch.objects.create(
        name="launch",
        mode="scaffolding",
        created_by=user,
        experiment=experiment,
        kind=GenerationBatch.Kind.LAUNCH,
        request={"trials": 1},
    )
    extra = GenerationJob.objects.filter(experiment=experiment).first()
    Experiment.objects.filter(id=experiment.id).update(repeats=2)
    GenerationJob.objects.filter(id=extra.id).update(repeat_index=1)
    experiment.refresh_from_db()
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    planned = data["planned_comparisons"]
    assert (planned["planned_repeats"], planned["beyond_plan_excluded"], planned["stopped_early"]) == (1, 1, False)
    assert {f["status"] for f in planned["families"]} == {"confirmatory"}
    assert "design_extended" in {v["code"] for v in data["validity"]}


# ── code-only severities, instrument coverage, the app as the unit, condition arms ──


def _job(user, experiment, condition, template, repeat, status, pass_rate, *, cost=0.01, model=None, ablation=None):
    """One analyzed run: a 3-line app with one bandit (security) finding, evaluated with *status*.

    *condition* None is a job outside any experiment (of *model*); *ablation*
    is its ``healing_ablation`` (deploy outcome per other healing mode).
    """
    job = GenerationJobFactory(
        created_by=user,
        mode="scaffolding",
        status=GenerationJob.Status.COMPLETED,
        model=model or condition.model,
        app_requirement=template,
        experiment=experiment,
        condition=condition,
        repeat_index=repeat,
        stack_slug="flask-react",
        prompt_hash=f"h-{template.slug}",
        result_data={
            "files": {"app.py": "x = 1\n" * 100},
            "evaluation": {"status": status, "summary": {"pass_rate": pass_rate}},
            **({"healing_ablation": ablation} if ablation else {}),
        },
        metrics={"cost": cost},
    )
    run = AnalysisRunFactory(
        created_by=user,
        generation_job=job,
        status=AnalysisRun.Status.COMPLETED,
        source_code=None,
    )
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    FindingFactory(result=bandit, rule_id="B105", line_number=3, severity="low", file_path="app.py")
    return job, run


def test_security_bounds_count_code_security_issues_only(study):
    """Advisories, quality findings and test code never count as code security issues (P2-P4)."""
    user, experiment = study
    runs = AnalysisRun.objects.filter(generation_job__experiment=experiment).order_by("created_at")
    for run in runs:
        audit = ToolResultFactory(run=run, tool_slug="pip-audit", category="dependency")
        FindingFactory(
            result=audit,
            rule_id="PYSEC-2026-1",
            severity="critical",
            category="dependency",
            file_path="requirements.txt",
            line_number=None,
        )
    first = runs.first()
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

    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
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
    scaffold = next(v for v in data["validity"] if v["code"] == "scaffold_dependencies")
    assert scaffold["section"] == "issues, security_bounds"
    inherited = data["issues"]["totals"]["dependency_inherited"]
    assert scaffold["message"].startswith(f"{inherited} of 12 dependency advisories")

    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "## Code security issues by normalized severity (heuristic), with bounds" in md
    assert "## Dependency advisories by model (package vulnerabilities, not code issues)" in md
    assert "Levels the tool printed" in md
    assert "Max severity |" not in md


def test_tools_that_missed_apps_are_named_and_left_out(study):
    user, experiment = study
    runs = list(AnalysisRun.objects.filter(generation_job__experiment=experiment).order_by("created_at"))
    for run in runs[1:]:
        eslint = ToolResultFactory(run=run, tool_slug="eslint", category="quality")
        FindingFactory(result=eslint, rule_id="no-unused-vars", severity="low", file_path="frontend/src/App.jsx")
    ToolResultFactory(run=runs[0], tool_slug="eslint", category="quality", status=ToolResult.Status.FAILED)

    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    coverage = data["protocol"]["instrument_coverage"]
    assert "eslint" in coverage["tools_partial"]
    assert "eslint" not in {t["tool"] for t in data["issues"]["by_tool"]}
    codes = {v["code"]: v["message"] for v in data["validity"]}
    assert "tool_failures" not in codes
    assert "eslint (11 of 12 runs measured; 1 failed)" in codes["tools_dropped"]
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "| Tools left out (did not measure every run) | eslint (11 of 12 runs: 1 failed) |" in md
    assert "| Tools counted (measured every analyzed run) | bandit, radon, ruff |" in md


@pytest.fixture
def repeats_study():
    """One model, two apps: app 0 passes twice; app 1's second repeat is missing (one failing run)."""
    user = UserFactory()
    templates = [AppRequirementTemplateFactory(slug=f"rep-{i}") for i in range(2)]
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=2, status=Experiment.Status.COMPLETED)
    condition = ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(model_id="vendor/rep"))
    experiment.baseline_condition = condition
    experiment.save()
    _job(user, experiment, condition, templates[0], 0, "passed", 1.0)
    _job(user, experiment, condition, templates[0], 1, "passed", 1.0)
    _job(user, experiment, condition, templates[1], 0, "failed", 0.0)
    return user, experiment


def test_repeats_are_averaged_per_app(repeats_study):
    """The unit is the app: an app's repeats count once (P5), in means, intervals, bounds and few_apps."""
    user, experiment = repeats_study
    data = study_report.generate_study_report({"experiment_id": str(experiment.id), "seed": 1}, user=user)
    (outcome,) = data["model_outcomes"]
    # Mean of app means (1.0, 0.0), not the pooled 2/3 of three runs.
    assert (outcome["apps"], outcome["runs"], outcome["pass_rate"]) == (2, 3, 0.5)
    assert outcome["functional_passed"] == 0.5
    overall = data["funnel"]["overall"]
    assert (overall["apps"], overall["jobs"], overall["evaluated"]) == (2, 3, 3)
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
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "100.0% (3/3 runs, 2 apps) [34.2, 100.0]" in md


def test_mixed_repeats_use_a_cluster_bootstrap():
    user = UserFactory()
    templates = [AppRequirementTemplateFactory(slug=f"mix-{i}") for i in range(3)]
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=2)
    condition = ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(model_id="vendor/mix"))
    for i, t in enumerate(templates):
        _job(user, experiment, condition, t, 0, "passed", 1.0)
        _job(user, experiment, condition, t, 1, "failed" if i == 0 else "passed", 0.5 if i == 0 else 1.0)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id), "seed": 3}, user=user)
    functional = data["funnel"]["overall"]["functional_rate"]
    assert functional["ci_method"] == "cluster_bootstrap"
    assert functional["rate"] == pytest.approx((0.5 + 1 + 1) / 3, abs=1e-4)
    lo, hi = functional["ci95"]
    assert lo < functional["rate"] <= hi


def test_arms_sharing_a_model_are_grouped_by_condition():
    """Two conditions on one model (prompt-profile or harness arms) are never pooled (P6)."""
    user = UserFactory()
    templates = [AppRequirementTemplateFactory(slug=f"arm-{i}") for i in range(2)]
    shared, other = LLMModelFactory(model_id="vendor/shared"), LLMModelFactory(model_id="vendor/other")
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=1, status=Experiment.Status.COMPLETED)
    arms = [
        ExperimentConditionFactory(experiment=experiment, model=shared, label="Shared A"),
        ExperimentConditionFactory(experiment=experiment, model=shared, label="Shared B"),
        ExperimentConditionFactory(experiment=experiment, model=other, label="Other"),
    ]
    for arm, (status, rate) in zip(arms, (("passed", 1.0), ("failed", 0.0), ("passed", 1.0)), strict=True):
        for t in templates:
            _job(user, experiment, arm, t, 0, status, rate)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert data["protocol"]["grouped_by"] == "condition"
    assert data["protocol"]["models"] == ["vendor/other", "vendor/shared"]
    outcomes = {o["model"]: o for o in data["model_outcomes"]}
    assert set(outcomes) == {"Shared A", "Shared B", "Other"}
    assert (outcomes["Shared A"]["pass_rate"], outcomes["Shared B"]["pass_rate"]) == (1.0, 0.0)
    assert outcomes["Shared B"]["model_id"] == "vendor/shared"
    assert {r["model"] for r in data["funnel"]["by_model"]} == {"Shared A", "Shared B", "Other"}
    assert {r["model"] for r in data["ranking"]["ranking"]} == {"Shared A", "Shared B", "Other"}
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "## Outcome funnel by condition" in md
    assert "## Issues by condition (deduplicated)" in md
    assert "| Shared B |" in md


def test_comparisons_without_pairs_are_not_tested(study):
    """A baseline without values is not a null result: no test, no 'not significant', no place in the family."""
    user, experiment = study
    Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
    _set_plan(experiment, secondary_metrics=["issues_per_kloc"])
    AnalysisRun.objects.filter(generation_job__condition=experiment.baseline_condition).delete()
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    planned = data["planned_comparisons"]
    family = next(f for f in planned["families"] if f["metric"] == "issues_per_kloc")
    assert family["common_cells"] == 0
    assert all(c["skipped"] == "no_baseline_data" for c in family["comparisons"])
    assert all(c["significant"] is None and c["p_adjusted"] is None for c in family["comparisons"])
    assert planned["comparisons_tested"] == 3  # deploy_rate only
    message = next(v["message"] for v in data["validity"] if v["code"] == "untested_comparisons")
    m1 = experiment.conditions.select_related("model").get(model__model_id="vendor/m1")
    assert f"issues_per_kloc: {study_report.condition_label(m1)}" in message
    not_analyzed = next(v["message"] for v in data["validity"] if v["code"] == "not_analyzed")
    assert not_analyzed.startswith("3 generated run(s) have no finished static analysis")
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "Not tested (the baseline has no values for this metric)" in md
    assert "fewer than 0 apps" not in md


def test_significance_is_decided_on_unrounded_p():
    """10 apps, 25 tests, every delta positive: exact Holm 0.0488 < 0.05; rounding p to 0.002 first gave 0.05."""
    experiment = ExperimentFactory(status=Experiment.Status.COMPLETED)
    conditions = [ExperimentConditionFactory(experiment=experiment) for _ in range(6)]
    experiment.baseline_condition = conditions[0]
    experiment.save()
    metrics = ["pass_rate", "unique_issues_per_app", "issues_per_kloc", "security_per_app", "cost_per_app"]
    plan = normalize_analysis_plan(
        {"primary_metric": metrics[0], "secondary_metrics": metrics[1:], "multiplicity": "all"},
    )
    rows = []
    for c in conditions:
        better = c != conditions[0]
        for app in range(10):
            rows.append(
                {
                    "job_id": f"{c.id}-{app}",
                    "condition_id": c.id,
                    "model": "m",
                    "mode": "scaffolding",
                    "app_id": app,
                    "repeat_index": 0,
                    "status": "completed",
                    "generated": True,
                    "analyzed": True,
                    "evaluated": True,
                    "deployed": True,
                    "functional_passed": better,
                    "pass_rate": 0.6 if better else 0.5,
                    "unique_issues": 2 if better else 1,
                    "code_unique": 2 if better else 1,
                    "loc": 1000,
                    "security_unique": 1 if better else 0,
                    "cost": 0.2 if better else 0.1,
                },
            )
    design = {
        "planned_repeats": 1,
        "beyond_plan_excluded": 0,
        "stopped_early": False,
        "interim": False,
        "plan_frozen": True,
    }
    planned = study_report.planned_comparisons_section(rows, experiment, plan, design)
    tested = _tested(planned)
    assert planned["family_size"] == len(tested) == 25
    assert all(c["p_wilcoxon"] == pytest.approx(2 / 2**10, rel=1e-3) for c in tested)
    assert all(c["p_adjusted"] == pytest.approx(25 * 2 / 2**10, rel=1e-3) for c in tested)
    assert all(c["significant"] is True for c in tested)


def test_confirmatory_labels_need_the_plans_analysis_on_the_planned_sample(study):
    user, experiment = study
    _set_plan(
        experiment,
        secondary_metrics=["pass_rate"],
        ranking_criteria=[{"key": "deploy_rate", "direction": "benefit", "weight": 1}],
    )
    Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert data["protocol"]["confirmatory"] == {"design": True, "reasons": []}
    assert data["ranking"]["status"] == "confirmatory"
    assert data["model_outcomes"][0]["status"]["deploy_rate"] == "confirmatory"
    small = next(v["message"] for v in data["validity"] if v["code"] == "small_n_models")
    assert "pre-registered ranking describes these 4 models only" in small

    Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.RUNNING)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert data["ranking"]["status"] == "exploratory"
    assert set(data["model_outcomes"][0]["status"].values()) == {"exploratory"}
    assert data["protocol"]["confirmatory"]["reasons"] == ["interim: the experiment has not finished"]

    Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
    job = GenerationJob.objects.filter(experiment=experiment).first()
    GenerationJob.objects.filter(id=job.id).update(status=GenerationJob.Status.CANCELLED)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert data["planned_comparisons"]["stopped_early"] is True
    assert data["ranking"]["status"] == "exploratory"
    assert set(data["model_outcomes"][0]["status"].values()) == {"exploratory"}
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "| Confirmatory analysis | no: stopped early: planned cells were cancelled |" in md

    Experiment.objects.filter(id=experiment.id).update(analysis_plan_frozen_at=None)
    GenerationJob.objects.filter(id=job.id).update(status=GenerationJob.Status.COMPLETED)
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    assert {f["status"] for f in data["planned_comparisons"]["families"]} == {"exploratory"}
    assert "plan_not_frozen" in {v["code"] for v in data["validity"]}


def test_protocol_names_the_experiment_baseline_and_plan(study):
    user, experiment = study
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    report = Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed")
    md = renderers.render_markdown(report)
    baseline = data["planned_comparisons"]["baseline_condition"]
    assert baseline == data["protocol"]["experiment"]["baseline_condition"]
    assert f"| Baseline condition | {baseline} |" in md
    assert f"| Experiment | {experiment.name} |" in md
    assert "| Primary metric | deploy_rate |" in md
    assert f"vs baseline {baseline}" in md
    assert "## Plan metrics by model" in md
    tex = renderers.render_latex(report)
    assert "Correct \\textbar{} deployed" in tex
    assert "\\textless{} 0.05" in tex


def test_an_empty_scope_reports_unmeasured_not_zero():
    data = study_report.generate_study_report({"seed": 0}, user=UserFactory())
    assert data["funnel"]["overall"]["jobs"] == 0
    assert data["funnel"]["overall"]["deploy_rate"]["rate"] is None
    assert all(r["issues"] is None for r in data["security_bounds"]["overall"])
    report = Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed")
    md = renderers.render_markdown(report)
    assert "Generated before" not in md
    assert "\\begin{longtable}" in renderers.render_latex(report)


# ── recorded instruments, ranking ties, one label per group, model-level correlations ──


def test_mixed_commands_and_parsers_of_counted_tools_are_flagged(study):
    """The command and parser behind each counted result are recorded; a mix within a counted tool is a threat."""
    from backend.analysis.services.runner import command_fingerprint

    user, experiment = study
    results = ToolResult.objects.filter(run__generation_job__experiment=experiment)
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
    runs = list(AnalysisRun.objects.filter(generation_job__experiment=experiment).order_by("created_at"))
    for i, run in enumerate(runs[1:]):
        ToolResultFactory(run=run, tool_slug="eslint", category="quality", summary={"command_sha": f"e{i % 2}"})

    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
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
    """One model under two conditions that share a label, plus jobs outside the experiment for two models.

    Arm 0 deploys only because the build was healed (unhealed: not deployed);
    arm 1 deploys either way. The jobs outside the experiment never build.
    """
    user = UserFactory()
    templates = [AppRequirementTemplateFactory(slug=f"col-{i}") for i in range(2)]
    shared = LLMModelFactory(model_id="vendor/shared", context_window=64_000)
    other = LLMModelFactory(model_id="vendor/other", context_window=32_000)
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=1, status=Experiment.Status.COMPLETED)
    arms = [ExperimentConditionFactory(experiment=experiment, model=shared, label="Same") for _ in range(2)]
    for arm, unhealed in zip(arms, (False, True), strict=True):
        for t in templates:
            _job(user, experiment, arm, t, 0, "passed", 1.0, ablation={"none": {"deployed": unhealed}})
    for model in (shared, other):
        for t in templates:
            _job(user, None, None, t, 0, "build_failed", 0.0, model=model, ablation={"none": {"deployed": False}})
    return user, arms


def test_every_table_names_a_group_alike(arms_and_adhoc):
    """Issue and healing tables use the funnel's groups: colliding labels get their id, ad-hoc jobs stay per model."""
    user, arms = arms_and_adhoc
    data = study_report.generate_study_report({"seed": 0}, user=user)
    assert data["protocol"]["grouped_by"] == "condition"
    a, b = (f"Same (#{arm.id})" for arm in arms)
    expected = {a, b, "vendor/other", "vendor/shared"}
    assert {r["model"] for r in data["funnel"]["by_model"]} == expected
    by_group = {r["model"]: r for r in data["issues"]["by_group"]}
    assert set(by_group) == expected
    # by_condition pools every job outside the experiment, whatever its model, in one row; by_group does not.
    assert (by_group["vendor/other"]["apps"], by_group["vendor/other"]["runs"]) == (2, 2)
    assert by_group[a]["condition_id"] == arms[0].id
    healing = {r["model"]: r for r in data["healing"]["by_group"]}
    assert set(healing) == expected
    assert (healing[a]["rescued_vs_none"], healing[b]["rescued_vs_none"]) == (2, 0)

    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    issues = md.split("## Issues by condition (deduplicated)")[1].split("\n## ")[0]
    assert f"| {a} |" in issues
    assert f"| {b} |" in issues
    assert "| vendor/other |" in issues
    assert "\n| - |" not in issues
    healing_table = md.split("## Deploy rate with and without build healing")[1].split("\n## ")[0]
    # Distinct apps next to runs (each arm built 2 apps once).
    assert "| Condition | Apps | Runs |" in healing_table
    assert f"| {a} | 2 | 2 |" in healing_table


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
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert f"Tied: {top}; vendor/other = vendor/shared." in md


def test_unknown_test_code_and_uncovered_ai_findings_are_not_zero(study):
    user, experiment = study
    # A single-blob result has no paths: its test code is unknown, not zero lines.
    job = GenerationJob.objects.filter(experiment=experiment).first()
    GenerationJob.objects.filter(id=job.id).update(
        result_data={**job.result_data, "files": {}, "backend_code": "x = 1\n" * 20},
    )
    data = study_report.generate_study_report({"experiment_id": str(experiment.id), "include_ai": True}, user=user)
    test_loc = {r["job_id"]: r["test_loc"] for r in data["per_app"]}
    assert test_loc[str(job.id)] is None
    assert set(test_loc.values()) == {0, None}
    assert data["issues"]["params"]["ai_counted"] == []
    excluded = next(v["message"] for v in data["validity"] if v["code"] == "ai_excluded")
    assert excluded.startswith("AI-reviewer findings were requested, but no AI reviewer reviewed every analyzed app")
    md = renderers.render_markdown(
        Report(report_type=Report.Type.STUDY_REPORT, title="t", report_data=data, status="completed"),
    )
    assert "| AI-reviewer findings | requested, but none counted (no AI reviewer reviewed every analyzed run) |" in md
