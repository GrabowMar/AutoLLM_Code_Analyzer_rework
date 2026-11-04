"""The generate step launches an ad-hoc study through ``study.launch.launch_adhoc`` (spec 9.4)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.automation.engine import dispatchers
from backend.automation.tests.factories import PipelineStepRunFactory
from backend.generation.models.choices import RunStatus
from backend.generation.testing.content import design_content
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import StudyFactory

pytestmark = pytest.mark.django_db

_LAUNCH = "backend.generation.study.launch.launch_adhoc"


def test_generate_launches_the_design_and_waits_for_its_runs():
    step_run = PipelineStepRunFactory()
    study = StudyFactory()
    runs = [GenerationRunFactory(study=study, tested=True), GenerationRunFactory(study=study, status=RunStatus.FAILED)]
    launched = {"study_id": study.id, "plan_hash": "f" * 64, "run_ids": [run.id for run in runs]}

    with patch(_LAUNCH, return_value=launched) as launch:
        output = dispatchers.dispatch_generate(step_run, {"design": design_content()}, {})

    design = launch.call_args.args[0]
    assert design.mode == "app"
    assert launch.call_args.kwargs["launch"] is True
    assert output["status"] == "completed"
    assert output["study_id"] == str(study.id)
    assert output["generation_run_ids"] == [str(run.id) for run in runs]
    assert output["generation_run_id"] == str(runs[0].id)
    assert output["run_statuses"] == {"completed": 1, "failed": 1}


def test_generate_without_a_design_fails_without_launching():
    with patch(_LAUNCH) as launch:
        output = dispatchers.dispatch_generate(PipelineStepRunFactory(), {"model_id": "x/y"}, {})

    assert output["status"] == "failed"
    assert "design" in output["error"]
    launch.assert_not_called()


def test_generate_rejects_an_invalid_design():
    with patch(_LAUNCH) as launch:
        output = dispatchers.dispatch_generate(PipelineStepRunFactory(), {"design": {"mode": "app"}}, {})

    assert output["status"] == "failed"
    assert output["error"].startswith("invalid design")
    launch.assert_not_called()


def test_generate_fails_when_no_run_completed():
    run = GenerationRunFactory(status=RunStatus.CANCELLED)
    with patch(_LAUNCH, return_value={"study_id": None, "run_ids": [run.id]}):
        output = dispatchers.dispatch_generate(PipelineStepRunFactory(), {"design": design_content()}, {})

    assert output["status"] == "failed"
    assert output["run_statuses"] == {"cancelled": 1}


def test_generate_times_out_on_runs_that_never_finish():
    run = GenerationRunFactory(status=RunStatus.RUNNING)
    with (
        patch(_LAUNCH, return_value={"study_id": None, "run_ids": [run.id]}),
        patch.object(dispatchers, "_POLL_INTERVAL", 0),
    ):
        output = dispatchers.dispatch_generate(
            PipelineStepRunFactory(),
            {"design": design_content(), "timeout_seconds": 0.01},
            {},
        )

    assert output["status"] == "failed"
    assert "not finished" in output["error"]
