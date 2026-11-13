"""Dynamic (DAST) tools: target resolution, command rendering, skip behaviour."""

from __future__ import annotations

import json

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import AnalyzerWorkspaceFactory
from backend.analysis.tests.factories import InstalledToolFactory
from backend.analysis.tests.factories import generation_run
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _dynamic_tool(**kwargs) -> AnalyzerTool:
    defaults = {
        "slug": "dast-fixture",
        "kind": "container",
        "analysis_mode": "dynamic",
        "parser_key": "nuclei",
        "run_cmd": "nuclei -u {url} -tags {config:tags}",
        "default_config": {"tags": "misconfig"},
    }
    return AnalyzerToolFactory(**{**defaults, **kwargs})


def _running_app(job, user, name="llm-app1") -> ContainerInstance:
    return ContainerInstanceFactory(
        name=name,
        generation_job=job,
        created_by=user,
        status=ContainerInstance.Status.RUNNING,
    )


def _inspect_with_ip(ip="172.17.0.5", running=True):
    return lambda name: {
        "State": {"Running": running},
        "NetworkSettings": {"Networks": {"bridge": {"IPAddress": ip}}},
    }


# --- render_command ----------------------------------------------------------


def test_render_command_fills_target_url_and_quoted_options():
    tool = AnalyzerToolFactory.build(
        run_cmd="scan {target} --url {url}/ --tags {config:tags} --n {config:n} --flag {config:missing}",
        default_config={"tags": "a,b", "n": 3},
    )
    cmd = runner.render_command(tool, target="/work", url="http://10.0.0.2:8000", config={"tags": "x; rm -rf /"})
    assert cmd == "scan /work --url http://10.0.0.2:8000/ --tags 'x; rm -rf /' --n 3 --flag ''"


# --- resolve_app_url ---------------------------------------------------------


def test_resolve_requires_generation_job(user):
    run = AnalysisRunFactory(created_by=user, source_code={"a.py": "x"})
    url, reason = runner.resolve_app_url(run)
    assert url == ""
    assert "generated app" in reason


def test_resolve_reports_app_not_running(user):
    job = generation_run(user)
    ContainerInstanceFactory(generation_job=job, created_by=user, status=ContainerInstance.Status.STOPPED)
    run = AnalysisRunFactory(created_by=user, generation_job=job)
    url, reason = runner.resolve_app_url(run)
    assert url == ""
    assert "not running" in reason


def test_resolve_uses_container_ip_on_default_bridge(user, monkeypatch, settings):
    settings.DOCKER_APPS_NETWORK = ""
    job = generation_run(user)
    _running_app(job, user)
    monkeypatch.setattr(runner.docker_manager, "inspect", _inspect_with_ip("172.17.0.9"))
    run = AnalysisRunFactory(created_by=user, generation_job=job)
    assert runner.resolve_app_url(run) == ("http://172.17.0.9:8000", "")


def test_resolve_uses_container_name_on_apps_network(user, settings):
    settings.DOCKER_APPS_NETWORK = "apps"
    job = generation_run(user)
    _running_app(job, user, name="llm-app2")
    run = AnalysisRunFactory(created_by=user, generation_job=job)
    assert runner.resolve_app_url(run) == ("http://llm-app2:8000", "")


def test_resolve_never_targets_another_users_app(user, settings):
    settings.DOCKER_APPS_NETWORK = "apps"
    other = UserFactory()
    job = generation_run(other)
    _running_app(job, other)
    run = AnalysisRunFactory(created_by=user, generation_job=job)
    url, reason = runner.resolve_app_url(run)
    assert url == ""
    assert reason


# --- execute -----------------------------------------------------------------


def _stub_workspace(monkeypatch, exec_calls, output=""):
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})

    def _exec(name, cmd, **kwargs):
        exec_calls.append(cmd)
        return {"exit_code": 0, "output": output}

    monkeypatch.setattr(runner.docker_manager, "exec_in", _exec)


def test_execute_skips_dynamic_tool_when_app_not_running(user, monkeypatch):
    _dynamic_tool()
    job = generation_run(user, files={"app.py": "print(1)\n"})
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        generation_job=job,
        tool_slugs=["dast-fixture"],
    )
    calls: list[str] = []
    _stub_workspace(monkeypatch, calls)

    runner.execute(run)
    run.refresh_from_db()

    result = ToolResult.objects.get(run=run, tool_slug="dast-fixture")
    assert result.status == ToolResult.Status.SKIPPED
    assert "not running" in result.error_message
    assert not any("nuclei" in c for c in calls)
    assert run.status == AnalysisRun.Status.COMPLETED


def test_execute_runs_dynamic_tool_against_app_with_user_config(user, monkeypatch, settings):
    settings.DOCKER_APPS_NETWORK = "apps"
    tool = _dynamic_tool()
    workspace = AnalyzerWorkspaceFactory(user=user)
    InstalledToolFactory(workspace=workspace, tool=tool, config={"tags": "exposure"})
    job = generation_run(user, files={"app.py": "print(1)\n"})
    _running_app(job, user, name="llm-app3")
    run = AnalysisRunFactory(created_by=user, workspace=workspace, generation_job=job, tool_slugs=["dast-fixture"])
    hit = {
        "template-id": "git-config",
        "info": {"name": "Git Config Disclosure", "severity": "medium"},
        "matched-at": "http://llm-app3:8000/.git/config",
    }
    calls: list[str] = []
    _stub_workspace(monkeypatch, calls, output=json.dumps(hit))

    runner.execute(run)

    # Prefixed with the run's marker so cancel can kill it (runner._kill_script).
    assert f": llmlab-analysis-run:{run.pk}; nuclei -u http://llm-app3:8000 -tags exposure" in calls
    result = ToolResult.objects.get(run=run, tool_slug="dast-fixture")
    assert result.status == ToolResult.Status.COMPLETED
    assert result.raw_output["target_url"] == "http://llm-app3:8000"
    assert result.raw_output["command"] == "nuclei -u http://llm-app3:8000 -tags exposure"
    finding = Finding.objects.get(result=result)
    assert (finding.file_path, finding.severity) == ("/.git/config", "medium")


# --- liveness around each dynamic tool --------------------------------------------


def _dast_run(user, settings, name="llm-app4"):
    settings.DOCKER_APPS_NETWORK = "apps"
    _dynamic_tool()
    workspace = AnalyzerWorkspaceFactory(user=user)
    job = generation_run(user, files={"app.py": "print(1)\n"})
    _running_app(job, user, name=name)
    return AnalysisRunFactory(created_by=user, workspace=workspace, generation_job=job, tool_slugs=["dast-fixture"])


def _scripted_workspace(monkeypatch, calls, *, probes, output=""):
    """Each curl probe answers with the next exit code of *probes*; the tool prints *output*."""
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    answers = iter(probes)

    def _exec(name, cmd, **kwargs):
        calls.append(cmd)
        if cmd.startswith("curl "):
            code = next(answers)
            return {"exit_code": code, "output": "" if code == 0 else "curl: (7) Failed to connect"}
        return {"exit_code": 0, "output": output}

    monkeypatch.setattr(runner.docker_manager, "exec_in", _exec)


def test_dynamic_tool_is_skipped_when_the_app_does_not_answer(user, monkeypatch, settings):
    # The database still says "running", but the app is down.
    run = _dast_run(user, settings)
    calls: list[str] = []
    _scripted_workspace(monkeypatch, calls, probes=[7])

    runner.execute(run)
    run.refresh_from_db()

    result = ToolResult.objects.get(run=run, tool_slug="dast-fixture")
    assert result.status == ToolResult.Status.SKIPPED
    assert result.summary["skip_reason"] == runner.SKIP_APP_NOT_RUNNING
    assert "did not answer at http://llm-app4:8000" in result.error_message
    assert not any("nuclei" in c for c in calls)
    assert "curl -sS -o /dev/null --max-time 10 http://llm-app4:8000/" in calls
    assert run.summary["tools_unmeasured"] == 1


def test_app_dying_during_the_scan_is_never_a_clean_zero(user, monkeypatch, settings):
    run = _dast_run(user, settings)
    calls: list[str] = []
    # Alive before the scan, dead after it (asked twice); nuclei printed nothing.
    _scripted_workspace(monkeypatch, calls, probes=[0, 7, 7], output="")

    runner.execute(run)
    result = ToolResult.objects.get(run=run, tool_slug="dast-fixture")
    assert result.status == ToolResult.Status.SKIPPED
    assert result.summary["skip_reason"] == runner.SKIP_APP_NOT_RUNNING
    assert "stopped answering" in result.error_message
    assert result.raw_output["command"].startswith("nuclei -u http://llm-app4:8000")


def test_app_dying_during_the_scan_keeps_findings_on_a_failed_result(user, monkeypatch, settings):
    run = _dast_run(user, settings)
    hit = {
        "template-id": "git-config",
        "info": {"name": "Git Config Disclosure", "severity": "medium"},
        "matched-at": "http://llm-app4:8000/.git/config",
    }
    _scripted_workspace(monkeypatch, [], probes=[0, 7, 7], output=json.dumps(hit))

    runner.execute(run)
    run.refresh_from_db()
    result = ToolResult.objects.get(run=run, tool_slug="dast-fixture")
    # Incomplete scan: not a measurement, but what it saw stays inspectable.
    assert result.status == ToolResult.Status.FAILED
    assert "stopped answering" in result.error_message
    assert result.findings.count() == 1
    assert result.summary["command_sha"] == runner.command_fingerprint(
        "nuclei -u {url} -tags misconfig",
    )
    assert run.status == AnalysisRun.Status.FAILED


def test_an_app_that_answers_the_second_probe_after_the_scan_is_measured(user, monkeypatch, settings):
    run = _dast_run(user, settings)
    hit = {"template-id": "x-frame", "info": {"name": "Missing XFO", "severity": "info"}, "matched-at": "http://a/"}
    _scripted_workspace(monkeypatch, [], probes=[0, 28, 0], output=json.dumps(hit))
    runner.execute(run)
    result = ToolResult.objects.get(run=run, tool_slug="dast-fixture")
    assert result.status == ToolResult.Status.COMPLETED
