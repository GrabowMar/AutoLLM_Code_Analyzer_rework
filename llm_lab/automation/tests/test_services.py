"""Tests for automation services."""

from __future__ import annotations

import pytest

from llm_lab.automation.models import Pipeline
from llm_lab.automation.models import PipelineRun
from llm_lab.automation.services import clone_pipeline
from llm_lab.automation.services import next_cron_time
from llm_lab.automation.services import trigger_run
from llm_lab.automation.services import validate_pipeline_dsl
from llm_lab.automation.tests.factories import PipelineFactory
from llm_lab.automation.tests.factories import PipelineStepFactory
from llm_lab.users.tests.factories import UserFactory


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
def test_validate_dsl_script_missing_code() -> None:
    config = {
        "steps": [
            {"id": "s1", "name": "S", "kind": "script", "config": {}, "depends_on": []},
        ],
    }
    errors = validate_pipeline_dsl(config)
    assert any("code" in e for e in errors)


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
