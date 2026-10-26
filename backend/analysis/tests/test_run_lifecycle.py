"""Live feedback of a run: per-tool timing and events, phases, the run queue and its reaping."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import AnalyzerWorkspaceFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def events(monkeypatch):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(runner.realtime, "publish", lambda channel, event: sent.append((channel, event)))
    return sent


def _run_with_tools(monkeypatch, user, *, ruff_output):
    AnalyzerToolFactory(slug="fixture-ruff", kind="container", parser_key="ruff", run_cmd="ruff {target}")
    # Reads only HTML: not applicable to a Python-only app.
    AnalyzerToolFactory(slug="fixture-html", kind="container", parser_key="ruff", file_patterns=["*.html"])
    run = AnalysisRunFactory(
        created_by=user,
        workspace=AnalyzerWorkspaceFactory(user=user),
        tool_slugs=["fixture-ruff", "fixture-html"],
        source_code={"app.py": "import os\n"},
    )
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda name, cmd, **k: (
            {"exit_code": 0, "output": ""} if cmd.startswith("find ") else {"exit_code": 1, "output": ruff_output}
        ),
    )
    return run


RUFF_OUTPUT = json.dumps(
    [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "/work/app.py",
            "location": {"row": 1, "column": 8},
            "end_location": {"row": 1, "column": 10},
        },
    ],
)


def test_execute_times_tools_and_streams_their_results(monkeypatch, user, events):
    run = _run_with_tools(monkeypatch, user, ruff_output=RUFF_OUTPUT)
    runner.execute(run)
    run.refresh_from_db()

    ran = run.results.get(tool_slug="fixture-ruff")
    skipped = run.results.get(tool_slug="fixture-html")
    assert ran.status == ToolResult.Status.COMPLETED
    assert ran.started_at is not None
    assert ran.duration_seconds is not None
    assert ran.duration_seconds >= 0
    # A tool that never ran has no timing.
    assert skipped.status == ToolResult.Status.SKIPPED
    assert skipped.started_at is None
    assert skipped.duration_seconds is None

    channel = f"analysis:{run.id}"
    assert {c for c, _ in events} == {channel}
    phases = [e["phase"] for _, e in events if e["type"] == "progress"]
    assert phases == [runner.PHASE_PREPARING, runner.PHASE_TOOLS]
    results = [(e["result"]["tool_slug"], e["result"]["status"]) for _, e in events if e["type"] == "result"]
    assert results == [("fixture-ruff", "running"), ("fixture-ruff", "completed"), ("fixture-html", "skipped")]
    statuses = [e["status"] for _, e in events if e["type"] == "status"]
    assert statuses == ["running", "completed"]


def test_finalize_counts_levels_as_the_tools_reported_them(monkeypatch, user, events):
    run = _run_with_tools(monkeypatch, user, ruff_output=RUFF_OUTPUT)
    runner.execute(run)
    run.refresh_from_db()
    finding = run.results.get(tool_slug="fixture-ruff").findings.get()
    # Ruff rates nothing: the stored normalized level is a heuristic, the reported one is empty.
    assert finding.tool_severity == ""
    assert run.summary["unrated_findings"] == 1
    assert run.summary["reported_severity_counts"] == {}
    assert "phase" not in run.summary


def test_reported_levels_are_stored_and_counted(monkeypatch, user, events):
    from backend.analysis.services.base import AnalyzerOutput
    from backend.analysis.services.base import FindingData

    AnalyzerToolFactory(slug="fixture-ai", kind="ai", parser_key="")
    run = AnalysisRunFactory(created_by=user, tool_slugs=["fixture-ai"], source_code={"app.py": "x = 1\n"})
    output = AnalyzerOutput(
        findings=[
            FindingData(severity="high", category="security", title="a", tool_severity="HIGH"),
            FindingData(severity="high", category="security", title="b", tool_severity="high"),
            FindingData(severity="low", category="quality", title="c"),
        ],
    )
    monkeypatch.setattr(runner.ai_runner, "run", lambda *a, **k: output)
    runner.execute(run)
    run.refresh_from_db()
    assert sorted(run.results.get().findings.values_list("tool_severity", flat=True)) == ["", "HIGH", "high"]
    assert run.summary["reported_severity_counts"] == {"high": 2}
    assert run.summary["unrated_findings"] == 1


def test_dispatch_many_queues_runs_on_one_thread(monkeypatch, user, django_capture_on_commit_callbacks):
    queued = [AnalysisRunFactory(created_by=user) for _ in range(3)]
    started: list = []
    monkeypatch.setattr(runner, "dispatch_in_thread", lambda fn, *args, **kw: started.append((fn, args)))
    with django_capture_on_commit_callbacks(execute=True):
        runner.dispatch_many([r.id for r in queued])
        # The thread starts only after the creating transaction commits.
        assert started == []

    assert len(started) == 1
    fn, args = started[0]
    assert fn is runner._execute_queue
    assert args == ([str(r.id) for r in queued],)
    # Queued runs carry a heartbeat, so a dead queue can be told from a live one.
    for run in queued:
        run.refresh_from_db()
        assert run.heartbeat_at is not None


def test_execute_queue_runs_in_order(monkeypatch, user):
    order: list[str] = []
    monkeypatch.setattr(runner, "_execute", order.append)
    runner._execute_queue(["a", "b", "c"])
    assert order == ["a", "b", "c"]


def test_reaper_fails_queued_runs_whose_queue_died(user):
    old = timezone.now() - runner.STALE_AFTER - timedelta(minutes=1)
    dead_queue = AnalysisRunFactory(created_by=user, status="pending", heartbeat_at=old)
    live_queue = AnalysisRunFactory(created_by=user, status="pending", heartbeat_at=timezone.now())
    never_queued = AnalysisRunFactory(created_by=user, status="pending", heartbeat_at=None)

    assert runner.reap_stale_runs() == 1
    for run in (dead_queue, live_queue, never_queued):
        run.refresh_from_db()
    assert dead_queue.status == AnalysisRun.Status.FAILED
    assert dead_queue.error_message == runner.QUEUE_INTERRUPTED_MESSAGE
    assert live_queue.status == never_queued.status == AnalysisRun.Status.PENDING


def test_failed_preparation_drops_the_phase(monkeypatch, user, events):
    run = _run_with_tools(monkeypatch, user, ruff_output=RUFF_OUTPUT)

    def broken(ws):
        msg = "workspace down"
        raise RuntimeError(msg)

    monkeypatch.setattr(runner.workspace_service, "require_ready_container", broken)
    runner.execute(run)
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.FAILED
    assert run.error_message == "workspace down"
    assert "phase" not in run.summary
    assert run.duration_seconds is not None
