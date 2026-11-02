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
    # ruff ran on j1 only: count it anyway to exercise cross-tool dedup.
    data = study.issue_breakdowns(study.study_jobs(user), common_tools_only=False)

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


def test_breakdowns_count_each_tools_latest_result(scope):
    user, j1, _ = scope
    # Re-running bandit replaces bandit's findings only; semgrep's and ruff's
    # earlier results for the same (unchanged) code still count.
    _run(j1, {"bandit": [("B105", 10, "low")]})
    data = study.issue_breakdowns(study.study_jobs(user), common_tools_only=False)
    a1 = {a["job_id"]: a for a in data["per_app"]}[str(j1.id)]
    assert a1["raw_findings"] == 4  # bandit 1 (new run) + semgrep 1 + ruff 2
    assert {t["tool"]: t["raw_findings"] for t in data["by_tool"]}["bandit"] == 2  # j1 1 + j2 1


def test_failed_rerun_does_not_replace_a_measurement(scope):
    user, j1, _ = scope
    run = _run(j1, {})
    ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.FAILED)
    data = study.issue_breakdowns(study.study_jobs(user), common_tools_only=False)
    assert {a["job_id"]: a for a in data["per_app"]}[str(j1.id)]["raw_findings"] == 5


def test_breakdowns_exclude_ai_findings_by_default(scope):
    user, j1, _ = scope
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRun.objects.get(generation_job=j1)
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), rule_id="x", line_number=3)
    jobs = study.study_jobs(user)
    assert study.issue_breakdowns(jobs, common_tools_only=False)["totals"]["raw_findings"] == 6
    assert study.issue_breakdowns(jobs, include_ai=True, common_tools_only=False)["totals"]["raw_findings"] == 7


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
    assert data["added_requirements"] == [{"package": "requests", "apps": 1, "runs": 1}]
    assert data["patches"] == [{"patch": "flask_port", "apps": 1, "runs": 1}]
    # Pairs are runs: four runs of one app (same model, template and mode), not four apps.
    assert (t["apps"], t["runs"]) == (1, 4)
    # Ad-hoc jobs have no condition: one row for them, not one per model.
    assert [(r["condition_id"], r["apps"], r["runs"]) for r in data["by_condition"]] == [(None, 1, 4)]


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
    res = client.get("/api/statistics/study/breakdowns/?include_issues=true&common_tools_only=false")
    assert len(res.json()["issues"]) == 4


def test_study_scope_is_owner_only(client, scope):
    client.force_login(UserFactory())
    assert client.get("/api/statistics/study/breakdowns/").json()["totals"]["apps"] == 0


def test_breakdowns_count_only_tools_that_measured_every_app(scope):
    """An app a tool never measured must not look cleaner than one it did."""
    user, _j1, _j2 = scope
    data = study.issue_breakdowns(study.study_jobs(user))
    assert data["coverage"]["tools_common"] == ["bandit", "semgrep"]
    assert data["coverage"]["tools_partial"]["ruff"]["apps_missing"] == 1
    assert {r["tool"] for r in data["by_tool"]} <= {"bandit", "semgrep"}
    assert data["totals"]["raw_findings"] == 4


def test_not_applicable_skip_counts_as_measured(scope):
    user, _j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    ToolResultFactory(
        run=run,
        tool_slug="ruff",
        status=ToolResult.Status.SKIPPED,
        summary={"skip_reason": "not_applicable"},
    )
    assert "ruff" in study.issue_breakdowns(study.study_jobs(user))["coverage"]["tools_common"]


def test_failed_tool_is_not_measured(scope):
    user, _j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    ToolResultFactory(run=run, tool_slug="ruff", status=ToolResult.Status.FAILED)
    coverage = study.issue_breakdowns(study.study_jobs(user))["coverage"]
    assert coverage["tools_partial"]["ruff"] == {"apps_measured": 1, "apps_missing": 1, "failed": 1}


def test_dependency_advisories_split_by_who_added_the_package():
    from backend.runtime.models import Stack
    from backend.statistics.services.dependencies import manifest_packages

    Stack.objects.create(
        slug="dep-stack",
        version=1,
        files={
            "requirements.txt": "Flask-CORS==4.0.0\nPyJWT==2.8.0\n",
            "frontend/package.json": '{"devDependencies": {"vite": "^5"}}',
        },
    )
    user = UserFactory()
    files = {
        "app.py": "x = 1\n" * 100,
        "requirements.txt": "flask_cors==4.0.0  # kept\nPyJWT==2.8.0\nrequests==2.0\n",
    }
    job = _job(
        user,
        LLMModelFactory(),
        AppRequirementTemplateFactory(),
        result_data={"files": files},
        stack_slug="dep-stack",
        resolved_bundle={"stack": {"slug": "dep-stack", "version": 1}},
    )
    assert manifest_packages(files) == {"flask-cors", "pyjwt", "requests"}
    run = _run(job, {"bandit": [("B105", 10, "low")]})
    osv = ToolResultFactory(run=run, tool_slug="osv-scanner")
    for vuln, pkg in (("GHSA-1", "Flask-Cors"), ("GHSA-2", "requests"), ("GHSA-3", "esbuild")):
        FindingFactory(
            result=osv,
            rule_id=vuln,
            category="dependency",
            line_number=None,
            file_path="requirements.txt",
            title=f"{pkg} {vuln}",
            tool_specific_data={"package": pkg, "vuln_id": vuln},
        )
    data = study.issue_breakdowns(study.study_jobs(user), common_tools_only=False)
    app = data["per_app"][0]
    assert (app["unique_issues"], app["code_unique"], app["dependency_unique"], app["dependency_added"]) == (
        4,
        1,
        3,
        1,
    )
    # Only the code issue counts per kLOC (100 source lines).
    assert app["unique_per_kloc"] == 10.0


def _app_row(user, **kw):
    data = study.issue_breakdowns(study.study_jobs(user), **kw)
    assert len(data["per_app"]) == 1
    return data, data["per_app"][0]


def test_vulnerable_version_the_model_pinned_is_its_own():
    """Package and version: a downgraded scaffold package is the model's, the scaffold's own range is not."""
    import json

    from backend.runtime.models import Stack

    Stack.objects.create(
        slug="npm-stack",
        version=1,
        files={"frontend/package.json": json.dumps({"dependencies": {"axios": "^1.6.0", "vite": "^5.0.0"}})},
    )
    user = UserFactory()
    package_json = {"dependencies": {"axios": "1.6.8", "vite": "5.4.21"}}
    job = _job(
        user,
        LLMModelFactory(),
        AppRequirementTemplateFactory(),
        result_data={"files": {"app.py": "x = 1\n" * 100, "frontend/package.json": json.dumps(package_json)}},
        stack_slug="npm-stack",
        resolved_bundle={"stack": {"slug": "npm-stack", "version": 1}},
    )
    run = _run(job, {"bandit": [("B105", 10, "low")]})
    osv = ToolResultFactory(run=run, tool_slug="osv-scanner")
    for vuln, pkg, installed, fixed, severity in (
        ("GHSA-ax", "axios", "1.6.8", ["1.7.4"], "high"),  # ^1.6.0 installs the fix
        ("GHSA-vi", "vite", "5.4.21", ["6.0.9"], "critical"),  # ^5.0.0 is vulnerable too
    ):
        FindingFactory(
            result=osv,
            rule_id=vuln,
            category="dependency",
            severity=severity,
            line_number=None,
            file_path="frontend/package-lock.json",
            title=f"{pkg} {vuln}",
            tool_specific_data={
                "package": pkg,
                "vuln_id": vuln,
                "installed_version": installed,
                "fixed_versions": fixed,
            },
        )
    data, app = _app_row(user)
    assert (app["dependency_unique"], app["dependency_added"], app["dependency_inherited"]) == (2, 1, 1)
    assert {i["issue_class"]: i["dependency_added"] for i in data["issues"] if i["kind"] == "dependency"} == {
        "vulnerable-dependency:axios": True,
        "vulnerable-dependency:vite": False,
    }
    # Advisories are not code security: the security severity counts leave them out.
    assert (app["unique_normalized_critical"], app["security_normalized_critical"]) == (1, 0)
    assert app["security_normalized_low"] == 1
    assert "unique_critical" not in app  # normalized counts say so in their names (P1)


def test_deptry_manifest_findings_are_code_issues_not_advisories():
    user = UserFactory()
    job = _job(user, LLMModelFactory(), AppRequirementTemplateFactory())
    run = _run(job, {"bandit": []})
    FindingFactory(
        result=ToolResultFactory(run=run, tool_slug="deptry"),
        rule_id="DEP001",
        category="dependency",
        line_number=3,
        file_path="app.py",
        title="'werkzeug' imported but missing from the dependency definitions",
        tool_specific_data={"module": "werkzeug"},
    )
    _data, app = _app_row(user)
    assert (app["code_unique"], app["manifest_unique"], app["unique_per_kloc"]) == (1, 1, 10.0)
    # No vulnerability scanner and no DAST tool measured this app: unknown, not zero.
    assert (app["dependency_unique"], app["dependency_added"], app["runtime_unique"]) == (None, None, None)


def test_manifest_counts_are_unmeasured_without_a_manifest_checker(scope):
    user, _j1, _j2 = scope
    data = study.issue_breakdowns(study.study_jobs(user))
    assert {a["manifest_unique"] for a in data["per_app"]} == {None}
    assert {a["manifest_unused_scaffold"] for a in data["per_app"]} == {None}
    assert (data["totals"]["manifest_unique"], data["totals"]["manifest_unused_scaffold"]) == (None, None)


def test_unused_calls_on_scaffold_packages_are_counted_apart():
    import json

    from backend.runtime.models import Stack

    Stack.objects.create(
        slug="ui-stack",
        version=1,
        files={"frontend/package.json": json.dumps({"dependencies": {"clsx": "^2.0.0", "react": "^18.2.0"}})},
    )
    user = UserFactory()
    package_json = {"dependencies": {"clsx": "^2.0.0", "react": "^18.2.0", "lodash": "^4.17.21"}}
    job = _job(
        user,
        LLMModelFactory(),
        AppRequirementTemplateFactory(),
        result_data={"files": {"app.py": "x = 1\n" * 100, "frontend/package.json": json.dumps(package_json)}},
        stack_slug="ui-stack",
        resolved_bundle={"stack": {"slug": "ui-stack", "version": 1}},
    )
    run = _run(job, {"bandit": []})
    depcheck = ToolResultFactory(run=run, tool_slug="depcheck")
    for pkg in ("clsx", "lodash"):
        FindingFactory(
            result=depcheck,
            rule_id="unused-dependencies",
            category="dependency",
            line_number=None,
            file_path="frontend/package.json",
            title=f"Unused dependency '{pkg}'",
            tool_specific_data={"package": pkg},
        )
    _data, app = _app_row(user)
    # Both are the app's own manifest issues (P2); one is a package the scaffold preinstalls.
    assert (app["code_unique"], app["manifest_unique"], app["manifest_unused_scaffold"]) == (2, 2, 1)


def test_dast_findings_are_runtime_issues_counted_once_per_class():
    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic", category="security")
    user = UserFactory()
    job = _job(user, LLMModelFactory(), AppRequirementTemplateFactory())
    run = _run(job, {"bandit": [("B105", 10, "low")]})
    zap = ToolResultFactory(run=run, tool_slug="zap")
    for url in ("/", "/robots.txt", "/app.js"):
        FindingFactory(
            result=zap,
            rule_id="10038",
            severity="medium",
            line_number=None,
            file_path=url,
            tool_specific_data={"issue_key": "missing-header:content-security-policy"},
        )
    data, app = _app_row(user)
    assert data["params"]["dynamic_counted"] == ["zap"]
    assert (app["code_unique"], app["security_unique"], app["runtime_unique"]) == (1, 1, 1)
    assert app["unique_per_kloc"] == 10.0  # the header findings are not lines of code
    # All kinds add up: the header counts once in unique_issues too (not once per crawled URL).
    assert (app["raw_findings"], app["unique_issues"]) == (4, 2)
    assert app["unique_normalized_medium"] == 1
    assert data["totals"]["duplicate_findings"] == 2
    # The cluster list stays per crawled URL.
    assert sum(1 for i in data["issues"] if i["kind"] == "runtime") == 3


def test_test_code_findings_and_lines_are_counted_apart():
    user = UserFactory()
    files = {"app.py": "x = 1\n" * 100, "tests/test_api.py": "def test_a():\n    login('admin', 'pw')\n" * 25}
    job = _job(user, LLMModelFactory(), AppRequirementTemplateFactory(), result_data={"files": files})
    run = _run(job, {"bandit": []})
    bandit = ToolResult.objects.get(run=run, tool_slug="bandit")
    for rule, line, path in (
        ("B105", 4, "app.py"),
        ("B106", 2, "tests/test_api.py"),
        ("B310", 9, "scripts/api_smoke.py"),
    ):
        FindingFactory(result=bandit, rule_id=rule, line_number=line, file_path=path, severity="low")
    data, app = _app_row(user)
    assert (app["code_unique"], app["code_unique_tests"]) == (1, 2)
    assert (app["security_unique"], app["security_unique_tests"]) == (1, 2)
    assert (app["loc"], app["test_loc"]) == (100, 50)
    assert app["unique_per_kloc"] == 10.0
    assert {i["file_path"]: i["kind"] for i in data["issues"]} == {
        "app.py": "code",
        "tests/test_api.py": "test",
        "scripts/api_smoke.py": "test",
    }


def test_by_tool_lists_a_counted_tool_that_found_nothing(scope):
    user, j1, j2 = scope
    for job in (j1, j2):
        ToolResultFactory(run=AnalysisRun.objects.get(generation_job=job), tool_slug="radon")
    data = study.issue_breakdowns(study.study_jobs(user))
    assert "radon" in data["coverage"]["tools_common"]
    radon = {t["tool"]: t for t in data["by_tool"]}["radon"]
    assert (radon["raw_findings"], radon["unique_issues"], radon["apps_with_findings"]) == (0, 0, 0)
    assert (radon["apps_measured"], radon["reported_levels"]) == (2, {})


def test_tool_tables_show_the_levels_tools_printed(scope):
    from backend.analysis.models import Finding

    user, _j1, _j2 = scope
    Finding.objects.filter(rule_id="B608").update(tool_severity="MEDIUM")
    data = study.issue_breakdowns(study.study_jobs(user))
    rules = {(r["tool"], r["rule_id"]): r for r in data["by_tool_rule"]}
    assert rules[("bandit", "B608")]["reported_levels"] == {"MEDIUM": 1}
    assert rules[("bandit", "B201")]["reported_levels"] == {"unrated": 1}
    # The normalized maximum is labelled as such; no bare "max_severity".
    assert rules[("bandit", "B201")]["max_severity_normalized"] == "high"
    assert "max_severity" not in rules[("bandit", "B201")]
    assert {t["tool"]: t["reported_levels"] for t in data["by_tool"]}["bandit"] == {"unrated": 2, "MEDIUM": 1}
    # Normalized counts say so in their names; no bare raw_<severity> that reads like the tool's label.
    bandit = {t["tool"]: t for t in data["by_tool"]}["bandit"]
    assert (bandit["raw_normalized_high"], bandit["raw_normalized_medium"]) == (1, 1)
    assert not any(k.startswith("raw_") and "normalized" not in k and k != "raw_findings" for k in bandit)
    assert rules[("bandit", "B608")]["raw_normalized_medium"] == 1


def test_issue_clusters_name_the_job_and_per_app_rows_the_run(scope):
    user, j1, _j2 = scope
    data = study.issue_breakdowns(study.study_jobs(user), common_tools_only=False)
    per_app = {a["job_id"]: a for a in data["per_app"]}
    assert per_app[str(j1.id)]["run_id"] == str(AnalysisRun.objects.get(generation_job=j1).id)
    assert {i["job_id"] for i in data["issues"]} == set(per_app)
    assert all("severity_normalized" in i and "severity" not in i and "run_id" not in i for i in data["issues"])


def test_conditions_of_one_model_are_grouped_apart_and_repeats_are_one_app():
    from backend.generation.tests.factories import ExperimentConditionFactory
    from backend.generation.tests.factories import ExperimentFactory

    user = UserFactory()
    model, app = LLMModelFactory(model_id="vendor/a"), AppRequirementTemplateFactory(slug="todo")
    experiment = ExperimentFactory(created_by=user)
    arm_a = ExperimentConditionFactory(experiment=experiment, model=model, label="harness A")
    arm_b = ExperimentConditionFactory(experiment=experiment, model=model, label="")
    for condition, issues_per_repeat in ((arm_a, [1, 3]), (arm_b, [2, 2, 5])):
        for repeat, n in enumerate(issues_per_repeat):
            job = _job(user, model, app, experiment=experiment, condition=condition, repeat_index=repeat)
            _run(job, {"bandit": [(f"B9{i:02d}", 1 + 10 * i, "low") for i in range(n)]})
    data = study.issue_breakdowns(study.study_jobs(user))

    # An unlabelled arm is named as the experiment's own pages name it.
    assert {(a["condition_id"], a["condition_label"]) for a in data["per_app"]} == {
        (arm_a.id, "harness A"),
        (arm_b.id, f"{model.model_name} / {arm_b.method_label()}"),
    }
    arms = {r["condition_label"]: r for r in data["by_condition"]}
    assert (arms["harness A"]["apps"], arms["harness A"]["runs"]) == (1, 2)
    assert arms["harness A"]["mean_unique_per_app"] == 2.0
    assert arms["harness A"]["unique_per_kloc"] == 20.0
    # One model, two arms, one app each: two apps, five runs; each app is its repeats' mean.
    (by_model,) = data["by_model"]
    assert (by_model["apps"], by_model["runs"], by_model["code_unique"]) == (2, 5, 13)
    assert by_model["mean_unique_per_app"] == 2.5  # (2 + 3) / 2, not 13 / 5
    assert (data["totals"]["apps"], data["totals"]["runs"]) == (2, 5)


def test_include_ai_says_when_a_partial_reviewer_was_dropped(scope):
    user, j1, _ = scope
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRun.objects.get(generation_job=j1)
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), rule_id="x", line_number=3)
    data = study.issue_breakdowns(study.study_jobs(user), include_ai=True)
    assert (data["params"]["ai_counted"], data["params"]["ai_partial"]) == ([], ["llm_review"])
    assert data["totals"]["raw_findings"] == 4
    everything = study.issue_breakdowns(study.study_jobs(user), include_ai=True, common_tools_only=False)
    assert everything["params"]["ai_counted"] == ["llm_review"]


def test_empty_scope_totals_are_unmeasured_not_zero():
    totals = study.issue_breakdowns(study.study_jobs(UserFactory()))["totals"]
    assert (totals["apps"], totals["runs"]) == (0, 0)
    assert totals["code_unique"] is None
    assert totals["unique_per_kloc"] is None


def test_table_rows_match_the_published_columns(scope):
    user, _j1, _j2 = scope
    data = study.issue_breakdowns(study.study_jobs(user), common_tools_only=False)
    assert tuple(data["per_app"][0]) == study.PER_APP_KEYS
    assert tuple(data["by_model"][0]) == ("model", *study.GROUP_SUMMARY_KEYS)
    assert tuple(data["by_tool"][0]) == study.BY_TOOL_KEYS
    assert tuple(data["by_tool_rule"][0]) == study.BY_TOOL_RULE_KEYS
    assert tuple(data["issues"][0]) == study.ISSUE_KEYS


def test_export_writes_real_headers_for_tables_without_rows(tmp_path):
    import csv

    from django.core.management import call_command

    user = UserFactory()
    job = _job(user, LLMModelFactory(), AppRequirementTemplateFactory(), mode="agent")
    _run(job, {"bandit": [("B105", 10, "low")]})
    call_command("export_study_tables", out=str(tmp_path))

    def header(name):
        with (tmp_path / name).open(encoding="utf-8") as fh:
            return next(csv.reader(fh))

    # Agent builds are never healed: no rows, but the real columns.
    assert header("healing_per_app.csv")[:3] == ["job_id", "model", "model_name"]
    assert header("healing_by_model.csv") == ["model", "apps", "runs"]
    assert "condition_label" in header("issues_by_condition.csv")
    # Normalized severities are named so.
    assert "unique_normalized_high" in header("issues_per_app.csv")
    assert "unique_high" not in header("issues_per_app.csv")
    assert "raw_normalized_high" in header("issues_by_tool.csv")
    with (tmp_path / "tool_coverage.csv").open(encoding="utf-8") as fh:
        assert next(csv.DictReader(fh)) == {
            "tool": "bandit",
            "counted": "True",
            "runs_measured": "1",
            "runs_missing": "0",
            "reasons": "",
        }
    with (tmp_path / "issues_by_tool_rule.csv").open(encoding="utf-8") as fh:
        assert next(csv.DictReader(fh))["reported_levels"] == "unrated=1"


def test_export_neutralizes_spreadsheet_formulas_unless_asked_not_to(tmp_path):
    import csv

    from django.core.management import call_command

    user = UserFactory()
    job = _job(user, LLMModelFactory(), AppRequirementTemplateFactory())
    run = _run(job, {"bandit": []})
    FindingFactory(
        result=ToolResult.objects.get(run=run, tool_slug="bandit"),
        rule_id="B105",
        line_number=3,
        file_path="=HYPERLINK(1)",
    )

    def first_cluster(out, **options):
        call_command("export_study_tables", out=str(out), **options)
        with (out / "issue_clusters.csv").open(encoding="utf-8") as fh:
            return next(csv.DictReader(fh))

    row = first_cluster(tmp_path / "safe")
    assert (row["file_path"], row["line"]) == ("'=HYPERLINK(1)", "3")
    assert first_cluster(tmp_path / "raw", spreadsheet_safe=False)["file_path"] == "=HYPERLINK(1)"


def test_breakdowns_judge_a_changed_pin_on_what_the_scaffold_range_installed():
    """Two apps on the same installed axios get the same owner, whether they kept ``^1.6.0`` or pinned it."""
    import json

    from backend.runtime.models import Stack

    Stack.objects.create(
        slug="npm-stack",
        version=1,
        files={"frontend/package.json": json.dumps({"dependencies": {"axios": "^1.6.0"}})},
    )
    user = UserFactory()
    model, app = LLMModelFactory(), AppRequirementTemplateFactory()
    lock = {"lockfileVersion": 3, "packages": {"": {}, "node_modules/axios": {"version": "1.20.0"}}}
    for spec in ("^1.6.0", "1.20.0"):
        job = _job(
            user,
            model,
            app,
            result_data={
                "files": {
                    "app.py": "x = 1\n" * 100,
                    "frontend/package.json": json.dumps({"dependencies": {"axios": spec}}),
                    "frontend/package-lock.json": json.dumps(lock),
                },
            },
            stack_slug="npm-stack",
            resolved_bundle={"stack": {"slug": "npm-stack", "version": 1}},
        )
        run = _run(job, {"bandit": []})
        FindingFactory(
            result=ToolResultFactory(run=run, tool_slug="osv-scanner"),
            rule_id="GHSA-new",
            category="dependency",
            line_number=None,
            file_path="frontend/package-lock.json",
            title="axios GHSA-new",
            tool_specific_data={"package": "axios", "installed_version": "1.20.0", "fixed_versions": ["1.20.1"]},
        )
    data = study.issue_breakdowns(study.study_jobs(user))
    assert [(a["dependency_unique"], a["dependency_added"]) for a in data["per_app"]] == [(1, 0), (1, 0)]
