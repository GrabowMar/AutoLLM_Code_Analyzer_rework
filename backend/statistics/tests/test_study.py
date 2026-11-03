"""Study tables: issue breakdowns, tool coverage, tool inventory and run accounting (v3 runs)."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation import blobs
from backend.generation.testing import content as samples
from backend.generation.testing import factories as gf
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics.services import accounting
from backend.statistics.services import study
from backend.statistics.tests.helpers import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

NOT_APPLICABLE = {"skip_reason": "not_applicable", "measurement": "not_measured", "tool_applicable": False}


def _job(user, model, task=None, *, files=None, origins=None, **kw):
    """A tested run of *model* on *task* (a TaskSpecVersion) whose tree is *files* (default 100 lines of app.py)."""
    if task is not None:
        kw["task_version"] = task
    return make_run(
        user,
        files={"app.py": "x = 1\n" * 100} if files is None else files,
        origins=origins,
        model=model,
        **kw,
    )


def _task(slug):
    return gf.TaskSpecVersionFactory(task=gf.TaskSpecFactory(slug=slug))


def _starter(files):
    """A starter whose tree is *files* (``{path: text}``, real blobs)."""
    stack = gf.StackProfileFactory()
    entries = {path: {"blob": blobs.put_text(text), "mode": 0o644} for path, text in files.items()}
    return gf.StarterFactory(
        stack_profile=stack,
        content=samples.starter_content(stack.pinned_ref, files=entries),
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


def _rows(data):
    return {a["run_id"]: a for a in data["per_app"]}


@pytest.fixture
def scope():
    user = UserFactory()
    m1, m2 = LLMModelFactory(model_id="vendor/a"), LLMModelFactory(model_id="vendor/b")
    t1, t2 = _task("todo"), _task("blog")
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
    data = study.issue_breakdowns(study.study_runs(user), common_tools_only=False)

    assert data["totals"]["raw_findings"] == 6
    assert data["totals"]["unique_issues"] == 4  # flask-debug x3 tools -> 1
    per_app = _rows(data)
    a1 = per_app[str(j1.id)]
    assert (a1["raw_findings"], a1["unique_issues"], a1["multi_tool_issues"]) == (5, 3, 1)
    assert a1["unique_per_kloc"] == 30.0
    assert {r["model"] for r in data["by_model"]} == {"vendor/a", "vendor/b"}
    tasks = {study.run_labels(j)["task"]: n for j, n in ((j1, 3), (_j2, 1))}
    assert {r["task"]: r["unique_issues"] for r in data["by_task"]} == tasks
    assert all(t.startswith(("todo@", "blog@")) for t in tasks)
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
    data = study.issue_breakdowns(study.study_runs(user), common_tools_only=False)
    a1 = _rows(data)[str(j1.id)]
    assert a1["raw_findings"] == 4  # bandit 1 (new run) + semgrep 1 + ruff 2
    assert {t["tool"]: t["raw_findings"] for t in data["by_tool"]}["bandit"] == 2  # j1 1 + j2 1


def test_failed_rerun_does_not_replace_a_measurement(scope):
    user, j1, _ = scope
    run = _run(j1, {})
    ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.FAILED)
    data = study.issue_breakdowns(study.study_runs(user), common_tools_only=False)
    assert _rows(data)[str(j1.id)]["raw_findings"] == 5


def test_breakdowns_exclude_ai_findings_by_default(scope):
    user, j1, _ = scope
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRun.objects.get(generation_job=j1)
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), rule_id="x", line_number=3)
    runs = study.study_runs(user)
    assert study.issue_breakdowns(runs, common_tools_only=False)["totals"]["raw_findings"] == 6
    assert study.issue_breakdowns(runs, include_ai=True, common_tools_only=False)["totals"]["raw_findings"] == 7


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
    assert {e["slug"] for e in inventory["evaluators"]} == {"eval-tester", "smoke"}


def test_run_accounting_counts_actual_executions(scope):
    user, j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    run.tool_slugs = ["bandit", "semgrep", "eslint"]
    run.save(update_fields=["tool_slugs"])
    ToolResultFactory(run=run, tool_slug="eslint", status=ToolResult.Status.FAILED)
    built = {stage: {"status": "ok"} for stage in ("lock", "build", "start", "tester")}
    gf.RunEvaluationFactory(run=j1, measured=True, stages=built)
    gf.RunEvaluationFactory(run=j2, measured=True, stages={"lock": {"status": "ok"}, "build": {"status": "fail"}})

    data = accounting.run_accounting(study.study_runs(user))
    assert data["apps"] == {
        "runs": 2,
        "by_status": {"completed": 2},
        "by_outcome": {"tested": 2},
        "usable": 2,
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
    for path in ("breakdowns", "tools", "accounting"):
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
    data = study.issue_breakdowns(study.study_runs(user))
    assert data["coverage"]["tools_common"] == ["bandit", "semgrep"]
    assert data["coverage"]["tools_partial"]["ruff"]["apps_missing"] == 1
    assert {r["tool"] for r in data["by_tool"]} <= {"bandit", "semgrep"}
    assert data["totals"]["raw_findings"] == 4


def test_not_applicable_tool_is_not_measured(scope):
    """Spec 8.8: a tool with no applicable files is not measured (never "0 findings"), so it is not common."""
    user, _j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    ToolResultFactory(run=run, tool_slug="ruff", status=ToolResult.Status.SKIPPED, summary=NOT_APPLICABLE)
    coverage = study.issue_breakdowns(study.study_runs(user))["coverage"]
    assert "ruff" not in coverage["tools_common"]
    assert coverage["tools_partial"]["ruff"] == {"apps_measured": 1, "apps_missing": 1, "not_applicable": 1}


def test_failed_tool_is_not_measured(scope):
    user, _j1, j2 = scope
    run = AnalysisRun.objects.get(generation_job=j2)
    ToolResultFactory(run=run, tool_slug="ruff", status=ToolResult.Status.FAILED)
    coverage = study.issue_breakdowns(study.study_runs(user))["coverage"]
    assert coverage["tools_partial"]["ruff"] == {"apps_measured": 1, "apps_missing": 1, "failed": 1}


def test_dependency_advisories_split_by_who_added_the_package():
    from backend.statistics.services.dependencies import manifest_packages

    starter = _starter(
        {
            "requirements.txt": "Flask-CORS==4.0.0\nPyJWT==2.8.0\n",
            "frontend/package.json": '{"devDependencies": {"vite": "^5"}}',
        },
    )
    user = UserFactory()
    files = {
        "app.py": "x = 1\n" * 100,
        "requirements.txt": "flask_cors==4.0.0  # kept\nPyJWT==2.8.0\nrequests==2.0\n",
    }
    job = _job(user, LLMModelFactory(), files=files, origins={"requirements.txt": "modified"}, starter=starter)
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
    data = study.issue_breakdowns(study.study_runs(user), common_tools_only=False)
    app = data["per_app"][0]
    assert (app["unique_issues"], app["code_unique"], app["dependency_unique"], app["dependency_added"]) == (
        4,
        1,
        3,
        1,
    )
    # Only the code issue counts per kLOC (100 model-scope source lines).
    assert app["unique_per_kloc"] == 10.0
    assert (app["code_unique_model"], app["loc"], app["loc_app"]) == (1, 100, 100)


def _app_row(user, **kw):
    data = study.issue_breakdowns(study.study_runs(user), **kw)
    assert len(data["per_app"]) == 1
    return data, data["per_app"][0]


def test_vulnerable_version_the_model_pinned_is_its_own():
    """Package and version: a downgraded starter package is the model's, the starter's own range is not."""
    import json

    starter = _starter({"frontend/package.json": json.dumps({"dependencies": {"axios": "^1.6.0", "vite": "^5.0.0"}})})
    user = UserFactory()
    package_json = {"dependencies": {"axios": "1.6.8", "vite": "5.4.21"}}
    job = _job(
        user,
        LLMModelFactory(),
        files={"app.py": "x = 1\n" * 100, "frontend/package.json": json.dumps(package_json)},
        origins={"frontend/package.json": "modified"},
        starter=starter,
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
    job = _job(user, LLMModelFactory())
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
    data = study.issue_breakdowns(study.study_runs(user))
    assert {a["manifest_unique"] for a in data["per_app"]} == {None}
    assert {a["manifest_unused_starter"] for a in data["per_app"]} == {None}
    assert (data["totals"]["manifest_unique"], data["totals"]["manifest_unused_starter"]) == (None, None)


def test_unused_calls_on_starter_packages_are_counted_apart():
    import json

    starter = _starter({"frontend/package.json": json.dumps({"dependencies": {"clsx": "^2.0.0", "react": "^18.2.0"}})})
    user = UserFactory()
    package_json = {"dependencies": {"clsx": "^2.0.0", "react": "^18.2.0", "lodash": "^4.17.21"}}
    job = _job(
        user,
        LLMModelFactory(),
        files={"app.py": "x = 1\n" * 100, "frontend/package.json": json.dumps(package_json)},
        origins={"frontend/package.json": "modified"},
        starter=starter,
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
    # Both are the app's own manifest issues (P2); one is a package the starter preinstalls.
    assert (app["code_unique"], app["manifest_unique"], app["manifest_unused_starter"]) == (2, 2, 1)


def test_dast_findings_are_runtime_issues_counted_once_per_class():
    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic", category="security")
    user = UserFactory()
    job = _job(user, LLMModelFactory())
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
    job = _job(user, LLMModelFactory(), files=files)
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
    data = study.issue_breakdowns(study.study_runs(user))
    assert "radon" in data["coverage"]["tools_common"]
    radon = {t["tool"]: t for t in data["by_tool"]}["radon"]
    assert (radon["raw_findings"], radon["unique_issues"], radon["apps_with_findings"]) == (0, 0, 0)
    assert (radon["apps_measured"], radon["reported_levels"]) == (2, {})


def test_tool_tables_show_the_levels_tools_printed(scope):
    from backend.analysis.models import Finding

    user, _j1, _j2 = scope
    Finding.objects.filter(rule_id="B608").update(tool_severity="MEDIUM")
    data = study.issue_breakdowns(study.study_runs(user))
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


def test_issue_clusters_name_the_run_and_per_app_rows_the_analysis(scope):
    user, j1, _j2 = scope
    data = study.issue_breakdowns(study.study_runs(user), common_tools_only=False)
    per_app = _rows(data)
    assert per_app[str(j1.id)]["analysis_run_id"] == str(AnalysisRun.objects.get(generation_job=j1).id)
    assert {i["run_id"] for i in data["issues"]} == set(per_app)
    assert all("severity_normalized" in i and "severity" not in i for i in data["issues"])


def test_conditions_of_one_model_are_grouped_apart_and_repeats_are_one_app():
    from backend.statistics.tests.helpers import StudyBuilder

    user = UserFactory()
    model = LLMModelFactory(model_id="vendor/a")
    built = StudyBuilder(user, repeats=3, n_conditions=0, n_units=1)
    arm_a = gf.StudyConditionFactory(plan=built.plan, study=built.study, model=model, label="harness A")
    arm_b = gf.StudyConditionFactory(plan=built.plan, study=built.study, model=model, label="")
    built.conditions = [arm_a, arm_b]
    for c, issues_per_repeat in ((0, [1, 3]), (1, [2, 2, 5])):
        for repeat, n in enumerate(issues_per_repeat):
            job = built.run(c, 0, repeat, files={"app.py": "x = 1\n" * 100})
            _run(job, {"bandit": [(f"B9{i:02d}", 1 + 10 * i, "low") for i in range(n)]})
    data = study.issue_breakdowns(study.study_runs(user, study_id=str(built.study.id)))

    # An unlabelled arm is named by its model.
    assert {(a["condition_id"], a["condition_label"]) for a in data["per_app"]} == {
        (arm_a.id, "harness A"),
        (arm_b.id, "vendor/a"),
    }
    arms = {r["condition_label"]: r for r in data["by_condition"]}
    assert (arms["harness A"]["apps"], arms["harness A"]["runs"]) == (1, 2)
    assert arms["harness A"]["mean_unique_per_app"] == 2.0
    assert arms["harness A"]["unique_per_kloc"] == 20.0
    # One model, two arms, one unit each: two apps, five runs; each app is its repeats' mean.
    (by_model,) = data["by_model"]
    assert (by_model["apps"], by_model["runs"], by_model["code_unique"]) == (2, 5, 13)
    assert by_model["mean_unique_per_app"] == 2.5  # (2 + 3) / 2, not 13 / 5
    assert (data["totals"]["apps"], data["totals"]["runs"]) == (2, 5)


def test_starter_issues_are_not_the_models_and_loc_is_model_scope():
    """Analysis reads the app scope; attribution (code_unique_model, per kLOC) is by file origin (Appendix A #4)."""
    user = UserFactory()
    files = {"app.py": "x = 1\n" * 100, "starter/base.py": "y = 2\n" * 300, "dist/app.js": "z\n"}
    job = _job(user, LLMModelFactory(), files=files, origins={"starter/base.py": "starter", "dist/app.js": "artifact"})
    run = _run(job, {"bandit": []})
    bandit = ToolResult.objects.get(run=run, tool_slug="bandit")
    for rule, line, path in (
        ("B105", 4, "/work/app.py"),
        ("B106", 2, "starter/base.py"),
        ("B107", 3, "starter/base.py"),
    ):
        FindingFactory(result=bandit, rule_id=rule, line_number=line, file_path=path, severity="low")
    data, app = _app_row(user)
    assert (app["code_unique"], app["code_unique_model"]) == (3, 1)
    assert (app["loc"], app["loc_app"]) == (100, 400)
    assert app["unique_per_kloc"] == 10.0
    assert {i["file_path"]: i["model_scope"] for i in data["issues"]} == {
        "/work/app.py": True,
        "starter/base.py": False,
    }


def test_a_retried_cell_counts_its_first_usable_attempt_once():
    from backend.statistics.tests.helpers import StudyBuilder

    user = UserFactory()
    built = StudyBuilder(user, repeats=1, n_conditions=1, n_units=1)
    first = built.run(0, 0, 0, attempt=1)
    built.run(0, 0, 0, attempt=2)  # a later attempt never replaces a usable one
    infra = built.run(0, 0, 0, attempt=3, tested=False, status="failed", outcome="infra_error", usable=False)
    extra = built.run(0, 0, 0, attempt=4, beyond_plan=True)
    for job in (first, infra, extra):
        _run(job, {"bandit": [("B105", 10, "low")]})
    ids = {str(r.pk) for r in study.study_runs(user, study_id=str(built.study.id))}
    assert ids == {str(first.pk)}
    assert {str(r.pk) for r in study.study_runs(user)} == {str(first.pk)}
    data = study.issue_breakdowns(study.study_runs(user, study_id=str(built.study.id)))
    assert [a["run_id"] for a in data["per_app"]] == [str(first.pk)]


def test_include_ai_says_when_a_partial_reviewer_was_dropped(scope):
    user, j1, _ = scope
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRun.objects.get(generation_job=j1)
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), rule_id="x", line_number=3)
    data = study.issue_breakdowns(study.study_runs(user), include_ai=True)
    assert (data["params"]["ai_counted"], data["params"]["ai_partial"]) == ([], ["llm_review"])
    assert data["totals"]["raw_findings"] == 4
    everything = study.issue_breakdowns(study.study_runs(user), include_ai=True, common_tools_only=False)
    assert everything["params"]["ai_counted"] == ["llm_review"]


def test_empty_scope_totals_are_unmeasured_not_zero():
    totals = study.issue_breakdowns(study.study_runs(UserFactory()))["totals"]
    assert (totals["apps"], totals["runs"]) == (0, 0)
    assert totals["code_unique"] is None
    assert totals["unique_per_kloc"] is None


def test_table_rows_match_the_published_columns(scope):
    user, _j1, _j2 = scope
    data = study.issue_breakdowns(study.study_runs(user), common_tools_only=False)
    assert tuple(data["per_app"][0]) == study.PER_APP_KEYS
    assert tuple(data["by_model"][0]) == ("model", *study.GROUP_SUMMARY_KEYS)
    assert tuple(data["by_tool"][0]) == study.BY_TOOL_KEYS
    assert tuple(data["by_tool_rule"][0]) == study.BY_TOOL_RULE_KEYS
    assert tuple(data["issues"][0]) == study.ISSUE_KEYS


def test_export_writes_real_headers_for_tables_without_rows(tmp_path):
    out = tmp_path / "out"
    import csv

    from django.core.management import call_command

    user = UserFactory()
    job = _job(user, LLMModelFactory())
    _run(job, {"bandit": [("B105", 10, "low")]})
    call_command("export_study_tables", out=str(out))

    def header(name):
        with (out / name).open(encoding="utf-8") as fh:
            return next(csv.reader(fh))

    # No metric tool ran: no rows, but the real columns.
    assert header("code_metrics_per_app.csv")[:3] == ["run_id", "model", "model_name"]
    assert header("issues_per_app.csv")[:3] == ["run_id", "model", "model_name"]
    assert "condition_label" in header("issues_by_condition.csv")
    assert header("issues_by_task.csv")[0] == "task"
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [
            *(
                f"issues_{name}.csv"
                for name in (
                    "per_app",
                    "by_model",
                    "by_condition",
                    "by_task",
                    "by_model_task",
                    "by_tool",
                    "by_tool_rule",
                )
            ),
            "tool_overlap.csv",
            "issue_clusters.csv",
            "tool_coverage.csv",
            "code_metrics_per_app.csv",
            "tool_inventory.csv",
            "tool_executions.csv",
            "study_summary.json",
        ],
    )
    # Normalized severities are named so.
    assert "unique_normalized_high" in header("issues_per_app.csv")
    assert "unique_high" not in header("issues_per_app.csv")
    assert "raw_normalized_high" in header("issues_by_tool.csv")
    with (out / "tool_coverage.csv").open(encoding="utf-8") as fh:
        assert next(csv.DictReader(fh)) == {
            "tool": "bandit",
            "counted": "True",
            "runs_measured": "1",
            "runs_missing": "0",
            "reasons": "",
        }
    with (out / "issues_by_tool_rule.csv").open(encoding="utf-8") as fh:
        assert next(csv.DictReader(fh))["reported_levels"] == "unrated=1"


def test_export_scopes_to_one_study(tmp_path):
    import json

    from django.core.management import call_command
    from django.core.management.base import CommandError

    from backend.statistics.tests.helpers import StudyBuilder

    user = UserFactory()
    built = StudyBuilder(user, repeats=1, n_conditions=1, n_units=1)
    in_study = built.run(0, 0, 0)
    _run(in_study, {"bandit": [("B105", 10, "low")]})
    _run(_job(user, LLMModelFactory()), {"bandit": [("B105", 10, "low")]})  # another run, outside the study
    call_command("export_study_tables", out=str(tmp_path / "out"), study=str(built.study.id))
    summary = json.loads((tmp_path / "out" / "study_summary.json").read_text(encoding="utf-8"))
    assert summary["scope"] == {"study": str(built.study.id), "stage": "main", "runs": 1}
    assert summary["issues"]["totals"]["runs"] == 1
    with pytest.raises(CommandError, match="no study"):
        call_command("export_study_tables", out=str(tmp_path / "x"), study="6f1c1d5e-9a43-4a8b-b6a4-3c3f4f0b9d2e")


def test_export_neutralizes_spreadsheet_formulas_unless_asked_not_to(tmp_path):
    import csv

    from django.core.management import call_command

    user = UserFactory()
    job = _job(user, LLMModelFactory())
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


def test_breakdowns_judge_a_changed_pin_on_what_the_starter_range_installed():
    """Two apps on the same installed axios get the same owner, whether they kept ``^1.6.0`` or pinned it."""
    import json

    starter = _starter({"frontend/package.json": json.dumps({"dependencies": {"axios": "^1.6.0"}})})
    user = UserFactory()
    model, task = LLMModelFactory(), _task("todo")
    lock = {"lockfileVersion": 3, "packages": {"": {}, "node_modules/axios": {"version": "1.20.0"}}}
    for spec in ("^1.6.0", "1.20.0"):
        job = _job(
            user,
            model,
            task,
            files={
                "app.py": "x = 1\n" * 100,
                "frontend/package.json": json.dumps({"dependencies": {"axios": spec}}),
                "frontend/package-lock.json": json.dumps(lock),
            },
            origins={"frontend/package.json": "starter" if spec == "^1.6.0" else "modified"},
            starter=starter,
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
    data = study.issue_breakdowns(study.study_runs(user))
    assert [(a["dependency_unique"], a["dependency_added"]) for a in data["per_app"]] == [(1, 0), (1, 0)]
