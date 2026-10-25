"""Reconciliation (spec §C.7): lost jobs, expired leases by phase, orphans, sessions, completion, schedules."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.execution import reconciler
from backend.execution.models import ExecutorInstance
from backend.execution.registry import JobRegistry
from backend.execution.tests.conftest import go_online
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_job
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import running_experiment
from backend.generation.models import Experiment
from backend.generation.models import ExperimentSchedule
from backend.generation.models import GenerationJob
from backend.inference.models import RelaySession

pytestmark = pytest.mark.django_db
S = GenerationJob.Status
ME = "exec-me:1:aaaa"
DEAD = "exec-dead:1:bbbb"


class FakeContainer:
    def __init__(self, labels, *, age_s=600):
        self.labels = {**labels, "llmlab.started": str(int(time.time() - age_s))}
        self.name = f"c-{id(self)}"
        self.removed = False

    def remove(self, force=False):
        self.removed = True


class _Containers:
    def __init__(self, items):
        self.items = items

    def list(self, all=False, filters=None):
        label = (filters or {}).get("label", "")
        key, _, value = label.partition("=")
        return [c for c in self.items if not c.removed and key in c.labels and (not value or c.labels[key] == value)]


class _Networks:
    def list(self, filters=None):
        return []


class FakeDocker:
    def __init__(self, containers=()):
        self.containers = _Containers(list(containers))
        self.networks = _Networks()


def _leased(user, *, owner=ME, expires_in=-10, **kw):
    kw.setdefault("phase", "running")
    kw.setdefault("stage_entered_at", timezone.now() - timedelta(minutes=5))
    return make_job(
        user,
        kw.pop("model", None) or make_model(),
        status=kw.pop("status", S.RUNNING),
        lease_owner=owner,
        lease_expires_at=timezone.now() + timedelta(seconds=expires_in),
        started_at=timezone.now() - timedelta(minutes=5),
        **kw,
    )


class TestLeases:
    def test_expired_sandbox_run_is_reaped_and_requeued(self, user, system_ep):
        job = _leased(user, lane="sandbox")
        container = FakeContainer({reconciler.AGENT_LABEL: str(job.id)})
        docker = FakeDocker([container])
        assert reconciler.expire_leases(ME, JobRegistry(), docker=docker) == 1
        job.refresh_from_db()
        assert (job.status, job.attempt, job.lease_owner) == (S.PENDING, 1, "")
        assert job.execution["attempts"][0]["reason"] == "executor_lost"
        assert container.removed

    def test_a_dead_instances_lease_counts_as_expired_at_once(self, user, system_ep):
        job = _leased(user, owner=DEAD, expires_in=60)
        assert reconciler.expire_leases(ME, JobRegistry()) == 1
        job.refresh_from_db()
        assert job.status == S.PENDING

    def test_evaluations_are_requeued_then_given_up(self, user, system_ep, settings):
        settings.EVAL_MAX_REQUEUES = 2
        job = _leased(user, phase="evaluating", result_data={"files": {}})
        for expected in (1, 2):
            assert reconciler.expire_leases(ME, JobRegistry()) == 1
            job.refresh_from_db()
            assert (job.status, job.phase, job.execution["eval_requeues"]) == (S.RUNNING, "eval_queued", expected)
            GenerationJob.objects.filter(id=job.id).update(
                phase="evaluating",
                lease_owner=ME,
                lease_expires_at=timezone.now() - timedelta(seconds=1),
            )
        reconciler.expire_leases(ME, JobRegistry())
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.COMPLETED, "")
        assert job.result_data["evaluation"]["status"] == "infra_error"

    def test_cancelled_row_with_a_stale_lease_is_released(self, user, system_ep):
        job = _leased(user, status=S.CANCELLED, phase="")
        reconciler.expire_leases(ME, JobRegistry())
        job.refresh_from_db()
        assert (job.status, job.lease_owner) == (S.CANCELLED, "")

    def test_registry_and_legacy_rows_are_never_touched(self, user, system_ep):
        registry = JobRegistry()
        mine = _leased(user)
        registry.add(mine.id, 0, "llm")
        legacy = _leased(user, queued_at=None)
        assert reconciler.expire_leases(ME, registry) == 0
        assert GenerationJob.objects.get(id=mine.id).status == S.RUNNING
        assert GenerationJob.objects.get(id=legacy.id).status == S.RUNNING


class TestLostJobs:
    def test_claimed_job_without_a_worker_is_requeued(self, user, system_ep):
        lost = _leased(user, expires_in=60)
        recent = _leased(user, expires_in=60, stage_entered_at=timezone.now())
        live = _leased(user, expires_in=60)
        registry = JobRegistry()
        registry.add(live.id, 0, "llm")
        assert reconciler.requeue_lost(ME, registry) == 1
        statuses = dict(GenerationJob.objects.values_list("id", "status"))
        assert (statuses[lost.id], statuses[recent.id], statuses[live.id]) == (S.PENDING, S.RUNNING, S.RUNNING)

    def test_lost_evaluation_goes_back_to_eval_queued(self, user, system_ep):
        job = _leased(user, expires_in=60, phase="evaluating")
        reconciler.requeue_lost(ME, JobRegistry())
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.RUNNING, "eval_queued")


class TestOrphans:
    def test_only_containers_of_dead_jobs_older_than_two_minutes_go(self, user, system_ep):
        live = _leased(user, expires_in=60)
        dead = _leased(user, status=S.FAILED, phase="")
        registered = _leased(user, status=S.CANCELLED, phase="")
        registry = JobRegistry()
        registry.add(registered.id, 0, "sandbox")
        containers = {
            "live": FakeContainer({reconciler.AGENT_LABEL: str(live.id)}),
            "dead": FakeContainer({reconciler.AGENT_LABEL: str(dead.id)}),
            "young": FakeContainer({reconciler.EVAL_LABEL: str(dead.id)}, age_s=10),
            "registered": FakeContainer({reconciler.AGENT_LABEL: str(registered.id)}),
        }
        assert reconciler.reap_orphans(FakeDocker(containers.values()), registry) == 1
        assert {name for name, c in containers.items() if c.removed} == {"dead"}


class TestSessions:
    def test_stale_session_ends_and_standby_is_promoted(self, user, system_ep):
        endpoint = make_endpoint(user)
        active = RelaySession.objects.get(endpoint=endpoint)
        RelaySession.objects.filter(pk=active.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
        standby = go_online(endpoint, role="standby")
        assert reconciler.end_stale_sessions() == 1
        active.refresh_from_db()
        standby.refresh_from_db()
        assert (active.ended_reason, standby.role) == ("timeout", "active")

    def test_expired_token_ends_its_sessions(self, user, system_ep):
        endpoint = make_endpoint(user)
        session = RelaySession.objects.get(endpoint=endpoint)
        session.token.expires_at = timezone.now() - timedelta(minutes=1)
        session.token.save()
        assert reconciler.end_expired_token_sessions() == 1
        session.refresh_from_db()
        assert session.ended_reason == "token_expired"


def test_dead_instances_are_stopped(db):
    old = ExecutorInstance.objects.create(
        id=DEAD,
        hostname="h",
        pid=1,
        code_version="x",
        last_seen_at=timezone.now() - timedelta(minutes=5),
    )
    assert reconciler.stop_dead_instances(ME) == 1
    old.refresh_from_db()
    assert old.stopped_at is not None


def test_completion_sweep(user, system_ep):
    done = running_experiment(user)
    make_job(user, make_model(), experiment=done, status=S.COMPLETED)
    busy = running_experiment(user)
    make_job(user, make_model(), experiment=busy)
    cancelled = running_experiment(user, status=Experiment.Status.CANCELLED)
    stray = make_job(user, make_model(), experiment=cancelled)
    # Claimed while the experiment cancel skipped its locked row: cancelled now, lease kept for its worker.
    claimed = _leased(user, experiment=cancelled, expires_in=60)
    grading = _leased(user, experiment=cancelled, expires_in=60, phase="evaluating")
    assert reconciler.completion_sweep() == 1
    assert Experiment.objects.get(id=done.id).status == Experiment.Status.COMPLETED
    assert Experiment.objects.get(id=busy.id).status == Experiment.Status.RUNNING
    assert GenerationJob.objects.get(id=stray.id).status == S.CANCELLED
    claimed.refresh_from_db()
    assert (claimed.status, claimed.lease_owner) == (S.CANCELLED, ME)
    assert GenerationJob.objects.get(id=grading.id).status == S.RUNNING


def test_schedules_are_fast_forwarded_without_burst(user):
    experiment = running_experiment(user)
    stale = ExperimentSchedule.objects.create(
        experiment=experiment,
        action="top_up",
        cron="0 * * * *",
        created_by=user,
        next_run_at=timezone.now() - timedelta(hours=5),
    )
    due = ExperimentSchedule.objects.create(
        experiment=experiment,
        action="top_up",
        cron="0 * * * *",
        created_by=user,
        next_run_at=timezone.now() - timedelta(minutes=10),
    )
    assert reconciler.fast_forward_schedules() == 1
    stale.refresh_from_db()
    due.refresh_from_db()
    assert stale.next_run_at > timezone.now()
    assert due.next_run_at < timezone.now()  # within one period: fires normally


def test_reconcile_runs_every_step_even_when_one_fails(user, system_ep, monkeypatch):
    def boom(*_a, **_kw):
        msg = "nope"
        raise RuntimeError(msg)

    monkeypatch.setattr(reconciler, "end_stale_sessions", boom)
    job = _leased(user, owner=DEAD)
    report = reconciler.reconcile(ME, JobRegistry())
    assert report.expired == 1
    assert any(e.startswith("sessions") for e in report.errors)
    assert GenerationJob.objects.get(id=job.id).status == S.PENDING


def test_reconcile_reaps_through_a_fresh_client_and_closes_it(user, system_ep):
    class ClosingDocker(FakeDocker):
        closed = False

        def close(self):
            self.closed = True

    job = _leased(user, owner=DEAD, lane="sandbox")
    container = FakeContainer({"llmlab.agent-run": str(job.id)})
    docker = ClosingDocker([container])
    reconciler.reconcile(ME, JobRegistry(), docker_factory=lambda: docker)
    assert container.removed
    assert docker.closed
