"""The executor process (spec §C.2) with synchronous pools: leadership, loop, heartbeat, shutdown, stage dispatch."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connections
from django.utils import timezone

from backend.execution import transitions
from backend.execution.executor import BoundedPool
from backend.execution.executor import Executor
from backend.execution.executor import PoolFullError
from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import make_run
from backend.execution.tests.conftest import running_study
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import StudyPostRun
from backend.generation.models.choices import RunStatus
from backend.generation.tests.factories import StudyCellFactory

pytestmark = pytest.mark.django_db
S = RunStatus


class FakeRedis:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    def blpop(self, keys, timeout=0):
        self.calls += 1
        if self.fail:
            msg = "redis down"
            raise ConnectionError(msg)


def _executor(**kw) -> Executor:
    kw.setdefault("sync_pools", True)
    kw.setdefault("start_proxy", False)
    kw.setdefault("start_pumps", False)
    kw.setdefault("redis_client", FakeRedis())
    kw.setdefault("tick_seconds", 0.05)
    return Executor(**kw)


@pytest.fixture
def leader():
    """A started executor (leader lock, instance row, pools) torn down after the test."""
    executor = _executor()
    assert executor.acquire_leadership()
    executor.startup()
    yield executor
    executor.shutdown()


@pytest.fixture
def fake_stages(monkeypatch):
    """The generation stage (``agentic.dispatch.run_stage``, WP-08) and evaluation (``run_eval_stage``, WP-09).

    Generation records the phase it ran in and hands the run to evaluation (sandbox lane) or ends it
    (llm lane); evaluation records its phase and ends the run ``tested``.
    """
    seen: dict = {"generated": [], "evaluated": []}

    def run_stage(run):
        seen["phase_during_run"] = GenerationRun.objects.get(id=run.id).phase
        seen["generated"].append(run.id)
        run.result = {"termination": "completed"}
        if run.lane == "sandbox":
            transitions.finish_stage(run, transitions.OUTCOME_EVALUATE)
        else:
            run.outcome = "output_invalid"
            transitions.finish_stage(run, transitions.OUTCOME_DONE)

    def run_eval_stage(run):
        seen["phase_during_eval"] = GenerationRun.objects.get(id=run.id).phase
        seen["evaluated"].append(run.id)
        run.outcome = "tested"
        transitions.finish_stage(run, transitions.OUTCOME_DONE)

    monkeypatch.setattr("backend.generation.agentic.dispatch.run_stage", run_stage)
    monkeypatch.setattr("backend.generation.evaluation.suite.run_eval_stage", run_eval_stage)
    return seen


class TestLeadership:
    def test_a_second_executor_exits(self, leader):
        second = _executor()
        assert second.acquire_leadership() is False
        assert second.run() == 1

    def test_losing_the_lock_stops_claiming(self, leader, user, system_ep):
        make_run(user, make_model())
        pid = leader.lock_conn.connection.info.backend_pid
        killer = connections.create_connection("default")
        with killer.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(%s)", [pid])
        killer.close()
        assert leader.tick() is None
        assert leader.stop_event.is_set()
        assert leader.exit_code == 1
        assert GenerationRun.objects.filter(status=S.RUNNING).count() == 0


class TestPreviewBuilds:
    def test_preview_is_run_by_eval_pool_before_a_sandbox_in_small_daemon(self, leader, user, system_ep, settings):
        from backend.execution.models import RuntimeBuildLease
        from backend.runtime.models import ContainerAction
        from backend.runtime.tests.factories import ContainerActionFactory

        settings.APPS_DIND_MEM_LIMIT = "4g"
        action = ContainerActionFactory(action_type=ContainerAction.ActionType.BUILD)
        job = make_run(user, make_model(), lane="sandbox")

        def execute(action_id, *, claimed, cancelled):
            assert action_id == action.pk
            assert claimed
            assert not cancelled()
            assert leader.pools["eval"].inflight == 1
            assert RuntimeBuildLease.objects.filter(action=action).exists()
            action.mark_completed()

        with (
            mock.patch("backend.runtime.services.container_service._execute", side_effect=execute) as build,
            mock.patch("backend.execution.workers.run_job") as generation,
        ):
            leader.tick()
        build.assert_called_once()
        generation.assert_not_called()
        job.refresh_from_db()
        assert job.status == S.PENDING
        assert not RuntimeBuildLease.objects.exists()

    def test_pending_preview_prevents_stop_when_idle(self, leader):
        from backend.runtime.models import ContainerAction
        from backend.runtime.tests.factories import ContainerActionFactory

        ContainerActionFactory(action_type=ContainerAction.ActionType.BUILD)
        assert not leader._idle()

    def test_heartbeat_renews_preview_worker_lease(self, leader):
        from backend.execution import runtime_builds
        from backend.execution.models import RuntimeBuildLease
        from backend.runtime.models import ContainerAction
        from backend.runtime.tests.factories import ContainerActionFactory

        action = ContainerActionFactory(action_type=ContainerAction.ActionType.BUILD)
        runtime_builds.claim(leader.executor_id, 1)
        leader.registry.add(action.pk, 0, "preview")
        RuntimeBuildLease.objects.filter(action=action).update(expires_at=timezone.now())
        leader.heartbeat()
        assert not runtime_builds.cancelled(action.pk, leader.executor_id)

    def test_a_proxy_that_cannot_bind_stops_the_executor(self, settings):
        # TEST-NET-3 address: not on any interface here -> EADDRNOTAVAIL, like a
        # misconfigured LLM_PROXY_BIND or a container without the llmproxy network.
        settings.LLM_PROXY_BIND = "203.0.113.77:8090"
        executor = _executor(start_proxy=True)
        assert executor.acquire_leadership()
        try:
            executor.startup()
            assert executor.stop_event.wait(10)
            assert executor.exit_code == 1
            assert executor.stop_reason == "llm-proxy stopped"
        finally:
            executor.shutdown()

    def test_a_non_fatal_service_crash_keeps_the_executor_running(self, leader):
        done = threading.Event()
        leader._start_service("boom", "backend.execution.tests.test_executor_loop", "_crash_service", done)
        assert done.wait(5)
        time.sleep(0.1)
        assert not leader.stop_event.is_set()
        assert leader.exit_code == 0


def _crash_service(done):
    done.set()
    msg = "boom"
    raise RuntimeError(msg)


class TestLoop:
    def test_an_llm_run_goes_through_the_dispatcher(self, leader, user, system_ep, fake_stages):
        run = make_run(user, make_model())
        leader.tick()
        run.refresh_from_db()
        assert (run.status, run.phase, run.lease_owner, run.outcome, run.usable) == (
            S.COMPLETED,
            "",
            "",
            "output_invalid",
            True,
        )
        assert fake_stages["generated"] == [run.id]
        assert fake_stages["phase_during_run"] == "running"
        assert len(leader.registry) == 0

    def test_agentic_run_releases_its_sandbox_before_evaluation(self, leader, user, system_ep, fake_stages, settings):
        settings.APPS_DIND_MEM_LIMIT = "16g"
        ExecutorSettings.objects.filter(pk=1).update(sandbox_slots=1, eval_slots=1)
        first, second = (make_run(user, make_model(), lane="sandbox") for _ in range(2))
        leader.tick()
        states = dict(GenerationRun.objects.filter(id__in=[first.id, second.id]).values_list("id", "phase"))
        # One sandbox slot: the first ran and went to eval_queued, freeing the slot for nobody yet this tick.
        assert sorted(states.values()) == ["", "eval_queued"]
        leader.tick()  # evaluation of the first + sandbox run of the second
        leader.tick()  # evaluation of the second
        rows = {r.id: r for r in GenerationRun.objects.filter(id__in=[first.id, second.id])}
        assert rows[first.id].status == rows[second.id].status == S.COMPLETED
        assert rows[first.id].outcome == rows[second.id].outcome == "tested"
        assert (fake_stages["phase_during_run"], fake_stages["phase_during_eval"]) == ("running", "evaluating")

    def test_reference_runs_are_claimed_by_phase(self, leader, user, system_ep, fake_stages):
        from backend.execution.api import enqueue_evaluation
        from backend.generation.tests.factories import GenerationRunFactory

        reference = GenerationRunFactory(reference=True, status=S.PENDING, phase="", created_by=user)
        enqueue_evaluation([reference.id])
        leader.tick()
        reference.refresh_from_db()
        assert (reference.status, reference.outcome) == (S.COMPLETED, "tested")
        assert fake_stages["generated"] == []
        assert fake_stages["evaluated"] == [reference.id]

    def test_sandbox_and_evaluation_take_turns_when_memory_is_short(
        self,
        leader,
        user,
        system_ep,
        fake_stages,
        settings,
    ):
        settings.APPS_DIND_MEM_LIMIT = "4g"
        ExecutorSettings.objects.filter(pk=1).update(sandbox_slots=1, eval_slots=1)
        first, second = (make_run(user, make_model(), lane="sandbox") for _ in range(2))
        leader.tick()
        assert leader.slots.exclusive_eval
        leader.tick()  # the evaluation of the first runs; the second sandbox waits for it
        assert GenerationRun.objects.get(id=first.id).status == S.COMPLETED
        assert GenerationRun.objects.get(id=second.id).status == S.PENDING
        leader.tick()
        leader.tick()
        assert GenerationRun.objects.get(id=second.id).status == S.COMPLETED

    def test_the_last_run_completes_the_study(self, leader, user, system_ep, fake_stages):
        study = running_study(user)
        run = make_run(user, make_model(), study=study, sequence=0)
        leader.tick()
        run.refresh_from_db()
        assert (run.status, Study.objects.get(pk=study.pk).status) == (S.COMPLETED, "completed")

    def test_an_unimplemented_stage_leaves_the_run_to_the_lost_run_check(self, leader, user, system_ep):
        # Until WP-08 lands, run_stage raises NotImplementedError: the worker logs it and keeps the lease.
        run = make_run(user, make_model())
        leader.tick()
        run.refresh_from_db()
        assert (run.status, run.lease_owner) == (S.RUNNING, leader.executor_id)
        assert len(leader.registry) == 0

    def test_redis_errors_never_busy_spin(self):
        executor = _executor(redis_client=FakeRedis(fail=True), tick_seconds=0.2)
        started = time.monotonic()
        executor.wait_for_wake()
        assert time.monotonic() - started >= 0.19

    def test_failed_submit_is_an_infra_retry(self, leader, user, system_ep, monkeypatch):
        job = make_run(user, make_model())

        def refuse(*_a, **_kw):
            msg = "no threads"
            raise PoolFullError(msg)

        monkeypatch.setattr(leader.pools["llm"], "submit", refuse)
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.attempt) == (S.PENDING, 1)
        assert job.execution["attempts"][0]["reason"] == "submit_failed"

    def test_a_worker_that_dies_before_registering_is_found_lost(self, leader, user, system_ep, monkeypatch):
        job = make_run(user, make_model())
        monkeypatch.setattr("backend.execution.workers.run_job", lambda ctx, claimed: None)
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.lease_owner) == (S.RUNNING, leader.executor_id)
        GenerationRun.objects.filter(id=job.id).update(stage_entered_at=timezone.now() - timedelta(minutes=1))
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.attempt) == (S.PENDING, 1)
        assert job.execution["attempts"][0]["reason"] == "executor_lost"

    def test_stop_when_idle(self, user, system_ep, fake_stages):
        run = make_run(user, make_model())
        executor = _executor(stop_when_idle=True)
        assert executor.run() == 0
        assert GenerationRun.objects.get(id=run.id).status == S.COMPLETED
        assert ExecutorInstance.objects.get(id=executor.executor_id).stopped_at is not None


class TestHeartbeatAndShutdown:
    def test_heartbeat_renews_only_registered_jobs(self, leader, user, system_ep):
        soon = timezone.now() + timedelta(seconds=5)
        owned = make_run(user, make_model(), status=S.RUNNING, lease_owner=leader.executor_id, lease_expires_at=soon)
        orphan = make_run(user, make_model(), status=S.RUNNING, lease_owner=leader.executor_id, lease_expires_at=soon)
        leader.registry.add(owned.id, 0, "llm")
        assert leader.heartbeat() == 1
        owned.refresh_from_db()
        orphan.refresh_from_db()
        assert owned.lease_expires_at > soon + timedelta(seconds=30)
        assert orphan.lease_expires_at == soon
        instance = ExecutorInstance.objects.get(id=leader.executor_id)
        assert instance.config["registry"] == 1

    def test_shutdown_expires_own_leases(self, user, system_ep):
        executor = _executor()
        assert executor.acquire_leadership()
        executor.startup()
        job = make_run(
            user,
            make_model(),
            status=S.RUNNING,
            lease_owner=executor.executor_id,
            lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        executor.shutdown()
        job.refresh_from_db()
        assert job.lease_expires_at <= timezone.now()
        instance = ExecutorInstance.objects.get(id=executor.executor_id)
        assert (instance.stopped_at is not None, instance.is_leader) == (True, False)
        assert executor.lock_conn is None


class TestAutomation:
    def _post_run(self, user, *, study=None, **kw):
        kw.setdefault("trigger", "manual")
        return StudyPostRun.objects.create(study=study or running_study(user), config={}, **kw)

    def test_a_post_run_interrupted_by_a_restart_is_resumed(self, leader, user, system_ep):
        self._post_run(
            user,
            status="running",
            lease_owner="previous-executor",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        with mock.patch.object(leader.pools["post"], "submit") as submit:
            leader._automation()
        assert [c.args[0] for c in submit.call_args_list] == [leader_workers().run_post_run]

    def test_a_live_post_run_is_not_resubmitted(self, leader, user, system_ep):
        self._post_run(
            user,
            status="running",
            lease_owner="other",
            lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        with mock.patch.object(leader.pools["post"], "submit") as submit:
            leader._automation()
        submit.assert_not_called()

    def test_a_completion_post_run_waits_for_its_study(self, user):
        study = running_study(user)
        post_run = self._post_run(user, study=study, trigger="completed")
        assert not leader_workers().claimable_post_runs().filter(pk=post_run.pk).exists()
        Study.objects.filter(pk=study.pk).update(status="completed")
        assert leader_workers().claimable_post_runs().filter(pk=post_run.pk).exists()

    def test_the_post_run_worker_leases_and_runs_one_post_run(self, leader, user, monkeypatch):
        post_run = self._post_run(user)
        ran = []
        monkeypatch.setattr("backend.generation.study.automation.run_post_run", lambda p: ran.append(p.pk))
        leader_workers().run_post_run(leader.ctx)
        post_run.refresh_from_db()
        assert ran == [post_run.pk]
        assert (post_run.status, post_run.lease_owner) == ("running", leader.executor_id)
        assert post_run.started_at is not None

    def test_an_unimplemented_post_run_goes_back_to_pending(self, leader, user):
        post_run = self._post_run(user)
        leader_workers().run_post_run(leader.ctx)  # study.automation is still a WP-10 skeleton
        post_run.refresh_from_db()
        assert (post_run.status, post_run.lease_owner) == ("pending", "")

    def test_schedules_fire_on_their_own_pool_never_behind_a_post_run(self, leader, monkeypatch):
        fired = []
        monkeypatch.setattr("backend.generation.study.automation.fire_due_schedules", fired.append)
        with mock.patch.object(leader.pools["post"], "submit") as post_submit:
            leader._automation()
        assert len(fired) == 1
        post_submit.assert_not_called()


def leader_workers():
    from backend.execution import workers

    return workers


class TestRegistry:
    def test_a_finishing_stage_never_drops_the_next_stage_of_the_same_job(self):
        from backend.execution.registry import JobRegistry

        registry = JobRegistry()
        generation = registry.add("job-1", 0, "sandbox")
        evaluation = registry.add("job-1", 0, "eval")  # claimed while generation still unwinds
        registry.remove("job-1", generation)
        assert "job-1" in registry
        assert registry.entries()[0] is evaluation
        registry.remove("job-1", evaluation)
        assert "job-1" not in registry


class TestLiveEvents:
    def test_endpoint_state_is_published_only_when_it_changes(self, leader, user, system_ep):
        endpoint = make_endpoint(user)
        with mock.patch("backend.realtime.events.publish_endpoint_event") as publish:
            leader._publish_endpoints()
            leader._publish_endpoints()
            assert [c.args[0] for c in publish.call_args_list] == [endpoint.pk]  # never the OpenRouter row
            owner, public = publish.call_args.args[1:]
            assert (owner["type"], owner["status"], public["session_count"]) == ("endpoint", "online", 1)
            assert "sessions" not in public
            endpoint.loaded_models = [{"name": "qwen", "size": 10, "size_vram": 8}, {"name": "llama"}]
            endpoint.save(update_fields=["loaded_models"])
            leader._publish_endpoints()
        assert publish.call_count == 2
        # Same object shape as REST ``live.loaded`` (the UI keys chips by name).
        assert publish.call_args.args[1]["loaded"] == [
            {"name": "qwen", "size_vram": 8, "cpu_spill": True},
            {"name": "llama", "size_vram": None, "cpu_spill": False},
        ]
        assert publish.call_args.args[2]["loaded_count"] == 2

    def test_tick_carries_current_run_rollups(self, leader, user, system_ep):
        study = running_study(user)
        cell = StudyCellFactory(condition__plan__study=study)
        StudyCellFactory(condition__plan__study=study)  # no run yet: counted in the total only
        common = {"study": study, "cell": cell, "queued_at": None, "phase": ""}
        make_run(user, make_model(), status=S.FAILED, outcome="infra_error", cell_attempt=1, **common)
        make_run(user, make_model(), status=S.COMPLETED, outcome="tested", cell_attempt=2, **common)  # the top-up
        result = leader.scheduler.tick(leader.slots, {name: p.free() for name, p in leader.pools.items()})
        with mock.patch("backend.realtime.events.publish_study_event") as publish:
            leader._publish_ticks(result)
        assert publish.call_args.args[0] == study.id
        event = publish.call_args.args[1]
        assert event["type"] == "tick"
        # The cell's current run (first usable attempt) only: the superseded FAILED row is not counted.
        assert (event["counts"].get("completed"), event["counts"].get("failed", 0)) == (1, 0)
        assert event["cells"] == {"total": 2, "done": 1, "failed_final": 0, "in_flight": 0}
        from backend.generation.contracts.events import parse_study_event

        parse_study_event(event)

    def test_capacity_event_reports_stale_code(self, leader, monkeypatch):
        result = leader.scheduler.tick(leader.slots, {name: p.free() for name, p in leader.pools.items()})
        with mock.patch("backend.realtime.events.publish_capacity") as publish:
            monkeypatch.setattr("backend.execution.capacity.code_version", lambda: leader.code_version)
            leader._publish_capacity(result)
            assert publish.call_args.args[0]["executor"]["code_stale"] is False
            monkeypatch.setattr("backend.execution.capacity.code_version", lambda: "edited-on-disk")
            leader._publish_capacity(result)
            assert publish.call_args.args[0]["executor"]["code_stale"] is True


def test_bounded_pool_counts_real_free_threads():
    import threading

    pool = BoundedPool("t", 1)
    gate = threading.Event()
    pool.submit(gate.wait, 5)
    assert pool.free() == 0
    with pytest.raises(PoolFullError):
        pool.submit(print)
    pool.resize(2)
    assert pool.free() == 1  # the old pool's busy thread still counts
    gate.set()
    assert pool.wait_idle(5)
    assert pool.free() == 2
    pool.shutdown()
