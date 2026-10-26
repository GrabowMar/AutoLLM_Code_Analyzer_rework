"""The executor process (spec §C.2) with synchronous pools: leadership, loop, heartbeat, shutdown, orchestration."""

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
from backend.execution.tests.conftest import make_job
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import running_experiment
from backend.generation.models import GenerationJob
from backend.generation.services.agent.runner import AgentRunner
from backend.inference.access import LLMAccessError
from backend.inference.chat import ChatResult

pytestmark = pytest.mark.django_db
S = GenerationJob.Status


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


def _response(content="hello"):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "cost": 0.0},
    }


@pytest.fixture
def fake_llm():
    with (
        mock.patch(
            "backend.inference.access.resolve_access",
            side_effect=lambda user, model: mock.Mock(is_openrouter=True, endpoint=None),
        ),
        mock.patch("backend.inference.chat.chat", return_value=ChatResult(_response(), {})) as chat,
    ):
        yield chat


class TestLeadership:
    def test_a_second_executor_exits(self, leader):
        second = _executor()
        assert second.acquire_leadership() is False
        assert second.run() == 1

    def test_losing_the_lock_stops_claiming(self, leader, user, system_ep):
        make_job(user, make_model())
        pid = leader.lock_conn.connection.info.backend_pid
        killer = connections.create_connection("default")
        with killer.cursor() as cursor:
            cursor.execute("SELECT pg_terminate_backend(%s)", [pid])
        killer.close()
        assert leader.tick() is None
        assert leader.stop_event.is_set()
        assert leader.exit_code == 1
        assert GenerationJob.objects.filter(status=S.RUNNING).count() == 0

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
    def test_custom_job_runs_to_completion(self, leader, user, system_ep, fake_llm):
        job = make_job(user, make_model(), mode=GenerationJob.Mode.CUSTOM, custom_user_prompt="hi")
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.phase, job.lease_owner) == (S.COMPLETED, "", "")
        assert job.result_data["content"] == "hello"
        assert fake_llm.call_args.kwargs["job"].id == job.id
        assert len(leader.registry) == 0

    @pytest.fixture
    def fake_agent(self, monkeypatch, settings):
        settings.AGENT_AUTO_EVALUATE = True
        seen: dict = {}

        def run_stage(runner):
            seen["phase_during_run"] = GenerationJob.objects.get(id=runner.job.id).phase
            runner.job.result_data = {"agent": {"termination": "completed"}, "files": {}}
            return transitions.OUTCOME_EVALUATE

        def evaluate_job(job, save=None):
            seen["phase_during_eval"] = GenerationJob.objects.get(id=job.id).phase
            job.result_data = {**job.result_data, "evaluation": {"status": "passed"}}
            save(job, ["result_data"])

        monkeypatch.setattr(AgentRunner, "run_stage", run_stage)
        monkeypatch.setattr("backend.generation.services.evaluation.runner.evaluate_job", evaluate_job)
        return seen

    def test_agent_job_releases_its_sandbox_before_evaluation(self, leader, user, system_ep, fake_agent, settings):
        settings.APPS_DIND_MEM_LIMIT = "16g"
        ExecutorSettings.objects.filter(pk=1).update(sandbox_slots=1, eval_slots=1)
        seen = fake_agent
        first, second = (make_job(user, make_model(), lane="sandbox") for _ in range(2))
        leader.tick()
        states = dict(GenerationJob.objects.values_list("id", "phase"))
        # One sandbox slot: the first ran and went to eval_queued, freeing the slot for nobody yet this tick.
        assert sorted(states.values()) == ["", "eval_queued"]
        leader.tick()  # evaluation of the first + sandbox run of the second
        leader.tick()  # evaluation of the second
        rows = {j.id: j for j in GenerationJob.objects.all()}
        assert rows[first.id].status == rows[second.id].status == S.COMPLETED
        assert rows[first.id].result_data["evaluation"]["status"] == "passed"
        assert seen == {"phase_during_run": "running", "phase_during_eval": "evaluating"}

    def test_evaluation_keeps_the_executor_validity_notes(self, leader, user, system_ep, fake_agent, settings):
        settings.APPS_DIND_MEM_LIMIT = "16g"
        # A second attempt: the eval stage's validity must still say so (not only the sandbox stage's).
        job = make_job(user, make_model(), lane="sandbox", attempt=1)
        leader.tick()
        leader.tick()
        job.refresh_from_db()
        assert job.status == S.COMPLETED
        assert job.result_data["evaluation"]["status"] == "passed"
        codes = {n["code"] for n in job.result_data["agent"]["validity"]["notes"]}
        assert {"infra_retried"} <= codes

    def test_sandbox_and_evaluation_take_turns_when_memory_is_short(
        self,
        leader,
        user,
        system_ep,
        fake_agent,
        settings,
    ):
        settings.APPS_DIND_MEM_LIMIT = "4g"
        ExecutorSettings.objects.filter(pk=1).update(sandbox_slots=1, eval_slots=1)
        first, second = (make_job(user, make_model(), lane="sandbox") for _ in range(2))
        leader.tick()
        assert leader.slots.exclusive_eval
        leader.tick()  # the evaluation of the first runs; the second sandbox waits for it
        assert GenerationJob.objects.get(id=first.id).status == S.COMPLETED
        assert GenerationJob.objects.get(id=second.id).status == S.PENDING
        leader.tick()
        leader.tick()
        assert GenerationJob.objects.get(id=second.id).status == S.COMPLETED

    def test_scaffolding_experiment_completes_the_experiment(self, leader, user, system_ep, fake_llm, monkeypatch):
        experiment = running_experiment(user, post_run={"evaluate": False})
        monkeypatch.setattr(
            "backend.generation.services.orchestrator.GenerationService._run_scaffolding",
            lambda self, job: self._call_llm(job, job.model.model_id, [], stage="backend"),
        )
        job = make_job(user, make_model(), experiment=experiment, sequence=0)
        with mock.patch("backend.generation.services.experiment_automation.on_experiment_completed") as done:
            leader.tick()
        job.refresh_from_db()
        experiment.refresh_from_db()
        assert (job.status, experiment.status) == (S.COMPLETED, "completed")
        done.assert_called_once()

    def test_redis_errors_never_busy_spin(self):
        executor = _executor(redis_client=FakeRedis(fail=True), tick_seconds=0.2)
        started = time.monotonic()
        executor.wait_for_wake()
        assert time.monotonic() - started >= 0.19

    def test_failed_submit_is_an_infra_retry(self, leader, user, system_ep, monkeypatch):
        job = make_job(user, make_model())

        def refuse(*_a, **_kw):
            msg = "no threads"
            raise PoolFullError(msg)

        monkeypatch.setattr(leader.pools["llm"], "submit", refuse)
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.attempt) == (S.PENDING, 1)
        assert job.execution["attempts"][0]["reason"] == "submit_failed"

    def test_a_worker_that_dies_before_registering_is_found_lost(self, leader, user, system_ep, monkeypatch):
        job = make_job(user, make_model())
        monkeypatch.setattr("backend.execution.workers.run_job", lambda ctx, claimed: None)
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.lease_owner) == (S.RUNNING, leader.executor_id)
        GenerationJob.objects.filter(id=job.id).update(stage_entered_at=timezone.now() - timedelta(minutes=1))
        leader.tick()
        job.refresh_from_db()
        assert (job.status, job.attempt) == (S.PENDING, 1)
        assert job.execution["attempts"][0]["reason"] == "executor_lost"

    def test_stop_when_idle(self, user, system_ep, fake_llm):
        make_job(user, make_model(), mode=GenerationJob.Mode.CUSTOM, custom_user_prompt="hi")
        executor = _executor(stop_when_idle=True)
        assert executor.run() == 0
        assert GenerationJob.objects.get().status == S.COMPLETED
        assert ExecutorInstance.objects.get(id=executor.executor_id).stopped_at is not None


class TestHeartbeatAndShutdown:
    def test_heartbeat_renews_only_registered_jobs(self, leader, user, system_ep):
        soon = timezone.now() + timedelta(seconds=5)
        owned = make_job(user, make_model(), status=S.RUNNING, lease_owner=leader.executor_id, lease_expires_at=soon)
        orphan = make_job(user, make_model(), status=S.RUNNING, lease_owner=leader.executor_id, lease_expires_at=soon)
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
        job = make_job(
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
    def _post_run(self, user, **kw):
        from backend.generation.models import ExperimentPostRun

        return ExperimentPostRun.objects.create(experiment=running_experiment(user), trigger="manual", config={}, **kw)

    def test_a_post_run_interrupted_by_a_restart_is_resumed(self, leader, user, system_ep):
        from backend.generation.models import ExperimentPostRun

        self._post_run(
            user,
            status=ExperimentPostRun.Status.RUNNING,
            lease_owner="previous-executor",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        with mock.patch.object(leader.pools["post"], "submit") as submit:
            leader._automation()
        assert [c.args[0] for c in submit.call_args_list] == [leader_workers().run_post_run]

    def test_a_live_post_run_is_not_resubmitted(self, leader, user, system_ep):
        from backend.generation.models import ExperimentPostRun

        self._post_run(
            user,
            status=ExperimentPostRun.Status.RUNNING,
            lease_owner="other",
            lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        with mock.patch.object(leader.pools["post"], "submit") as submit:
            leader._automation()
        submit.assert_not_called()

    def test_schedule_actions_never_queue_behind_a_post_run(self, leader, monkeypatch):
        fired = []

        def due(now, submit):
            submit(None, 42)
            return [42]

        monkeypatch.setattr("backend.generation.services.experiment_automation.run_due_schedules", due)
        with (
            mock.patch.object(leader.pools["post"], "submit") as post_submit,
            mock.patch.object(leader.pools["schedule"], "submit", side_effect=lambda *a, **k: fired.append(a)),
        ):
            leader._automation()
        assert fired == [(leader_workers().run_schedule_action, 42)]
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

    def test_tick_carries_current_attempt_rollups(self, leader, user, system_ep):
        from backend.generation.tests.factories import AppRequirementTemplateFactory
        from backend.generation.tests.factories import ExperimentConditionFactory

        x = running_experiment(user, repeats=2)
        app = AppRequirementTemplateFactory()
        x.app_requirements.add(app)
        first, _second = ExperimentConditionFactory(experiment=x), ExperimentConditionFactory(experiment=x)
        cell = {"experiment": x, "condition": first, "app_requirement": app, "repeat_index": 0}
        make_job(user, first.model, status=S.FAILED, phase="", queued_at=None, **cell)
        make_job(user, first.model, status=S.COMPLETED, phase="", queued_at=None, **cell)  # the top-up retry
        result = leader.scheduler.tick(leader.slots, {name: p.free() for name, p in leader.pools.items()})
        with mock.patch("backend.realtime.events.publish_experiment_event") as publish:
            leader._publish_ticks(result)
        event = publish.call_args.args[1]
        assert event["type"] == "tick"
        # Current attempt per cell, like REST status: the superseded FAILED row is not counted.
        assert (event["counts"]["completed"], event["counts"]["failed"]) == (1, 0)
        assert event["cells"] == {"total": 4, "done": 1, "failed_final": 0, "in_flight": 0}

    def test_capacity_event_reports_stale_code(self, leader, monkeypatch):
        result = leader.scheduler.tick(leader.slots, {name: p.free() for name, p in leader.pools.items()})
        with mock.patch("backend.realtime.events.publish_capacity") as publish:
            monkeypatch.setattr("backend.execution.capacity.code_version", lambda: leader.code_version)
            leader._publish_capacity(result)
            assert publish.call_args.args[0]["executor"]["code_stale"] is False
            monkeypatch.setattr("backend.execution.capacity.code_version", lambda: "edited-on-disk")
            leader._publish_capacity(result)
            assert publish.call_args.args[0]["executor"]["code_stale"] is True


class TestInfraClassification:
    """GenerationService.execute(claimed=True) → finish_stage / infra_retry (spec §C.7)."""

    def _claimed(self, user, model, **kw):
        return make_job(
            user,
            model,
            mode=GenerationJob.Mode.CUSTOM,
            custom_user_prompt="hi",
            status=S.RUNNING,
            phase="running",
            lease_owner="exec:1",
            started_at=timezone.now(),
            **kw,
        )

    def test_offline_endpoint_before_any_call_is_retried(self, user, system_ep):
        from backend.generation.services.orchestrator import GenerationService

        job = self._claimed(user, make_model())
        error = LLMAccessError("endpoint_offline", "no relay")
        with mock.patch.object(GenerationService, "_build_client_for", side_effect=error):
            GenerationService().execute(job, claimed=True)
        job.refresh_from_db()
        assert (job.status, job.attempt) == (S.PENDING, 1)

    def test_local_error_after_a_successful_call_is_an_interruption(self, user, system_ep):
        from backend.generation.services.orchestrator import GenerationService

        endpoint = make_endpoint(user)
        job = self._claimed(user, make_model(endpoint, "qwen"))
        client = mock.Mock()
        client.chat_completion.side_effect = [_response(), LLMAccessError("timeout")]
        service = GenerationService()

        def build(j):
            service._access = mock.Mock(is_openrouter=False, endpoint=endpoint, api_flavor="native")
            return client

        def run_twice(j):
            service._call_llm(j, "m", [], stage="backend")
            service._call_llm(j, "m", [], stage="frontend")

        with (
            mock.patch.object(service, "_build_client_for", side_effect=build),
            mock.patch.object(service, "_run_custom", side_effect=run_twice),
        ):
            service.execute(job, claimed=True)
        job.refresh_from_db()
        assert (job.status, job.attempt) == (S.PENDING, 1)
        assert job.execution["attempts"][0]["reason"] == "infra_interrupted"
        assert job.artifacts.count() == 0  # the attempt's artifacts are gone

    def test_model_errors_fail_the_job(self, user, system_ep):
        from backend.generation.services.orchestrator import GenerationService

        job = self._claimed(user, make_model())
        error = LLMAccessError("upstream_error", "context too long", status_code=400)
        with mock.patch.object(GenerationService, "_build_client_for", side_effect=error):
            GenerationService().execute(job, claimed=True)
        job.refresh_from_db()
        assert (job.status, job.attempt, job.lease_owner) == (S.FAILED, 0, "")


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
