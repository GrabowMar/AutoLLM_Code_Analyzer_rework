"""Run accounting: planned vs executed tool runs, evaluated apps."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.testing import factories as gf
from backend.statistics.services import accounting
from backend.statistics.services.runs import study_runs
from backend.statistics.tests.helpers import analyze
from backend.statistics.tests.helpers import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_partial_rerun_is_not_counted_as_unplanned_executions():
    user = UserFactory()
    run = make_run(user)
    analyze(run, ["bandit", "ruff", "semgrep"])
    analyze(run, ["bandit"])  # re-run one tool; the other two results still count

    data = accounting.run_accounting(study_runs(user))

    assert data["executions"]["static"] == 3
    assert data["executions"]["planned_static"] == 3


def test_planned_tool_that_never_produced_a_result_is_a_gap():
    user = UserFactory()
    run = make_run(user)
    analysis = analyze(run, ["bandit"])
    analysis.tool_slugs = ["bandit", "zap"]
    analysis.save(update_fields=["tool_slugs"])
    # A cancelled analysis's selection was never carried out: it is not planned work.
    analyze(run, ["trivy"], status=AnalysisRun.Status.CANCELLED, results=False)

    data = accounting.run_accounting(study_runs(user))

    assert data["executions"]["planned_static"] == 2
    assert data["executions"]["static"] == 1


def test_failed_run_selection_counts_as_planned():
    user = UserFactory()
    run = make_run(user)
    analysis = analyze(run, ["bandit", "ruff"], status=AnalysisRun.Status.FAILED, results=False)
    ToolResultFactory(run=analysis, tool_slug="bandit", status=ToolResult.Status.FAILED)

    data = accounting.run_accounting(study_runs(user))

    assert data["executions"]["planned_static"] == 2
    assert data["executions"]["static"] == 1


def test_not_measured_and_errored_evaluations_are_not_counted_as_evaluated():
    user = UserFactory()
    built = {stage: {"status": "ok"} for stage in ("lock", "build", "start", "tester")}
    gf.RunEvaluationFactory(run=make_run(user), measured=True)
    gf.RunEvaluationFactory(run=make_run(user), measured=True)
    gf.RunEvaluationFactory(
        run=make_run(user),
        status="not_measured",
        stages={"lock": {"status": "ok"}, "build": {"status": "fail"}},
    )
    gf.RunEvaluationFactory(run=make_run(user), status="error", stages=built)
    # Re-evaluated: the newest row is the run's latest evaluation; a retest only feeds test-retest agreement.
    retested = make_run(user)
    first = gf.RunEvaluationFactory(run=retested, status="error")
    gf.RunEvaluationFactory(run=retested, measured=True, stages=built)
    gf.RunEvaluationFactory(run=retested, measured=True, retest_of=first)

    data = accounting.run_accounting(study_runs(user))

    suite = data["evaluations"]["suite"]
    assert suite["executions"] == 6
    assert suite["retests"] == 1
    assert suite["runs"] == 5
    assert (suite["not_measured"], suite["error"]) == (1, 1)
    assert suite["evaluated"] == 3
    assert suite["not_deployed"] == 1
    assert data["apps"]["evaluated"] == 3
    assert (data["apps"]["runs"], data["apps"]["usable"]) == (5, 5)
    assert data["executions"]["dynamic"] == 6
