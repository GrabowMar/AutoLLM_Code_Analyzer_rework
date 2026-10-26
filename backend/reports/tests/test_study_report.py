"""The study report end to end: sections, validity checks, planned comparisons, Markdown/LaTeX export."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import FindingFactory
from backend.generation.models import GenerationJob
from backend.generation.services.analysis_plan import normalize_analysis_plan
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.tests.measured import PLAN_B
from backend.rankings.tests.measured import make_run
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
            # The engine grouped bandit B105 + ruff S105 on one line into one issue
            # (one issue_key); each F401 is its own issue.
            unused = n % 3
            run = make_run(
                job,
                tools={"bandit": ToolResult.Status.SUCCEEDED, "ruff": ToolResult.Status.SUCCEEDED},
                findings={"low": 2, "medium": unused},
                issues={"low": 1, "medium": unused},
                measured_sloc=50 + n * 10 + 30,
                complexity_max=2.0 + n % 4,
                security_issues=1,
            )
            bandit = run.results.get(tool_slug="bandit")
            ruff = run.results.get(tool_slug="ruff")
            key = f"secret-{n}"
            FindingFactory(result=bandit, rule_id="B105", line_number=3, severity="low", issue_key=key)
            FindingFactory(
                result=ruff,
                rule_id="S105",
                line_number=3,
                severity="low",
                category="maintainability",
                issue_key=key,
            )
            for k in range(unused):
                FindingFactory(
                    result=ruff,
                    rule_id="F401",
                    line_number=10 + k,
                    severity="medium",
                    category="maintainability",
                    issue_key=f"unused-{n}-{k}",
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
    assert totals["static_measured_apps"] == 12
    assert data["issues"]["coverage"]["measured"] == 12
    assert data["protocol"]["analysis_plan"]["plan_hash"] == data["issues"]["plan_hash"]
    assert {t["slug"] for t in data["protocol"]["tools"]} == {"bandit", "ruff"}
    row = data["per_app"][0]
    assert row["measured_loc"] is not None
    assert row["analysis_status"] == "measured"

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
    assert data["protocol"]["report_version"] == 2


def test_unmeasured_apps_are_missing_not_clean(study):
    user, experiment = study
    job = GenerationJob.objects.filter(experiment=experiment).order_by("created_at").first()
    run = job.analysis_runs.get()
    run.measurement = {**run.measurement, "static": {**run.measurement["static"], "available": False}}
    run.save(update_fields=["measurement"])
    other = GenerationJob.objects.filter(experiment=experiment).order_by("created_at")[1]
    other.analysis_runs.update(plan_hash=PLAN_B)
    unanalysed = GenerationJob.objects.filter(experiment=experiment).order_by("created_at")[2]
    unanalysed.analysis_runs.all().delete()

    data = study_report.generate_study_report({"experiment_id": str(experiment.id), "seed": 1}, user=user)
    rows = {r["job_id"]: r for r in data["per_app"]}
    assert rows[str(job.id)]["analyzed"] is True
    assert rows[str(job.id)]["static_measured"] is False
    assert rows[str(job.id)]["unique_issues"] is None
    assert rows[str(other.id)]["analysis_status"] == "other_plan"
    assert rows[str(unanalysed.id)]["analysis_status"] == "not_analysed"
    assert data["security_bounds"]["apps_analyzed"] == 9
    assert data["issues"]["totals"]["static_measured_apps"] == 9
    codes = {v["code"] for v in data["validity"]}
    assert {"not_measured", "static_unavailable", "mixed_analysis_plans"} <= codes
    assert data["funnel"]["overall"]["analyzed"] == 10
    assert data["funnel"]["overall"]["static_measured"] == 9


def test_pass_rate_is_never_a_guessed_zero(study):
    user, experiment = study
    job = GenerationJob.objects.filter(experiment=experiment).order_by("created_at").first()
    job.result_data = {**job.result_data, "evaluation": {"status": "failed", "summary": {}}}
    job.save(update_fields=["result_data"])
    data = study_report.generate_study_report({"experiment_id": str(experiment.id)}, user=user)
    row = next(r for r in data["per_app"] if r["job_id"] == str(job.id))
    assert row["deployed"] is True
    assert row["pass_rate"] is None


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


def test_study_report_command_pins_the_analysis_plan(study, tmp_path):
    import json

    from django.core.management import call_command

    _user, experiment = study
    out = tmp_path / "study.json"
    call_command("study_report", "--out", str(out), "--experiment", str(experiment.id), "--plan-hash", PLAN_B)
    data = json.loads(out.read_text())
    # Nothing was analysed under PLAN_B: issue tables are empty, never zero-filled.
    assert data["issues"]["plan_hash"] == PLAN_B
    assert data["issues"]["totals"]["unique_issues"] is None
    assert data["issues"]["coverage"]["other_plan"] == 12
