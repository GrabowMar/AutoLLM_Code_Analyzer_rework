from __future__ import annotations

import json

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import AnalyzerWorkspaceFactory

pytestmark = pytest.mark.django_db


def test_execute_container_tool(monkeypatch, user):
    # Non-seeded slug: the factory's django_get_or_create=("slug",) would
    # silently return the auto-seeded "bandit" row and discard the overrides.
    AnalyzerToolFactory(
        slug="bandit-fixture",
        kind="container",
        parser_key="bandit",
        run_cmd="bandit -r {target} -f json",
    )
    workspace = AnalyzerWorkspaceFactory(user=user)
    run = AnalysisRunFactory(
        created_by=user,
        workspace=workspace,
        tool_slugs=["bandit-fixture"],
        source_code={"backend": "import hashlib"},
    )

    monkeypatch.setattr(
        runner.workspace_service,
        "require_ready_container",
        lambda ws: "cname",
    )
    monkeypatch.setattr(
        runner.docker_manager,
        "copy_files_in",
        lambda *a, **k: {"status": "ok"},
    )
    bandit_out = json.dumps(
        {
            "results": [
                {
                    "filename": "/work/backend.py",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "issue_text": "md5",
                    "test_id": "B303",
                    "line_number": 1,
                },
            ],
        },
    )
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        # The /work cleanup succeeds; bandit exits 1 because it found something.
        lambda name, cmd, **k: (
            {"exit_code": 0, "output": ""} if cmd.startswith("find ") else {"exit_code": 1, "output": bandit_out}
        ),
    )

    runner.execute(run)
    run.refresh_from_db()

    assert run.status == AnalysisRun.Status.COMPLETED
    assert run.summary["total_findings"] == 1
    finding = Finding.objects.get(result__run=run, severity="high")
    # Container tools report /work-prefixed paths; persisted findings must use
    # the same identity as every other tool.
    assert finding.file_path == "backend.py"
    assert run.duration_seconds is not None


def test_execute_metrics_tool(monkeypatch, user):
    AnalyzerToolFactory(
        slug="radon-fixture",
        kind="container",
        parser_key="radon",
        run_cmd="radon cc {target} -j",
    )
    workspace = AnalyzerWorkspaceFactory(user=user)
    run = AnalysisRunFactory(
        created_by=user,
        workspace=workspace,
        tool_slugs=["radon-fixture"],
        source_code={"backend": "def f(): pass"},
    )

    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    radon_out = json.dumps(
        {
            "backend.py": [
                {"type": "function", "name": "simple", "rank": "B", "complexity": 6, "lineno": 1},
                {"type": "function", "name": "monster", "rank": "F", "complexity": 41, "lineno": 10},
            ],
        },
    )
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda *a, **k: {"exit_code": 0, "output": radon_out},
    )

    runner.execute(run)
    run.refresh_from_db()

    assert run.status == AnalysisRun.Status.COMPLETED
    result = run.results.get()
    assert result.metrics["max_complexity"] == 41
    assert result.metrics["total_blocks"] == 2
    assert result.metrics["rank_distribution"]["F"] == 1
    # Only the F-rank block surfaces as a finding; the B block is metric data.
    assert Finding.objects.filter(result__run=run).count() == 1
    assert run.summary["metrics_by_tool"]["radon-fixture"]["average_complexity"] == 23.5


def test_execute_skips_unknown_tool(monkeypatch, user):
    run = AnalysisRunFactory(created_by=user, tool_slugs=["ghost"], source_code={"backend": "x"})
    runner.execute(run)
    run.refresh_from_db()
    result = run.results.get()
    assert result.status == "skipped"


def test_materialize_scaffolding_keys():
    """Scaffolding blobs get sensible filenames/extensions (JS frontend != .py)."""
    files = runner._materialize(
        {"backend_code": "import os\n", "frontend_code": "const x = 1;\n", "content": "x = 1\n"},
    )
    assert set(files) == {"backend_code.py", "frontend_code.jsx", "content.py"}


def test_materialize_preserves_real_paths():
    """Copilot file maps keep their true relative paths/extensions."""
    files = runner._materialize(
        {"frontend/src/App.jsx": "x", "backend/app.py": "y", "/leading/slash.py": "z"},
    )
    assert "frontend/src/App.jsx" in files
    assert "backend/app.py" in files
    assert "leading/slash.py" in files  # leading slash stripped


def test_get_code_for_analysis_from_scaffolding_job(user):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "backend_code": "import os\n",
            "frontend_code": "const x = 1;\n",
            "backend_truncated": False,
            "backend_files": 3,
        },
    )
    run = AnalysisRunFactory(created_by=user, source_code=None, generation_job=job)
    code = run.get_code_for_analysis()
    assert code == {"backend_code": "import os\n", "frontend_code": "const x = 1;\n"}
    # And materialization yields proper extensions end to end.
    files = runner._materialize(code)
    assert set(files) == {"backend_code.py", "frontend_code.jsx"}


def test_get_code_for_analysis_prefers_copilot_files(user):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "content": "ignored blob",
            "backend_code": "ignored blob",
            "files": {"backend/app.py": "print('hi')", "frontend/src/App.jsx": "x"},
        },
    )
    run = AnalysisRunFactory(created_by=user, source_code=None, generation_job=job)
    code = run.get_code_for_analysis()
    assert code == {"backend/app.py": "print('hi')", "frontend/src/App.jsx": "x"}


def test_execute_ai_tool(monkeypatch, user):
    AnalyzerToolFactory(slug="llm_review", kind="ai", parser_key="")
    run = AnalysisRunFactory(created_by=user, tool_slugs=["llm_review"], source_code={"backend": "x"})

    from backend.analysis.services.base import AnalyzerOutput
    from backend.analysis.services.base import FindingData

    monkeypatch.setattr(
        runner.ai_runner,
        "run",
        lambda *a, **k: AnalyzerOutput(
            findings=[FindingData(severity="medium", category="quality", title="ai")],
        ),
    )
    runner.execute(run)
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.COMPLETED
    assert Finding.objects.filter(result__run=run).count() == 1


def test_ai_tool_gets_materialized_files_and_workspace_config(monkeypatch, user):
    from backend.analysis.services.base import AnalyzerOutput
    from backend.analysis.tests.factories import InstalledToolFactory

    tool = AnalyzerToolFactory(slug="llm_review", kind="ai", parser_key="")
    workspace = AnalyzerWorkspaceFactory(user=user)
    InstalledToolFactory(workspace=workspace, tool=tool, config={"max_tokens": 1234})
    run = AnalysisRunFactory(
        created_by=user,
        workspace=workspace,
        tool_slugs=["llm_review"],
        source_code={"backend_code": "import os\n"},
    )

    seen = {}

    def fake_run(tool, code, config, usr):
        seen["code"] = code
        seen["config"] = config
        return AnalyzerOutput(findings=[])

    monkeypatch.setattr(runner.ai_runner, "run", fake_run)
    runner.execute(run)

    # The AI reviews the same filenames the container tools analyze, and the
    # per-user tool settings actually reach the execution.
    assert set(seen["code"]) == {"backend_code.py"}
    assert seen["config"] == {"max_tokens": 1234}


def test_truncated_generation_input_flagged_in_summary(monkeypatch, user):
    from backend.analysis.services.base import AnalyzerOutput
    from backend.generation.tests.factories import GenerationJobFactory

    AnalyzerToolFactory(slug="llm_review", kind="ai", parser_key="")
    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "backend_code": "import os\n",
            "frontend_code": "const x = 1;\n",
            "backend_truncated": False,
            "frontend_truncated": True,
        },
    )
    run = AnalysisRunFactory(
        created_by=user,
        tool_slugs=["llm_review"],
        source_code=None,
        generation_job=job,
    )
    monkeypatch.setattr(runner.ai_runner, "run", lambda *a, **k: AnalyzerOutput(findings=[]))

    runner.execute(run)
    run.refresh_from_db()
    assert run.summary["truncated_inputs"] == ["frontend"]


def _container_run(monkeypatch, user, tool_kw, exec_result, source=None):
    """Run one container tool whose command returns *exec_result* (cleanup succeeds)."""
    AnalyzerToolFactory(slug="fixture-tool", kind="container", **tool_kw)
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        tool_slugs=["fixture-tool"],
        source_code=source or {"app.py": "import os\n"},
    )
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda name, cmd, **k: {"exit_code": 0, "output": ""} if cmd.startswith("find ") else exec_result,
    )
    runner.execute(run)
    run.refresh_from_db()
    return run, run.results.get()


@pytest.mark.parametrize(
    ("exec_result", "message"),
    [
        ({"exit_code": 137, "output": "partial"}, "timeout"),
        ({"exit_code": 127, "output": "sh: ruff: not found"}, "not found"),
        ({"exit_code": 2, "output": "ruff: error: bad flag"}, "Exited with code 2"),
    ],
)
def test_tool_failures_are_not_clean_results(monkeypatch, user, exec_result, message):
    run, result = _container_run(monkeypatch, user, {"parser_key": "ruff", "run_cmd": "ruff {target}"}, exec_result)
    assert result.status == "failed"
    assert message in result.error_message
    assert run.status == AnalysisRun.Status.FAILED
    assert result.raw_output["command"] == "ruff /work"


def test_unparsable_output_with_findings_exit_still_completes(monkeypatch, user):
    # Linters exit 1 because they found something: that is a result, not a failure.
    out = json.dumps([{"code": "F401", "message": "unused", "filename": "/work/app.py", "location": {"row": 1}}])
    _, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff {target}"},
        {"exit_code": 1, "output": out},
    )
    assert result.status == "completed"
    assert result.findings.count() == 1
    assert result.summary["config"] == {}


def test_tool_without_applicable_files_is_skipped(monkeypatch, user):
    run, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "eslint", "run_cmd": "eslint {target}", "file_patterns": ["*.js", "*.jsx"]},
        {"exit_code": 0, "output": "[]"},
    )
    assert result.status == "skipped"
    assert result.summary["skip_reason"] == runner.SKIP_NOT_APPLICABLE
    assert run.status == AnalysisRun.Status.COMPLETED


def test_applicable_files_match_names_and_paths():
    tool = AnalyzerToolFactory.build(file_patterns=["requirements*.txt", "frontend/*.json"])
    files = {"backend/requirements-dev.txt": "", "frontend/package.json": "", "app.py": ""}
    assert sorted(runner.applicable_files(tool, files)) == ["backend/requirements-dev.txt", "frontend/package.json"]
    assert runner.applicable_files(AnalyzerToolFactory.build(file_patterns=[]), {"a.py": ""}) == ["a.py"]


def test_workspace_cleanup_failure_fails_the_run(monkeypatch, user):
    AnalyzerToolFactory(slug="fixture-tool", kind="container", parser_key="ruff", run_cmd="ruff {target}")
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        tool_slugs=["fixture-tool"],
        source_code={"app.py": "x = 1\n"},
    )
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda *a, **k: {"exit_code": 1, "output": "find: cannot delete '/work/frontend': Permission denied"},
    )
    runner.execute(run)
    run.refresh_from_db()
    # Stale files from the previous run would otherwise be scanned as this app's.
    assert run.status == AnalysisRun.Status.FAILED
    assert "Could not clear the workspace" in run.error_message


def test_job_metadata_fields_are_not_analyzed(user):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(created_by=user, result_data={"content": "print(1)", "finish_reason": "stop"})
    run = AnalysisRunFactory(created_by=user, source_code=None, generation_job=job)
    assert run.get_code_for_analysis() == {"content": "print(1)"}


def test_single_manifest_hallucinated_dependency_is_stored_on_failed_result(monkeypatch, user):
    # One requirements.txt whose only problem is a package that does not exist:
    # the audit could not run (failed, not measured), but the finding is real.
    raw = "### requirements.txt\n!! ERROR: No matching distribution found for flask-magic-auth==1.0\n"
    run, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "pip_audit", "run_cmd": "audit {target}", "file_patterns": ["requirements*.txt"]},
        {"exit_code": 0, "output": raw},
        source={"requirements.txt": "flask-magic-auth==1.0\n"},
    )
    assert result.status == "failed"
    assert result.error_message.startswith("No manifest could be analyzed")
    assert [f.rule_id for f in result.findings.all()] == ["nonexistent-version"]
    assert result.summary["finding_count"] == 1
    assert result.summary["warnings"][0].startswith("Not analyzed: requirements.txt")
    assert run.summary["total_findings"] == 1


def _tool_run(user, slugs):
    for slug in slugs:
        AnalyzerToolFactory(slug=slug, kind="container", parser_key="ruff", run_cmd="ruff {target}")
    return AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        tool_slugs=slugs,
        source_code={"app.py": "import os\n"},
    )


def test_cancel_during_a_tool_stops_the_run_and_sticks(monkeypatch, user):
    run = _tool_run(user, ["fixture-a", "fixture-b"])
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    commands = []

    def fake_exec(name, cmd, **k):
        commands.append(cmd)
        if cmd.startswith("find "):
            return {"exit_code": 0, "output": ""}
        # The user cancels while the first tool runs; cancel kills it (exit 137).
        AnalysisRun.objects.filter(pk=run.pk).update(status=AnalysisRun.Status.CANCELLED)
        return {"exit_code": 137, "output": ""}

    monkeypatch.setattr(runner.docker_manager, "exec_in", fake_exec)
    runner.execute(run)
    run.refresh_from_db()

    assert run.status == AnalysisRun.Status.CANCELLED
    results = {r.tool_slug: r for r in run.results.all()}
    assert {r.status for r in results.values()} == {"skipped"}
    assert {r.summary["skip_reason"] for r in results.values()} == {runner.SKIP_CANCELLED}
    # The second tool never ran; the first carried the cancel marker.
    assert len(commands) == 2
    assert commands[1].startswith(f": llmlab-analysis-run:{run.pk}; ruff /work")
    assert results["fixture-a"].raw_output == {}
    assert run.summary["tools_run"] == 2


def test_cancelled_before_start_never_runs(monkeypatch, user):
    run = _tool_run(user, ["fixture-a"])
    AnalysisRun.objects.filter(pk=run.pk).update(status=AnalysisRun.Status.CANCELLED)
    monkeypatch.setattr(runner.docker_manager, "exec_in", lambda *a, **k: pytest.fail("must not run"))
    runner.execute(run)
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.CANCELLED
    assert not run.results.exists()


def test_finalize_and_failure_do_not_overwrite_cancelled(user):
    run = _tool_run(user, ["fixture-a"])
    AnalysisRun.objects.filter(pk=run.pk).update(status=AnalysisRun.Status.CANCELLED)
    runner._finalize(run, ["completed"])
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.CANCELLED
    assert run.summary["tools_completed"] == 1
    runner._fail(run, "boom")
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.CANCELLED
    assert run.error_message == ""


def test_cancel_kills_in_flight_tool_in_workspace(monkeypatch, user, django_capture_on_commit_callbacks):
    from backend.runtime.tests.factories import ContainerInstanceFactory

    run = _tool_run(user, ["fixture-a"])
    run.workspace.container = ContainerInstanceFactory(name="analyzer-ws")
    run.workspace.save()
    AnalysisRun.objects.filter(pk=run.pk).update(status=AnalysisRun.Status.RUNNING)
    calls = []
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda name, cmd, **k: calls.append((name, cmd)) or {"exit_code": 0, "output": "killed 1"},
    )
    with django_capture_on_commit_callbacks() as callbacks:
        assert runner.cancel(run) is True
    assert run.status == AnalysisRun.Status.CANCELLED
    # The kill waits for the cancel to be committed (the runner must see it when the tool dies).
    assert calls == []
    for callback in callbacks:
        callback()
    assert calls[0][0] == "analyzer-ws"
    # The script assembles the marker at run time, so it never matches itself.
    assert f"llmlab-analysis-run:{run.pk}" not in calls[0][1]
    assert str(run.pk) in calls[0][1]
    # Already finished: nothing to cancel.
    assert runner.cancel(run) is False


def test_kill_script_kills_only_the_marked_process_group():
    import shutil
    import subprocess
    import time
    from pathlib import Path

    timeout_bin, sh_bin = shutil.which("timeout"), shutil.which("sh")
    if not timeout_bin or not sh_bin or not Path("/proc/self/stat").exists():
        pytest.skip("needs coreutils timeout and /proc")
    marker = "llmlab-analysis-run"
    # Same shape as a tool exec: timeout -s KILL <s> sh -c ": <marker>:<run>; <cmd>".
    tool = subprocess.Popen([timeout_bin, "-s", "KILL", "60", sh_bin, "-c", f": {marker}:r1; sleep 30"])  # noqa: S603
    other = subprocess.Popen([timeout_bin, "-s", "KILL", "60", sh_bin, "-c", f": {marker}:r2; sleep 30"])  # noqa: S603
    try:
        time.sleep(0.3)
        out = subprocess.run(  # noqa: S603
            [sh_bin, "-c", runner._kill_script("r1")],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert out.stdout.strip() == "killed 1"
        assert tool.wait(timeout=5) == -9
        assert other.poll() is None
    finally:
        other.kill()
        tool.kill()


def test_reap_stale_runs_fails_only_runs_without_a_live_heartbeat(user):
    from datetime import timedelta

    from django.utils import timezone

    from backend.analysis.models import ToolResult

    now = timezone.now()
    old = now - runner.STALE_AFTER - timedelta(minutes=1)
    dead = AnalysisRunFactory(created_by=user, status="running", started_at=old, heartbeat_at=old)
    ToolResult.objects.create(run=dead, tool_slug="semgrep", status="running")
    legacy = AnalysisRunFactory(created_by=user, status="running", started_at=old, heartbeat_at=None)
    # Long-running but beating: a live thread in some process owns it.
    alive = AnalysisRunFactory(created_by=user, status="running", started_at=old, heartbeat_at=now)
    pending = AnalysisRunFactory(created_by=user, status="pending")

    assert runner.reap_stale_runs() == 2
    for run in (dead, legacy, alive, pending):
        run.refresh_from_db()
    assert dead.status == legacy.status == AnalysisRun.Status.FAILED
    assert dead.error_message == runner.INTERRUPTED_MESSAGE
    assert dead.results.get().status == "failed"
    assert alive.status == AnalysisRun.Status.RUNNING
    assert pending.status == AnalysisRun.Status.PENDING


def test_execute_records_a_heartbeat(monkeypatch, user):
    run = _tool_run(user, ["fixture-a"])
    monkeypatch.setattr(runner, "_execute_locked", lambda r: r)
    runner.execute(run)
    run.refresh_from_db()
    assert run.heartbeat_at is not None
