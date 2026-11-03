"""Post-run analyses: which apps still need which analyzers, resuming, and a crashing tool."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from backend.generation.tests.test_post_run import EXECUTE
from backend.generation.tests.test_post_run import _completed_experiment
from backend.generation.tests.test_post_run import _fake_execute
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import ToolResult
from backend.analysis.services import runner
from backend.generation.models import ExperimentPostRun
from backend.generation.models import GenerationJob
from backend.generation.services import experiment_automation as automation
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = GenerationJob.Status


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def tools():
    for slug in ("bandit", "ruff"):
        AnalyzerTool.objects.get_or_create(slug=slug, defaults={"name": slug.title()})


def _config(*slugs):
    return {"analysis": {"enabled": True, "tool_slugs": list(slugs), "only_passing": False}}


def _measured(user, job, run_status=AnalysisRun.Status.COMPLETED, **results):
    """A finished run of *job* with one result per ``slug=status`` (``"na"``: skipped as not applicable)."""
    run = AnalysisRun.objects.create(created_by=user, generation_job=job, status=run_status, tool_slugs=list(results))
    for slug, status in results.items():
        if status == "na":
            ToolResult.objects.create(
                run=run,
                tool_slug=slug,
                status="skipped",
                summary={"skip_reason": "not_applicable"},
            )
        else:
            ToolResult.objects.create(run=run, tool_slug=slug, status=status)
    return run


def _completed_jobs(jobs):
    return [j for j in jobs.values() if j.status == S.COMPLETED]


def test_only_measured_tools_count_as_analyzed(user, tools):
    experiment, jobs = _completed_experiment(user, post_run=_config("bandit", "ruff"))
    done, partial, failed = _completed_jobs(jobs)
    _measured(user, done, bandit="completed", ruff="na")  # not applicable is a measurement
    _measured(user, partial, bandit="completed")  # an ad-hoc bandit-only run
    # The whole run failed (workspace down): it measured nothing.
    _measured(user, failed, run_status=AnalysisRun.Status.FAILED, bandit="failed", ruff="failed")

    targets = automation.analysis_targets(experiment, only_passing=False, tool_slugs=["bandit", "ruff"])
    assert {t.id: t.missing_tool_slugs for t in targets} == {partial.id: ["ruff"], failed.id: ["bandit", "ruff"]}


def test_post_run_runs_only_the_missing_tools(user, tools):
    experiment, jobs = _completed_experiment(user, post_run=_config("bandit", "ruff"))
    done, partial, _ = _completed_jobs(jobs)
    _measured(user, done, bandit="completed", ruff="completed")
    _measured(user, partial, ruff="completed")
    post_run = automation.on_experiment_completed(experiment)

    with mock.patch(EXECUTE, side_effect=_fake_execute()) as execute:
        automation.advance_post_runs("exec-1")
    post_run.refresh_from_db()
    assert post_run.status == ExperimentPostRun.Status.COMPLETED, post_run.error
    slugs = {call.args[0].generation_job_id: call.args[0].tool_slugs for call in execute.call_args_list}
    # The untouched job gets every configured tool, in configured order.
    assert slugs[partial.id] == ["bandit"]
    assert sorted(slugs.values()) == [["bandit"], ["bandit", "ruff"]]
    assert post_run.progress["analysis_total"] == 2


def test_resumed_post_run_reanalyzes_the_job_its_dead_executor_left_running(user, tools):
    experiment, jobs = _completed_experiment(user, post_run=_config("bandit"))
    victim, *others = _completed_jobs(jobs)
    for job in others:
        _measured(user, job, bandit="completed")
    old = timezone.now() - timedelta(minutes=2)
    orphan = AnalysisRun.objects.create(
        created_by=user,
        generation_job=victim,
        status=AnalysisRun.Status.RUNNING,
        tool_slugs=["bandit"],
        started_at=old,
        heartbeat_at=old,
    )
    ToolResult.objects.create(run=orphan, tool_slug="bandit", status="running")
    ExperimentPostRun.objects.create(
        experiment=experiment,
        trigger="completed",
        config=_config("bandit"),
        status=ExperimentPostRun.Status.RUNNING,
        lease_owner="dead",
        lease_expires_at=timezone.now() - timedelta(seconds=5),
        analysis_run_ids=[str(orphan.id)],
    )

    with mock.patch(EXECUTE, side_effect=_fake_execute()) as execute:
        post_run = automation.advance_post_runs("exec-2")
    orphan.refresh_from_db()
    assert orphan.status == AnalysisRun.Status.FAILED
    assert orphan.error_message == runner.INTERRUPTED_MESSAGE
    assert orphan.results.get().status == "failed"
    assert [call.args[0].generation_job_id for call in execute.call_args_list] == [victim.id]
    post_run.refresh_from_db()
    assert post_run.analysis_run_ids[0] == str(orphan.id)
    assert len(post_run.analysis_run_ids) == 2


def test_a_crashing_tool_in_a_post_run_leaves_no_result_running(user, monkeypatch):
    AnalyzerTool.objects.update_or_create(slug="fixture-ai", defaults={"name": "AI", "kind": "ai", "parser_key": ""})
    AnalyzerTool.objects.get_or_create(slug="bandit", defaults={"name": "Bandit"})
    experiment, jobs = _completed_experiment(user, post_run=_config("fixture-ai"))
    for job in _completed_jobs(jobs):
        GenerationJob.objects.filter(id=job.id).update(result_data={"files": {"app.py": "x = 1\n"}})
    automation.on_experiment_completed(experiment)

    def broken_review(*args, **kwargs):
        msg = "unexpected provider response"
        raise TypeError(msg)

    monkeypatch.setattr(runner.ai_runner, "run", broken_review)
    post_run = automation.advance_post_runs("exec-1")
    post_run.refresh_from_db()

    assert post_run.status == ExperimentPostRun.Status.COMPLETED, post_run.error
    runs = AnalysisRun.objects.filter(id__in=post_run.analysis_run_ids)
    assert runs.count() == 3
    assert set(runs.values_list("status", flat=True)) == {AnalysisRun.Status.FAILED}
    results = ToolResult.objects.filter(run__in=runs)
    assert set(results.values_list("status", flat=True)) == {"failed"}
    assert all(r.error_message.startswith("Internal error: TypeError") for r in results)
    assert post_run.progress["analysis_failed"] == 3
