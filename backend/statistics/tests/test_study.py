"""Study tables: issue breakdowns, healing impact, tool inventory and run accounting."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics.services import accounting
from backend.statistics.services import study
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _job(user, model, app, **kw):
    kw.setdefault("result_data", {"files": {"app.py": "x = 1\n" * 100}})
    kw.setdefault("mode", "scaffolding")
    return GenerationJobFactory(
        created_by=user,
        status=GenerationJob.Status.COMPLETED,
        model=model,
        app_requirement=app,
        **kw,
    )


def _run(job, findings_by_tool, *, tool_slugs=None):
    run = AnalysisRunFactory(
        created_by=job.created_by,
        generation_job=job,
        status=AnalysisRun.Status.COMPLETED,
        source_code=None,
        tool_slugs=tool_slugs or list(findings_by_tool),
    )
    for tool, findings in findings_by_tool.items():
        result = ToolResultFactory(run=run, tool_slug=tool)
        for rule, line, severity in findings:
            FindingFactory(result=result, rule_id=rule, line_number=line, severity=severity, file_path="app.py")
    return run


@pytest.fixture
def scope():
    user = UserFactory()
    m1, m2 = LLMModelFactory(model_id="vendor/a"), LLMModelFactory(model_id="vendor/b")
    t1, t2 = AppRequirementTemplateFactory(slug="todo"), AppRequirementTemplateFactory(slug="blog")
    j1 = _job(user, m1, t1)
    j2 = _job(user, m2, t2)
    _run(
        j1,
        {
            "bandit": [("B201", 40, "high"), ("B105", 10, "low")],
            "semgrep": [("python.flask.security.audit.debug-enabled.debug-enabled", 40, "medium")],
            "ruff": [("S201", 40, "low"), ("F401", 1, "low")],
        },
    )
    _run(j2, {"bandit": [("B608", 7, "medium")], "semgrep": []})
    return user, j1, j2


def test_breakdowns_dedupe_and_group(scope):
    user, j1, _j2 = scope
    data = study.issue_breakdowns(study.study_jobs(user))

    assert data["totals"]["raw_findings"] == 6
    assert data["totals"]["unique_issues"] == 4  # flask-debug x3 tools -> 1
    per_app = {a["job_id"]: a for a in data["per_app"]}
    a1 = per_app[str(j1.id)]
    assert (a1["raw_findings"], a1["unique_issues"], a1["multi_tool_issues"]) == (5, 3, 1)
    assert a1["unique_per_kloc"] == 30.0
    assert {r["model"] for r in data["by_model"]} == {"vendor/a", "vendor/b"}
    assert {r["template"]: r["unique_issues"] for r in data["by_template"]} == {"todo": 3, "blog": 1}
    tools = {t["tool"]: t for t in data["by_tool"]}
    assert tools["bandit"]["raw_findings"] == 3
    assert tools["ruff"]["exclusive"] == 1
    rules = {(r["tool"], r["rule_id"]): r for r in data["by_tool_rule"]}
    assert rules[("ruff", "S201")]["issue_class"] == "flask-debug"


def test_breakdowns_count_only_the_latest_run(scope):
    user, j1, _ = scope
    _run(j1, {"bandit": [("B105", 10, "low")]})
    data = study.issue_breakdowns(study.study_jobs(user))
    assert {a["job_id"]: a["unique_issues"] for a in data["per_app"]}[str(j1.id)] == 1


def test_breakdowns_exclude_ai_findings_by_default(scope):
    user, j1, _ = scope
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRun.objects.get(generation_job=j1)
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), rule_id="x", line_number=3)
    jobs = study.study_jobs(user)
    assert study.issue_breakdowns(jobs)["totals"]["raw_findings"] == 6
    assert study.issue_breakdowns(jobs, include_ai=True)["totals"]["raw_findings"] == 7


def _evaluated(user, model, app, healed_status, raw_status, *, patches=()):
    return _job(
        user,
        model,
        app,
        result_data={
            "evaluation": {
                "status": healed_status,
                "build": {
                    "healing": {
                        "mode": "full",
                        "applicable": True,
                        "changed": bool(patches),
                        "patches": list(patches),
                        "added_requirements": ["requests"] if patches else [],
                    },
                },
            },
            "healing_ablation": {"none": {"status": raw_status, "deployed": raw_status in ("passed", "failed")}},
        },
        metrics={"functional": {"deployed": healed_status in ("passed", "failed"), "healing_mode": "full"}},
    )


def test_healing_impact_pairs_outcomes():
    user = UserFactory()
    model, app = LLMModelFactory(model_id="vendor/a"), AppRequirementTemplateFactory()
    _evaluated(user, model, app, "passed", "build_failed", patches=["app.py:flask_port"])  # rescued
    _evaluated(user, model, app, "failed", "failed")
    _evaluated(user, model, app, "start_failed", "start_failed")
    _evaluated(user, model, app, "build_failed", "passed")  # broken by healing
    _job(
        user,
        model,
        app,
        mode="agent",
        result_data={"evaluation": {"status": "passed", "build": {"healing": {"applicable": False}}}},
    )

    data = study.healing_impact(study.study_jobs(user))
    t = data["totals"]
    assert data["modes"] == ["full", "none"]
    assert data["not_applicable"] == 1
    assert (t["deploy_rate_full"], t["deploy_rate_none"]) == (50.0, 50.0)
    assert (t["rescued_vs_none"], t["broken_vs_none"], t["paired_none"]) == (1, 1, 4)
    assert t["healing_effect_pp_vs_none"] == 0.0
    assert t["mcnemar_p_vs_none"] == 1.0
    assert data["added_requirements"] == [{"package": "requests", "apps": 1}]
    assert data["patches"] == [{"patch": "flask_port", "apps": 1}]


def test_binomial_sign_test():
    assert study._binomial_two_sided(0, 10) == pytest.approx(0.002, abs=1e-3)
    assert study._binomial_two_sided(0, 0) is None


def test_tool_inventory_counts_catalog_and_platform():
    from backend.analysis.models import AnalyzerTool

    AnalyzerToolFactory(slug="t-sec", category="security", kind="container")
    AnalyzerToolFactory(slug="t-off", category="lint", kind="container", is_enabled=False)
    AnalyzerToolFactory(slug="t-ai", category="ai", kind="ai")
    inventory = accounting.tool_inventory()
    counts = inventory["counts"]
    enabled = AnalyzerTool.objects.filter(is_enabled=True)
    assert counts["catalog_registered"] == AnalyzerTool.objects.count()
    assert counts["catalog_enabled"] == enabled.count()
    assert counts["catalog_enabled_ai"] == enabled.filter(kind="ai").count()
    assert counts["total_enabled"] == enabled.count() + len(accounting.PLATFORM_EVALUATORS)
    assert "t-off" in {t["slug"] for t in inventory["analyzers"]}
    assert {e["slug"] for e in inventory["evaluators"]} == {"conformance-tester", "smoke"}


def test_run_accounting_counts_actual_executions(scope):
    user, j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    run.tool_slugs = ["bandit", "semgrep", "eslint"]
    run.save(update_fields=["tool_slugs"])
    ToolResultFactory(run=run, tool_slug="eslint", status=ToolResult.Status.FAILED)
    j1.result_data = {**j1.result_data, "evaluation": {"status": "passed"}}
    j1.save(update_fields=["result_data"])
    j2.result_data = {**j2.result_data, "evaluation": {"status": "build_failed"}}
    j2.save(update_fields=["result_data"])

    data = accounting.run_accounting(study.study_jobs(user))
    assert data["apps"] == {
        "jobs": 2,
        "by_status": {"completed": 2},
        "generated": 2,
        "analyzed": 2,
        "evaluated": 2,
        "deployed": 1,
    }
    tools = {t["tool"]: t for t in data["tools"]["latest"]}
    assert tools["eslint"]["failed"] == 1
    assert tools["bandit"]["apps"] == 2
    assert data["executions"]["planned_static"] == 6
    assert data["executions"]["static"] == 6
    assert data["executions"]["dynamic"] == 2
    assert data["executions"]["total"] == 8


def test_study_endpoints(client, scope):
    user, _, _ = scope
    client.force_login(user)
    for path in ("breakdowns", "healing", "tools", "accounting"):
        res = client.get(f"/api/statistics/study/{path}/")
        assert res.status_code == 200, (path, res.content)
    res = client.get("/api/statistics/study/breakdowns/?include_issues=true")
    assert len(res.json()["issues"]) == 4


def test_study_scope_is_owner_only(client, scope):
    client.force_login(UserFactory())
    assert client.get("/api/statistics/study/breakdowns/").json()["totals"]["apps"] == 0
