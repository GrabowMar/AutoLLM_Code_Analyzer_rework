"""The claim algorithm (spec §C.4): slots, endpoint caps, liveness, residency, fairness, prefetch, locking."""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.db import transaction
from django.utils import timezone

from backend.execution.scheduler import CIRCUIT_OPEN
from backend.execution.scheduler import DRAINING
from backend.execution.scheduler import ENDPOINT_FULL
from backend.execution.scheduler import ENDPOINT_OFFLINE
from backend.execution.scheduler import EXPERIMENT_CAP
from backend.execution.scheduler import LLM_THREADS_FULL
from backend.execution.scheduler import MODEL_NOT_SERVED
from backend.execution.scheduler import RESIDENCY_WAIT
from backend.execution.scheduler import SANDBOX_FULL
from backend.execution.scheduler import WARMING_MODEL
from backend.execution.scheduler import Scheduler
from backend.execution.tests.conftest import free
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_job
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import running_experiment
from backend.execution.tests.conftest import slots
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
S = GenerationJob.Status
OWNER = "exec-test:1:abcd"


def _scheduler(**kw) -> Scheduler:
    return Scheduler(OWNER, **kw)


def _claimed_ids(result):
    return {str(c.job_id) for c in result.claimed}


def _reasons(result):
    return set(result.blocked().values())


class TestSlots:
    def test_sandbox_slot_cap(self, user, system_ep):
        jobs = [make_job(user, make_model(), lane="sandbox") for _ in range(3)]
        sched = _scheduler()
        first = sched.tick(slots(sandbox_slots=1), free())
        assert len(first.claimed) == 1
        claimed = GenerationJob.objects.get(id=first.claimed[0].job_id)
        assert (claimed.status, claimed.phase, claimed.lease_owner) == (S.RUNNING, "running", OWNER)
        assert claimed.lease_expires_at > timezone.now()
        assert {"started_seq", "claimed_at", "queue_seconds", "executor"} <= set(claimed.execution)
        second = sched.tick(slots(sandbox_slots=1), free())
        assert second.claimed == []
        assert _reasons(second) == {SANDBOX_FULL}
        assert claimed.id in {j.id for j in jobs}

    def test_retried_attempt_queue_time_counts_from_its_own_enqueue(self, user, system_ep):
        job = make_job(user, make_model(), lane="sandbox")
        now = timezone.now()
        GenerationJob.objects.filter(id=job.id).update(
            queued_at=now - timedelta(hours=2),
            attempt=1,
            run_after=now - timedelta(seconds=5),
            execution={"requeued_at": (now - timedelta(seconds=35)).isoformat()},
        )
        result = _scheduler().tick(slots(sandbox_slots=1), free())
        assert len(result.claimed) == 1
        job.refresh_from_db()
        assert 0 <= job.execution["queue_seconds"] < 60

    def test_first_attempt_queue_time_counts_from_queued_at(self, user, system_ep):
        job = make_job(user, make_model(), lane="sandbox")
        GenerationJob.objects.filter(id=job.id).update(queued_at=timezone.now() - timedelta(hours=1))
        _scheduler().tick(slots(sandbox_slots=1), free())
        job.refresh_from_db()
        assert job.execution["queue_seconds"] >= 3500

    def test_admission_is_bounded_by_free_pool_threads(self, user, system_ep):
        for _ in range(3):
            make_job(user, make_model())
        result = _scheduler().tick(slots(llm_job_threads=16), free(llm=1))
        assert len(result.claimed) == 1
        assert _reasons(result) == {LLM_THREADS_FULL}

    def test_a_cancelled_job_still_held_by_its_worker_keeps_its_slot(self, user, system_ep):
        make_job(user, make_model(), lane="sandbox", status=S.CANCELLED, lease_owner=OWNER, phase="")
        make_job(user, make_model(), lane="sandbox")
        result = _scheduler().tick(slots(sandbox_slots=1), free())
        assert result.claimed == []
        assert result.snapshot.sandbox_used == 1

    def test_evaluations_are_claimed_first_and_take_no_sandbox_slot(self, user, system_ep):
        waiting = make_job(
            user,
            make_model(),
            lane="sandbox",
            status=S.RUNNING,
            phase="eval_queued",
            stage_entered_at=timezone.now(),
        )
        fresh = make_job(user, make_model(), lane="sandbox")
        result = _scheduler().tick(slots(sandbox_slots=1, eval_slots=1), free())
        lanes = {str(c.job_id): c.lane for c in result.claimed}
        assert lanes == {str(waiting.id): "eval", str(fresh.id): "sandbox"}
        waiting.refresh_from_db()
        assert (waiting.phase, waiting.lease_owner) == ("evaluating", OWNER)

    def test_draining_claims_nothing(self, user, system_ep):
        make_job(user, make_model())
        make_job(user, make_model(), status=S.RUNNING, phase="eval_queued")
        result = _scheduler().tick(slots(draining=True), free())
        assert result.claimed == []
        assert _reasons(result) == {DRAINING}


class TestEndpoints:
    def test_openrouter_jobs_use_the_system_rows_cap(self, user, system_ep):
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=1)
        for _ in range(3):
            make_job(user, make_model())
        result = _scheduler().tick(slots(), free())
        assert len(result.claimed) == 1
        assert _reasons(result) == {ENDPOINT_FULL}

    def test_local_job_cap_defaults_to_max_concurrency(self, user, system_ep):
        endpoint = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        endpoint.max_concurrency = 2
        endpoint.save()
        model = make_model(endpoint, "m")
        for _ in range(3):
            make_job(user, model)
        result = _scheduler().tick(slots(), free())
        assert len(result.claimed) == 2
        assert result.endpoint_reasons() == {endpoint.pk: ENDPOINT_FULL}

    def test_offline_and_not_served(self, user, system_ep):
        offline = make_endpoint(user, online=False, kind="openai_compatible", api_flavor="openai")
        missing = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("other", "")])
        wrong_digest = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "sha:1")])
        make_job(user, make_model(offline, "m"))
        make_job(user, make_model(missing, "m"))
        make_job(user, make_model(wrong_digest, "m", digest="sha:2"))
        result = _scheduler().tick(slots(), free())
        assert result.claimed == []
        assert result.endpoint_reasons() == {
            offline.pk: ENDPOINT_OFFLINE,
            missing.pk: MODEL_NOT_SERVED,
            wrong_digest.pk: MODEL_NOT_SERVED,
        }

    def test_disabled_endpoint_and_open_circuit(self, user, system_ep):
        disabled = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        disabled.enabled = False
        disabled.save()
        tripped = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        make_job(user, make_model(disabled, "m"))
        make_job(user, make_model(tripped, "m"))
        sched = _scheduler()
        for _ in range(3):
            sched.breaker.record_failure(tripped.pk)
        result = sched.tick(slots(), free())
        assert result.endpoint_reasons() == {disabled.pk: ENDPOINT_OFFLINE, tripped.pk: CIRCUIT_OPEN}


def _k1_endpoint(user, models=("a", "b"), **kw):
    kw.setdefault("kind", "openai_compatible")
    kw.setdefault("api_flavor", "openai")
    kw.setdefault("max_concurrency", 4)
    return make_endpoint(user, served=[(m, "") for m in models], max_resident_models=1, **kw)


class TestResidency:
    @pytest.mark.parametrize("policy", ["blocked", "interleaved"])
    def test_k1_never_starts_a_second_model(self, user, system_ep, policy):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x = running_experiment(user, scheduling_policy=policy)
        # Interleaved order inside one lane: a, b, a
        for seq, model in enumerate([a, b, a]):
            make_job(user, model, experiment=x, sequence=seq)
        other = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("c", "")])
        make_job(user, make_model(other, "c"))
        result = _scheduler().tick(slots(), free())
        # The lane head (a) runs; b waits behind it although a slot is free; the other endpoint proceeds.
        started = GenerationJob.objects.filter(status=S.RUNNING)
        assert {j.model.served_model_name for j in started} == {"a", "c"}
        assert result.endpoint_reasons()[endpoint.pk] == RESIDENCY_WAIT

    def test_other_lanes_with_the_resident_model_proceed(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        make_job(user, a, status=S.RUNNING, phase="running", lease_owner=OWNER)
        x, y = running_experiment(user), running_experiment(user)
        make_job(user, b, experiment=x, sequence=0)
        make_job(user, a, experiment=y, sequence=0)
        result = _scheduler().tick(slots(), free())
        assert {GenerationJob.objects.get(id=c.job_id).model_id for c in result.claimed} == {a.id}

    def test_starvation_guard_drains_for_swap(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        busy = make_job(user, a, status=S.RUNNING, phase="running", lease_owner=OWNER)
        x, y = running_experiment(user), running_experiment(user)
        for seq in range(5):
            make_job(user, a, experiment=x, sequence=seq)
        starved = make_job(user, b, experiment=y, sequence=0)
        sched = _scheduler(starvation_s=0)
        first = sched.tick(slots(), free())
        assert starved.id not in {c.job_id for c in first.claimed}
        assert len(first.claimed) >= 1  # x keeps model a busy
        # Next tick: y has waited past the (patched) limit → drain-for-swap: no new a jobs on the endpoint.
        second = sched.tick(slots(), free())
        assert second.claimed == []
        GenerationJob.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, phase="", lease_owner="")
        third = sched.tick(slots(), free())
        assert [c.job_id for c in third.claimed] == [starved.id]
        assert busy.id not in {c.job_id for c in third.claimed}

    def test_a_starved_lane_whose_model_disappears_releases_the_endpoint(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        make_job(user, a, status=S.RUNNING, phase="running", lease_owner=OWNER)
        x, y = running_experiment(user), running_experiment(user)
        for seq in range(6):
            make_job(user, a, experiment=x, sequence=seq)
        make_job(user, b, experiment=y, sequence=0)
        sched = _scheduler(starvation_s=0)
        sched.tick(slots(), free())  # y waits for residency: its clock starts
        assert sched.tick(slots(), free()).claimed == []  # drain-for-swap holds x
        # `ollama rm b` / relay restarted with a partial list: y can no longer run here.
        RelaySession.objects.filter(endpoint=endpoint).update(models=[{"name": "a", "digest": ""}])
        GenerationJob.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, phase="", lease_owner="")
        third = sched.tick(slots(), free())
        assert third.claimed, "the endpoint must not idle behind a lane that cannot run"
        assert {GenerationJob.objects.get(id=c.job_id).model_id for c in third.claimed} == {a.id}
        assert third.lanes[(str(y.id), endpoint.pk)].reason == MODEL_NOT_SERVED
        assert (str(y.id), endpoint.pk) not in sched.residency_wait_since  # not waiting for residency any more

    def test_a_starved_lane_at_its_experiment_cap_does_not_idle_the_endpoint(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x = running_experiment(user)
        y = running_experiment(user, max_concurrency=1)
        for seq in range(6):
            make_job(user, a, experiment=x, sequence=seq)
        # y is at its cap through its OpenRouter lane; the K=1 endpoint is drained (nothing resident).
        make_job(user, make_model(), experiment=y, status=S.RUNNING, phase="running", lease_owner=OWNER)
        make_job(user, b, experiment=y, sequence=0)
        sched = _scheduler(starvation_s=0)
        sched.residency_wait_since[(str(y.id), endpoint.pk)] = timezone.now() - timedelta(hours=1)
        result = sched.tick(slots(), free())
        assert result.lanes[(str(y.id), endpoint.pk)].reason == EXPERIMENT_CAP
        assert result.claimed, "x keeps the endpoint while y is held by its own cap"
        assert {GenerationJob.objects.get(id=c.job_id).experiment_id for c in result.claimed} == {x.id}

    def test_lanes_held_by_a_drain_do_not_start_their_own_starvation_clock(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x, y = running_experiment(user), running_experiment(user)
        make_job(user, a, experiment=x, status=S.RUNNING, phase="running", lease_owner=OWNER)
        for seq in range(1, 4):
            make_job(user, a, experiment=x, sequence=seq)
        make_job(user, b, experiment=y, sequence=0)
        make_job(user, b, experiment=y, sequence=1)
        sched = _scheduler(starvation_s=600)
        long_ago = timezone.now() - timedelta(hours=1)
        sched.residency_wait_since[(str(y.id), endpoint.pk)] = long_ago
        held = sched.tick(slots(), free())
        assert held.lanes[(str(x.id), endpoint.pk)].reason == RESIDENCY_WAIT
        assert (str(x.id), endpoint.pk) not in sched.residency_wait_since
        # The drain ends after a long time; y's model is swapped in.
        GenerationJob.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, phase="", lease_owner="")
        swapped = sched.tick(slots(), free())
        assert {GenerationJob.objects.get(id=c.job_id).experiment_id for c in swapped.claimed} == {y.id}
        # x's clock starts now (real residency wait), so b keeps the endpoint for a full window.
        sched.tick(slots(), free())
        since = sched.residency_wait_since.get((str(x.id), endpoint.pk))
        assert since is not None
        assert since > long_ago + timedelta(minutes=30)
        assert sched._drain_for_swap(timezone.now(), {(str(x.id), endpoint.pk): [object()]}) == {}

    def test_experiment_option_tightens_k(self, user, system_ep):
        endpoint = make_endpoint(
            user,
            kind="openai_compatible",
            api_flavor="openai",
            served=[("a", ""), ("b", "")],
            max_concurrency=4,
        )
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x = running_experiment(user, execution_options={"max_resident_models": 1})
        make_job(user, a, experiment=x, sequence=0)
        make_job(user, b, experiment=x, sequence=1)
        assert len(_scheduler().tick(slots(), free()).claimed) == 1


class TestOrderAndFairness:
    def test_lane_order_is_the_frozen_sequence(self, user, system_ep):
        x = running_experiment(user)
        jobs = [make_job(user, make_model(), experiment=x, sequence=seq) for seq in (2, 0, 1)]
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=1)
        sched = _scheduler()
        order = []
        for _ in range(3):
            result = sched.tick(slots(), free())
            order += [c.job_id for c in result.claimed]
            GenerationJob.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, lease_owner="")
        assert order == [jobs[1].id, jobs[2].id, jobs[0].id]

    def test_lane_window_lets_small_experiments_and_adhoc_jobs_in(self, user, system_ep):
        big = running_experiment(user)
        now = timezone.now()
        GenerationJob.objects.bulk_create(
            [
                GenerationJob(
                    mode="scaffolding",
                    created_by=user,
                    experiment=big,
                    sequence=i,
                    queued_at=now,
                    lane="llm",
                )
                for i in range(300)
            ],
        )
        small = make_job(user, make_model(), experiment=running_experiment(user), sequence=0)
        adhoc = make_job(user, make_model())
        result = _scheduler().tick(slots(llm_job_threads=32), free(llm=32))
        claimed = {c.job_id for c in result.claimed}
        assert {small.id, adhoc.id} <= claimed
        assert len(claimed) == 32

    def test_fair_share_per_user_first(self, system_ep):
        busy_user, idle_user = UserFactory(), UserFactory()
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=3)
        for _ in range(2):
            make_job(busy_user, make_model(), status=S.RUNNING, phase="running", lease_owner=OWNER)
        make_job(busy_user, make_model(), priority=0)
        theirs = make_job(idle_user, make_model(), priority=0)
        result = _scheduler().tick(slots(), free())
        assert [c.job_id for c in result.claimed] == [theirs.id]

    def test_priority_orders_lanes(self, user, system_ep):
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=1)
        low = running_experiment(user, priority=-5)
        high = running_experiment(user, priority=5)
        make_job(user, make_model(), experiment=low, sequence=0, priority=-5)
        urgent = make_job(user, make_model(), experiment=high, sequence=0, priority=5)
        assert [c.job_id for c in _scheduler().tick(slots(), free()).claimed] == [urgent.id]

    def test_openrouter_lane_does_not_take_every_thread_from_local_lanes(self, user, system_ep):
        endpoint = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        x = running_experiment(user)
        for seq in range(40):
            make_job(user, make_model(), experiment=x, sequence=seq)
        local = make_job(user, make_model(endpoint, "m"), experiment=x, sequence=40)
        sched = _scheduler()
        result = sched.tick(slots(llm_job_threads=4), free(llm=4))
        assert len(result.claimed) == 4
        assert local.id in {c.job_id for c in result.claimed}

    def test_experiment_cap_paused_and_backoff(self, user, system_ep):
        capped = running_experiment(user, max_concurrency=1)
        for seq in range(3):
            make_job(user, make_model(), experiment=capped, sequence=seq)
        paused = running_experiment(user, status=Experiment.Status.PAUSED)
        make_job(user, make_model(), experiment=paused, sequence=0)
        make_job(user, make_model(), run_after=timezone.now() + timedelta(minutes=5))
        result = _scheduler().tick(slots(), free())
        assert len(result.claimed) == 1
        assert GenerationJob.objects.get(id=result.claimed[0].job_id).experiment_id == capped.id
        assert _reasons(result) == {EXPERIMENT_CAP}


def _warmup_enqueue(access, path, body, **kw):
    return LLMRequest.objects.create(
        endpoint=access.endpoint,
        source=kw["source"],
        path=path,
        body=body,
        model_name=kw["model_name"],
        digest=kw.get("digest", ""),
        deadline_at=timezone.now() + timedelta(hours=1),
    ).id


class TestPrefetch:
    @pytest.fixture
    def fake_inference(self):
        def access(user, model):
            return mock.Mock(endpoint=model.endpoint, keep_alive="30m", num_ctx=8192)

        with (
            mock.patch("backend.inference.access.resolve_access", side_effect=access),
            mock.patch("backend.inference.requests.enqueue", side_effect=_warmup_enqueue) as enqueue,
        ):
            yield enqueue

    def test_ollama_head_waits_for_one_warmup(self, user, system_ep, fake_inference):
        endpoint = make_endpoint(user, served=[("qwen", "")], max_concurrency=2)
        model = make_model(endpoint, "qwen")
        jobs = [make_job(user, model) for _ in range(2)]
        sched = _scheduler()
        first = sched.tick(slots(), free())
        assert first.claimed == []
        assert _reasons(first) == {WARMING_MODEL}
        warmup = LLMRequest.objects.get(source="warmup")
        assert (warmup.path, warmup.body["prompt"], warmup.model_name) == ("/api/generate", "", "qwen")
        # Same context as the job's chat calls, or Ollama reloads the model on the first real request.
        assert warmup.body["options"] == {"num_ctx": 8192}
        assert sched.tick(slots(), free()).claimed == []  # still loading; no second warm-up
        assert LLMRequest.objects.filter(source="warmup").count() == 1
        LLMRequest.objects.filter(pk=warmup.pk).update(status="done", load_ms=1500, completed_at=timezone.now())
        third = sched.tick(slots(), free())
        assert {c.job_id for c in third.claimed} == {j.id for j in jobs}
        assert GenerationJob.objects.get(id=jobs[0].id).execution["prefetch_load_s"] == 1.5

    def test_agent_warmup_uses_the_frozen_num_ctx(self, user, system_ep, fake_inference):
        endpoint = make_endpoint(user, served=[("qwen", "")])
        model = make_model(endpoint, "qwen")
        job = make_job(user, model, lane="sandbox")
        bundle = dict(job.resolved_bundle)
        bundle["endpoint"] = {**bundle["endpoint"], "num_ctx": 16384, "keep_alive": "1h"}
        GenerationJob.objects.filter(pk=job.pk).update(resolved_bundle=bundle)
        _scheduler().tick(slots(), free())
        warmup = LLMRequest.objects.get(source="warmup")
        assert warmup.body["options"] == {"num_ctx": 16384}
        assert warmup.body["keep_alive"] == "1h"

    def test_failed_warmup_admits_cold(self, user, system_ep, fake_inference):
        endpoint = make_endpoint(user, served=[("qwen", "")])
        job = make_job(user, make_model(endpoint, "qwen"))
        sched = _scheduler()
        sched.tick(slots(), free())
        LLMRequest.objects.filter(source="warmup").update(status="failed", error="oom", completed_at=timezone.now())
        assert [c.job_id for c in sched.tick(slots(), free()).claimed] == [job.id]
        job.refresh_from_db()
        assert job.execution["prefetch_error"] == "oom"

    def test_loaded_model_and_non_ollama_need_no_warmup(self, user, system_ep, fake_inference):
        loaded = make_endpoint(user, served=[("qwen", "")])
        LLMEndpoint.objects.filter(pk=loaded.pk).update(
            loaded_models=[{"name": "qwen", "at": timezone.now().isoformat()}],
        )
        vllm = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        make_job(user, make_model(loaded, "qwen"))
        make_job(user, make_model(vllm, "m"))
        assert len(_scheduler().tick(slots(), free()).claimed) == 2
        fake_inference.assert_not_called()

    def test_warmup_can_be_turned_off(self, user, system_ep, fake_inference):
        endpoint = make_endpoint(user, served=[("qwen", "")])
        x = running_experiment(user, execution_options={"warmup": False})
        make_job(user, make_model(endpoint, "qwen"), experiment=x, sequence=0)
        assert len(_scheduler().tick(slots(), free()).claimed) == 1
        fake_inference.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_a_row_locked_by_a_concurrent_transaction_is_skipped():
    from backend.inference.access import system_openrouter

    system_openrouter()
    user = UserFactory()
    locked = make_job(user, make_model())
    other = make_job(user, make_model())
    holding, release = threading.Event(), threading.Event()

    def hold():
        try:
            with transaction.atomic():
                list(GenerationJob.objects.select_for_update().filter(id=locked.id))
                holding.set()
                release.wait(10)
        finally:
            connection.close()

    thread = threading.Thread(target=hold)
    thread.start()
    assert holding.wait(10)
    try:
        result = _scheduler().tick(slots(), free())
    finally:
        release.set()
        thread.join(10)
    assert [c.job_id for c in result.claimed] == [other.id]
    assert GenerationJob.objects.get(id=locked.id).status == S.PENDING
    assert len(_scheduler().tick(slots(), free()).claimed) == 1  # picked up next tick


def test_dry_run_writes_nothing(user, system_ep):
    job = make_job(user, make_model())
    result = _scheduler().tick(slots(), free(), dry_run=True)
    assert result.admitted_order == [job.id]
    assert result.claimed == []
    assert GenerationJob.objects.get(id=job.id).status == S.PENDING
