from __future__ import annotations

import json

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
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


# --- robustness: one tool's crash, unsavable output -----------------------------


def _stub_container(monkeypatch, exec_result):
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda name, cmd, **k: {"exit_code": 0, "output": ""} if cmd.startswith("find ") else exec_result,
    )


RUFF_ONE = json.dumps([{"code": "F401", "message": "unused", "filename": "/work/app.py", "location": {"row": 1}}])


def test_a_crashing_tool_fails_alone_and_the_next_tool_still_runs(monkeypatch, user):
    AnalyzerToolFactory(slug="fixture-ai", kind="ai", parser_key="")
    AnalyzerToolFactory(slug="fixture-ruff", kind="container", parser_key="ruff", run_cmd="ruff {target}")
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        tool_slugs=["fixture-ai", "fixture-ruff"],
        source_code={"app.py": "import os\n"},
    )
    _stub_container(monkeypatch, {"exit_code": 1, "output": RUFF_ONE})

    def broken_review(*args, **kwargs):
        # e.g. a provider response the client did not expect
        msg = "'NoneType' object has no attribute 'strip'"
        raise AttributeError(msg)

    monkeypatch.setattr(runner.ai_runner, "run", broken_review)
    runner.execute(run)
    run.refresh_from_db()

    results = {r.tool_slug: r for r in run.results.all()}
    assert results["fixture-ai"].status == "failed"
    assert results["fixture-ai"].error_message.startswith("Internal error: AttributeError")
    assert results["fixture-ai"].duration_seconds is not None
    assert results["fixture-ruff"].status == "completed"
    assert run.status == AnalysisRun.Status.PARTIAL
    assert (run.summary["tools_completed"], run.summary["tools_failed"]) == (1, 1)
    assert run.summary["total_findings"] == 1


def test_a_failed_save_leaves_no_findings_on_the_failed_result(monkeypatch, user):
    run, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff {target}"},
        {"exit_code": 1, "output": RUFF_ONE},
    )
    assert result.status == "completed"  # control: the same run without the crash

    def boom(findings):
        msg = "db hiccup"
        raise RuntimeError(msg)

    monkeypatch.setattr(runner, "_reported_counts", boom)
    rerun = AnalysisRunFactory(
        created_by=user,
        workspace=run.workspace,
        tool_slugs=["fixture-tool"],
        source_code={"app.py": "import os\n"},
    )
    runner.execute(rerun)
    rerun.refresh_from_db()
    failed = rerun.results.get()
    assert failed.status == "failed"
    assert "db hiccup" in failed.error_message
    # Findings and status are written together: nothing half-stored.
    assert failed.findings.count() == 0
    assert rerun.status == AnalysisRun.Status.FAILED


def test_nul_bytes_in_tool_output_are_stored_replaced(monkeypatch, user):
    # A JSON "\u0000" escape becomes a real NUL once parsed; Postgres rejects it.
    hit = {
        "code": "S105",
        "message": "SQLite format 3\u0000 exposed",
        "filename": "/work/app.py",
        "location": {"row": 1},
    }
    out = json.dumps([hit])
    run, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff {target}"},
        {"exit_code": 1, "output": out + "\x00"},
    )
    assert result.status == "completed"
    finding = result.findings.get()
    assert "\x00" not in finding.title + finding.description
    assert "�" in finding.title + finding.description
    assert "\x00" not in result.raw_output["stdout"]
    assert run.status == AnalysisRun.Status.COMPLETED


def test_scrub_replaces_nul_in_nested_values():
    assert runner._scrub({"a\x00": ["x\x00y", {"k": "\x00"}], "n": 1}) == {
        "a�": ["x�y", {"k": "�"}],
        "n": 1,
    }


# --- exit codes ------------------------------------------------------------------


def test_exit_127_without_a_shell_message_is_the_tools_own_error(monkeypatch, user):
    # osv-scanner exits 127 for its own "general error"; with stderr discarded
    # nothing says "not found", so do not claim the binary is missing.
    _, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff {target} 2>/dev/null"},
        {"exit_code": 127, "output": ""},
    )
    assert result.status == "failed"
    assert result.error_message.startswith("Exited with code 127")
    assert "reinstall" not in result.error_message
    assert "Verify" in result.error_message


def test_early_sigkill_is_not_reported_as_a_timeout():
    tool = AnalyzerToolFactory.build(run_timeout=1200)
    early = runner._exit_error(tool, 137, "", elapsed=0.4)
    assert "after 0.4s, before its 1200s timeout" in early
    assert "Killed after 1200s" in runner._exit_error(tool, 137, "", elapsed=1200.2)


# --- provenance ---------------------------------------------------------------


def test_results_record_command_and_parser_fingerprints(monkeypatch, user):
    _, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff {target}"},
        {"exit_code": 1, "output": RUFF_ONE},
    )
    assert result.summary["command_sha"] == runner.command_fingerprint("ruff /work")
    assert result.summary["parser_sha"] == runner.parser_fingerprint("ruff")
    assert len(result.summary["parser_sha"]) == 12


def test_failed_results_keep_their_provenance(monkeypatch, user):
    _, result = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff --bad {target}", "file_patterns": ["*.py"]},
        {"exit_code": 2, "output": "ruff: error: bad flag"},
    )
    assert result.status == "failed"
    assert result.summary["command_sha"] == runner.command_fingerprint("ruff --bad /work")
    assert result.summary["config"] == {}
    patterns = AnalyzerToolFactory.build(file_patterns=["*.py"])
    assert result.summary["patterns_sha"] == runner.patterns_fingerprint(patterns)


def test_file_patterns_are_part_of_the_provenance_of_measured_and_not_applicable_results(monkeypatch, user):
    tool = AnalyzerToolFactory.build(file_patterns=["*.py", "*.pyi"])
    assert runner.patterns_fingerprint(tool) == runner.patterns_fingerprint(
        AnalyzerToolFactory.build(file_patterns=["*.pyi", "*.py"]),
    )
    assert runner.patterns_fingerprint(tool) != runner.patterns_fingerprint(
        AnalyzerToolFactory.build(file_patterns=["*.py"]),
    )
    # Dynamic tools scan the running app whatever its files.
    assert runner.patterns_fingerprint(AnalyzerToolFactory.build(analysis_mode="dynamic", file_patterns=["*"])) == ""

    first, measured = _container_run(
        monkeypatch,
        user,
        {"parser_key": "ruff", "run_cmd": "ruff {target}", "file_patterns": ["*.py", "*.pyi"]},
        {"exit_code": 1, "output": RUFF_ONE},
    )
    assert measured.summary["patterns_sha"] == runner.patterns_fingerprint(tool)

    AnalyzerToolFactory(slug="fixture-hadolint", kind="container", parser_key="ruff", file_patterns=["Dockerfile"])
    run = AnalysisRunFactory(
        created_by=user,
        workspace=first.workspace,
        tool_slugs=["fixture-hadolint"],
        source_code={"app.py": "import os\n"},
    )
    runner.execute(run)
    skipped = run.results.get()
    assert (skipped.status, skipped.summary["skip_reason"]) == ("skipped", "not_applicable")
    assert skipped.summary["patterns_sha"] == runner.patterns_fingerprint(
        AnalyzerToolFactory.build(file_patterns=["Dockerfile"]),
    )
    # The build does not decide applicability: no version on a not-applicable skip.
    assert "tool_version" not in skipped.summary


def test_stored_stdout_says_when_it_was_cut(monkeypatch):
    tool = AnalyzerToolFactory.build(parser_key="ruff", run_cmd="ruff {target}")
    outputs = iter(["x" * (runner._STDOUT_STORE_LIMIT + 1), RUFF_ONE])
    monkeypatch.setattr(runner.docker_manager, "exec_in", lambda *a, **k: {"exit_code": 1, "output": next(outputs)})
    cut = runner._run_container_tool(tool, "cname")
    assert len(cut.raw_output["stdout"]) == runner._STDOUT_STORE_LIMIT
    assert cut.raw_output["stdout_truncated"] is True
    assert runner._run_container_tool(tool, "cname").raw_output["stdout_truncated"] is False


def test_failed_ai_review_keeps_the_files_it_could_not_fit(monkeypatch, user):
    from backend.analysis.services.base import AnalyzerOutput

    AnalyzerToolFactory(slug="fixture-review", kind="ai", parser_key="")
    run = AnalysisRunFactory(
        created_by=user,
        tool_slugs=["fixture-review"],
        source_code={"frontend/src/App.jsx": "x = 1\n"},
    )
    monkeypatch.setattr(
        runner.ai_runner,
        "run",
        lambda *a, **k: AnalyzerOutput(
            error="No reviewable file fits the prompt budget: frontend/src/App.jsx",
            summary={"files_not_reviewed": ["frontend/src/App.jsx"], "model": "m"},
        ),
    )
    runner.execute(run)
    result = run.results.get()
    assert result.status == "failed"
    assert result.summary["files_not_reviewed"] == ["frontend/src/App.jsx"]
    assert result.summary["model"] == "m"


def test_command_fingerprint_ignores_the_per_app_url_and_sees_command_changes():
    one = runner.command_fingerprint("nuclei -u http://172.17.0.5:8000 -silent", "http://172.17.0.5:8000")
    other = runner.command_fingerprint("nuclei -u http://llm-app9:8000 -silent", "http://llm-app9:8000")
    assert one == other
    assert one != runner.command_fingerprint("nuclei -u http://llm-app9:8000 -silent -as", "http://llm-app9:8000")
    assert runner.parser_fingerprint("no-such-parser") == ""


# --- summary counters -----------------------------------------------------------


def test_summary_counts_code_findings_apart_from_advisories_and_skips(user):
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    AnalyzerToolFactory(slug="dast-counter", analysis_mode="dynamic")
    run = AnalysisRunFactory(created_by=user, status="running")
    osv = ToolResultFactory(run=run, tool_slug="osv-scanner", category="dependencies")
    FindingFactory(result=osv, category="dependency", rule_id="GHSA-1")
    FindingFactory(result=osv, category="dependency", rule_id="GHSA-2")
    # P2: every dependency finding of a vulnerability scanner is an advisory,
    # a package the model invented too (the study tables attribute it to the model).
    FindingFactory(result=osv, category="dependency", rule_id="nonexistent-package")
    deptry = ToolResultFactory(run=run, tool_slug="deptry", category="dependencies")
    FindingFactory(result=deptry, category="dependency", rule_id="DEP002")  # manifest hygiene: code
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="bandit"), category="security")
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="dast-counter"), category="security")
    ToolResultFactory(run=run, tool_slug="hadolint", status="skipped", summary={"skip_reason": "not_applicable"})
    ToolResultFactory(run=run, tool_slug="zap", status="skipped", summary={"skip_reason": "app_not_running"})
    ToolResultFactory(run=run, tool_slug="ghost", status="skipped", summary={"skip_reason": "unavailable"})

    runner._finalize(run, ["completed"] * 4 + ["skipped"] * 3)
    run.refresh_from_db()
    s = run.summary
    assert s["total_findings"] == 6
    assert (s["code_findings"], s["dependency_findings"], s["runtime_findings"]) == (2, 3, 1)
    assert (s["tools_not_applicable"], s["tools_unmeasured"]) == (1, 2)
    # Nothing failed: still completed, but the summary says two tools measured nothing.
    assert run.status == AnalysisRun.Status.COMPLETED


def test_a_scope_no_tool_measured_is_none_not_zero(user):
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    AnalyzerToolFactory(slug="dast-unmeasured", analysis_mode="dynamic")
    assert "dast-unmeasured" in AnalyzerTool.dynamic_slugs()
    assert "bandit" not in AnalyzerTool.dynamic_slugs()
    run = AnalysisRunFactory(created_by=user, status="running")
    ToolResultFactory(run=run, tool_slug="bandit")  # measured code: a clean zero
    # The only vulnerability scanner failed and the DAST tool found the app down.
    ToolResultFactory(run=run, tool_slug="pip-audit", category="dependencies", status="failed")
    ToolResultFactory(
        run=run,
        tool_slug="dast-unmeasured",
        status="skipped",
        summary={"skip_reason": "app_not_running"},
    )
    runner._finalize(run, ["completed", "failed", "skipped"])
    run.refresh_from_db()
    s = run.summary
    assert (s["code_findings"], s["dependency_findings"], s["runtime_findings"]) == (0, None, None)

    # A scope that has findings shows them, even if they sit on a failed result;
    # not applicable is a measurement (a true zero).
    other = AnalysisRunFactory(created_by=user, status="running")
    failed = ToolResultFactory(run=other, tool_slug="osv-scanner", category="dependencies", status="failed")
    FindingFactory(result=failed, category="dependency", rule_id="nonexistent-package")
    ToolResultFactory(run=other, tool_slug="semgrep", status="skipped", summary={"skip_reason": "not_applicable"})
    counts = runner.run_scope_counts([other.id, str(run.id)])
    assert counts[other.id]["dependency_findings"] == 1
    assert counts[other.id]["code_findings"] == 0
    assert counts[other.id]["runtime_findings"] is None
    assert counts[str(run.id)]["dependency_findings"] is None


# --- the analyzed tree ----------------------------------------------------------


def test_empty_package_files_are_analyzed_but_never_make_a_tool_applicable(monkeypatch, user):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(
        created_by=user,
        result_data={"files": {"pkg/__init__.py": "", "pkg/a.py": "from .b import x\n", "requirements.txt": ""}},
    )
    AnalyzerToolFactory(slug="fixture-ruff", kind="container", parser_key="ruff", run_cmd="ruff {target}")
    AnalyzerToolFactory(
        slug="fixture-audit",
        kind="container",
        parser_key="ruff",
        run_cmd="audit {target}",
        file_patterns=["requirements*.txt"],
    )
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        generation_job=job,
        source_code=None,
        tool_slugs=["fixture-ruff", "fixture-audit"],
    )
    copied = {}
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda name, files, **k: copied.update(files) or {})
    monkeypatch.setattr(runner.docker_manager, "exec_in", lambda name, cmd, **k: {"exit_code": 0, "output": "[]"})
    runner.execute(run)

    # The package marker reaches /work: the tools see the tree the app runs with.
    assert copied == {"pkg/__init__.py": "", "pkg/a.py": "from .b import x\n", "requirements.txt": ""}
    results = {r.tool_slug: r for r in run.results.all()}
    assert results["fixture-ruff"].status == "completed"
    # An empty manifest gives the audit nothing to read.
    assert results["fixture-audit"].status == "skipped"
    assert results["fixture-audit"].summary["skip_reason"] == runner.SKIP_NOT_APPLICABLE


def test_files_in_dependency_or_scratch_dirs_are_left_out_of_the_analyzed_tree(monkeypatch, user):
    from backend.generation.tests.factories import GenerationJobFactory

    files = {
        "app.py": "x = 1\n",
        ".tmp/rr/package/package.json": '{"name": "react-router"}',
        ".tmp/rr/package/README.md": "docs",
        "frontend/node_modules/x/index.js": "y",
    }
    job = GenerationJobFactory(created_by=user, result_data={"files": files})
    AnalyzerToolFactory(slug="fixture-ruff", kind="container", parser_key="ruff", run_cmd="ruff {target}")
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        generation_job=job,
        source_code=None,
        tool_slugs=["fixture-ruff"],
    )
    copied = {}
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda name, f, **k: copied.update(f) or {})
    monkeypatch.setattr(runner.docker_manager, "exec_in", lambda name, cmd, **k: {"exit_code": 0, "output": "[]"})
    runner.execute(run)

    assert copied == {"app.py": "x = 1\n"}
    run.refresh_from_db()
    assert run.summary["excluded_paths"]["count"] == 3
    assert ".tmp/rr/package/package.json" in run.summary["excluded_paths"]["sample"]


def test_non_zero_exit_whose_errors_were_all_deliberately_not_counted_is_a_measurement(monkeypatch, user):
    """mypy exits 1 because of ORM-model errors the parser does not count: it ran, so 0 counted findings is real."""
    AnalyzerToolFactory(slug="fixture-mypy", kind="container", parser_key="mypy", run_cmd="mypy {target}")
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        tool_slugs=["fixture-mypy"],
        source_code={"app.py": "x = 1\n"},
    )
    raw = 'app.py:33: error: Name "db.Model" is not defined  [name-defined]\nFound 1 error in 1 file\n'
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {})

    def exec_in(name, cmd, **k):
        is_mypy = "mypy" in (cmd if isinstance(cmd, str) else " ".join(cmd))
        return {"exit_code": 1, "output": raw} if is_mypy else {"exit_code": 0, "output": ""}

    monkeypatch.setattr(runner.docker_manager, "exec_in", exec_in)
    runner.execute(run)

    run.refresh_from_db()
    assert run.status == "completed", (run.status, run.error_message)
    result = run.results.get()
    assert result.status == "completed"
    assert result.findings.count() == 0
    assert "Not counted: 1 error(s) on ORM model classes" in result.summary["warnings"][0]


def test_materialize_keeps_real_paths_and_drops_empty_blobs():
    files = runner._materialize(
        {"Dockerfile": "FROM python", "backend/Makefile": "all:", "pkg/__init__.py": "", "backend_code": " "},
    )
    assert files == {"Dockerfile": "FROM python", "backend/Makefile": "all:", "pkg/__init__.py": ""}


def test_ai_tool_without_reviewable_files_is_not_applicable(monkeypatch, user):
    AnalyzerToolFactory(slug="fixture-ai", kind="ai", parser_key="")
    run = AnalysisRunFactory(
        created_by=user,
        tool_slugs=["fixture-ai"],
        source_code={"frontend/package-lock.json": "{}", "app.min.js": "x"},
    )
    monkeypatch.setattr(runner.ai_runner, "run", lambda *a, **k: pytest.fail("nothing to review"))
    runner.execute(run)
    result = run.results.get()
    assert result.status == "skipped"
    assert result.summary["skip_reason"] == runner.SKIP_NOT_APPLICABLE


def test_truncated_agent_output_is_flagged_like_the_reports_do(monkeypatch, user):
    from backend.analysis.services.base import AnalyzerOutput
    from backend.generation.services.experiment_results import output_truncated
    from backend.generation.tests.factories import GenerationJobFactory

    AnalyzerToolFactory(slug="llm_review", kind="ai", parser_key="")
    data = {"files": {"app.py": "x = 1\n"}, "agent": {"final_response_truncated": True}}
    job = GenerationJobFactory(created_by=user, result_data=data)
    run = AnalysisRunFactory(created_by=user, tool_slugs=["llm_review"], source_code=None, generation_job=job)
    monkeypatch.setattr(runner.ai_runner, "run", lambda *a, **k: AnalyzerOutput(findings=[]))
    runner.execute(run)
    run.refresh_from_db()
    assert output_truncated(data) is True
    assert run.summary["truncated_inputs"] == ["agent"]
