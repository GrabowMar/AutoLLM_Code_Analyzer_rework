"""Recovery paths: reconcile(), request_cancel() on orphaned runs, and the heartbeat thread."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.engine import executor
from backend.analysis.engine import sandbox
from backend.analysis.engine.reviewer import agentic_sandbox
from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests import executor_fakes as fx

Status = AnalysisRun.Status
TStatus = ToolResult.Status
APP = {"app.py": "import os\n"}


@pytest.fixture(autouse=True)
def _executor_settings(settings):
    settings.ANALYSIS_HEARTBEAT_THREAD = False
    settings.ANALYSIS_USE_CELERY = False
    settings.ANALYSIS_MAX_ATTEMPTS = 3
    settings.ANALYSIS_STALE_SECONDS = 180


@pytest.fixture
def world(monkeypatch):
    return fx.install_world(monkeypatch, adapters_by_slug={"ruff": fx.FakeAdapter("ruff", findings=[fx.raw("S1")])})


def _submit(user) -> AnalysisRun:
    return executor.submit_run(
        user=user,
        plan=fx.make_plan([fx.tool_entry("ruff"), fx.tool_entry("bandit")]),
        source_code=APP,
        sample=fx.make_sample(APP),
        dispatch=False,
    )


def _running(run, *, minutes_ago: float, attempt: int = 1, cancel: bool = False) -> None:
    AnalysisRun.objects.filter(pk=run.pk).update(
        status=Status.RUNNING,
        lease_owner="worker-1",
        heartbeat_at=timezone.now() - timedelta(minutes=minutes_ago),
        attempt=attempt,
        cancel_requested=cancel,
        started_at=timezone.now() - timedelta(minutes=minutes_ago + 1),
    )
    ToolResult.objects.filter(run=run, tool_slug="ruff").update(status=TStatus.RUNNING, attempt=attempt)


def test_stale_running_run_is_requeued(user, world, django_capture_on_commit_callbacks):
    run = _submit(user)
    _running(run, minutes_ago=10)
    with django_capture_on_commit_callbacks(execute=True):
        report = executor.reconcile()
    assert report["requeued"] == [str(run.pk)]
    run.refresh_from_db()
    assert run.status == Status.QUEUED
    assert run.lease_owner == ""
    assert run.heartbeat_at is None
    assert run.phase == "requeued"
    assert "stopped heartbeating" in run.error_message
    assert ToolResult.objects.get(run=run, tool_slug="ruff").status == TStatus.PENDING
    assert world.enqueued == [(str(run.pk), 0)]


def test_live_running_run_is_left_alone(user, world):
    run = _submit(user)
    _running(run, minutes_ago=1)
    report = executor.reconcile()
    assert report["requeued"] == []
    run.refresh_from_db()
    assert run.status == Status.RUNNING
    assert run.lease_owner == "worker-1"


def test_stale_run_with_a_cancel_request_is_cancelled(user, world):
    run = _submit(user)
    _running(run, minutes_ago=10, cancel=True)
    report = executor.reconcile()
    assert report["cancelled"] == [str(run.pk)]
    run.refresh_from_db()
    assert run.status == Status.CANCELLED
    assert set(ToolResult.objects.filter(run=run).values_list("status", flat=True)) == {TStatus.CANCELLED}


def test_stale_run_past_max_attempts_fails_with_infra_error(user, world):
    run = _submit(user)
    _running(run, minutes_ago=10, attempt=3)
    report = executor.reconcile()
    assert report["failed"] == [str(run.pk)]
    run.refresh_from_db()
    assert run.status == Status.FAILED
    assert run.error_message.startswith("infra_error:")
    assert run.completed_at is not None
    assert run.summary["schema_version"] == 2
    assert set(ToolResult.objects.filter(run=run).values_list("failure_reason", flat=True)) == {"infra_error"}


def test_lost_queued_runs_are_redispatched(user, world, django_capture_on_commit_callbacks):
    old = _submit(user)
    recent = _submit(user)
    AnalysisRun.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
    with django_capture_on_commit_callbacks(execute=True):
        report = executor.reconcile()
    assert report["redispatched"] == [str(old.pk)]
    assert world.enqueued == [(str(old.pk), 0)]
    old.refresh_from_db()
    assert old.updated_at > timezone.now() - timedelta(minutes=1)
    recent.refresh_from_db()
    assert recent.status == Status.QUEUED


def test_queued_run_with_a_cancel_flag_is_cancelled(user, world):
    run = _submit(user)
    AnalysisRun.objects.filter(pk=run.pk).update(
        cancel_requested=True,
        updated_at=timezone.now() - timedelta(minutes=10),
    )
    report = executor.reconcile()
    assert report["cancelled"] == [str(run.pk)]
    run.refresh_from_db()
    assert run.status == Status.CANCELLED


def test_reap_spares_running_runs(user, world, monkeypatch):
    calls = []
    monkeypatch.setattr(sandbox, "reap", lambda **kwargs: calls.append(kwargs) or {"containers": 2})
    live = _submit(user)
    _running(live, minutes_ago=1)
    _submit(user)
    report = executor.reconcile()
    assert report["reaped"] == {"containers": 2}
    assert calls == [{"live_run_ids": {str(live.pk)}, "older_than_s": executor.REAP_MIN_AGE_SECONDS}]


def test_reap_failure_is_reported_not_raised(user, world, monkeypatch):
    def reap(**kwargs):
        msg = "daemon down"
        raise sandbox.SandboxError(msg)

    monkeypatch.setattr(sandbox, "reap", reap)
    report = executor.reconcile()
    assert report["reap_error"] == "daemon down"


def test_reconcile_reaps_review_sandboxes_of_runs_that_are_not_live(user, world, monkeypatch):
    calls = []
    monkeypatch.setattr(
        agentic_sandbox,
        "reap_review_sandboxes",
        lambda **kwargs: calls.append(kwargs) or {"containers": 0, "networks": 2},
    )
    live = _submit(user)
    _running(live, minutes_ago=1)
    report = executor.reconcile()
    assert report["review_reaped"] == {"containers": 0, "networks": 2}
    assert calls == [{"live_run_ids": {str(live.pk)}, "older_than_s": executor.REAP_MIN_AGE_SECONDS}]


def test_review_reap_failure_never_breaks_reconcile(user, world, monkeypatch):
    def reap_review_sandboxes(**kwargs):
        msg = "daemon gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(agentic_sandbox, "reap_review_sandboxes", reap_review_sandboxes)
    run = _submit(user)
    _running(run, minutes_ago=10)
    report = executor.reconcile()
    assert report["requeued"] == [str(run.pk)]
    assert report["reaped"] == {"containers": 0, "volumes": 0, "errors": 0}
    assert report["review_reaped"] is None
    assert report["review_reap_error"] == "daemon gone"


def test_maybe_reconcile_is_throttled(world, monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "reconcile", lambda: calls.append(1))
    monkeypatch.setitem(executor._last_reconcile, "at", -1e9)
    assert executor.maybe_reconcile(background=False)
    assert not executor.maybe_reconcile(background=False)
    assert calls == [1]


def test_cancel_of_an_orphaned_running_run_is_immediate(user, world):
    run = _submit(user)
    _running(run, minutes_ago=10)
    run = executor.request_cancel(run)
    assert run.status == Status.CANCELLED
    assert run.lease_owner == ""
    assert set(ToolResult.objects.filter(run=run).values_list("status", flat=True)) == {TStatus.CANCELLED}


def test_cancel_of_a_live_running_run_only_flags_it(user, world):
    run = _submit(user)
    _running(run, minutes_ago=0)
    run = executor.request_cancel(run)
    assert run.status == Status.RUNNING
    assert run.cancel_requested


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_heartbeat_thread_keeps_the_lease_and_relays_cancel(user, monkeypatch, settings):
    """Real thread + its own DB connection (committed rows), as in a worker."""
    settings.ANALYSIS_HEARTBEAT_THREAD = True
    settings.ANALYSIS_HEARTBEAT_SECONDS = 1
    world = fx.install_world(monkeypatch, adapters_by_slug={"ruff": fx.FakeAdapter("ruff")})
    # Only the heartbeat thread may notice the cancel flag in this test.
    monkeypatch.setattr(executor, "CANCEL_POLL_SECONDS", 3600.0)
    run = executor.submit_run(
        user=user,
        plan=fx.make_plan([fx.tool_entry("ruff")]),
        source_code=APP,
        sample=fx.make_sample(APP),
        dispatch=False,
    )
    observed = {}

    def slow_tool(*, is_cancelled, heartbeat, invocation):
        claimed = AnalysisRun.objects.get(pk=run.pk).heartbeat_at
        time.sleep(2.5)
        observed["advanced"] = AnalysisRun.objects.get(pk=run.pk).heartbeat_at > claimed
        observed["result_touched"] = ToolResult.objects.get(run=run).updated_at
        AnalysisRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not is_cancelled():
            time.sleep(0.1)
        observed["cancel_seen"] = is_cancelled()
        return fx.ExecOutcome(exit_code=None, cancelled=True)

    world.sandbox.outcomes["ruff"] = [slow_tool]
    executor.process_run(str(run.pk))
    run.refresh_from_db()
    assert observed == {"advanced": True, "result_touched": observed["result_touched"], "cancel_seen": True}
    assert run.status == Status.CANCELLED
    assert executor.slot_status()["in_use"] == 0
