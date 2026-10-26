"""The study report end to end: sections, validity checks, planned comparisons, Markdown/LaTeX export."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
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
