"""Experiment post-runs (spec §F.5): creation on completion, v2 analyses, the report, deferral.

The analysis engine is faked at its public boundary (``plan.freeze_plan`` /
``validate_profile_config`` / ``default_profile_config``, ``catalog.load_specs``,
``executor.submit_run`` / ``request_cancel``), as in the automation analyze-step tests.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisProfileFactory
from backend.execution.models import ExecutorSettings
from backend.generation.models import Experiment
from backend.generation.models import ExperimentPostRun
from backend.generation.models import GenerationJob
from backend.generation.services import experiment_automation as automation
from backend.generation.services.experiments import ExperimentError
from backend.generation.services.experiments import normalize_post_run
from backend.generation.tests.test_experiments_v2_api import _graded
from backend.generation.tests.test_experiments_v2_api import _scaffolding_experiment
from backend.rankings.tests.measured import fake_engine  # noqa: F401 - fixture
from backend.rankings.tests.measured import make_run
from backend.reports.models import Report
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"
S = GenerationJob.Status
DEFAULT_CONFIG = {
    "tools": [{"slug": "ruff", "required": True, "config": {}}, {"slug": "bandit", "required": True, "config": {}}],
    "reviewer": {"enabled": False},
    "scope": {},
    "outcomes": {},
}


class FakeEngine:
    """Records frozen plans and submissions; a run's final status is configurable per job."""

    def __init__(self):
        self.frozen: list[dict] = []
        self.submitted: list = []
        self.cancelled: list = []
        self.final: dict = {}
        self.fail_submit: set = set()
        self.initial_status = None

    def freeze_plan(self, *, profile=None, config=None, user=None):
        config = profile.config if profile is not None else config
        plan = {"schema_version": 1, "tools": [{"slug": t["slug"]} for t in config.get("tools", [])], "reviewer": None}
        self.frozen.append({"profile": profile, "plan": plan})
        return plan

    def submit_run(self, *, user, plan, profile=None, generation_job=None, purpose="adhoc", name="", **_):
        from backend.analysis.engine.plan import plan_hash

        if generation_job.id in self.fail_submit:
            msg = "No file of the target is in scope for analysis"
            raise ValueError(msg)
        self.submitted.append(generation_job.id)
        status = self.initial_status or self.final.get(generation_job.id, AnalysisRun.Status.COMPLETED)
        return make_run(generation_job, user=user, plan_hash=plan_hash(plan), status=status, purpose=purpose)

    def request_cancel(self, run):
        self.cancelled.append(run.id)
        AnalysisRun.objects.filter(id=run.id).update(status=AnalysisRun.Status.CANCELLED)
        return run


@pytest.fixture
def engine(monkeypatch, fake_engine):  # noqa: F811
    from backend.analysis.engine import catalog
    from backend.analysis.engine import executor
    from backend.analysis.engine import plan

    fake = FakeEngine()
    specs = {slug: mock.Mock(kind="static", default_required=True, default_config={}) for slug in ("ruff", "bandit")}
    monkeypatch.setattr(plan, "default_profile_config", lambda: DEFAULT_CONFIG)
    monkeypatch.setattr(plan, "validate_profile_config", lambda config: config)
    monkeypatch.setattr(plan, "freeze_plan", fake.freeze_plan)
    monkeypatch.setattr(catalog, "load_specs", lambda refresh=False: specs)
    monkeypatch.setattr(executor, "submit_run", fake.submit_run)
    monkeypatch.setattr(executor, "request_cancel", fake.request_cancel)
    monkeypatch.setattr(automation, "_sleep", lambda s: None)
    return fake


@pytest.fixture
def user():
    return UserFactory()


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


class TestCreation:
    def test_completion_creates_one_frozen_post_run_only_when_enabled(self, user, engine):
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

    def test_manual_post_run_supersedes_one_left_running_by_a_dead_executor(self, user):
        from datetime import timedelta

        experiment, _ = _completed_experiment(user, post_run={"report": {"enabled": True}})
        now = timezone.now()
        stale = ExperimentPostRun.objects.create(
            experiment=experiment,
            trigger="completed",
            config={},
            status=ExperimentPostRun.Status.RUNNING,
            lease_owner="dead",
            lease_expires_at=now - timedelta(seconds=5),
        )
        live = automation.create_manual_post_run(experiment)
        stale.refresh_from_db()
        assert stale.status == ExperimentPostRun.Status.CANCELLED
        assert "restart" in stale.error
        assert live.status == ExperimentPostRun.Status.PENDING
        # A running post-run with a live lease still blocks a second one.
        live.status = ExperimentPostRun.Status.RUNNING
        live.lease_expires_at = now + timedelta(minutes=5)
        live.save(update_fields=["status", "lease_expires_at"])
        with pytest.raises(ExperimentError, match="already"):
            automation.create_manual_post_run(experiment)

    def test_unknown_analyzer_is_refused(self):
        # Validated against the v2 tool catalog (data/tools), not v1 rows.
        with pytest.raises(ExperimentError, match="Unknown analyzer"):
            normalize_post_run({"analysis": {"enabled": True, "tool_slugs": ["nope"]}}, "agent")
        with pytest.raises(ExperimentError, match="at least one analyzer"):
            normalize_post_run({"analysis": {"enabled": True, "tool_slugs": []}}, "agent")
        # Legacy slugs map onto their v2 names.
        config = normalize_post_run({"analysis": {"enabled": True, "tool_slugs": ["llm_review", "bandit"]}}, "agent")
        assert config["analysis"]["tool_slugs"] == ["ai_review", "bandit"]

    def test_a_profile_alone_enables_analysis_and_must_be_usable(self, user):
        profile = AnalysisProfileFactory(created_by=user)
        config = normalize_post_run({"analysis": {"enabled": True, "profile": profile.slug}}, "agent", user)
        assert config["analysis"] == {
            "enabled": True,
            "tool_slugs": [],
            "only_passing": False,
            "profile": profile.slug,
        }
        with pytest.raises(ExperimentError, match="not found or not usable"):
            normalize_post_run({"analysis": {"enabled": True, "profile": profile.slug}}, "agent", UserFactory())


class TestAdvance:
    def test_analyses_run_under_one_frozen_plan_for_jobs_without_one_then_the_report(self, user, engine):
        from backend.analysis.engine.plan import plan_hash

        config = {
            "analysis": {"enabled": True, "tool_slugs": ["bandit"], "only_passing": False},
            "report": {"enabled": True, "exclude_truncated": True},
        }
        experiment, jobs = _completed_experiment(user, post_run=config)
        expected_plan = {"schema_version": 1, "tools": [{"slug": "bandit"}], "reviewer": None}
        completed = [j for j in jobs.values() if j.status == S.COMPLETED]
        # Already analysed under the same plan: skipped. Under another plan: analysed again.
        make_run(completed[0], user=user, plan_hash=plan_hash(expected_plan))
        make_run(completed[1], user=user, plan_hash="0" * 64)
        post_run = automation.on_experiment_completed(experiment)

        done = automation.advance_post_runs("exec-1")
        assert done.id == post_run.id
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.COMPLETED, post_run.error
        # Three completed current jobs, one already analysed under the plan; the failed job is skipped.
        assert engine.submitted == [completed[1].id, completed[2].id]
        assert len(engine.frozen) == 1
        assert post_run.progress == {
            "analysis_done": 2,
            "analysis_total": 2,
            "analysis_failed": 0,
            "analysis_plan_hash": plan_hash(expected_plan),
        }
        assert post_run.config["analysis"]["frozen_plan"] == expected_plan
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
        # Nothing left to claim.
        assert automation.advance_post_runs("exec-1") is None

    def test_a_profile_is_frozen_with_the_tool_list_restricting_it(self, user, engine):
        profile = AnalysisProfileFactory(created_by=user, config=DEFAULT_CONFIG)
        config = {"analysis": {"enabled": True, "profile": profile.slug, "tool_slugs": ["ruff"]}}
        experiment, _ = _completed_experiment(user, post_run=config)
        automation.on_experiment_completed(experiment)
        post_run = automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.COMPLETED, post_run.error
        assert engine.frozen[0]["plan"]["tools"] == [{"slug": "ruff"}]
        assert post_run.config["analysis"]["frozen_profile_id"] == str(profile.id)

    def test_only_passing_filters_on_a_perfect_pass_rate(self, user, engine):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"], "only_passing": True}}
        experiment, jobs = _completed_experiment(user, post_run=config)
        automation.on_experiment_completed(experiment)
        automation.advance_post_runs("exec-1")
        assert set(engine.submitted) == {
            j.id for j in jobs.values() if (j.metrics.get("functional") or {}).get("pass_rate") == 1.0
        }

    def test_a_failed_analysis_is_counted_and_the_rest_continue(self, user, engine):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"]}}
        experiment, jobs = _completed_experiment(user, post_run=config)
        automation.on_experiment_completed(experiment)
        completed = [j for j in jobs.values() if j.status == S.COMPLETED]
        engine.fail_submit = {completed[0].id}
        engine.final = {completed[1].id: AnalysisRun.Status.FAILED}

        post_run = automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.COMPLETED
        assert post_run.progress["analysis_failed"] == 2
        assert post_run.progress["analysis_done"] == 3
        assert len(post_run.analysis_run_ids) == 2

    def test_waits_for_queued_runs_and_renews_the_lease(self, user, engine, monkeypatch):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"]}}
        experiment, _ = _completed_experiment(user, post_run=config, n_apps=1)
        automation.on_experiment_completed(experiment)
        engine.initial_status = AnalysisRun.Status.QUEUED
        polls = []

        def finish(_seconds):
            polls.append(1)
            AnalysisRun.objects.update(status=AnalysisRun.Status.COMPLETED)

        monkeypatch.setattr(automation, "_sleep", finish)
        post_run = automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert polls == [1]
        assert post_run.status == ExperimentPostRun.Status.COMPLETED
        assert post_run.progress["analysis_done"] == 2

    def test_unfinished_runs_are_cancelled_at_the_deadline(self, user, engine, monkeypatch):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"]}}
        experiment, _ = _completed_experiment(user, post_run=config, n_apps=1)
        automation.on_experiment_completed(experiment)
        engine.initial_status = AnalysisRun.Status.RUNNING
        monkeypatch.setattr(automation, "POST_RUN_ANALYSIS_TIMEOUT_SECONDS", 0)
        post_run = automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert len(engine.cancelled) == 2
        assert post_run.status == ExperimentPostRun.Status.COMPLETED
        assert post_run.progress["analysis_failed"] == 2

    def test_a_lost_lease_stops_and_the_new_owner_resumes_without_resubmitting(self, user, engine, monkeypatch):
        config = {"analysis": {"enabled": True, "tool_slugs": ["bandit"]}}
        experiment, _ = _completed_experiment(user, post_run=config, n_apps=1)
        post_run = automation.on_experiment_completed(experiment)
        engine.initial_status = AnalysisRun.Status.QUEUED

        def steal(_seconds):
            ExperimentPostRun.objects.filter(id=post_run.id).update(
                lease_owner="other",
                lease_expires_at=timezone.now() - timezone.timedelta(seconds=1),
            )

        monkeypatch.setattr(automation, "_sleep", steal)
        automation.advance_post_runs("exec-1")
        post_run.refresh_from_db()
        assert post_run.status == ExperimentPostRun.Status.RUNNING
        assert len(engine.submitted) == 2

        AnalysisRun.objects.update(status=AnalysisRun.Status.COMPLETED)
        resumed = automation.advance_post_runs("exec-2")
        resumed.refresh_from_db()
        assert resumed.status == ExperimentPostRun.Status.COMPLETED
        assert len(engine.submitted) == 2  # the recorded runs were waited for, not submitted again
        assert len(engine.frozen) == 1  # the stored plan was reused
        assert resumed.progress["analysis_done"] == 2

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


def test_post_run_api(client, user, engine):
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
    assert res.json()["config"] == {**spec, "analysis": {**spec["analysis"], "profile": None}}
    res = client.post(f"{base}run/", data="{}", content_type="application/json")
    assert (res.status_code, res.json()["trigger"], res.json()["status"]) == (200, "manual", "pending")
    assert client.post(f"{base}run/", data="{}", content_type="application/json").status_code == 400
    assert len(client.get(base).json()["runs"]) == 1
