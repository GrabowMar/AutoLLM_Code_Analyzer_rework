"""Tests for the automation execution engine (Phase 7b).

Uses ``pytest.mark.django_db(transaction=True)`` because the engine
runs steps in daemon threads that open their own DB connections.
Dispatchers are mocked so no real generation/analysis/report work is done.
"""

from __future__ import annotations

import time
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import patch

import pytest

from llm_lab.automation.engine.batches import expand_batch
from llm_lab.automation.engine.batches import update_batch_status
from llm_lab.automation.engine.params import resolve_params
from llm_lab.automation.engine.runner import execute_run
from llm_lab.automation.engine.scheduler import due_schedules
from llm_lab.automation.engine.scheduler import tick
from llm_lab.automation.models import BatchItem
from llm_lab.automation.models import PipelineRun
from llm_lab.automation.models import RunStatus
from llm_lab.automation.tests.factories import BatchFactory
from llm_lab.automation.tests.factories import PipelineFactory
from llm_lab.automation.tests.factories import PipelineRunFactory
from llm_lab.automation.tests.factories import PipelineStepFactory
from llm_lab.automation.tests.factories import PipelineStepRunFactory
from llm_lab.automation.tests.factories import ScheduleFactory
from llm_lab.users.tests.factories import UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_run(
    run_id,
    timeout=10,
    expected_statuses=("succeeded", "failed", "cancelled"),
):
    """Poll until run reaches a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = PipelineRun.objects.get(id=run_id)
        if run.status in expected_statuses:
            return run
        time.sleep(0.2)
    return PipelineRun.objects.get(id=run_id)


def _make_pipeline_with_steps(user, step_configs):
    """Create a Pipeline with PipelineSteps.

    step_configs: list of dicts with keys: name, kind, config, depends_on
    """
    pipeline = PipelineFactory(owner=user)
    steps = []
    for i, sc in enumerate(step_configs):
        step = PipelineStepFactory(
            pipeline=pipeline,
            order=i,
            name=sc["name"],
            kind=sc.get("kind", "wait"),
            config=sc.get("config", {}),
            depends_on=sc.get("depends_on", []),
        )
        steps.append(step)
    return pipeline, steps


def _make_run_with_steps(pipeline):
    """Create a PipelineRun + PipelineStepRun for each step."""
    run = PipelineRunFactory(pipeline=pipeline, status="pending")
    for step in pipeline.steps.order_by("order"):
        PipelineStepRunFactory(
            run=run,
            step=step,
            status="pending",
            retries_remaining=step.config.get("max_retries", 0),
        )
    return run


# ---------------------------------------------------------------------------
# Param resolution tests
# ---------------------------------------------------------------------------


class TestParamResolution:
    def test_simple_reference(self):
        config = {"target": "{{steps.step1.output.job_id}}"}
        prior = {"step1": {"output": {"job_id": "abc-123"}}}
        result = resolve_params(config, prior, {})
        assert result["target"] == "abc-123"

    def test_run_params_reference(self):
        config = {"model": "{{params.model_id}}"}
        result = resolve_params(config, {}, {"model_id": "gpt-4"})
        assert result["model"] == "gpt-4"

    def test_missing_reference_unchanged(self):
        config = {"x": "{{steps.missing.output.key}}"}
        result = resolve_params(config, {}, {})
        assert result["x"] == "{{steps.missing.output.key}}"

    def test_nested_dict_resolution(self):
        config = {"nested": {"url": "{{steps.container.output.url}}"}}
        prior = {"container": {"output": {"url": "http://localhost:8080"}}}
        result = resolve_params(config, prior, {})
        assert result["nested"]["url"] == "http://localhost:8080"

    def test_list_resolution(self):
        config = {"models": ["{{params.model_id}}", "static"]}
        result = resolve_params(config, {}, {"model_id": "gpt-4"})
        assert result["models"] == ["gpt-4", "static"]

    def test_no_refs_unchanged(self):
        config = {"key": "plain_value", "num": 42}
        result = resolve_params(config, {}, {})
        assert result == config


# ---------------------------------------------------------------------------
# Engine: Linear pipeline success
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLinearPipelineSuccess:
    def test_single_wait_step_succeeds(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "wait1", "kind": "wait", "config": {"seconds": 0}}],
        )
        run = _make_run_with_steps(pipeline)

        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED

    def test_two_sequential_steps_succeed(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {"name": "step1", "kind": "wait", "config": {"seconds": 0}},
                {
                    "name": "step2",
                    "kind": "wait",
                    "config": {"seconds": 0},
                    "depends_on": ["step1"],
                },
            ],
        )
        run = _make_run_with_steps(pipeline)

        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED
        step_runs = list(run.step_runs.order_by("created_at"))
        assert all(sr.status == RunStatus.SUCCEEDED for sr in step_runs)

    def test_notify_step_succeeds(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {
                    "name": "notif",
                    "kind": "notify",
                    "config": {"channel": "test", "message": "hello"},
                },
            ],
        )
        run = _make_run_with_steps(pipeline)

        with patch("llm_lab.automation.engine.dispatchers.publish"):
            execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Engine: DAG parallelism
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDagParallelism:
    def test_two_independent_steps_run_in_parallel(self):
        """Two steps with no deps should both be dispatched before either finishes."""
        user = UserFactory()
        # Use a small sleep to detect parallel execution
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {"name": "a", "kind": "wait", "config": {"seconds": 0.1}},
                {"name": "b", "kind": "wait", "config": {"seconds": 0.1}},
            ],
        )
        run = _make_run_with_steps(pipeline)

        start = time.monotonic()
        execute_run(run.id)
        elapsed = time.monotonic() - start

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED
        # With 0.25s poll interval + 0.1s wait per step, parallel should be ~0.35s max.
        # Serial would be ~0.7s+; use 0.7s threshold to confirm parallelism.
        assert elapsed < 0.7

    def test_dependent_step_waits_for_parent(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {"name": "parent", "kind": "wait", "config": {"seconds": 0}},
                {
                    "name": "child",
                    "kind": "wait",
                    "config": {"seconds": 0},
                    "depends_on": ["parent"],
                },
            ],
        )
        run = _make_run_with_steps(pipeline)
        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED
        child_sr = run.step_runs.get(step__name="child")
        parent_sr = run.step_runs.get(step__name="parent")
        assert parent_sr.completed_at <= child_sr.started_at


# ---------------------------------------------------------------------------
# Engine: Failure propagation
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFailurePropagation:
    def test_failed_step_marks_run_failed(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "bad", "kind": "wait", "config": {"seconds": 0}}],
        )
        run = _make_run_with_steps(pipeline)

        # Force the step run into failed state before the engine touches it
        sr = run.step_runs.first()
        sr.status = "failed"
        sr.error = "pre-set failure"
        sr.completed_at = datetime.now(UTC)
        sr.save()

        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED

    def test_downstream_steps_skipped_on_failure(self):
        """When step1 fails, step2 (depends on step1) should be cancelled."""
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {"name": "step1", "kind": "wait", "config": {"seconds": 0}},
                {
                    "name": "step2",
                    "kind": "wait",
                    "config": {"seconds": 0},
                    "depends_on": ["step1"],
                },
            ],
        )
        run = _make_run_with_steps(pipeline)

        # Pre-fail step1
        sr1 = run.step_runs.get(step__name="step1")
        sr1.status = "failed"
        sr1.error = "injected failure"
        sr1.completed_at = datetime.now(UTC)
        sr1.save()

        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED
        sr2 = run.step_runs.get(step__name="step2")
        assert sr2.status == RunStatus.CANCELLED

    def test_generate_dispatcher_failure_propagates(self):
        """When the generate dispatcher returns a failed output, step should fail."""
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {
                    "name": "gen",
                    "kind": "generate",
                    "config": {"model_id": "x", "template_slug": "y"},
                },
            ],
        )
        run = _make_run_with_steps(pipeline)

        with patch(
            "llm_lab.automation.engine.dispatchers.dispatch_generate",
            return_value={"status": "failed", "error": "LLM API error"},
        ):
            execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED
        sr = run.step_runs.first()
        assert sr.status == RunStatus.FAILED
        assert "LLM API error" in sr.error


# ---------------------------------------------------------------------------
# Engine: Retries  # noqa: ERA001
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestRetries:
    def test_step_succeeds_on_second_attempt(self):
        """Step fails first time, succeeds on retry."""
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {
                    "name": "flaky",
                    "kind": "wait",
                    "config": {"seconds": 0, "max_retries": 1},
                },
            ],
        )
        run = _make_run_with_steps(pipeline)
        sr = run.step_runs.first()
        # Give it 1 retry
        sr.retries_remaining = 1
        sr.save()

        call_count = {"n": 0}
        original_wait = __import__(
            "llm_lab.automation.engine.dispatchers",
            fromlist=["dispatch_wait"],
        ).dispatch_wait

        def flaky_wait(step_run, params, run_params):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"status": "failed", "error": "transient error"}
            return original_wait(step_run, params, run_params)

        with patch(
            "llm_lab.automation.engine.dispatchers.dispatch_wait",
            side_effect=flaky_wait,
        ):
            execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED
        sr.refresh_from_db()
        assert sr.attempt == 2
        assert call_count["n"] == 2

    def test_exhausted_retries_fails_step(self):
        """Step fails all attempts → run fails."""
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {
                    "name": "always_fail",
                    "kind": "wait",
                    "config": {"seconds": 0, "max_retries": 1},
                },
            ],
        )
        run = _make_run_with_steps(pipeline)
        sr = run.step_runs.first()
        sr.retries_remaining = 1
        sr.save()

        with patch(
            "llm_lab.automation.engine.dispatchers.dispatch_wait",
            return_value={"status": "failed", "error": "always fails"},
        ):
            execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.FAILED


# ---------------------------------------------------------------------------
# Engine: Cancellation  # noqa: ERA001
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCancellation:
    def test_cancelled_run_stops_gracefully(self):
        """Mark run as cancelled before it starts; engine should exit cleanly."""
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "w", "kind": "wait", "config": {"seconds": 0}}],
        )
        run = _make_run_with_steps(pipeline)
        run.status = "cancelled"
        run.save()

        execute_run(run.id)

        run.refresh_from_db()
        # Engine should not override cancelled to running
        assert run.status == RunStatus.CANCELLED


# ---------------------------------------------------------------------------
# Engine: Param resolution from prior step output
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestParamResolutionFromStepOutput:
    def test_script_receives_prior_step_output(self):
        """step2 config references step1's output via {{steps.step1.output.key}}."""
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {"name": "step1", "kind": "wait", "config": {"seconds": 0}},
                {
                    "name": "step2",
                    "kind": "script",
                    "config": {
                        "code": "set_variables",
                        "variables": {
                            "upstream_waited": "{{steps.step1.output.waited_seconds}}",
                        },
                    },
                    "depends_on": ["step1"],
                },
            ],
        )
        run = _make_run_with_steps(pipeline)
        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED
        sr2 = run.step_runs.get(step__name="step2")
        assert sr2.status == RunStatus.SUCCEEDED
        # waited_seconds should be resolved from step1's output
        assert sr2.output.get("variables", {}).get("upstream_waited") == 0.0


# ---------------------------------------------------------------------------
# Batch expansion
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestBatchExpansion:
    def test_matrix_creates_correct_number_of_runs(self):
        user = UserFactory()
        pipeline = PipelineFactory(owner=user)
        PipelineStepFactory(
            pipeline=pipeline,
            name="w",
            kind="wait",
            config={"seconds": 0},
        )

        batch = BatchFactory(
            owner=user,
            config={
                "pipeline_id": str(pipeline.id),
                "matrix": {
                    "models": ["gpt-4", "claude-3"],
                    "templates": ["todo-app", "chat-app"],
                },
            },
        )

        with patch("llm_lab.automation.engine.batches.execute_run"):
            run_ids = expand_batch(batch.id)

        assert len(run_ids) == 4

    def test_matrix_creates_batch_items(self):
        user = UserFactory()
        pipeline = PipelineFactory(owner=user)
        PipelineStepFactory(
            pipeline=pipeline,
            name="w",
            kind="wait",
            config={"seconds": 0},
        )

        batch = BatchFactory(
            owner=user,
            config={
                "pipeline_id": str(pipeline.id),
                "matrix": {"models": ["a", "b"], "templates": ["t1"]},
            },
        )

        with patch("llm_lab.automation.engine.batches.execute_run"):
            expand_batch(batch.id)

        items = list(BatchItem.objects.filter(batch=batch))
        assert len(items) == 2
        item_params = [item.params for item in items]
        assert {"models": "a", "templates": "t1"} in item_params
        assert {"models": "b", "templates": "t1"} in item_params

    def test_update_batch_status_all_succeeded(self):
        user = UserFactory()
        batch = BatchFactory(owner=user)
        run = PipelineRunFactory()
        BatchItem.objects.create(batch=batch, pipeline_run=run, status="succeeded")
        BatchItem.objects.create(batch=batch, pipeline_run=run, status="succeeded")

        status = update_batch_status(batch.id)
        assert status == "succeeded"

    def test_update_batch_status_any_failed(self):
        user = UserFactory()
        batch = BatchFactory(owner=user)
        run = PipelineRunFactory()
        BatchItem.objects.create(batch=batch, pipeline_run=run, status="succeeded")
        BatchItem.objects.create(batch=batch, pipeline_run=run, status="failed")

        status = update_batch_status(batch.id)
        assert status == "failed"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScheduler:
    def test_due_schedules_returns_overdue_only(self):
        user = UserFactory()
        pipeline = PipelineFactory(owner=user)

        now = datetime.now(UTC)
        past = now - timedelta(hours=1)
        future = now + timedelta(hours=1)

        due = ScheduleFactory(
            pipeline=pipeline,
            owner=user,
            enabled=True,
            next_run_at=past,
        )
        ScheduleFactory(
            pipeline=pipeline,
            owner=user,
            enabled=True,
            next_run_at=future,
        )
        ScheduleFactory(
            pipeline=pipeline,
            owner=user,
            enabled=False,
            next_run_at=past,
        )

        result = due_schedules(now=now)
        assert len(result) == 1
        assert result[0].id == due.id

    def test_tick_triggers_due_schedule(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "w", "kind": "wait", "config": {"seconds": 0}}],
        )
        now = datetime.now(UTC)
        ScheduleFactory(
            pipeline=pipeline,
            owner=user,
            enabled=True,
            cron_expression="* * * * *",
            next_run_at=now - timedelta(minutes=1),
        )

        with (
            patch("llm_lab.automation.engine.runner.execute_run"),
            patch("llm_lab.automation.services._celery_available", return_value=False),
        ):
            fired = tick(now=now)

        assert fired == 1

    def test_tick_advances_next_run_at(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "w", "kind": "wait", "config": {"seconds": 0}}],
        )
        now = datetime.now(UTC)
        schedule = ScheduleFactory(
            pipeline=pipeline,
            owner=user,
            enabled=True,
            cron_expression="0 * * * *",
            next_run_at=now - timedelta(hours=1),
        )
        original_next = schedule.next_run_at

        with (
            patch("llm_lab.automation.services._celery_available", return_value=False),
            patch("llm_lab.automation.engine.runner.execute_run"),
        ):
            tick(now=now)

        schedule.refresh_from_db()
        assert schedule.next_run_at > original_next
        assert schedule.last_run_at is not None


# ---------------------------------------------------------------------------
# Dispatcher mocking
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDispatcherMocking:
    def test_generate_dispatcher_called(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [
                {
                    "name": "gen",
                    "kind": "generate",
                    "config": {"model_id": "m", "template_slug": "t"},
                },
            ],
        )
        run = _make_run_with_steps(pipeline)

        with patch(
            "llm_lab.automation.engine.dispatchers.dispatch_generate",
            return_value={"generation_job_id": "fake-id", "status": "completed"},
        ) as mock_gen:
            execute_run(run.id)

        mock_gen.assert_called_once()
        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED

    def test_analyze_dispatcher_called(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "ana", "kind": "analyze", "config": {"analyzers": ["static"]}}],
        )
        run = _make_run_with_steps(pipeline)

        with patch(
            "llm_lab.automation.engine.dispatchers.dispatch_analyze",
            return_value={"analysis_task_id": "fake-id", "status": "completed"},
        ) as mock_ana:
            execute_run(run.id)

        mock_ana.assert_called_once()
        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED

    def test_report_dispatcher_called(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "rep", "kind": "report", "config": {"report_type": "summary"}}],
        )
        run = _make_run_with_steps(pipeline)

        with patch(
            "llm_lab.automation.engine.dispatchers.dispatch_report",
            return_value={"report_id": "fake-id", "status": "completed"},
        ) as mock_rep:
            execute_run(run.id)

        mock_rep.assert_called_once()
        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED

    def test_script_noop(self):
        user = UserFactory()
        pipeline, _ = _make_pipeline_with_steps(
            user,
            [{"name": "scr", "kind": "script", "config": {"code": "noop"}}],
        )
        run = _make_run_with_steps(pipeline)
        execute_run(run.id)

        run.refresh_from_db()
        assert run.status == RunStatus.SUCCEEDED
        sr = run.step_runs.first()
        assert sr.output.get("operation") == "noop"
