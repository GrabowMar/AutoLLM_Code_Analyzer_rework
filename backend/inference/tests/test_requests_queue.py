"""The durable request queue (spec §D.2, §E.2): claim caps, leases, dedupe waiters, cancel, purge."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.db import connection
from django.utils import timezone

from backend.inference import doorbell
from backend.inference import requests as q
from backend.inference.access import endpoint_access
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.inference.tests.conftest import UNUSED_HASH
from backend.inference.tests.conftest import make_endpoint

Status = LLMRequest.Status


def _enqueue(endpoint, n=1, **kwargs):
    access = endpoint_access(endpoint, served_model_name=kwargs.pop("model", "m:7b"))
    return [q.enqueue(access, "/api/chat", {"x": i}, **kwargs) for i in range(n)]


def _session(endpoint, *, capacity=8, models=(("m:7b", ""),)):
    token = RelayToken.objects.create(
        endpoint=endpoint,
        prefix=f"llr_t{timezone.now().timestamp():.0f}"[:16],
        secret_hash=UNUSED_HASH,
    )
    return RelaySession.objects.create(
        endpoint=endpoint,
        token=token,
        relay_version="1.0.0",
        upstream_kind="ollama",
        capacity=capacity,
        last_seen_at=timezone.now(),
        models=[{"name": n, "digest": d} for n, d in models],
    )


@pytest.mark.django_db
class TestClaimAndComplete:
    def test_fifo_with_source_offsets(self, endpoint):
        analysis = _enqueue(endpoint, source="analysis")[0]
        probe = _enqueue(endpoint, source="probe")[0]
        first, second = _enqueue(endpoint, n=2, priority=0)
        high = _enqueue(endpoint, priority=5)[0]
        endpoint.max_concurrency = 10
        endpoint.save()
        order = [r.pk for r in q.claim(endpoint.pk, limit=10, pump_owner="p")]
        assert order == [high, first, second, probe, analysis]

    def test_endpoint_and_session_caps(self, endpoint):
        _enqueue(endpoint, n=5)
        session = _session(endpoint, capacity=1)
        assert len(q.claim(endpoint.pk, limit=5, session=session)) == 1
        assert q.claim(endpoint.pk, limit=5, session=session) == []
        # endpoint.max_concurrency = 2: one more slot for a pump
        assert len(q.claim(endpoint.pk, limit=5, pump_owner="p")) == 1
        assert q.claim(endpoint.pk, limit=5, pump_owner="p") == []

    def test_digest_and_name_filter(self, endpoint):
        access = endpoint_access(endpoint, served_model_name="m:7b")
        wanted = q.enqueue(access, "/api/chat", {}, digest="sha256:aa")
        q.enqueue(access, "/api/chat", {}, digest="sha256:bb")
        q.enqueue(endpoint_access(endpoint, served_model_name="other:1b"), "/api/chat", {})
        version = q.enqueue(endpoint_access(endpoint), "/api/version", None, method="GET")
        endpoint.max_concurrency = 10
        endpoint.save()
        rows = q.claim(endpoint.pk, limit=10, pump_owner="p", served={("m:7b", "sha256:aa")})
        assert {r.pk for r in rows} == {wanted, version}

    def test_complete_is_idempotent_and_claim_scoped(self, endpoint):
        rid = _enqueue(endpoint)[0]
        row = q.claim(endpoint.pk, limit=1, pump_owner="p")[0]
        assert q.complete(rid, "wrong", pump_owner="p", ok=True, payload={}).reason == "stale_claim"
        assert q.complete(rid, row.claim_token, pump_owner="other", ok=True, payload={}).reason == "stale_claim"
        native = {"message": {"content": "x"}, "prompt_eval_count": 3, "eval_count": 2, "eval_duration": 2_000_000}
        outcome = q.complete(rid, row.claim_token, pump_owner="p", ok=True, status_code=200, payload=native)
        assert outcome.accepted
        assert q.complete(rid, row.claim_token, pump_owner="p", ok=True, payload={}).reason == "already_done"
        done = LLMRequest.objects.get(pk=rid)
        assert (done.status, done.body, done.prompt_tokens, done.completion_tokens, done.eval_ms) == (
            Status.DONE,
            None,
            3,
            2,
            2,
        )
        assert done.response == native

    def test_retryable_errors_requeue_then_fail(self, endpoint):
        rid = _enqueue(endpoint)[0]
        for attempt in range(3):
            row = q.claim(endpoint.pk, limit=1, pump_owner="p")[0]
            assert row.attempt == attempt
            q.complete(rid, row.claim_token, pump_owner="p", ok=False, error_kind="connect", error_message="refused")
        row = LLMRequest.objects.get(pk=rid)
        assert (row.status, row.error_kind) == (Status.FAILED, "connect")

    def test_http_error_is_done_with_status(self, endpoint):
        rid = _enqueue(endpoint)[0]
        row = q.claim(endpoint.pk, limit=1, pump_owner="p")[0]
        q.complete(rid, row.claim_token, pump_owner="p", ok=True, status_code=500, payload={"error": "oom"})
        row = LLMRequest.objects.get(pk=rid)
        assert (row.status, row.response_status, row.error_kind) == (Status.DONE, 500, "http")


@pytest.mark.django_db
class TestLeases:
    def test_unacked_lease_expiry_requeues_without_attempt(self, endpoint):
        rid = _enqueue(endpoint)[0]
        session = _session(endpoint)
        q.claim(endpoint.pk, limit=1, session=session)
        assert q.requeue_expired(timezone.now() + timedelta(seconds=61)) == 1
        row = LLMRequest.objects.get(pk=rid)
        assert (row.status, row.attempt, row.session_id, row.claim_token) == (Status.QUEUED, 0, None, "")

    def test_acked_lease_expiry_uses_attempts(self, endpoint):
        rid = _enqueue(endpoint)[0]
        for _ in range(3):
            q.claim(endpoint.pk, limit=1, pump_owner="p")
            q.requeue_expired(timezone.now() + timedelta(seconds=1000))
        row = LLMRequest.objects.get(pk=rid)
        assert (row.status, row.error_kind) == (Status.FAILED, "lease_expired")

    def test_ack_extends_and_stale_pairs_are_cancelled(self, endpoint):
        live, stale = _enqueue(endpoint, n=2)
        session = _session(endpoint)
        rows = {r.pk: r for r in q.claim(endpoint.pk, limit=2, session=session)}
        outcome = q.process_inflight(
            session,
            [
                {"id": str(live), "claim_token": rows[live].claim_token},
                {"id": str(stale), "claim_token": "token-of-an-earlier-claim"},
            ],
            polled_at=timezone.now(),
            request_timeout_s=900,
        )
        assert outcome.acked == [str(live)]
        assert outcome.cancel == [{"id": str(stale), "claim_token": "token-of-an-earlier-claim"}]
        acked = LLMRequest.objects.get(pk=live)
        assert acked.acked_at is not None
        assert acked.lease_expires_at > timezone.now() + timedelta(seconds=900)
        not_extended = LLMRequest.objects.get(pk=stale)
        assert not_extended.acked_at is None
        assert not_extended.lease_expires_at < timezone.now() + timedelta(seconds=61)

    def test_lost_claims_are_requeued_without_attempt(self, endpoint):
        rid = _enqueue(endpoint)[0]
        session = _session(endpoint)
        q.claim(endpoint.pk, limit=1, session=session)
        LLMRequest.objects.filter(pk=rid).update(leased_at=timezone.now() - timedelta(seconds=11))
        outcome = q.process_inflight(session, [], polled_at=timezone.now(), request_timeout_s=900)
        assert outcome.lost == 1
        row = LLMRequest.objects.get(pk=rid)
        assert (row.status, row.attempt) == (Status.QUEUED, 0)

    def test_session_requeue_and_cancel_listed(self, endpoint, local_model):
        rid = _enqueue(endpoint)[0]
        session = _session(endpoint)
        row = q.claim(endpoint.pk, limit=1, session=session)[0]
        q.cancel_request(rid)
        assert q.cancelled_among(session, [{"id": str(rid), "claim_token": row.claim_token}]) == [
            {"id": str(rid), "claim_token": row.claim_token},
        ]
        other = _enqueue(endpoint)[0]
        q.claim(endpoint.pk, limit=1, session=session)
        assert q.requeue_session(session, reason="bye") == 1
        assert LLMRequest.objects.get(pk=other).status == Status.QUEUED

    def test_deadline_expires(self, endpoint):
        rid = _enqueue(endpoint)[0]
        LLMRequest.objects.filter(pk=rid).update(deadline_at=timezone.now() - timedelta(seconds=1))
        q.requeue_expired()
        assert LLMRequest.objects.get(pk=rid).status == Status.EXPIRED

    def test_purge_keeps_metrics(self, endpoint):
        rid = _enqueue(endpoint)[0]
        LLMRequest.objects.filter(pk=rid).update(
            created_at=timezone.now() - timedelta(days=8),
            response={"x": 1},
            prompt_tokens=5,
        )
        assert q.purge_old() == 1
        row = LLMRequest.objects.get(pk=rid)
        assert (row.body, row.response, row.prompt_tokens) == (None, None, 5)


@pytest.mark.django_db
def test_cancel_for_jobs_by_attempt(endpoint, local_model):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(model=local_model, created_by=endpoint.owner)
    access = endpoint_access(endpoint, served_model_name="m:7b")
    keep = q.enqueue(access, "/api/chat", {}, job=job)
    job.attempt = 1
    drop = q.enqueue(access, "/api/chat", {}, job=job)
    assert q.cancel_for_jobs([job.pk], attempt=1) == 1
    assert LLMRequest.objects.get(pk=drop).status == Status.CANCELLED
    assert LLMRequest.objects.get(pk=keep).status == Status.QUEUED


@pytest.mark.django_db(transaction=True)
def test_caps_hold_under_50_concurrent_claimers(owner):
    endpoint = make_endpoint(owner, max_concurrency=3)
    _enqueue(endpoint, n=40)
    session = _session(endpoint, capacity=2)
    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(50)

    def worker(i):
        try:
            barrier.wait()
            rows = q.claim(endpoint.pk, limit=2, session=session if i % 2 else None, pump_owner="" if i % 2 else "p")
            with lock:
                claimed.extend(str(r.pk) for r in rows)
        finally:
            close_old_connections()
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == len(set(claimed)) == 3
    assert LLMRequest.objects.filter(endpoint=endpoint, status=Status.LEASED, session=session).count() <= 2


@pytest.mark.django_db(transaction=True)
def test_two_waiters_share_one_response(owner):
    endpoint = make_endpoint(owner)
    rid = _enqueue(endpoint)[0]
    results = []

    def waiter():
        try:
            results.append(q.wait_result(rid, max_wait=20))
        finally:
            connection.close()

    threads = [threading.Thread(target=waiter) for _ in range(2)]
    for t in threads:
        t.start()
    row = q.claim(endpoint.pk, limit=1, pump_owner="p")[0]
    q.complete(rid, row.claim_token, pump_owner="p", ok=True, status_code=200, payload={"message": {"content": "hi"}})
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 2
    assert all(r.status == Status.DONE and r.response == {"message": {"content": "hi"}} for r in results)
    assert sum(1 for r in results if r.first_delivery) == 1
    assert LLMRequest.objects.get(pk=rid).response is not None  # kept after delivery


@pytest.mark.django_db(transaction=True)
def test_wait_result_max_wait_and_cancel_check(owner):
    endpoint = make_endpoint(owner)
    rid = _enqueue(endpoint)[0]
    with pytest.raises(q.WaitTimeout):
        q.wait_result(rid, max_wait=0.2)
    assert LLMRequest.objects.get(pk=rid).status == Status.QUEUED
    row = q.wait_result(rid, cancel_check=lambda: True)
    assert row.status == Status.CANCELLED


def test_doorbell_list_is_trimmed_to_one(settings):
    for _ in range(5):
        doorbell.ring_endpoint(42)
    client = doorbell.client()
    assert client.llen(doorbell.wake_key(42)) == 1
    assert 0 < client.ttl(doorbell.wake_key(42)) <= doorbell.WAKE_TTL_S
    client.delete(doorbell.wake_key(42))
