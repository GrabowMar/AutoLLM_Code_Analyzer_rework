"""Study tables: issue breakdowns, healing impact, tool inventory and run accounting."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalyzerToolFactory
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


_STATIC = {"bandit": ToolResult.Status.SUCCEEDED, "semgrep": ToolResult.Status.SUCCEEDED, "ruff": "succeeded"}


def _run(job, findings, *, issues, plan_hash=PLAN_A, tools=None, **measure):
    """A v2 run of *job*: a §4.4 measurement plus its Finding rows grouped by the engine's issue_key.

    *findings*: ``(tool, rule, line, severity, issue_key)``.
    """
    by_sev: dict[str, int] = {}
    for *_rest, severity, _key in findings:
        by_sev[severity] = by_sev.get(severity, 0) + 1
    run = make_run(
        job,
        plan_hash=plan_hash,
        tools=tools or _STATIC,
        findings=by_sev,
        issues=issues,
        measured_sloc=100,
        **measure,
    )
    results = {r.tool_slug: r for r in run.results.all()}
    for tool, rule, line, severity, key in findings:
        FindingFactory(
            result=results[tool],
            rule_id=rule,
            line_number=line,
            severity=severity,
            file_path="app.py",
            issue_key=key,
        )
    return run


J1_FINDINGS = [
    ("bandit", "B201", 40, "high", "debug"),
    ("semgrep", "python.flask.security.audit.debug-enabled.debug-enabled", 40, "medium", "debug"),
    ("ruff", "S201", 40, "low", "debug"),
    ("bandit", "B105", 10, "low", "secret"),
    ("ruff", "F401", 1, "low", "import"),
]


@pytest.fixture
def scope():
    user = UserFactory()
    m1, m2 = LLMModelFactory(model_id="vendor/a"), LLMModelFactory(model_id="vendor/b")
    t1, t2 = AppRequirementTemplateFactory(slug="todo"), AppRequirementTemplateFactory(slug="blog")
    j1 = _job(user, m1, t1)
    j2 = _job(user, m2, t2)
    _run(j1, J1_FINDINGS, issues={"high": 1, "low": 2}, security_issues=2)
    _run(j2, [("bandit", "B608", 7, "medium", "sql")], issues={"medium": 1}, security_issues=1)
    return user, j1, j2


def test_breakdowns_read_the_engine_dedup_and_measurement(scope):
    user, j1, _j2 = scope
    data = study.issue_breakdowns(study.study_jobs(user))

    assert data["totals"]["raw_findings"] == 6
    assert data["totals"]["unique_issues"] == 4  # flask-debug x3 tools -> 1 (engine issue_key)
    assert data["totals"]["loc"] == 200
    assert data["totals"]["unique_per_kloc"] == 20.0
    per_app = {a["job_id"]: a for a in data["per_app"]}
    a1 = per_app[str(j1.id)]
    assert (a1["raw_findings"], a1["unique_issues"], a1["multi_tool_issues"]) == (5, 3, 1)
    assert (a1["unique_high"], a1["unique_low"], a1["security_unique"]) == (1, 2, 2)
    assert a1["unique_per_kloc"] == 30.0
    assert a1["loc"] == 100
    assert a1["plan_hash"] == PLAN_A
    assert {r["model"] for r in data["by_model"]} == {"vendor/a", "vendor/b"}
    assert {r["template"]: r["unique_issues"] for r in data["by_template"]} == {"todo": 3, "blog": 1}
    tools = {t["tool"]: t for t in data["by_tool"]}
    assert tools["bandit"]["raw_findings"] == 3
    assert tools["ruff"]["exclusive"] == 1
    rules = {(r["tool"], r["rule_id"]): r for r in data["by_tool_rule"]}
    assert rules[("ruff", "S201")]["issue_class"] == "flask-debug"
    debug = next(i for i in data["issues"] if i["issue_key"] == "debug")
    assert (debug["tools"], debug["severity"]) == (["bandit", "ruff", "semgrep"], "high")
    assert data["plan_hash"] == PLAN_A
    assert data["coverage"]["measured"] == 2


def test_breakdowns_count_only_the_canonical_run(scope):
    user, j1, _ = scope
    _run(j1, [("bandit", "B105", 10, "low", "secret")], issues={"low": 1})
    data = study.issue_breakdowns(study.study_jobs(user))
    assert {a["job_id"]: a["unique_issues"] for a in data["per_app"]}[str(j1.id)] == 1
    assert data["totals"]["raw_findings"] == 2


def test_breakdowns_keep_ai_consensus_apart(scope):
    user, j1, _ = scope
    run = AnalysisRun.objects.get(generation_job=j1)
    run.measurement = measurement_json(
        findings={"high": 1, "medium": 1, "low": 3},
        issues={"high": 1, "low": 2},
        measured_sloc=100,
        security_issues=2,
        ai={"high": 1},
    )
    run.save(update_fields=["measurement"])
    ai = ToolResultFactory(run=run, tool_slug="ai_review", tool_kind="ai", category="ai")
    for _ in range(3):  # three replicates, one consensus opinion
        FindingFactory(result=ai, is_ai=True, counted=True, is_consensus=True, issue_key="ai-1", line_number=3)
    jobs = study.study_jobs(user)

    plain = study.issue_breakdowns(jobs)
    with_ai = study.issue_breakdowns(jobs, include_ai=True)
    for data in (plain, with_ai):
        assert data["totals"]["raw_findings"] == 6
        assert data["totals"]["unique_issues"] == 4
        assert data["totals"]["ai_consensus_issues"] == 1
        assert data["totals"]["ai_reviewed_apps"] == 1
        assert "ai_review" not in {t["tool"] for t in data["by_tool"]}
    assert "ai_issues" not in plain
    assert [(i["n_findings"], i["tools"]) for i in with_ai["ai_issues"]] == [(3, ["ai_review"])]
    assert with_ai["ai_by_rule"][0]["findings"] == 1
    per_app = {a["job_id"]: a for a in with_ai["per_app"]}
    assert per_app[str(j1.id)]["ai_consensus_issues"] == 1


def test_unavailable_static_measurement_is_missing_not_zero(scope):
    user, _j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    run.measurement = measurement_json(static_available=False, measured_sloc=100)
    run.save(update_fields=["measurement"])
    data = study.issue_breakdowns(study.study_jobs(user))
    per_app = {a["job_id"]: a for a in data["per_app"]}
    a2 = per_app[str(j2.id)]
    assert (a2["raw_findings"], a2["unique_issues"], a2["security_unique"], a2["unique_per_kloc"]) == (
        None,
        None,
        None,
        None,
    )
    assert a2["static_available"] is False
    # Sums and the pooled density cover the measured app only (100 SLOC, 3 issues).
    assert data["totals"]["static_measured_apps"] == 1
    assert (data["totals"]["unique_issues"], data["totals"]["loc"], data["totals"]["unique_per_kloc"]) == (
        3,
        100,
        30.0,
    )
    blog = next(r for r in data["by_template"] if r["template"] == "blog")
    assert blog["unique_issues"] is None
    assert blog["unique_critical"] is None
    assert blog["mean_unique_per_app"] is None


def test_other_plans_and_unanalysed_jobs_are_reported_not_counted(scope):
    user, j1, j2 = scope
    j3 = _job(user, j1.model, j1.app_requirement)
    j4 = _job(user, j1.model, j1.app_requirement)
    _run(j3, [("bandit", "B105", 1, "low", "x")], issues={"low": 1}, plan_hash=PLAN_B)
    data = study.issue_breakdowns(study.study_jobs(user))
    assert {a["job_id"] for a in data["per_app"]} == {str(j1.id), str(j2.id)}
    assert (data["coverage"]["other_plan"], data["coverage"]["not_analysed"]) == (1, 1)
    pinned = study.issue_breakdowns(study.study_jobs(user), plan_hash=PLAN_B)
    assert [a["job_id"] for a in pinned["per_app"]] == [str(j3.id)]
    assert pinned["totals"]["unique_issues"] == 1
    assert str(j4.id) not in {a["job_id"] for a in pinned["per_app"]}


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
    run.plan = {"schema_version": 1, "tools": [{"slug": s} for s in ("bandit", "semgrep", "eslint")]}
    run.save(update_fields=["plan"])
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
    assert data["executions"]["static"] == 7  # j1: 3, j2: 3 + the failed eslint
    assert data["executions"]["dynamic"] == 2
    assert data["executions"]["total"] == 9

    # Scoped to a report's canonical runs, only those runs are "latest".
    only_j1 = accounting.run_accounting(
        study.study_jobs(user),
        run_ids=[AnalysisRun.objects.get(generation_job=j1).id],
    )
    assert only_j1["apps"]["analyzed"] == 1
    assert only_j1["executions"]["static"] == 3


def test_run_accounting_counts_the_smoke_probe_under_its_own_key():
    user = UserFactory()
    model, app = LLMModelFactory(), AppRequirementTemplateFactory()
    _job(user, model, app, metrics={"smoke": {"source": "smoke", "health_ok": True, "passed": True}})
    # Legacy: the probe used to overwrite metrics["functional"].
    _job(user, model, app, metrics={"functional": {"health_ok": True, "passed": False}})
    # The conformance evaluator's record is not a smoke run.
    _job(user, model, app, metrics={"functional": {"source": "conformance", "health_ok": True, "passed": True}})
    smoke = accounting.run_accounting(study.study_jobs(user))["evaluations"]["smoke"]
    assert smoke == {"executions": 2, "by_status": {"passed": 1, "failed": 1}}


def test_study_endpoints(client, scope):
    user, _, _ = scope
    client.force_login(user)
    for path in ("breakdowns", "healing", "tools", "accounting"):
        res = client.get(f"/api/statistics/study/{path}/")
        assert res.status_code == 200, (path, res.content)
    res = client.get("/api/statistics/study/breakdowns/?include_issues=true")
    assert len(res.json()["issues"]) == 4
    res = client.get(f"/api/statistics/study/breakdowns/?plan_hash={PLAN_B}")
    assert res.json()["totals"]["apps"] == 0
    assert res.json()["coverage"]["other_plan"] == 2
    assert client.get("/api/statistics/study/breakdowns/?plan_hash=nope").status_code == 422
    for path in ("breakdowns", "healing", "accounting"):
        assert client.get(f"/api/statistics/study/{path}/?experiment_id=not-a-uuid").status_code == 422


def test_study_scope_is_owner_only(client, scope):
    client.force_login(UserFactory())
    assert client.get("/api/statistics/study/breakdowns/").json()["totals"]["apps"] == 0


def test_export_study_tables_command_writes_v2_tables(scope, tmp_path):
    import csv
    import json

    from django.core.management import call_command

    call_command("export_study_tables", "--out", str(tmp_path), "--plan-hash", PLAN_A, "--include-ai")
    summary = json.loads((tmp_path / "study_summary.json").read_text())
    assert summary["issues"]["plan_hash"] == PLAN_A
    assert summary["issues"]["totals"]["unique_issues"] == 4
    assert summary["accounting"]["apps"]["analyzed"] == 2
    with (tmp_path / "issue_clusters.csv").open() as fh:
        assert len(list(csv.DictReader(fh))) == 4
    assert (tmp_path / "ai_issue_clusters.csv").exists()
