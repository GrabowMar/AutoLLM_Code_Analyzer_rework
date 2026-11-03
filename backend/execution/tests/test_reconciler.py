"""Reconciliation (spec §C.7): lost runs, expired leases by phase, orphans, sessions, study completion."""

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
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import make_run
from backend.execution.tests.conftest import running_study
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models.choices import RunStatus
from backend.inference.models import RelaySession

pytestmark = pytest.mark.django_db
S = RunStatus
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
    return make_run(
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
        job = _leased(user, phase="evaluating", tree_hash="d" * 64)
        for expected in (1, 2):
            assert reconciler.expire_leases(ME, JobRegistry()) == 1
            job.refresh_from_db()
            assert (job.status, job.phase, job.execution["eval_requeues"]) == (S.RUNNING, "eval_queued", expected)
            GenerationRun.objects.filter(id=job.id).update(
                phase="evaluating",
                lease_owner=ME,
                lease_expires_at=timezone.now() - timedelta(seconds=1),
            )
        reconciler.expire_leases(ME, JobRegistry())
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.COMPLETED, "")
        # A tester that never ran is an infra error, never a pass rate of 0 (section 6.12).
        assert (job.outcome, job.outcome_reason, job.usable) == ("infra_error", "eval_lost", False)
        assert "lost 3 times" in job.result["error"]

    def test_a_lost_reevaluation_keeps_the_outcome_it_had(self, user, system_ep, settings):
        settings.EVAL_MAX_REQUEUES = 0
        job = _leased(
            user,
            phase="evaluating",
            outcome="tested",
            execution={"reeval": True, "reeval_prev_status": "completed"},
        )
        reconciler.expire_leases(ME, JobRegistry())
        job.refresh_from_db()
        assert (job.status, job.outcome, job.usable) == (S.COMPLETED, "tested", True)

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
        assert GenerationRun.objects.get(id=mine.id).status == S.RUNNING
        assert GenerationRun.objects.get(id=legacy.id).status == S.RUNNING


class TestLostRuns:
    def test_claimed_run_without_a_worker_is_requeued(self, user, system_ep):
        lost = _leased(user, expires_in=60)
        recent = _leased(user, expires_in=60, stage_entered_at=timezone.now())
        live = _leased(user, expires_in=60)
        registry = JobRegistry()
        registry.add(live.id, 0, "llm")
        assert reconciler.requeue_lost(ME, registry) == 1
        statuses = dict(GenerationRun.objects.values_list("id", "status"))
        assert (statuses[lost.id], statuses[recent.id], statuses[live.id]) == (S.PENDING, S.RUNNING, S.RUNNING)

    def test_lost_evaluation_goes_back_to_eval_queued(self, user, system_ep):
        job = _leased(user, expires_in=60, phase="evaluating")
        reconciler.requeue_lost(ME, JobRegistry())
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.RUNNING, "eval_queued")


class TestOrphans:
    def test_only_containers_of_dead_runs_older_than_two_minutes_go(self, user, system_ep):
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
    done = running_study(user)
    make_run(user, make_model(), study=done, status=S.COMPLETED)
    busy = running_study(user)
    make_run(user, make_model(), study=busy)
    cancelled = running_study(user, status="cancelled")
    stray = make_run(user, make_model(), study=cancelled)
    # Claimed while the study cancel skipped its locked row: cancelled now, lease kept for its worker.
    claimed = _leased(user, study=cancelled, expires_in=60)
    grading = _leased(user, study=cancelled, expires_in=60, phase="evaluating")
    assert reconciler.completion_sweep() == 1
    assert Study.objects.get(id=done.id).status == "completed"
    assert Study.objects.get(id=busy.id).status == "running"
    assert GenerationRun.objects.get(id=stray.id).status == S.CANCELLED
    claimed.refresh_from_db()
    assert (claimed.status, claimed.lease_owner) == (S.CANCELLED, ME)
    assert GenerationRun.objects.get(id=grading.id).status == S.RUNNING


def test_reconcile_runs_every_step_even_when_one_fails(user, system_ep, monkeypatch):
    def boom(*_a, **_kw):
        msg = "nope"
        raise RuntimeError(msg)

    monkeypatch.setattr(reconciler, "end_stale_sessions", boom)
    job = _leased(user, owner=DEAD)
    report = reconciler.reconcile(ME, JobRegistry())
    assert report.expired == 1
    assert any(e.startswith("sessions") for e in report.errors)
    assert GenerationRun.objects.get(id=job.id).status == S.PENDING


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
