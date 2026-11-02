"""GET /analysis/runs/{id}/context/: setup line, source LOC, test-code split, rule prevalence; findings in tests."""

from __future__ import annotations

import pytest
from django.test import Client

from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

FILES = {
    "app.py": "import os\n\n# a comment\nprint(os.getcwd())\n",
    "frontend/src/App.jsx": "export default function App() {\n  return null\n}\n",
    "tests/test_app.py": "def test_ok():\n    assert True\n",
    "package-lock.json": "{}\n",
    ".tmp/pkg/README.md": "scratch\n",
    ".tmp/pkg/index.js": "module.exports = 1\n",
    "node_modules/x/index.js": "module.exports = 2\n",
}


def _job(user, model, *, experiment=None, condition=None, app=None, repeat=0, files=None, **extra):
    return GenerationJobFactory(
        created_by=user,
        status="completed",
        mode=extra.pop("mode", "agent"),
        model=model,
        app_requirement=app or AppRequirementTemplateFactory(),
        experiment=experiment,
        condition=condition,
        repeat_index=repeat,
        result_data={"files": files or {"app.py": "x = 1\n"}},
        **extra,
    )


def _result(run, tool, status="completed", skip_reason=None, findings=()):
    summary = {"skip_reason": skip_reason} if skip_reason else {}
    result = ToolResultFactory(run=run, tool_slug=tool, category="security", status=status, summary=summary)
    for rule, path in findings:
        FindingFactory(result=result, rule_id=rule, file_path=path, category="security")
    return result


@pytest.fixture
def corpus():
    """An experiment of three apps (two of model A, one of model B), plus one more app of A outside it.

    J1 (A): bandit B101 in a test file and B105 in app.py; ruff F401.
    J2 (A): bandit B105; ruff failed but kept an F401 finding (not a measurement); an older run had B101.
    J3 (B): bandit not applicable (a measured zero); ruff F401.
    J4 (A, no experiment): bandit B105.
    Another user's app has B105 too.
    """
    user = UserFactory()
    model_a = LLMModelFactory(model_id="vendor/a", model_name="Model A")
    model_b = LLMModelFactory(model_id="vendor/b", model_name="Model B")
    experiment = ExperimentFactory(created_by=user, name="Exp one", repeats=2)
    cond_a = ExperimentConditionFactory(experiment=experiment, model=model_a, label="A arm")
    cond_b = ExperimentConditionFactory(experiment=experiment, model=model_b, label="B arm")
    app = AppRequirementTemplateFactory(name="Todo", slug="todo")
    j1 = _job(
        user,
        model_a,
        experiment=experiment,
        condition=cond_a,
        app=app,
        repeat=1,
        files=FILES,
        bundle_key="agent:opencode@1.0",
        agent_config={"harness": "opencode"},
        resolved_bundle={"config_fingerprint": "cfg123"},
    )
    j2 = _job(user, model_a, experiment=experiment, condition=cond_a, app=app, repeat=0)
    j3 = _job(user, model_b, experiment=experiment, condition=cond_b, app=app, repeat=0)
    j4 = _job(user, model_a)

    run1 = AnalysisRunFactory(created_by=user, generation_job=j1, source_code=None, status="completed")
    _result(run1, "bandit", findings=[("B101", "tests/test_app.py"), ("B105", "app.py")])
    _result(run1, "ruff", findings=[("F401", "app.py")])

    old2 = AnalysisRunFactory(created_by=user, generation_job=j2, source_code=None, status="completed")
    _result(old2, "bandit", findings=[("B101", "app.py")])
    run2 = AnalysisRunFactory(created_by=user, generation_job=j2, source_code=None, status="completed")
    _result(run2, "bandit", findings=[("B105", "app.py")])
    _result(run2, "ruff", status="failed", findings=[("F401", "app.py")])
    # The old run is older: the latest bandit result of J2 is run2's.
    old2.created_at = run2.created_at.replace(year=run2.created_at.year - 1)
    old2.save(update_fields=["created_at"])
    old2.results.update(created_at=old2.created_at)

    run3 = AnalysisRunFactory(created_by=user, generation_job=j3, source_code=None, status="completed")
    _result(run3, "bandit", status="skipped", skip_reason="not_applicable")
    _result(run3, "ruff", findings=[("F401", "app.py")])

    run4 = AnalysisRunFactory(created_by=user, generation_job=j4, source_code=None, status="completed")
    _result(run4, "bandit", findings=[("B105", "app.py")])

    stranger = UserFactory()
    other = AnalysisRunFactory(
        created_by=stranger,
        generation_job=_job(stranger, model_a),
        source_code=None,
        status="completed",
    )
    _result(other, "bandit", findings=[("B105", "app.py")])
    client = Client()
    client.force_login(user)
    return {"user": user, "client": client, "run1": run1, "experiment": experiment, "j1": j1, "run4": run4}


def _context(client, run, query=""):
    return client.get(f"/api/analysis/runs/{run.id}/context/{query}")


def _rules(data):
    return {(r["tool"], r["rule_id"]): r for r in data["rule_prevalence"]}


def test_setup_line_names_the_design_cell_and_method(corpus):
    data = _context(corpus["client"], corpus["run1"]).json()
    job = data["job"]
    assert job["id"] == str(corpus["j1"].id)
    assert (job["experiment_id"], job["experiment_name"]) == (str(corpus["experiment"].id), "Exp one")
    assert job["condition_label"] == "A arm"
    # repeat_index is 0-based; N is the repeats planned at launch (the experiment's, without a launch batch).
    assert (job["repeat_index"], job["planned_repeats"]) == (1, 2)
    assert (job["model_id"], job["model_name"], job["app_name"], job["app_slug"]) == (
        "vendor/a",
        "Model A",
        "Todo",
        "todo",
    )
    assert job["model_pk"] == corpus["j1"].model_id
    assert (job["harness"], job["bundle_key"], job["config_fingerprint"]) == (
        "opencode",
        "agent:opencode@1.0",
        "cfg123",
    )


def test_source_loc_uses_the_study_definition_and_lists_excluded_paths(corpus):
    loc = _context(corpus["client"], corpus["run1"]).json()["source_loc"]
    # app.py: 2 code lines (blank and comment left out); App.jsx: 3; the test file apart.
    assert loc["by_layer"] == {"backend": 2, "frontend": 3, "tests": 2}
    assert (loc["total"], loc["files"], loc["test_files"]) == (5, 2, 1)
    # package-lock.json is stored but not source; .tmp/ and node_modules/ are never analyzed.
    assert loc["other_files"] == 1
    assert loc["excluded_paths"]["count"] == 3
    assert loc["excluded_paths"]["dirs"] == {".tmp": 2, "node_modules": 1}
    assert loc["excluded_paths"]["sample"] == [".tmp/pkg/README.md", ".tmp/pkg/index.js", "node_modules/x/index.js"]


def test_test_split_counts_findings_in_test_files_apart(corpus):
    split = {r["tool"]: r for r in _context(corpus["client"], corpus["run1"]).json()["test_split"]}
    assert split["bandit"] == {
        "tool": "bandit",
        "findings_in_tests": 1,
        "findings_in_source": 1,
        "findings_in_excluded": 0,
    }
    assert split["ruff"] == {
        "tool": "ruff",
        "findings_in_tests": 0,
        "findings_in_source": 1,
        "findings_in_excluded": 0,
    }


def test_prevalence_denominators_are_the_apps_each_tool_measured(corpus):
    data = _context(corpus["client"], corpus["run1"]).json()
    assert (data["scope"], data["scopes_available"]) == ("experiment", ["experiment", "mine"])
    assert (data["n"], data["model_n"], data["self_in_corpus"]) == (3, 2, True)
    rules = _rules(data)
    # bandit measured all three apps (J3: not applicable, a measured zero); B105 in J1 and J2.
    b105 = rules[("bandit", "B105")]
    assert (b105["apps_with_rule"], b105["apps_measured"]) == (2, 3)
    assert (b105["model_apps_with_rule"], b105["model_apps_measured"]) == (2, 2)
    assert b105["count_in_run"] == 1
    # J2's older run had B101, but its latest bandit result does not.
    b101 = rules[("bandit", "B101")]
    assert (b101["apps_with_rule"], b101["apps_measured"]) == (1, 3)
    # ruff failed on J2: not in the denominator, and its leftover finding is not counted either.
    f401 = rules[("ruff", "F401")]
    assert (f401["apps_with_rule"], f401["apps_measured"]) == (2, 2)
    assert (f401["model_apps_with_rule"], f401["model_apps_measured"]) == (1, 1)
    assert b105["issue_class"] == "hardcoded-secret"
    assert b105["scope"] == "code"


def test_mine_scope_counts_all_of_the_callers_apps_only(corpus):
    data = _context(corpus["client"], corpus["run1"], "?scope=mine").json()
    assert (data["scope"], data["n"], data["model_n"]) == ("mine", 4, 3)
    b105 = _rules(data)[("bandit", "B105")]
    # J1, J2, J4 of the caller; the other user's app is never counted.
    assert (b105["apps_with_rule"], b105["apps_measured"]) == (3, 4)
    assert (b105["model_apps_with_rule"], b105["model_apps_measured"]) == (3, 3)


def test_experiment_scope_falls_back_for_an_app_outside_experiments(corpus):
    data = _context(corpus["client"], corpus["run4"], "?scope=experiment").json()
    assert (data["scope"], data["scopes_available"]) == ("mine", ["mine"])
    assert data["notes"]
    assert data["job"]["experiment_id"] is None
    assert data["job"]["planned_repeats"] is None


def test_unknown_scope_and_other_users_runs(corpus):
    assert _context(corpus["client"], corpus["run1"], "?scope=everything").status_code == 400
    stranger = Client()
    stranger.force_login(UserFactory())
    assert _context(stranger, corpus["run1"]).status_code == 404
    assert stranger.get("/api/analysis/runs/not-a-uuid/context/").status_code == 404


def test_pasted_code_has_no_setup_and_counts_rules_over_the_callers_apps(corpus):
    run = AnalysisRunFactory(
        created_by=corpus["user"],
        source_code={"app.py": "x = 1\ny = 2\n", "tests/test_x.py": "assert 1\n"},
        status="completed",
    )
    _result(run, "bandit", findings=[("B105", "app.py")])
    data = _context(corpus["client"], run).json()
    assert data["job"] is None
    assert (data["scope"], data["model_n"]) == ("mine", None)
    assert data["source_loc"]["by_layer"] == {"backend": 2, "frontend": 0, "tests": 1}
    b105 = _rules(data)[("bandit", "B105")]
    assert (b105["apps_with_rule"], b105["apps_measured"], b105["model_apps_with_rule"]) == (3, 4, None)
    assert data["self_in_corpus"] is False


def test_findings_api_splits_code_findings_by_test_files(corpus):
    client, run = corpus["client"], corpus["run1"]
    url = f"/api/analysis/runs/{run.id}/findings/"
    tests = client.get(f"{url}?scope=tests").json()
    assert [(f["rule_id"], f["in_tests"]) for f in tests["items"]] == [("B101", True)]
    source = client.get(f"{url}?scope=source").json()
    assert {f["rule_id"] for f in source["items"]} == {"B105", "F401"}
    assert not any(f["in_tests"] for f in source["items"])
    grouped = client.get(f"{url}?group=true").json()
    assert {f["rule_id"]: f["in_tests"] for f in grouped["items"]}["B101"] is True
    facets = client.get(f"{url}facets/").json()
    assert facets["code_parts"] == {"source": 2, "tests": 1, "excluded": 0}
    assert client.get(f"{url}facets/?scope=tests").json()["total"] == 1


def test_advisories_and_runtime_findings_are_never_test_code(corpus):
    """A manifest or URL path that looks like a test path is not test code: only code findings are split."""
    run = AnalysisRunFactory(created_by=corpus["user"], source_code={"app.py": "x = 1\n"}, status="completed")
    advisory = ToolResultFactory(run=run, tool_slug="pip-audit", category="dependencies")
    FindingFactory(result=advisory, rule_id="CVE-1", file_path="tests/requirements.txt", category="dependency")
    facets = corpus["client"].get(f"/api/analysis/runs/{run.id}/findings/facets/").json()
    assert facets["code_parts"] == {"source": None, "tests": None, "excluded": None}
    items = corpus["client"].get(f"/api/analysis/runs/{run.id}/findings/").json()["items"]
    assert [f["in_tests"] for f in items] == [False]


# --- Review regressions --------------------------------------------------------------


def _excluded(client, run):
    return _context(client, run).json()["source_loc"]["excluded_paths"]


def test_excluded_paths_say_whether_this_run_left_them_out(corpus):
    """A run finalized before the exclusion rule read those files: the page must not claim they were left out."""
    user, client, j1 = corpus["user"], corpus["client"], corpus["j1"]
    # Recorded by the runner (summary.excluded_paths): left out, and none of its findings lie there.
    now = AnalysisRunFactory(
        created_by=user,
        generation_job=j1,
        source_code=None,
        status="completed",
        summary={"total_findings": 0, "tools_run": 1, "excluded_paths": {"count": 3, "sample": []}},
    )
    assert (_excluded(client, now)["left_out"], _excluded(client, now)["findings_in_excluded"]) == (True, 0)
    # Finalized with tools but no record: the run predates the rule and its tools read them.
    old = AnalysisRunFactory(
        created_by=user,
        generation_job=j1,
        source_code=None,
        status="partial",
        summary={"total_findings": 2, "tools_run": 2},
    )
    _result(old, "jscpd", findings=[("duplicate-code", ".tmp/pkg/index.js"), ("duplicate-code", "app.py")])
    excluded = _excluded(client, old)
    assert (excluded["count"], excluded["left_out"], excluded["findings_in_excluded"]) == (3, False, 1)
    # Cancelled before any tool ran, or never finished: unknown, not "left out".
    queued = AnalysisRunFactory(
        created_by=user,
        generation_job=j1,
        source_code=None,
        status="cancelled",
        summary={"total_findings": 0, "tools_run": 0},
    )
    assert _excluded(client, queued)["left_out"] is None
    # No excluded paths at all: nothing to say.
    run3 = AnalysisRunFactory(created_by=user, source_code={"app.py": "x = 1\n"}, status="completed")
    assert (_excluded(client, run3)["count"], _excluded(client, run3)["left_out"]) == (0, None)


def test_findings_in_excluded_dirs_are_neither_tests_nor_source(corpus):
    """A run finalized before the exclusion rule read .tmp/ etc.: those findings are counted apart everywhere."""
    user, client, j1 = corpus["user"], corpus["client"], corpus["j1"]
    old = AnalysisRunFactory(
        created_by=user,
        generation_job=j1,
        source_code=None,
        status="partial",
        summary={"total_findings": 5, "tools_run": 1},
    )
    _result(
        old,
        "pylint",
        findings=[
            ("W0611", ".tmp/test_api.py"),  # a test file name inside a scratch dir: not the app's test code
            ("W0612", ".tmp/test_api.py"),
            ("C0114", "node_modules/x/index.js"),
            ("W0613", "tests/test_app.py"),
            ("C0115", "app.py"),
        ],
    )
    data = _context(client, old).json()
    assert data["test_split"] == [
        {"tool": "pylint", "findings_in_tests": 1, "findings_in_source": 1, "findings_in_excluded": 3},
    ]
    assert any(n.startswith("3 code findings of this run lie in excluded directories") for n in data["notes"])
    # The findings explorer splits the same way.
    url = f"/api/analysis/runs/{old.id}/findings/"
    assert [f["file_path"] for f in client.get(f"{url}?scope=tests").json()["items"]] == ["tests/test_app.py"]
    assert [f["file_path"] for f in client.get(f"{url}?scope=source").json()["items"]] == ["app.py"]
    facets = client.get(f"{url}facets/").json()
    assert facets["scopes"]["code"] == 5
    assert facets["code_parts"] == {"source": 1, "tests": 1, "excluded": 3}
    assert client.get(f"{url}facets/?scope=tests").json()["total"] == 1
    # Nothing excluded: no note.
    assert not any("excluded directories" in n for n in _context(client, corpus["run1"]).json()["notes"])


def test_issue_class_is_left_empty_when_a_rule_spans_several_classes(corpus):
    """nuclei's http-missing-security-headers is one class per header: no row may name one class for all."""
    run = AnalysisRunFactory(created_by=corpus["user"], source_code={"app.py": "x = 1\n"}, status="completed")
    result = ToolResultFactory(run=run, tool_slug="nuclei", category="security")
    for header in ("x-frame-options", "content-security-policy"):
        FindingFactory(
            result=result,
            rule_id="http-missing-security-headers",
            file_path="",
            tool_specific_data={"issue_key": f"missing-header:{header}"},
        )
    FindingFactory(result=result, rule_id="missing-sri", file_path="", tool_specific_data={"cwe": "CWE-353"})
    rules = _rules(_context(corpus["client"], run).json())
    assert rules[("nuclei", "http-missing-security-headers")]["issue_class"] == ""
    assert rules[("nuclei", "missing-sri")]["issue_class"] != ""


def test_advisory_tools_non_dependency_rules_are_code_scope(corpus):
    """A trivy secret finding is a code finding (runner.finding_scope_q), not a dependency advisory."""
    run = AnalysisRunFactory(created_by=corpus["user"], source_code={"app.py": "x = 1\n"}, status="completed")
    result = ToolResultFactory(run=run, tool_slug="trivy", category="security")
    FindingFactory(result=result, rule_id="aws-access-key-id", file_path="app.py", category="secrets")
    FindingFactory(result=result, rule_id="CVE-2024-1", file_path="requirements.txt", category="dependency")
    rules = _rules(_context(corpus["client"], run).json())
    assert rules[("trivy", "aws-access-key-id")]["scope"] == "code"
    assert rules[("trivy", "CVE-2024-1")]["scope"] == "dependencies"


def test_a_superseded_run_says_its_findings_may_not_be_counted(corpus):
    """J2's older run had B101; the corpus counts J2 by its latest bandit result, which does not."""
    old2 = AnalysisRun.objects.filter(generation_job__repeat_index=0, created_by=corpus["user"]).order_by(
        "created_at",
    )[0]
    data = _context(corpus["client"], old2).json()
    assert data["self_in_corpus"] is True
    assert (_rules(data)[("bandit", "B101")]["apps_with_rule"]) == 1  # J1 only: J2's latest bandit has none
    assert any("bandit result is not the one counted" in note for note in data["notes"])
    # The latest run's results are the counted ones: no such note.
    assert not any("not the one counted" in n for n in _context(corpus["client"], corpus["run1"]).json()["notes"])


def test_a_failed_tool_result_of_this_run_is_flagged_as_not_counted(corpus):
    """J2's ruff failed (it kept an F401 finding): the app is not in ruff's denominator, and the note says so."""
    run2 = AnalysisRun.objects.filter(results__tool_slug="ruff", results__status="failed").get()
    notes = _context(corpus["client"], run2).json()["notes"]
    assert any("ruff result is not the one counted" in n for n in notes)


def test_experiment_prevalence_is_interim_while_the_experiment_runs(corpus):
    experiment = corpus["experiment"]
    experiment.status = "running"
    experiment.save(update_fields=["status"])
    assert _context(corpus["client"], corpus["run1"]).json()["interim"] is True
    # The caller's whole corpus is not an experiment's sample: no interim flag.
    assert _context(corpus["client"], corpus["run1"], "?scope=mine").json()["interim"] is False
    experiment.status = "completed"
    experiment.save(update_fields=["status"])
    assert _context(corpus["client"], corpus["run1"]).json()["interim"] is False
