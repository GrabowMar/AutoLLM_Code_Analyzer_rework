"""Run accounting: planned vs executed tool runs, evaluated apps."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics.services import accounting
from backend.statistics.services import study
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _run(job, tools, *, status=AnalysisRun.Status.COMPLETED, results=True):
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=status, tool_slugs=list(tools))
    if results:
        for tool in tools:
            ToolResultFactory(run=run, tool_slug=tool)
    return run


def _job(user, **kw):
    return GenerationJobFactory(
        created_by=user,
        model=LLMModelFactory(),
        status=GenerationJob.Status.COMPLETED,
        **kw,
    )


def test_partial_rerun_is_not_counted_as_unplanned_executions():
    user = UserFactory()
    job = _job(user)
    _run(job, ["bandit", "ruff", "semgrep"])
    _run(job, ["bandit"])  # re-run one tool; the other two results still count

    data = accounting.run_accounting(study.study_jobs(user))

    assert data["executions"]["static"] == 3
    assert data["executions"]["planned_static"] == 3


def test_planned_tool_that_never_produced_a_result_is_a_gap():
    user = UserFactory()
    job = _job(user)
    run = _run(job, ["bandit"])
    run.tool_slugs = ["bandit", "zap"]
    run.save(update_fields=["tool_slugs"])
    # A cancelled run's selection was never carried out: it is not planned work.
    _run(job, ["trivy"], status=AnalysisRun.Status.CANCELLED, results=False)

    data = accounting.run_accounting(study.study_jobs(user))

    assert data["executions"]["planned_static"] == 2
    assert data["executions"]["static"] == 1


def test_failed_run_selection_counts_as_planned():
    user = UserFactory()
    job = _job(user)
    run = _run(job, ["bandit", "ruff"], status=AnalysisRun.Status.FAILED, results=False)
    ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.FAILED)

    data = accounting.run_accounting(study.study_jobs(user))

    assert data["executions"]["planned_static"] == 2
    assert data["executions"]["static"] == 1


def test_not_applicable_evaluation_is_not_counted_as_evaluated():
    user = UserFactory()
    for status in ("passed", "failed", "build_failed", "infra_error", "not_applicable"):
        _job(user, result_data={"evaluation": {"status": status}})

    data = accounting.run_accounting(study.study_jobs(user))

    conformance = data["evaluations"]["conformance"]
    assert conformance["executions"] == 5
    assert conformance["not_applicable"] == 1
    assert conformance["infra_error"] == 1
    assert conformance["evaluated"] == 3
    assert data["apps"]["evaluated"] == 3
    assert data["apps"]["deployed"] == 2
