"""WP-08 "done when": FakeHarness + FakeGateway + FakeEval give a ``tested`` run with RunFiles, RunManifest,
RunAttempt and RunEvaluation, and ``RunView`` returns consistent data."""

from __future__ import annotations

from typing import Any

import pytest

from backend.execution import transitions
from backend.generation.agentic import dispatch
from backend.generation.contracts.outcome import EvalSummary
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import RunManifest
from backend.generation.models import choices
from backend.generation.runs.view import run_view
from backend.generation.testing.fake_eval import FakeEvaluator
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.testing.golden_library import parent_app_files

pytestmark = pytest.mark.django_db(transaction=False)


def fake_eval_stage(run: GenerationRun, suite: Any) -> RunEvaluation:
    """What the eval lane (WP-09) does for a run in ``eval_queued``: evaluate, mirror, end ``tested``."""
    GenerationRun.objects.filter(pk=run.pk).update(phase=choices.RunPhase.EVALUATING, lease_owner="eval-worker")
    run.refresh_from_db()
    evaluation = FakeEvaluator().evaluate(run, suite)
    summary = EvalSummary(
        eval_suite=suite.pinned_ref,
        eval_level=evaluation.eval_level,
        status=evaluation.status,
        measured=True,
        agent_pass_rate=evaluation.result["metrics"]["agent_pass_rate"],
        evaluation_id=evaluation.pk,
    )
    run.metrics = {**run.metrics, "eval": summary.model_dump(mode="json")}
    run.outcome, run.outcome_reason = choices.Outcome.TESTED, ""
    assert transitions.finish_stage(run, transitions.OUTCOME_DONE)
    return evaluation


def test_tested_run_end_to_end(agentic_env) -> None:
    agentic_env.script.files = parent_app_files()
    agentic_env.script.calls = [canned_call(1), canned_call(2, finish_reason="length")]
    run = agentic_env.make_run()

    dispatch.run_stage(run, backend=agentic_env.backend)

    run.refresh_from_db()
    assert run.status == choices.RunStatus.RUNNING
    assert run.phase == choices.RunPhase.EVAL_QUEUED
    assert run.outcome == ""
    assert run.tree_hash
    assert run.workspace_blob
    attempt = RunAttempt.objects.get(run=run, attempt=1)
    assert attempt.termination == "completed"
    assert attempt.usage["requests_by_class"] == {"main": 2}
    assert attempt.usage["call_truncations"] == 1
    assert attempt.served["providers"] == {"FakeProvider": 2}
    assert attempt.trajectory_blob
    assert attempt.observed_inputs["calls_by_class"] == {"main": 2}
    assert GenerationArtifact.objects.filter(run=run, attempt=1).count() == 2
    origins = set(RunFile.objects.filter(run=run).values_list("origin", flat=True))
    assert {"starter", "modified", "authored"} <= origins
    assert run.metrics["loc_model"] > 0
    assert run.result["seed_tree_hash"]
    assert any(note["code"] == "call_truncations:1" for note in run.validity["notes"])
    steps = [step["step"] for step in run.result["steps"]]
    assert steps[:5] == ["resolve", "images", "workspace", "gateway", "sandbox"]

    suite = agentic_env.golden.get("eval_suite", "eval-v2")
    evaluation = fake_eval_stage(run, suite)

    run.refresh_from_db()
    assert run.status == choices.RunStatus.COMPLETED
    assert run.outcome == choices.Outcome.TESTED
    assert run.usable is True
    manifest = RunManifest.objects.get(run=run)
    view = run_view(run)
    assert view.outcome == "tested"
    assert view.usable is True
    assert view.fingerprints == {k: manifest.manifest["identity"][k] for k in view.fingerprints}
    assert view.latest_evaluation(suite.pinned_ref).pk == evaluation.pk
    assert view.manifest()["harness"]["name"] == "fake"
    model_files = view.code_files("model")
    assert "backend/todos.py" in model_files
    assert all(entry.origin in {"authored", "modified"} for entry in view.files("model"))
    assert view.loc("model") == run.metrics["loc_model"]
    assert view.cost_usd == pytest.approx(run.metrics["cost_usd"])
