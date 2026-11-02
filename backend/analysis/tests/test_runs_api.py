"""The runs API behind the analysis pages: list/stats, batches, preview, re-run, bulk actions, findings."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _post(client, path, data):
    return client.post(f"/api/analysis{path}", data=data, content_type="application/json")


@pytest.fixture
def no_dispatch(monkeypatch):
    calls: list = []
    monkeypatch.setattr(runner, "dispatch", lambda run_id: calls.append([run_id]))
    monkeypatch.setattr(runner, "dispatch_many", lambda ids: calls.append([str(i) for i in ids]))
    return calls


def _job(user, files=None):
    return GenerationJobFactory(
        created_by=user,
        status="completed",
        result_data={"files": files or {"app.py": "import os\n", "templates/index.html": "<p>hi</p>\n"}},
    )


# --- list / stats -------------------------------------------------------------


def test_list_shows_target_and_live_progress(api_client, user):
    job = _job(user)
    active = AnalysisRunFactory(
        created_by=user,
        generation_job=job,
        source_code=None,
        status="running",
        tool_slugs=["a", "b", "c"],
        summary={"phase": runner.PHASE_TOOLS},
    )
    ToolResultFactory(run=active, tool_slug="a", status="completed")
    FindingFactory(result=ToolResultFactory(run=active, tool_slug="b", status="running"))
    summary = {"total_findings": 3, "metrics_by_tool": {}}
    done = AnalysisRunFactory(created_by=user, status="completed", summary=summary)

    items = {i["id"]: i for i in api_client.get("/api/analysis/runs/").json()["items"]}

    live = items[str(active.id)]
    assert live["target"]["kind"] == "job"
    assert live["target"]["job_id"] == str(job.id)
    assert live["phase"] == runner.PHASE_TOOLS
    assert live["progress"] == {"total": 3, "done": 1, "running": ["b"], "findings": 1}
    finished = items[str(done.id)]
    assert finished["progress"] is None
    assert finished["target"]["kind"] == "code"
    assert "metrics_by_tool" not in finished["summary"]


def test_list_filters_and_sorts(api_client, user):
    few = AnalysisRunFactory(created_by=user, name="alpha", status="completed", summary={"total_findings": 2})
    many = AnalysisRunFactory(created_by=user, name="beta", status="failed", summary={"total_findings": 9})
    pending = AnalysisRunFactory(created_by=user, name="gamma", status="pending", tool_slugs=["semgrep"])
    AnalysisRunFactory(name="alpha")  # someone else's

    def ids(query):
        return [i["id"] for i in api_client.get(f"/api/analysis/runs/?{query}").json()["items"]]

    assert ids("search=alph") == [str(few.id)]
    assert ids(f"search={str(many.id)[:8]}") == [str(many.id)]
    assert ids("status=active") == [str(pending.id)]
    assert set(ids("status=completed,failed")) == {str(few.id), str(many.id)}
    assert ids("tool_slug=semgrep") == [str(pending.id)]
    assert ids("sort=findings_desc")[:2] == [str(many.id), str(few.id)]
    # By code findings: the run with more advisories but fewer code findings sorts after.
    FindingFactory(result=ToolResultFactory(run=few, tool_slug="bandit"))
    osv = ToolResultFactory(run=many, tool_slug="osv-scanner")
    FindingFactory(result=osv, category="dependency", rule_id="GHSA-1")
    by_code = ids("sort=code_findings_desc")
    assert by_code[0] == str(few.id)
    assert by_code.index(str(few.id)) < by_code.index(str(many.id))


def test_stats_count_the_callers_runs(api_client, user):
    AnalysisRunFactory(created_by=user, status="running")
    AnalysisRunFactory(created_by=user, status="completed")
    AnalysisRunFactory(status="running")
    body = api_client.get("/api/analysis/runs/stats/").json()
    assert body == {"total": 2, "active": 1, "by_status": {"running": 1, "completed": 1}}


# --- create / batch / preview --------------------------------------------------


def test_create_rejects_unknown_tools(api_client, no_dispatch):
    resp = _post(api_client, "/runs/", {"tool_slugs": ["nope"], "source_code": {"app.py": "x"}})
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]
    assert not no_dispatch


def test_create_names_the_run_after_its_app(api_client, user, no_dispatch):
    AnalyzerToolFactory(slug="bandit")
    job = _job(user)
    body = _post(api_client, "/runs/", {"tool_slugs": ["bandit"], "generation_job_id": str(job.id)}).json()
    assert body["name"] == body["target"]["label"]
    # One line each ("import os\n"): a trailing newline does not start another line.
    assert body["source_files"] == [{"path": "app.py", "lines": 1}, {"path": "templates/index.html", "lines": 1}]
    assert no_dispatch == [[body["id"]]]


def test_batch_queues_one_run_per_app(api_client, user, no_dispatch):
    AnalyzerToolFactory(slug="bandit")
    jobs = [_job(user), _job(user)]
    resp = _post(
        api_client,
        "/runs/batch/",
        {"tool_slugs": ["bandit", "bandit"], "generation_job_ids": [str(j.id) for j in jobs]},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    runs = list(AnalysisRun.objects.filter(batch_id=body["batch_id"]))
    assert {str(r.generation_job_id) for r in runs} == {str(j.id) for j in jobs}
    assert all(r.tool_slugs == ["bandit"] for r in runs)
    # One queue for the whole batch, in the order given.
    assert no_dispatch == [body["run_ids"]]
    listed = api_client.get(f"/api/analysis/runs/?batch_id={body['batch_id']}").json()
    assert listed["total"] == 2


def test_batch_refuses_other_users_apps(api_client, no_dispatch):
    AnalyzerToolFactory(slug="bandit")
    foreign = _job(UserFactory())
    resp = _post(api_client, "/runs/batch/", {"tool_slugs": ["bandit"], "generation_job_ids": [str(foreign.id)]})
    assert resp.status_code == 404
    assert not AnalysisRun.objects.exists()
    assert not no_dispatch


def test_preview_says_where_each_tool_runs(api_client, user):
    AnalyzerToolFactory(slug="py", file_patterns=["*.py"])
    AnalyzerToolFactory(slug="css", file_patterns=["*.css"])
    AnalyzerToolFactory(slug="dast", analysis_mode="dynamic")
    up = _job(user)
    down = _job(user)
    ContainerInstanceFactory(created_by=user, generation_job=up, status="running")

    resp = _post(
        api_client,
        "/runs/preview/",
        {"tool_slugs": ["py", "css", "dast"], "generation_job_ids": [str(up.id), str(down.id)]},
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    targets = {t["job_id"]: t for t in body["targets"]}
    assert targets[str(up.id)]["app_running"] is True
    assert targets[str(down.id)]["app_running"] is False
    assert targets[str(up.id)]["files"] == 2
    tools = {t["slug"]: t for t in body["tools"]}
    assert (tools["py"]["runs"], tools["py"]["not_applicable"]) == (2, 0)
    assert (tools["css"]["runs"], tools["css"]["not_applicable"]) == (0, 2)
    assert (tools["dast"]["runs"], tools["dast"]["app_not_running"]) == (1, 1)
    assert body["tool_runs"] == 3
    # Nothing is installed in this user's workspace.
    assert any("Not installed" in w for w in body["warnings"])
    assert not AnalysisRun.objects.exists()


# --- re-run / delete / bulk --------------------------------------------------------


def test_rerun_only_the_tools_that_measured_nothing(api_client, user, no_dispatch):
    for slug in ("ok", "broken", "na", "down"):
        AnalyzerToolFactory(slug=slug)
    source = AnalysisRunFactory(created_by=user, status="partial", tool_slugs=["ok", "broken", "na", "down"])
    ToolResultFactory(run=source, tool_slug="ok", status="completed")
    ToolResultFactory(run=source, tool_slug="broken", status="failed")
    ToolResultFactory(run=source, tool_slug="na", status="skipped", summary={"skip_reason": "not_applicable"})
    ToolResultFactory(run=source, tool_slug="down", status="skipped", summary={"skip_reason": "app_not_running"})

    resp = _post(api_client, f"/runs/{source.id}/rerun/", {"only_unmeasured": True})
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["tool_slugs"] == ["broken", "down"]
    assert body["id"] != str(source.id)
    assert no_dispatch == [[body["id"]]]

    full = _post(api_client, f"/runs/{source.id}/rerun/", {}).json()
    assert full["tool_slugs"] == ["ok", "broken", "na", "down"]


def test_rerun_with_everything_measured_is_refused(api_client, user, no_dispatch):
    AnalyzerToolFactory(slug="ok")
    source = AnalysisRunFactory(created_by=user, status="completed", tool_slugs=["ok"])
    ToolResultFactory(run=source, tool_slug="ok", status="completed")
    assert _post(api_client, f"/runs/{source.id}/rerun/", {"only_unmeasured": True}).status_code == 400


def test_active_runs_are_not_deleted(api_client, user):
    running = AnalysisRunFactory(created_by=user, status="running")
    done = AnalysisRunFactory(created_by=user, status="completed")
    response = api_client.delete(f"/api/analysis/runs/{running.id}/")
    assert response.status_code == 409

    body = _post(api_client, "/runs/bulk/", {"action": "delete", "ids": [str(running.id), str(done.id)]}).json()
    assert body == {"affected": 1, "skipped": 1}
    assert list(AnalysisRun.objects.values_list("id", flat=True)) == [running.id]


def test_bulk_cancel(api_client, user, monkeypatch):
    monkeypatch.setattr(runner, "_kill_in_flight", lambda run: None)
    pending = AnalysisRunFactory(created_by=user, status="pending")
    done = AnalysisRunFactory(created_by=user, status="completed")
    foreign = AnalysisRunFactory(status="pending")
    ids = [str(pending.id), str(done.id), str(foreign.id)]
    body = _post(api_client, "/runs/bulk/", {"action": "cancel", "ids": ids}).json()
    assert body == {"affected": 1, "skipped": 1}
    pending.refresh_from_db()
    foreign.refresh_from_db()
    assert pending.status == "cancelled"
    assert foreign.status == "pending"


# --- results / findings -------------------------------------------------------------


def test_result_detail_exposes_the_raw_output(api_client, user):
    run = AnalysisRunFactory(created_by=user)
    result = ToolResultFactory(run=run, raw_output={"command": "bandit -r /work", "exit_code": 1, "stdout": "{}"})
    body = api_client.get(f"/api/analysis/runs/{run.id}/results/{result.id}/").json()
    assert body["raw_output"]["exit_code"] == 1
    assert body["findings_count"] == 0
    other = ToolResultFactory()
    assert api_client.get(f"/api/analysis/runs/{other.run_id}/results/{other.id}/").status_code == 404


def _findings_run(user):
    run = AnalysisRunFactory(created_by=user, status="completed")
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    ruff = ToolResultFactory(run=run, tool_slug="ruff", category="lint")
    FindingFactory(result=bandit, severity="high", tool_severity="HIGH", file_path="app.py", rule_id="B602")
    FindingFactory(result=bandit, severity="low", tool_severity="LOW", file_path="app.py", rule_id="B101")
    # Ruff rates nothing: its normalized "high" is a heuristic and must not lead.
    FindingFactory(result=ruff, severity="high", tool_severity="", file_path="util.py", rule_id="F821", title="x")
    return run


def test_findings_filter_by_the_reported_level(api_client, user):
    run = _findings_run(user)
    url = f"/api/analysis/runs/{run.id}/findings/"
    assert [f["rule_id"] for f in api_client.get(url).json()["items"]] == ["B602", "B101", "F821"]
    assert [f["rule_id"] for f in api_client.get(f"{url}?tool_severity=high").json()["items"]] == ["B602"]
    unrated = api_client.get(f"{url}?tool_severity=unrated").json()["items"]
    assert [(f["rule_id"], f["tool_severity"]) for f in unrated] == [("F821", "")]
    assert [f["rule_id"] for f in api_client.get(f"{url}?search=util").json()["items"]] == ["F821"]
    assert [f["rule_id"] for f in api_client.get(f"{url}?sort=file").json()["items"]][-1] == "F821"


def test_grouped_findings_collapse_one_issue_reported_by_several_tools(api_client, user):
    run = AnalysisRunFactory(created_by=user, status="completed")
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    ruff = ToolResultFactory(run=run, tool_slug="ruff", category="lint")
    # Ruff's S602 is Bandit's B602 on the same line: one issue.
    FindingFactory(
        result=bandit,
        severity="high",
        tool_severity="HIGH",
        file_path="app.py",
        line_number=4,
        rule_id="B602",
    )
    FindingFactory(result=ruff, severity="high", tool_severity="", file_path="app.py", line_number=4, rule_id="S602")
    FindingFactory(result=ruff, severity="medium", tool_severity="", file_path="app.py", line_number=9, rule_id="F401")
    url = f"/api/analysis/runs/{run.id}/findings/"

    body = api_client.get(f"{url}?group=true").json()
    assert (body["total"], body["findings_total"]) == (2, 3)
    first = body["items"][0]
    assert (first["rule_id"], first["issue_class"]) == ("B602", "shell-injection")
    assert [(d["tool_slug"], d["rule_id"]) for d in first["duplicates"]] == [("ruff", "S602")]
    assert body["items"][1]["duplicates"] == []

    # Grouping applies to the filtered findings.
    only_ruff = api_client.get(f"{url}?group=true&tool_slug=ruff").json()
    assert (only_ruff["total"], only_ruff["items"][0]["duplicates"]) == (2, [])

    flat = api_client.get(url).json()
    assert (flat["total"], flat["findings_total"]) == (3, None)


def test_facets_count_reported_levels_and_unrated(api_client, user):
    run = _findings_run(user)
    body = api_client.get(f"/api/analysis/runs/{run.id}/findings/facets/").json()
    assert body["total"] == 3
    assert [(level["value"], level["count"]) for level in body["levels"]] == [("high", 1), ("low", 1)]
    assert body["unrated"] == 1
    assert {t["value"]: t["count"] for t in body["tools"]} == {"bandit": 2, "ruff": 1}
    assert body["files"][0] == {"value": "app.py", "count": 2, "label": ""}

    scoped = api_client.get(f"/api/analysis/runs/{run.id}/findings/facets/?tool_slug=ruff").json()
    assert scoped["total"] == 1
    assert scoped["levels"] == []
    assert len(scoped["tools"]) == 2  # the tool facet itself stays unscoped


def test_list_and_detail_fill_counters_older_runs_lack(api_client, user):
    old = AnalysisRunFactory(created_by=user, status="completed", summary={"total_findings": 3, "tools_completed": 2})
    osv = ToolResultFactory(run=old, tool_slug="osv-scanner", category="dependencies")
    FindingFactory(result=osv, category="dependency", rule_id="GHSA-x")
    FindingFactory(result=osv, category="dependency", rule_id="GHSA-y")
    FindingFactory(result=ToolResultFactory(run=old, tool_slug="bandit"), category="security")
    ToolResultFactory(run=old, tool_slug="hadolint", status="skipped", summary={"skip_reason": "not_applicable"})
    ToolResultFactory(run=old, tool_slug="zap", status="skipped", summary={"skip_reason": "app_not_running"})
    never = AnalysisRunFactory(created_by=user, status="failed", summary={})
    # Stored by an earlier version with a 0 for scopes no tool measured.
    zeros = {"code_findings": 0, "dependency_findings": 0, "runtime_findings": 0}
    stored = AnalysisRunFactory(
        created_by=user,
        status="completed",
        summary={"total_findings": 0, **zeros, "tools_not_applicable": 0, "tools_unmeasured": 0},
    )
    ToolResultFactory(run=stored, tool_slug="bandit")

    items = {i["id"]: i for i in api_client.get("/api/analysis/runs/").json()["items"]}
    summary = items[str(old.id)]["summary"]
    # The DAST tool found the app down: no runtime measurement, so None, not 0.
    assert (summary["code_findings"], summary["dependency_findings"], summary["runtime_findings"]) == (1, 2, None)
    assert (summary["tools_not_applicable"], summary["tools_unmeasured"]) == (1, 1)
    restated = items[str(stored.id)]["summary"]
    assert [restated[k] for k in zeros] == [0, None, None]
    # A run that measured nothing gets no invented zeros.
    assert "code_findings" not in items[str(never.id)]["summary"]
    detail = api_client.get(f"/api/analysis/runs/{old.id}/").json()["summary"]
    assert detail["code_findings"] == 1
    old.refresh_from_db()
    assert "code_findings" not in old.summary  # computed at read time, never written back


def _scoped_run(user):
    run = AnalysisRunFactory(created_by=user, status="completed")
    osv = ToolResultFactory(run=run, tool_slug="osv-scanner", category="dependencies")
    FindingFactory(result=osv, severity="high", tool_severity="MODERATE", category="dependency", rule_id="GHSA-1")
    FindingFactory(result=osv, severity="high", tool_severity="7.0", category="dependency", rule_id="GHSA-2")
    FindingFactory(result=osv, severity="medium", tool_severity="HIGH", category="dependency", rule_id="GHSA-3")
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    FindingFactory(result=bandit, severity="low", tool_severity="LOW", category="security", rule_id="B101")
    FindingFactory(result=bandit, severity="medium", tool_severity="warning", category="security", rule_id="B105")
    AnalyzerToolFactory(slug="dast-scope", analysis_mode="dynamic")
    dast = ToolResultFactory(run=run, tool_slug="dast-scope")
    FindingFactory(result=dast, severity="medium", tool_severity="Medium")
    return run


def test_findings_scope_separates_code_from_advisories_and_runtime(api_client, user):
    run = _scoped_run(user)
    url = f"/api/analysis/runs/{run.id}/findings/"
    assert {f["rule_id"] for f in api_client.get(f"{url}?scope=code").json()["items"]} == {"B101", "B105"}
    assert api_client.get(f"{url}?scope=dependencies").json()["total"] == 3
    assert [f["tool_slug"] for f in api_client.get(f"{url}?scope=runtime").json()["items"]] == ["dast-scope"]
    assert api_client.get(f"{url}?scope=nonsense").status_code == 400

    facets = api_client.get(f"{url}facets/").json()
    assert facets["scopes"] == {"code": 2, "dependencies": 3, "runtime": 1}
    code_only = api_client.get(f"{url}facets/?scope=code").json()
    assert code_only["total"] == 2
    assert {t["value"] for t in code_only["tools"]} == {"bandit"}
    assert code_only["scopes"] == facets["scopes"]


def test_facet_scopes_no_tool_measured_are_none(api_client, user):
    run = AnalysisRunFactory(created_by=user, status="completed")
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="bandit"), category="security")
    ToolResultFactory(run=run, tool_slug="pip-audit", category="dependencies", status="failed")
    facets = api_client.get(f"/api/analysis/runs/{run.id}/findings/facets/").json()
    assert facets["scopes"] == {"code": 1, "dependencies": None, "runtime": None}


def test_levels_and_the_severity_sort_follow_the_tools_own_words(api_client, user):
    run = _scoped_run(user)
    url = f"/api/analysis/runs/{run.id}/findings/"
    levels = [(lv["value"], lv["rank"]) for lv in api_client.get(f"{url}facets/").json()["levels"]]
    # By the word itself (CVSS scores by band), not by the normalized heuristic
    # that rates osv's MODERATE above its HIGH.
    assert levels == [("7.0", 1), ("high", 1), ("medium", 2), ("moderate", 2), ("warning", 2), ("low", 3)]
    ordered = [f["tool_severity"] for f in api_client.get(f"{url}?sort=severity").json()["items"]]
    assert ordered.index("HIGH") < ordered.index("MODERATE")
    assert ordered.index("7.0") < ordered.index("MODERATE")
    assert ordered[-1] == "LOW"


def test_level_rank_mirrors_the_ui_table():
    from backend.analysis.api.views.runs import level_rank

    assert [level_rank(w) for w in ("Critical", "error", "Moderate", "note", "info", "", "weird")] == [
        0,
        1,
        2,
        4,
        5,
        9,
        7,
    ]
    assert [level_rank(s) for s in ("9.8", "7.0", "5.3", "0.5", "0.0")] == [0, 1, 2, 3, 5]


def test_preview_does_not_count_empty_files_as_applicable(api_client, user):
    AnalyzerToolFactory(slug="reqs", file_patterns=["requirements*.txt"])
    job = _job(user, files={"app.py": "x = 1\n", "requirements.txt": "", "pkg/__init__.py": ""})
    body = _post(api_client, "/runs/preview/", {"tool_slugs": ["reqs"], "generation_job_ids": [str(job.id)]}).json()
    assert body["targets"][0]["files"] == 3
    assert body["targets"][0]["lines"] == 1
    assert (body["tools"][0]["runs"], body["tools"][0]["not_applicable"]) == (0, 1)
