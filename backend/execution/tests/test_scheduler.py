"""The claim algorithm (spec §C.4): slots, endpoint caps, liveness, residency, fairness, prefetch, locking.

Ported to v3: runs of studies (``generation_run`` / ``generation_study``), ``Study.max_parallel``,
``execution_options`` and the pinned served name.
"""

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
from backend.execution.scheduler import LLM_THREADS_FULL
from backend.execution.scheduler import MODEL_NOT_SERVED
from backend.execution.scheduler import RESIDENCY_WAIT
from backend.execution.scheduler import SANDBOX_FULL
from backend.execution.scheduler import STUDY_CAP
from backend.execution.scheduler import WARMING_MODEL
from backend.execution.scheduler import Scheduler
from backend.execution.tests.conftest import free
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import make_run
from backend.execution.tests.conftest import running_study
from backend.execution.tests.conftest import shared_slot_target
from backend.execution.tests.conftest import slots
from backend.generation.models import GenerationRun
from backend.generation.models.choices import RunStatus
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
S = RunStatus
OWNER = "exec-test:1:abcd"


def _scheduler(**kw) -> Scheduler:
    return Scheduler(OWNER, **kw)


def _claimed_ids(result):
    return {str(c.run_id) for c in result.claimed}


def _reasons(result):
    return set(result.blocked().values())


class TestSlots:
    def test_sandbox_slot_cap(self, user, system_ep):
        runs = [make_run(user, make_model(), lane="sandbox") for _ in range(3)]
        sched = _scheduler()
        first = sched.tick(slots(sandbox_slots=1), free())
        assert len(first.claimed) == 1
        claimed = GenerationRun.objects.get(id=first.claimed[0].run_id)
        assert (claimed.status, claimed.phase, claimed.lease_owner) == (S.RUNNING, "running", OWNER)
        assert claimed.lease_expires_at > timezone.now()
        assert {"started_seq", "claimed_at", "queue_seconds", "executor"} <= set(claimed.execution)
        second = sched.tick(slots(sandbox_slots=1), free())
        assert second.claimed == []
        assert _reasons(second) == {SANDBOX_FULL}
        assert claimed.id in {r.id for r in runs}

    def test_retried_attempt_queue_time_counts_from_its_own_enqueue(self, user, system_ep):
        job = make_run(user, make_model(), lane="sandbox")
        now = timezone.now()
        GenerationRun.objects.filter(id=job.id).update(
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
        job = make_run(user, make_model(), lane="sandbox")
        GenerationRun.objects.filter(id=job.id).update(queued_at=timezone.now() - timedelta(hours=1))
        _scheduler().tick(slots(sandbox_slots=1), free())
        job.refresh_from_db()
        assert job.execution["queue_seconds"] >= 3500

    def test_admission_is_bounded_by_free_pool_threads(self, user, system_ep):
        for _ in range(3):
            make_run(user, make_model())
        result = _scheduler().tick(slots(llm_job_threads=16), free(llm=1))
        assert len(result.claimed) == 1
        assert _reasons(result) == {LLM_THREADS_FULL}

    def test_a_cancelled_run_still_held_by_its_worker_keeps_its_slot(self, user, system_ep):
        make_run(user, make_model(), lane="sandbox", status=S.CANCELLED, lease_owner=OWNER, phase="")
        make_run(user, make_model(), lane="sandbox")
        result = _scheduler().tick(slots(sandbox_slots=1), free())
        assert result.claimed == []
        assert result.snapshot.sandbox_used == 1

    def test_evaluations_are_claimed_first_and_take_no_sandbox_slot(self, user, system_ep):
        waiting = make_run(
            user,
            make_model(),
            lane="sandbox",
            status=S.RUNNING,
            phase="eval_queued",
            stage_entered_at=timezone.now(),
        )
        fresh = make_run(user, make_model(), lane="sandbox")
        result = _scheduler().tick(slots(sandbox_slots=1, eval_slots=1), free())
        lanes = {str(c.run_id): c.lane for c in result.claimed}
        assert lanes == {str(waiting.id): "eval", str(fresh.id): "sandbox"}
        waiting.refresh_from_db()
        assert (waiting.phase, waiting.lease_owner) == ("evaluating", OWNER)

    def test_draining_claims_nothing(self, user, system_ep):
        make_run(user, make_model())
        make_run(user, make_model(), status=S.RUNNING, phase="eval_queued")
        result = _scheduler().tick(slots(draining=True), free())
        assert result.claimed == []
        assert _reasons(result) == {DRAINING}


class TestEndpoints:
    def test_openrouter_runs_use_the_system_rows_cap(self, user, system_ep):
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=1)
        for _ in range(3):
            make_run(user, make_model())
        result = _scheduler().tick(slots(), free())
        assert len(result.claimed) == 1
        assert _reasons(result) == {ENDPOINT_FULL}

    def test_local_job_cap_defaults_to_max_concurrency(self, user, system_ep):
        endpoint = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        endpoint.max_concurrency = 2
        endpoint.save()
        model = make_model(endpoint, "m")
        for _ in range(3):
            make_run(user, model)
        result = _scheduler().tick(slots(), free())
        assert len(result.claimed) == 2
        assert result.endpoint_reasons() == {endpoint.pk: ENDPOINT_FULL}

    def test_offline_and_not_served(self, user, system_ep):
        offline = make_endpoint(user, online=False, kind="openai_compatible", api_flavor="openai")
        missing = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("other", "")])
        wrong_digest = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "sha:1")])
        make_run(user, make_model(offline, "m"))
        make_run(user, make_model(missing, "m"))
        make_run(user, make_model(wrong_digest, "m", digest="sha:2"))
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
        make_run(user, make_model(disabled, "m"))
        make_run(user, make_model(tripped, "m"))
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
        x = running_study(user, scheduling=policy)
        # Interleaved order inside one lane: a, b, a
        for seq, model in enumerate([a, b, a]):
            make_run(user, model, study=x, sequence=seq)
        other = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("c", "")])
        make_run(user, make_model(other, "c"))
        result = _scheduler().tick(slots(), free())
        # The lane head (a) runs; b waits behind it although a slot is free; the other endpoint proceeds.
        started = GenerationRun.objects.filter(status=S.RUNNING)
        assert {j.model.served_model_name for j in started} == {"a", "c"}
        assert result.endpoint_reasons()[endpoint.pk] == RESIDENCY_WAIT

    def test_other_lanes_with_the_resident_model_proceed(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        make_run(user, a, status=S.RUNNING, phase="running", lease_owner=OWNER)
        x, y = running_study(user), running_study(user)
        make_run(user, b, study=x, sequence=0)
        make_run(user, a, study=y, sequence=0)
        result = _scheduler().tick(slots(), free())
        assert {GenerationRun.objects.get(id=c.run_id).model_id for c in result.claimed} == {a.id}

    def test_starvation_guard_drains_for_swap(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        busy = make_run(user, a, status=S.RUNNING, phase="running", lease_owner=OWNER)
        x, y = running_study(user), running_study(user)
        for seq in range(5):
            make_run(user, a, study=x, sequence=seq)
        starved = make_run(user, b, study=y, sequence=0)
        sched = _scheduler(starvation_s=0)
        first = sched.tick(slots(), free())
        assert starved.id not in {c.run_id for c in first.claimed}
        assert len(first.claimed) >= 1  # x keeps model a busy
        # Next tick: y has waited past the (patched) limit → drain-for-swap: no new a jobs on the endpoint.
        second = sched.tick(slots(), free())
        assert second.claimed == []
        GenerationRun.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, phase="", lease_owner="")
        third = sched.tick(slots(), free())
        assert [c.run_id for c in third.claimed] == [starved.id]
        assert busy.id not in {c.run_id for c in third.claimed}

    def test_a_starved_lane_whose_model_disappears_releases_the_endpoint(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        make_run(user, a, status=S.RUNNING, phase="running", lease_owner=OWNER)
        x, y = running_study(user), running_study(user)
        for seq in range(6):
            make_run(user, a, study=x, sequence=seq)
        make_run(user, b, study=y, sequence=0)
        sched = _scheduler(starvation_s=0)
        sched.tick(slots(), free())  # y waits for residency: its clock starts
        assert sched.tick(slots(), free()).claimed == []  # drain-for-swap holds x
        # `ollama rm b` / relay restarted with a partial list: y can no longer run here.
        RelaySession.objects.filter(endpoint=endpoint).update(models=[{"name": "a", "digest": ""}])
        GenerationRun.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, phase="", lease_owner="")
        third = sched.tick(slots(), free())
        assert third.claimed, "the endpoint must not idle behind a lane that cannot run"
        assert {GenerationRun.objects.get(id=c.run_id).model_id for c in third.claimed} == {a.id}
        assert third.lanes[(str(y.id), endpoint.pk)].reason == MODEL_NOT_SERVED
        assert (str(y.id), endpoint.pk) not in sched.residency_wait_since  # not waiting for residency any more

    def test_a_starved_lane_at_its_study_cap_does_not_idle_the_endpoint(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x = running_study(user)
        y = running_study(user, max_parallel=1)
        for seq in range(6):
            make_run(user, a, study=x, sequence=seq)
        # y is at its cap through its OpenRouter lane; the K=1 endpoint is drained (nothing resident).
        make_run(user, make_model(), study=y, status=S.RUNNING, phase="running", lease_owner=OWNER)
        make_run(user, b, study=y, sequence=0)
        sched = _scheduler(starvation_s=0)
        sched.residency_wait_since[(str(y.id), endpoint.pk)] = timezone.now() - timedelta(hours=1)
        result = sched.tick(slots(), free())
        assert result.lanes[(str(y.id), endpoint.pk)].reason == STUDY_CAP
        assert result.claimed, "x keeps the endpoint while y is held by its own cap"
        assert {GenerationRun.objects.get(id=c.run_id).study_id for c in result.claimed} == {x.id}

    def test_lanes_held_by_a_drain_do_not_start_their_own_starvation_clock(self, user, system_ep):
        endpoint = _k1_endpoint(user)
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x, y = running_study(user), running_study(user)
        make_run(user, a, study=x, status=S.RUNNING, phase="running", lease_owner=OWNER)
        for seq in range(1, 4):
            make_run(user, a, study=x, sequence=seq)
        make_run(user, b, study=y, sequence=0)
        make_run(user, b, study=y, sequence=1)
        sched = _scheduler(starvation_s=600)
        long_ago = timezone.now() - timedelta(hours=1)
        sched.residency_wait_since[(str(y.id), endpoint.pk)] = long_ago
        held = sched.tick(slots(), free())
        assert held.lanes[(str(x.id), endpoint.pk)].reason == RESIDENCY_WAIT
        assert (str(x.id), endpoint.pk) not in sched.residency_wait_since
        # The drain ends after a long time; y's model is swapped in.
        GenerationRun.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, phase="", lease_owner="")
        swapped = sched.tick(slots(), free())
        assert {GenerationRun.objects.get(id=c.run_id).study_id for c in swapped.claimed} == {y.id}
        # x's clock starts now (real residency wait), so b keeps the endpoint for a full window.
        sched.tick(slots(), free())
        since = sched.residency_wait_since.get((str(x.id), endpoint.pk))
        assert since is not None
        assert since > long_ago + timedelta(minutes=30)
        assert sched._drain_for_swap(timezone.now(), {(str(x.id), endpoint.pk): [object()]}) == {}

    def test_study_execution_option_tightens_k(self, user, system_ep):
        endpoint = make_endpoint(
            user,
            kind="openai_compatible",
            api_flavor="openai",
            served=[("a", ""), ("b", "")],
            max_concurrency=4,
        )
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x = running_study(user, execution_options={"max_resident_models": 1})
        make_run(user, a, study=x, sequence=0)
        make_run(user, b, study=x, sequence=1)
        assert len(_scheduler().tick(slots(), free()).claimed) == 1

    def test_study_residency_does_not_serialize_models_on_a_remote_api(self, user, system_ep):
        # OpenCode Go shape: openai_compatible, direct, list price, no declared K, job cap 3.
        endpoint = make_endpoint(
            user,
            kind="openai_compatible",
            api_flavor="openai",
            connection="direct",
            base_url="https://example.invalid/v1",
            cost_basis="list_price",
            served=[("a", ""), ("b", "")],
            max_concurrency=2,
            max_active_jobs=3,
        )
        a, b = make_model(endpoint, "a"), make_model(endpoint, "b")
        x = running_study(user, execution_options={"max_resident_models": 1})
        y = running_study(user, execution_options={"max_resident_models": 1, "per_endpoint": 1})
        make_run(user, a, study=x, sequence=0)
        make_run(user, b, study=x, sequence=1)
        make_run(user, b, study=y, sequence=0)
        make_run(user, a, study=y, sequence=1)
        result = _scheduler().tick(slots(), free())
        claimed = [GenerationRun.objects.get(id=c.run_id) for c in result.claimed]
        # Both models of x run at once (no residency wait); y is held at its per_endpoint=1; the
        # endpoint's max_active_jobs=3 caps the total.
        assert len(claimed) == 3
        assert {r.model_id for r in claimed if r.study_id == x.id} == {a.id, b.id}
        assert [r.model_id for r in claimed if r.study_id == y.id] == [b.id]
        assert RESIDENCY_WAIT not in {lane.reason for lane in result.lanes.values()}
        # A fourth run (another study) waits for the endpoint's job cap, not for residency.
        z = running_study(user, execution_options={"max_resident_models": 1})
        make_run(user, a, study=z, sequence=0)
        again = _scheduler().tick(slots(), free())
        assert again.claimed == []
        assert again.lanes[(str(z.id), endpoint.pk)].reason == ENDPOINT_FULL


class TestOrderAndFairness:
    def test_lane_order_is_the_frozen_sequence(self, user, system_ep):
        x = running_study(user)
        runs = [make_run(user, make_model(), study=x, sequence=seq) for seq in (2, 0, 1)]
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=1)
        sched = _scheduler()
        order = []
        for _ in range(3):
            result = sched.tick(slots(), free())
            order += [c.run_id for c in result.claimed]
            GenerationRun.objects.filter(status=S.RUNNING).update(status=S.COMPLETED, lease_owner="")
        assert order == [runs[1].id, runs[2].id, runs[0].id]

    def test_lane_window_lets_small_studies_and_adhoc_runs_in(self, user, system_ep):
        big = running_study(user)
        now = timezone.now()
        target = shared_slot_target()
        model = make_model()
        GenerationRun.objects.bulk_create(
            [
                GenerationRun(
                    kind="slot_single_shot",
                    protocol="slot@1",
                    slot_target=target,
                    parent_run=target.slot_set.parent_run,
                    model=model,
                    created_by=user,
                    study=big,
                    sequence=i,
                    queued_at=now,
                    lane="llm",
                )
                for i in range(300)
            ],
        )
        small = make_run(user, make_model(), study=running_study(user), sequence=0)
        adhoc = make_run(user, make_model())
        result = _scheduler().tick(slots(llm_job_threads=32), free(llm=32))
        claimed = {c.run_id for c in result.claimed}
        assert {small.id, adhoc.id} <= claimed
        assert len(claimed) == 32

    def test_fair_share_per_user_first(self, system_ep):
        busy_user, idle_user = UserFactory(), UserFactory()
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=3)
        for _ in range(2):
            make_run(busy_user, make_model(), status=S.RUNNING, phase="running", lease_owner=OWNER)
        make_run(busy_user, make_model(), priority=0)
        theirs = make_run(idle_user, make_model(), priority=0)
        result = _scheduler().tick(slots(), free())
        assert [c.run_id for c in result.claimed] == [theirs.id]

    def test_priority_orders_lanes(self, user, system_ep):
        LLMEndpoint.objects.filter(pk=system_ep.pk).update(max_active_jobs=1)
        low = running_study(user, priority=-5)
        high = running_study(user, priority=5)
        make_run(user, make_model(), study=low, sequence=0, priority=-5)
        urgent = make_run(user, make_model(), study=high, sequence=0, priority=5)
        assert [c.run_id for c in _scheduler().tick(slots(), free()).claimed] == [urgent.id]

    def test_openrouter_lane_does_not_take_every_thread_from_local_lanes(self, user, system_ep):
        endpoint = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        x = running_study(user)
        for seq in range(40):
            make_run(user, make_model(), study=x, sequence=seq)
        local = make_run(user, make_model(endpoint, "m"), study=x, sequence=40)
        sched = _scheduler()
        result = sched.tick(slots(llm_job_threads=4), free(llm=4))
        assert len(result.claimed) == 4
        assert local.id in {c.run_id for c in result.claimed}

    def test_study_cap_paused_and_backoff(self, user, system_ep):
        capped = running_study(user, max_parallel=1)
        for seq in range(3):
            make_run(user, make_model(), study=capped, sequence=seq)
        paused = running_study(user, status="paused")
        make_run(user, make_model(), study=paused, sequence=0)
        make_run(user, make_model(), run_after=timezone.now() + timedelta(minutes=5))
        result = _scheduler().tick(slots(), free())
        assert len(result.claimed) == 1
        assert GenerationRun.objects.get(id=result.claimed[0].run_id).study_id == capped.id
        assert _reasons(result) == {STUDY_CAP}


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
        runs = [make_run(user, model) for _ in range(2)]
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
        assert {c.run_id for c in third.claimed} == {r.id for r in runs}
        assert GenerationRun.objects.get(id=runs[0].id).execution["prefetch_load_s"] == 1.5

    def test_failed_warmup_admits_cold(self, user, system_ep, fake_inference):
        endpoint = make_endpoint(user, served=[("qwen", "")])
        job = make_run(user, make_model(endpoint, "qwen"))
        sched = _scheduler()
        sched.tick(slots(), free())
        LLMRequest.objects.filter(source="warmup").update(status="failed", error="oom", completed_at=timezone.now())
        assert [c.run_id for c in sched.tick(slots(), free()).claimed] == [job.id]
        job.refresh_from_db()
        assert job.execution["prefetch_error"] == "oom"

    def test_loaded_model_and_non_ollama_need_no_warmup(self, user, system_ep, fake_inference):
        loaded = make_endpoint(user, served=[("qwen", "")])
        LLMEndpoint.objects.filter(pk=loaded.pk).update(
            loaded_models=[{"name": "qwen", "at": timezone.now().isoformat()}],
        )
        vllm = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "")])
        make_run(user, make_model(loaded, "qwen"))
        make_run(user, make_model(vllm, "m"))
        assert len(_scheduler().tick(slots(), free()).claimed) == 2
        fake_inference.assert_not_called()

    def test_warmup_can_be_turned_off(self, user, system_ep, fake_inference):
        endpoint = make_endpoint(user, served=[("qwen", "")])
        x = running_study(user, execution_options={"warmup": False})
        make_run(user, make_model(endpoint, "qwen"), study=x, sequence=0)
        assert len(_scheduler().tick(slots(), free()).claimed) == 1
        fake_inference.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_a_row_locked_by_a_concurrent_transaction_is_skipped():
    from backend.inference.access import system_openrouter

    system_openrouter()
    user = UserFactory()
    locked = make_run(user, make_model())
    other = make_run(user, make_model())
    holding, release = threading.Event(), threading.Event()

    def hold():
        try:
            with transaction.atomic():
                list(GenerationRun.objects.select_for_update().filter(id=locked.id))
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
    assert [c.run_id for c in result.claimed] == [other.id]
    assert GenerationRun.objects.get(id=locked.id).status == S.PENDING
    assert len(_scheduler().tick(slots(), free()).claimed) == 1  # picked up next tick


def test_dry_run_writes_nothing(user, system_ep):
    job = make_run(user, make_model())
    result = _scheduler().tick(slots(), free(), dry_run=True)
    assert result.admitted_order == [job.id]
    assert result.claimed == []
    assert GenerationRun.objects.get(id=job.id).status == S.PENDING


class TestStudies:
    @pytest.mark.parametrize(("status", "claimed"), [("piloting", True), ("running", True), ("planned", False)])
    def test_runs_are_claimed_while_the_study_pilots_or_runs(self, user, system_ep, status, claimed):
        study = running_study(user, status=status)
        run = make_run(user, make_model(), study=study, sequence=0)
        result = _scheduler().tick(slots(), free())
        assert ([c.run_id for c in result.claimed] == [run.id]) is claimed

    def test_a_claim_carries_the_study_cell_and_condition(self, user, system_ep):
        from backend.generation.tests.factories import StudyCellFactory

        study = running_study(user, scheduling="blocked")
        cell = StudyCellFactory(condition__plan__study=study, repeat_index=1)
        run = make_run(
            user,
            make_model(),
            study=study,
            cell=cell,
            condition=cell.condition,
            repeat_index=1,
            sequence=0,
        )
        [claim] = _scheduler().tick(slots(), free()).claimed
        assert (claim.run_id, claim.study_id, claim.cell_id, claim.condition_id, claim.repeat_index, claim.lane) == (
            run.id,
            study.id,
            cell.id,
            cell.condition_id,
            1,
            "llm",
        )

    def test_lane_rows_read_the_v3_columns(self, user, system_ep):
        from backend.execution.scheduler import read_lane_heads

        endpoint = make_endpoint(user, kind="openai_compatible", api_flavor="openai", served=[("m", "sha:9")])
        study = running_study(user, scheduling="interleaved", max_parallel=3, execution_options={"warmup": False})
        run = make_run(user, make_model(endpoint, "m", digest="sha:9"), study=study, sequence=4)
        [row] = [r for r in read_lane_heads(system_ep.pk, 8) if r.id == run.id]
        assert (row.served_name, row.digest, row.scheduling, row.study_max_parallel) == (
            "m",
            "sha:9",
            "interleaved",
            3,
        )
        assert (row.slot_target_id, row.lane_key) == (run.slot_target_id, (str(study.id), endpoint.pk))
        assert row.warmup_enabled is False

    def test_lanes_are_per_study_and_endpoint(self, user, system_ep):
        x, y = running_study(user, max_parallel=1), running_study(user, max_parallel=1)
        for study in (x, y):
            for seq in range(2):
                make_run(user, make_model(), study=study, sequence=seq)
        result = _scheduler().tick(slots(), free())
        assert sorted(str(c.study_id) for c in result.claimed) == sorted([str(x.id), str(y.id)])
        assert _reasons(result) == {STUDY_CAP}
