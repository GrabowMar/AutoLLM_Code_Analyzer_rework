"""WP-10 "done when": the Done-when design launches and runs through the WP-08 runner with FakeHarness.

The chain exercised here is launch -> frozen manifest -> claim -> ``agentic.dispatch.run_stage`` (FakeHarness
in a ``LocalSandboxBackend`` behind a FakeGateway) -> eval stage (FakeEvaluator) -> ``transitions.finish_stage``
-> ``runs.queries.refresh_current``; then the cell state, ``current_runs`` and the results' ``by_factor`` and
attrition are checked. No outcome is written by hand.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone

from backend.execution import transitions
from backend.generation.agentic import dispatch
from backend.generation.agentic import runner
from backend.generation.contracts.outcome import EvalSummary
from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models import StudyCell
from backend.generation.models import choices
from backend.generation.runs.queries import attrition
from backend.generation.runs.queries import current_runs
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study.results import compute
from backend.generation.testing.fake_agentic import LocalSandboxBackend
from backend.generation.testing.fake_eval import FakeEvaluator
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.testing.fake_gateway import gateway_config
from backend.generation.testing.fake_harness import FakeHarness
from backend.generation.testing.fake_harness import FakeScript
from backend.generation.testing.golden_library import parent_app_files

pytestmark = pytest.mark.django_db

EXECUTOR = "executor-wp10"


@pytest.fixture
def fake_harness(monkeypatch) -> FakeScript:
    """Every harness of the plan runs as FakeHarness; the gateway config is the fake's (no real upstream)."""
    script = FakeScript(files=parent_app_files(), calls=[canned_call(1), canned_call(2)])
    monkeypatch.setattr(runner, "get_adapter", lambda _name: FakeHarness(script))

    def build(run: GenerationRun, *, attempt: int | None = None) -> Any:
        return gateway_config(run_id=str(run.pk), session_id=str(run.pk), attempt=attempt or run.attempt + 1)

    monkeypatch.setattr("backend.generation.budget.gateway_config.build", build)
    return script


def _claim(run: GenerationRun) -> GenerationRun:
    """What the scheduler's claim leaves on the row (the claim itself is WP-11/execution's, tested there)."""
    now = timezone.now()
    GenerationRun.objects.filter(pk=run.pk).update(
        status=choices.RunStatus.RUNNING,
        phase=choices.RunPhase.RUNNING,
        lease_owner=EXECUTOR,
        lease_expires_at=now + timezone.timedelta(minutes=5),
        queued_at=now,
        started_at=now,
    )
    run.refresh_from_db()
    return run


def _evaluate(run: GenerationRun, suite: Any) -> None:
    """The eval lane for a run in ``eval_queued`` (WP-09's stage, here with FakeEvaluator)."""
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


def test_done_when_design_launches_and_runs_a_block_with_fake_harness(
    make_study,
    golden,
    fake_study,
    fake_harness,
    django_capture_on_commit_callbacks,
):
    study = make_study()  # 2 stacks x 2 styles x 2 models x 2 tasks x 2 repeats
    with django_capture_on_commit_callbacks(execute=True):
        out = svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    study.refresh_from_db()
    assert (out["status"], out["created_runs"]) == ("running", 32)
    assert fake_study.enqueued
    assert len(fake_study.enqueued[0]) == 32

    first_block = min(StudyCell.objects.filter(study=study).values_list("block_index", flat=True))
    block = list(
        GenerationRun.objects.filter(study=study, cell__block_index=first_block).order_by("sequence"),
    )
    assert len(block) == 8  # one (repeat, unit) block holds every condition
    suite = golden.get("eval_suite", "eval-v2")
    backend = LocalSandboxBackend()
    for run in block:
        dispatch.run_stage(_claim(run), backend=backend)
        run.refresh_from_db()
        assert (run.status, run.phase) == (choices.RunStatus.RUNNING, choices.RunPhase.EVAL_QUEUED), run.result
        assert RunAttempt.objects.filter(run=run, attempt=1, termination="completed").exists()
        _evaluate(run, suite)

    for run in block:
        run.refresh_from_db()
        assert (run.status, run.outcome, run.usable) == (choices.RunStatus.COMPLETED, "tested", True)
        cell = StudyCell.objects.get(pk=run.cell_id)
        assert (cell.state, cell.current_run_id) == (choices.CellState.DONE, run.pk)
    others = StudyCell.objects.filter(study=study).exclude(block_index=first_block)
    assert set(others.values_list("state", flat=True)) == {choices.CellState.QUEUED}
    assert set(current_runs(study).filter(outcome="tested").values_list("pk", flat=True)) == {r.pk for r in block}
    assert current_runs(study).count() == 32  # every cell has its (queued) attempt 1

    res = compute(study)
    factors = res["by_factor"]["factors"]
    assert {"model", "stack_profile", "style"} <= set(factors)
    for levels in (factors["model"], factors["stack_profile"], factors["style"]):
        assert len(levels) == 2
        # Every level has a measured condition from the finished block; nothing is reported as 0 for the rest.
        assert all(row["n_measured_conditions"] >= 1 and row["emm"] is not None for row in levels.values())
    board = res["leaderboard"]
    assert sum(row["n_graded"] for row in board) == 8
    assert sum(row["n_missing"]["pending"] for row in board) == 24
    rows = attrition(study)
    assert len(rows) == 8
    assert sum(row["runs_by_outcome"].get("tested", 0) for row in rows) == 8
    assert sum(row["usable"] for row in rows) == 8
    assert sum(row["cells_without_run"] for row in rows) == 0
    assert all(row["planned_cells"] == 4 and row["infra_error"] == 0 for row in rows)
