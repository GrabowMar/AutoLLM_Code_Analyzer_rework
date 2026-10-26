"""Tests for automation services."""

from __future__ import annotations

import pytest

from backend.automation.models import Pipeline
from backend.automation.models import PipelineRun
from backend.automation.services import clone_pipeline
from backend.automation.services import next_cron_time
from backend.automation.services import trigger_run
from backend.automation.services import validate_pipeline_dsl
from backend.automation.tests.factories import PipelineFactory
from backend.automation.tests.factories import PipelineStepFactory
from backend.users.tests.factories import UserFactory


@pytest.mark.django_db
def test_validate_dsl_valid() -> None:
    config = {
        "steps": [
            {
                "id": "s1",
                "name": "Generate",
                "kind": "generate",
                "config": {"model_id": "gpt-4", "template_slug": "todo"},
                "depends_on": [],
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert errors == []


@pytest.mark.django_db
def test_validate_dsl_empty_steps() -> None:
    errors = validate_pipeline_dsl({"steps": []})
    assert errors == []


@pytest.mark.django_db
def test_validate_dsl_missing_steps_key() -> None:
    errors = validate_pipeline_dsl({})
    assert errors == []


@pytest.mark.django_db
def test_validate_dsl_generate_missing_model_id() -> None:
    config = {
        "steps": [
            {
                "id": "s1",
                "name": "Gen",
                "kind": "generate",
                "config": {"template_slug": "todo"},
                "depends_on": [],
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("model_id" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_generate_missing_template_slug() -> None:
    config = {
        "steps": [
            {
                "id": "s1",
                "name": "Gen",
                "kind": "generate",
                "config": {"model_id": "gpt-4"},
                "depends_on": [],
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("template_slug" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_missing_name() -> None:
    config = {
        "steps": [
            {
                "id": "s1",
                "kind": "generate",
                "config": {"model_id": "x", "template_slug": "y"},
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("name" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_missing_kind() -> None:
    config = {"steps": [{"id": "s1", "name": "S"}]}
    errors = validate_pipeline_dsl(config)
    assert any("kind" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_unknown_depends_on() -> None:
    config = {
        "steps": [
            {
                "id": "s1",
                "name": "Gen",
                "kind": "generate",
                "config": {"model_id": "x", "template_slug": "y"},
                "depends_on": ["non-existent-id"],
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("depends_on" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_duplicate_step_id() -> None:
    step = {
        "id": "s1",
        "name": "Gen",
        "kind": "generate",
        "config": {"model_id": "x", "template_slug": "y"},
        "depends_on": [],
    }
    errors = validate_pipeline_dsl({"steps": [step, step]})
    assert any("duplicate" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_script_no_required_config() -> None:
    """Script steps default to noop in the dispatcher; nothing is required at validation."""
    config = {
        "steps": [
            {"id": "s1", "name": "S", "kind": "script", "config": {}, "depends_on": []},
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert errors == []


@pytest.mark.django_db
def test_validate_dsl_unknown_kind() -> None:
    config = {
        "steps": [
            {"name": "x", "kind": "magic", "config": {}, "depends_on": []},
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("unknown kind" in e for e in errors)


@pytest.mark.django_db
def test_validate_dsl_depends_on_by_name() -> None:
    """The visual editor stores depends_on as step names — must be accepted."""
    config = {
        "steps": [
            {
                "name": "gen",
                "kind": "generate",
                "config": {"model_id": "x", "template_slug": "y"},
                "depends_on": [],
            },
            {
                "name": "analyze",
                "kind": "analyze",
                "config": {"analyzers": ["bandit"]},
                "depends_on": ["gen"],
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert errors == []


@pytest.mark.django_db
def test_validate_dsl_duplicate_step_name() -> None:
    config = {
        "steps": [
            {
                "name": "step_1",
                "kind": "generate",
                "config": {"model_id": "x", "template_slug": "y"},
                "depends_on": [],
            },
            {
                "name": "step_1",
                "kind": "wait",
                "config": {"seconds": 5},
                "depends_on": [],
            },
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("duplicate step name" in e for e in errors)


@pytest.mark.django_db
def test_clone_pipeline_creates_new() -> None:
    pipeline = PipelineFactory.create()
    PipelineStepFactory.create(pipeline=pipeline, name="Step A")
    cloned = clone_pipeline(pipeline, "Cloned Pipeline")
    assert cloned.id != pipeline.id
    assert cloned.name == "Cloned Pipeline"
    assert cloned.status == Pipeline.Status.DRAFT
    assert cloned.steps.count() == 1


@pytest.mark.django_db
def test_clone_pipeline_deep_copies_config() -> None:
    pipeline = PipelineFactory.create(config={"steps": [], "meta": "original"})
    cloned = clone_pipeline(pipeline, "Clone")
    cloned.config["meta"] = "changed"
    assert pipeline.config["meta"] == "original"


def test_next_cron_time_returns_datetime() -> None:
    from datetime import datetime

    result = next_cron_time("0 * * * *")
    assert isinstance(result, datetime)


@pytest.mark.django_db
def test_trigger_run_creates_pending_run() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create()
    PipelineStepFactory.create(pipeline=pipeline)
    run = trigger_run(pipeline, {"key": "value"}, user)
    assert run.status == "pending"
    assert run.pipeline == pipeline
    assert run.triggered_by == user
    assert PipelineRun.objects.filter(id=run.id).exists()


@pytest.mark.django_db
def test_trigger_run_creates_step_runs() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create()
    PipelineStepFactory.create(pipeline=pipeline, order=0)
    PipelineStepFactory.create(pipeline=pipeline, order=1)
    run = trigger_run(pipeline, {}, user)
    assert run.step_runs.count() == 2


def _single_step(kind: str, config: dict) -> dict:
    return {"steps": [{"name": "s", "kind": kind, "config": config, "depends_on": []}]}


@pytest.mark.parametrize(
    "config",
    [
        {"analyzers": ["bandit"]},
        {"tool_slugs": ["ruff", "ai_review"], "thresholds": {"high": 0, "total": 10}},
        {"profile": "research-default"},
        {"profile_id": "6f1c0a5e-1111-4a4a-9c9c-000000000000", "analyzers": []},
        {"tool_slugs": "{{params.tools}}", "timeout_seconds": 600},
        {"profile": "quick-check", "thresholds": {"critical": "{{params.max_critical}}"}},
    ],
)
@pytest.mark.django_db
def test_validate_dsl_accepts_runnable_analyze_steps(config) -> None:
    assert validate_pipeline_dsl(_single_step("analyze", config)) == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "config.profile or config.tool_slugs is required"),
        ({"analyzers": []}, "config.analyzers is empty"),
        ({"tool_slugs": []}, "config.tool_slugs is empty"),
        ({"tool_slugs": "bandit"}, "must be a list of tool slugs"),
        ({"tool_slugs": ["bandit", ""]}, "must be a list of tool slugs"),
        ({"tool_slugs": ["bandit"], "thresholds": {"severe": 1}}, "unknown severity"),
        ({"tool_slugs": ["bandit"], "thresholds": {"high": -1}}, "non-negative integer"),
        ({"tool_slugs": ["bandit"], "thresholds": [0]}, "thresholds must map"),
        ({"tool_slugs": ["bandit"], "timeout_seconds": 0}, "timeout_seconds"),
        ({"tool_slugs": ["bandit"], "source_code": "print(1)"}, "source_code must map"),
    ],
)
@pytest.mark.django_db
def test_validate_dsl_rejects_analyze_steps_that_measure_nothing(config, message) -> None:
    errors = validate_pipeline_dsl(_single_step("analyze", config))
    assert any(message in e for e in errors), errors


@pytest.mark.django_db
def test_validate_dsl_report_type_must_be_a_generator() -> None:
    assert validate_pipeline_dsl(_single_step("report", {"report_type": "model_analysis"})) == []
    assert any("report_type is required" in e for e in validate_pipeline_dsl(_single_step("report", {})))
    errors = validate_pipeline_dsl(_single_step("report", {"report_type": "summary"}))
    assert any("'summary' is not one of" in e for e in errors)


def test_pipeline_task_has_explicit_time_limits() -> None:
    from backend.automation import tasks

    task = tasks.run_pipeline_task
    # Far above the global 60 s / 300 s defaults: pipelines wait on generation + analysis.
    assert task.soft_time_limit == tasks.PIPELINE_SOFT_TIME_LIMIT
    assert task.time_limit == tasks.PIPELINE_TIME_LIMIT
    assert task.soft_time_limit >= 4 * 3600
    assert task.time_limit > task.soft_time_limit


def test_long_acks_late_tasks_fit_in_the_broker_visibility_timeout() -> None:
    """Redis redelivers an unacknowledged message after the visibility timeout (default 1 h): a healthy
    long acks_late task must end (at worst at its hard limit) before that, or a second worker starts it."""
    from django.conf import settings

    from backend.analysis import tasks as analysis_tasks
    from backend.automation import tasks

    timeout = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]
    for task in (tasks.run_pipeline_task, analysis_tasks.process_run_task):
        assert task.acks_late is True
        assert task.time_limit < timeout, task.name


@pytest.mark.django_db
def test_soft_time_limit_fails_the_run_and_cancels_pending_steps(monkeypatch) -> None:
    from celery.exceptions import SoftTimeLimitExceeded

    from backend.automation.engine import runner
    from backend.automation.models import PipelineStepRun
    from backend.automation.tests.factories import PipelineRunFactory
    from backend.automation.tests.factories import PipelineStepRunFactory

    run = PipelineRunFactory(status="pending")
    step_run = PipelineStepRunFactory(run=run, step=PipelineStepFactory(pipeline=run.pipeline, kind="wait"))

    def limit(run_id):
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(runner, "_is_cancelled", limit)
    monkeypatch.setattr(runner.connection, "close", lambda: None)

    runner.execute_run(run.id)

    run.refresh_from_db()
    assert run.status == "failed"
    assert run.error == "Pipeline exceeded its time limit"
    assert PipelineStepRun.objects.get(id=step_run.id).status == "cancelled"


@pytest.mark.django_db
def test_step_cancelled_in_flight_stays_cancelled_and_is_not_retried(monkeypatch) -> None:
    from backend.automation.engine import runner
    from backend.automation.models import PipelineRun
    from backend.automation.models import PipelineStepRun
    from backend.automation.tests.factories import PipelineRunFactory
    from backend.automation.tests.factories import PipelineStepRunFactory

    run = PipelineRunFactory(status="running")
    step_run = PipelineStepRunFactory(
        run=run,
        step=PipelineStepFactory(pipeline=run.pipeline, kind="analyze", config={"tool_slugs": ["bandit"]}),
        retries_remaining=2,
    )
    calls = []

    def dispatcher(sr, params, run_params):
        calls.append(sr.id)
        # the user cancels the pipeline while the analysis is being awaited
        PipelineRun.objects.filter(id=run.id).update(status="cancelled")
        PipelineStepRun.objects.filter(id=sr.id).update(status="cancelled")
        return {"status": "running", "error": "Pipeline cancelled; analysis cancellation requested"}

    monkeypatch.setattr(runner, "_get_dispatcher", lambda kind: dispatcher)
    monkeypatch.setattr(runner.connection, "close", lambda: None)

    runner.execute_step(step_run.id)

    step_run.refresh_from_db()
    assert calls == [step_run.id]
    assert step_run.status == "cancelled"
    assert step_run.retries_remaining == 2
    assert "Pipeline cancelled" in step_run.error
