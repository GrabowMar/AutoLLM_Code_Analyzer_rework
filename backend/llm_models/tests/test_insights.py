"""Model insights: source LOC, per-rule rows and the tools' own severity labels."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.services.insights import model_insights
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _job(user, model, **kw):
    return GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED, **kw)


def test_lines_of_code_are_source_loc_not_every_generated_line():
    user = UserFactory()
    model = LLMModelFactory()
    files = {"app.py": "x = 1\n" * 100, "frontend/package-lock.json": "{}\n" * 5000}
    _job(user, model, result_data={"files": files}, metrics={"cost": 0.5, "lines_of_code": 5100})
    # A legacy job without stored code keeps the generation-time count.
    _job(user, model, result_data={}, metrics={"cost": 0.5, "lines_of_code": 300})

    resources = model_insights(model, user)["generation"]["resources"]

    assert resources["mean_lines_of_code"] == pytest.approx(200.0)
    assert resources["cost_per_kloc"] == pytest.approx(1.0 / 400 * 1000)


def test_top_rules_are_one_row_per_rule_with_the_tools_labels():
    user = UserFactory()
    model = LLMModelFactory()
    job = _job(user, model, result_data={"files": {"app.py": "x = 1\n"}})
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    semgrep = ToolResultFactory(run=run, tool_slug="semgrep")
    ruff = ToolResultFactory(run=run, tool_slug="ruff", category="lint")
    for severity, label in (("high", "ERROR"), ("high", "ERROR"), ("medium", "WARNING")):
        Finding.objects.create(
            result=semgrep,
            rule_id="sql-injection",
            severity=severity,
            tool_severity=label,
            title="SQL built from input",
        )
    # Ruff prints no level: its normalized "low" is a heuristic default.
    Finding.objects.create(result=ruff, rule_id="F401", severity="low", tool_severity="", title="unused import")

    analysis = model_insights(model, user)["analysis"]

    rules = {(r["tool"], r["rule_id"]): r for r in analysis["top_rules"]}
    assert len(analysis["top_rules"]) == 2
    sqli = rules[("semgrep", "sql-injection")]
    assert sqli["count"] == 3
    assert sqli["tool_severity"] == "ERROR / WARNING"
    assert sqli["tool_severities"] == {"ERROR": 2, "WARNING": 1}
    assert sqli["normalized_severity"] == "high"
    assert "severity" not in sqli
    f401 = rules[("ruff", "F401")]
    assert f401["tool_severity"] == "unrated"
    assert f401["normalized_severity"] is None


def test_unrated_findings_are_not_counted_under_a_normalized_level():
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    user = UserFactory()
    model = LLMModelFactory()
    job = _job(user, model, result_data={"files": {"app.py": "x = 1\n"}})
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    jscpd = ToolResultFactory(run=run, tool_slug="jscpd", category="quality")
    review = ToolResultFactory(run=run, tool_slug="llm_review", category="ai")
    Finding.objects.create(result=bandit, rule_id="B105", severity="low", tool_severity="LOW", title="t")
    Finding.objects.create(result=jscpd, rule_id="dup", severity="medium", tool_severity="", title="t")
    Finding.objects.create(result=review, rule_id="x", severity="medium", tool_severity="", title="t")

    analysis = model_insights(model, user)["analysis"]

    assert analysis["findings_static"] == {"critical": 0, "high": 0, "medium": 0, "low": 1, "info": 0, "unrated": 1}
    assert analysis["findings_ai"]["unrated"] == 1
    assert analysis["findings_ai"]["medium"] == 0
    tools = {t["tool"]: t for t in analysis["by_tool"]}
    assert tools["jscpd"]["unrated"] == 1
    assert tools["jscpd"]["medium"] == 0
    assert tools["jscpd"]["total"] == 1
    assert tools["bandit"]["tool_severities"] == {"LOW": 1}


def test_dependency_advisories_are_counted_apart_from_code_findings():
    user = UserFactory()
    model = LLMModelFactory()
    job = _job(user, model, result_data={"files": {"app.py": "x = 1\n"}})
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    audit = ToolResultFactory(run=run, tool_slug="pip-audit", category="dependency")
    deptry = ToolResultFactory(run=run, tool_slug="deptry", category="dependency")
    for _ in range(3):
        Finding.objects.create(result=audit, severity="high", tool_severity="HIGH", category="dependency", title="t")
    # A missing dependency is the app's own manifest issue, not an advisory.
    Finding.objects.create(result=deptry, severity="low", tool_severity="", category="dependency", title="t")

    analysis = model_insights(model, user)["analysis"]

    assert analysis["findings_dependency"]["high"] == 3
    assert analysis["findings_static"]["high"] == 0
    assert analysis["findings_static"]["unrated"] == 1
