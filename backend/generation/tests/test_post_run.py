"""Experiment post-runs (spec §F.5): creation on completion, sequential analyses, the report, deferral."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.execution.models import ExecutorSettings
from backend.generation.models import Experiment
from backend.generation.models import ExperimentPostRun
from backend.generation.models import GenerationJob
from backend.generation.services import experiment_automation as automation
from backend.generation.services.experiments import ExperimentError
from backend.generation.services.experiments import normalize_post_run
from backend.generation.tests.test_experiments_v2_api import _graded
from backend.generation.tests.test_experiments_v2_api import _scaffolding_experiment
from backend.reports.models import Report
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"
EXECUTE = "backend.analysis.services.runner.execute"
S = GenerationJob.Status


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def tool():
    return AnalyzerTool.objects.get_or_create(slug="bandit", defaults={"name": "Bandit"})[0]


def _completed_experiment(user, *, post_run=None, n_apps=2):
    experiment, (a, b) = _scaffolding_experiment(user, n_apps=n_apps)
    apps = list(experiment.app_requirements.order_by("id"))
    jobs = {
        (a.id, apps[0].id): _graded(experiment, a, apps[0], 0, 1.0),
        (b.id, apps[0].id): _graded(experiment, b, apps[0], 0, 0.5),
    }
    if n_apps > 1:
        jobs[(a.id, apps[1].id)] = _graded(experiment, a, apps[1], 0, None, status=S.FAILED)
        jobs[(b.id, apps[1].id)] = _graded(experiment, b, apps[1], 0, 1.0)
    experiment.status = Experiment.Status.COMPLETED
    experiment.post_run = post_run or {}
    experiment.save(update_fields=["status", "post_run"])
    return experiment, jobs


def _fake_execute(statuses=None):
    statuses = statuses or {}

    def run(analysis_run):
        analysis_run.status = statuses.get(analysis_run.generation_job_id, AnalysisRun.Status.COMPLETED)
        analysis_run.completed_at = timezone.now()
        analysis_run.save(update_fields=["status", "completed_at"])
        return analysis_run

    return run


class TestCreation:
    def test_completion_creates_one_frozen_post_run_only_when_enabled(self, user, tool):
        experiment, _ = _completed_experiment(user)
        assert automation.on_experiment_completed(experiment) is None
        config = normalize_post_run({"analysis": {"enabled": True, "tool_slugs": ["bandit"]}}, "scaffolding")
        experiment.post_run = config
        experiment.save(update_fields=["post_run"])
        post_run = automation.on_experiment_completed(experiment)
        assert (post_run.trigger, post_run.status, post_run.config) == ("completed", "pending", config)
        # Later edits do not change the frozen copy.
        experiment.post_run = {**config, "report": {"enabled": True, "exclude_truncated": True}}
        experiment.save(update_fields=["post_run"])
        post_run.refresh_from_db()
        assert post_run.config["report"]["enabled"] is False

    def test_manual_post_run_refused_while_one_is_active(self, user):
        experiment, _ = _completed_experiment(user, post_run={"report": {"enabled": True}})
        first = automation.create_manual_post_run(experiment)
        assert first.trigger == "manual"
        with pytest.raises(ExperimentError, match="already"):
            automation.create_manual_post_run(experiment)

    def test_unknown_analyzer_is_refused(self):
        with pytest.raises(ExperimentError, match="Unknown analyzer"):
            normalize_post_run({"analysis": {"enabled": True, "tool_slugs": ["nope"]}}, "agent")
        with pytest.raises(ExperimentError, match="at least one analyzer"):
            normalize_post_run({"analysis": {"enabled": True, "tool_slugs": []}}, "agent")


class TestAdvance:
    def test_analyses_run_sequentially_for_jobs_without_one_then_the_report(self, user, tool):
        config = {
            "analysis": {"enabled": True, "tool_slugs": ["bandit"], "only_passing": False},
            "report": {"enabled": True, "exclude_truncated": True},
        }
        experiment, jobs = _completed_experiment(user, post_run=config)
        already = next(iter(jobs.values()))
        AnalysisRun.objects.create(created_by=user, generation_job=already, status=AnalysisRun.Status.COMPLETED)
        post_run = automation.on_experiment_completed(experiment)

        with mock.patch(EXECUTE, side_effect=_fake_execute()) as execute:
            done = automation.advance_post_runs("exec-1")
        assert done.id == post_run.id
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.COMPLETED, post_run.error
        # Three completed current jobs, one already analyzed; the failed job is skipped.
        assert execute.call_count == 2
        assert post_run.progress == {"analysis_done": 2, "analysis_total": 2, "analysis_failed": 0}
        assert len(post_run.analysis_run_ids) == 2
        assert (post_run.lease_owner, post_run.lease_expires_at) == ("", None)
        report = post_run.report
        assert (report.status, report.created_by_id, report.report_type) == (
            Report.Status.COMPLETED,
            user.id,
            "experiment_report",
        )
        results = report.report_data["results"]
        assert results["paired"]["pairing"] == "app_blocked"
        assert "comparisons" not in report.report_data
        # Nothing left to claim.
        assert automation.advance_post_runs("exec-1") is None

    def test_only_passing_filters_on_a_perfect_pass_rate(self, user, tool):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"], "only_passing": True}}
        experiment, jobs = _completed_experiment(user, post_run=config)
        automation.on_experiment_completed(experiment)
        with mock.patch(EXECUTE, side_effect=_fake_execute()) as execute:
            automation.advance_post_runs("exec-1")
        analyzed = {call.args[0].generation_job_id for call in execute.call_args_list}
        assert analyzed == {j.id for j in jobs.values() if (j.metrics.get("functional") or {}).get("pass_rate") == 1.0}

    def test_a_failed_analysis_is_counted_and_the_rest_continue(self, user, tool):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"]}}
        experiment, jobs = _completed_experiment(user, post_run=config)
        automation.on_experiment_completed(experiment)
        target = next(j for j in jobs.values() if j.status == S.COMPLETED)

        def flaky(analysis_run):
            if analysis_run.generation_job_id == target.id:
                msg = "container gone"
                raise RuntimeError(msg)
            return _fake_execute()(analysis_run)

        with mock.patch(EXECUTE, side_effect=flaky):
            post_run = automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.COMPLETED
        assert post_run.progress["analysis_failed"] == 1
        assert post_run.progress["analysis_done"] == 3
        assert AnalysisRun.objects.filter(generation_job=target).get().status == AnalysisRun.Status.FAILED

    def test_report_failure_is_recorded(self, user):
        experiment, _ = _completed_experiment(user, post_run={"report": {"enabled": True}})
        automation.on_experiment_completed(experiment)
        with mock.patch.dict(
            "backend.reports.services.generators.GENERATORS",
            {"experiment_report": mock.Mock(side_effect=RuntimeError("boom"))},
        ):
            post_run = automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.FAILED
        assert "boom" in post_run.error
        assert post_run.completed_at is not None

    def test_expired_leases_are_reclaimed(self, user):
        experiment, _ = _completed_experiment(user, post_run={"report": {"enabled": True}})
        stale = ExperimentPostRun.objects.create(
            experiment=experiment,
            trigger="manual",
            config=normalize_post_run({"report": {"enabled": True}}, "scaffolding"),
            status=ExperimentPostRun.Status.RUNNING,
            lease_owner="dead",
            lease_expires_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        post_run = automation.advance_post_runs("exec-2")
        assert post_run.id == stale.id
        stale.refresh_from_db()
        assert stale.status == ExperimentPostRun.Status.COMPLETED

    def test_draining_and_the_memory_rule_defer_post_runs(self, user, settings):
        experiment, jobs = _completed_experiment(user, post_run={"report": {"enabled": True}})
        automation.on_experiment_completed(experiment)
        executor_settings = ExecutorSettings.get()
        executor_settings.draining = True
        executor_settings.save()
        assert automation.advance_post_runs("exec-1") is None
        executor_settings.draining = False
        executor_settings.sandbox_slots, executor_settings.eval_slots = 1, 1
        executor_settings.save()

        # 4g only fits the analyzer workspace when the sandbox and eval lanes are not both busy.
        settings.APPS_DIND_MEM_LIMIT = "6g"
        busy = list(jobs.values())[:2]
        GenerationJob.objects.filter(id=busy[0].id).update(
            status=S.RUNNING,
            phase="running",
            lane="sandbox",
            queued_at=timezone.now(),
        )
        GenerationJob.objects.filter(id=busy[1].id).update(
            status=S.RUNNING,
            phase="evaluating",
            queued_at=timezone.now(),
        )
        assert automation.advance_post_runs("exec-1") is None
        settings.APPS_DIND_MEM_LIMIT = "16g"  # everything fits: no deferral
        assert automation.advance_post_runs("exec-1") is not None


def test_post_run_api(client, user, tool):
    client.force_login(user)
    experiment, _ = _completed_experiment(user)
    base = f"{API}/experiments/{experiment.id}/post-run/"
    body = client.get(base).json()
    assert body["config"]["evaluate"] is True
    assert body["runs"] == []
    res = client.post(f"{base}run/", data="{}", content_type="application/json")
    assert (res.status_code, res.json()["code"]) == (400, "post_run_disabled")
    spec = {
        "evaluate": True,
        "analysis": {"enabled": True, "tool_slugs": ["bandit"], "only_passing": False},
        "report": {"enabled": True, "exclude_truncated": True},
    }
    res = client.put(base, data=json.dumps(spec), content_type="application/json")
    assert res.status_code == 200, res.content
    assert res.json()["config"] == spec
    res = client.post(f"{base}run/", data="{}", content_type="application/json")
    assert (res.status_code, res.json()["trigger"], res.json()["status"]) == (200, "manual", "pending")
    assert client.post(f"{base}run/", data="{}", content_type="application/json").status_code == 400
    assert len(client.get(base).json()["runs"]) == 1
