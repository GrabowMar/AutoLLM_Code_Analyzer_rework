"""Relay protocol v1 views (spec §E): auth, sessions, long-poll, leases, results, limits."""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.test import Client
from django.utils import timezone

from backend.inference import requests as q
from backend.inference import tokens as relay_tokens
from backend.inference.access import endpoint_access
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.relay import views
from backend.inference.tests.conftest import make_endpoint

BASE = "/api/relay/v1/"
HELLO = {
    "relay_version": "1.0.0",
    "relay_id": "11111111-1111-1111-1111-111111111111",
    "hostname": "gpu-box-1",
    "upstream": {"kind": "ollama", "url_hint": "http://127.0.0.1:11434", "version": "0.12.3"},
    "parallel": 2,
}


@pytest.fixture(autouse=True)
def _fast_polls(settings):
    settings.RELAY_POLL_TIMEOUT_S = 1
    settings.RELAY_RATE_LIMIT_PER_S = 1000
    settings.TRUSTED_PROXY_CIDRS = []


class Relay:
    """A test-side relay speaking the protocol through the Django test client."""

    def __init__(self, raw_token: str, *, relay_id: str = HELLO["relay_id"], client: Client | None = None):
        self.client = client or Client()
        self.token = raw_token
        self.relay_id = relay_id
        self.session_id = ""

    def post(self, path: str, payload=None, *, raw: bytes | None = None, **headers):
        data = raw if raw is not None else json.dumps(payload or {}).encode()
        extra = {"HTTP_AUTHORIZATION": f"Bearer {self.token}", **headers}
        if self.session_id:
            extra["HTTP_X_RELAY_SESSION"] = self.session_id
        return self.client.post(BASE + path, data=data, content_type="application/json", **extra)

    def hello(self, **overrides):
        resp = self.post("hello", {**HELLO, "relay_id": self.relay_id, **overrides})
        if resp.status_code == 200 and resp.json().get("session_id"):
            self.session_id = resp.json()["session_id"]
        return resp

    def poll(self, inflight=(), free_slots=2):
        return self.post("poll", {"inflight": list(inflight), "free_slots": free_slots})


@pytest.fixture
def relay_setup(owner):
    endpoint = make_endpoint(owner, max_concurrency=2)
    raw, token = relay_tokens.generate_token(endpoint, label="t", created_by=owner)
    return endpoint, raw, token


def _enqueue(endpoint, n=1, model="m:7b", digest=""):
    access = endpoint_access(endpoint, served_model_name=model)
    return [q.enqueue(access, "/api/chat", {"model": model, "i": i}, digest=digest) for i in range(n)]


def _serve(session_id, models=(("m:7b", ""),)):
    RelaySession.objects.filter(pk=session_id).update(models=[{"name": n, "digest": d} for n, d in models])


@pytest.mark.django_db
class TestAuth:
    def test_bad_tokens_are_401(self, relay_setup):
        _endpoint, raw, token = relay_setup
        prefix, _, _secret = raw.partition(".")
        for bad in ("", "nonsense", f"llr_zzzzzzzz.{_secret}", f"{prefix}.wrong-secret"):
            resp = Relay(bad).hello()
            assert resp.status_code == 401
            assert resp.json() == {"error": "unauthorized"}
        relay_tokens.revoke(token)
        assert Relay(raw).hello().status_code == 401

    def test_rotation_grace_then_401(self, relay_setup, owner):
        _endpoint, raw, token = relay_setup
        new_raw, _new, _exp = relay_tokens.rotate(token, grace_minutes=60, user=owner)
        assert Relay(raw).hello().status_code == 200
        assert Relay(new_raw).hello(relay_id="22222222-2222-2222-2222-222222222222").status_code == 200
        type(token).objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        assert Relay(raw).hello().status_code == 401

    def test_rotated_token_reconnects_the_same_relay(self, relay_setup, owner):
        endpoint, raw, token = relay_setup
        old = Relay(raw)
        assert old.hello().json()["role"] == "active"
        new_raw, _new, _exp = relay_tokens.rotate(token, grace_minutes=60, user=owner)
        # The relay re-reads its token file (SIGHUP) and says hello again with the same relay_id.
        new = Relay(new_raw)
        resp = new.hello()
        assert resp.status_code == 200
        assert resp.json()["role"] == "active"
        assert RelaySession.objects.get(pk=old.session_id).ended_reason == "replaced"
        assert RelaySession.objects.filter(endpoint=endpoint, ended_at__isnull=True).count() == 1

    def test_disabled_endpoint_keeps_relay_alive(self, relay_setup):
        endpoint, raw, _token = relay_setup
        endpoint.enabled = False
        endpoint.save()
        relay = Relay(raw)
        doc = relay.hello().json()
        assert doc["endpoint_enabled"] is False
        _enqueue(endpoint)
        _serve(relay.session_id)
        poll = relay.poll().json()
        assert (poll["session_valid"], poll["endpoint_enabled"], poll["requests"]) == (True, False, [])

    def test_rate_limit(self, relay_setup, settings):
        settings.RELAY_RATE_LIMIT_PER_S = 2
        _endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        codes = [relay.hello(probe=True).status_code for _ in range(4)]
        assert 429 in codes
        assert codes[0] == 200


@pytest.mark.django_db
class TestHello:
    def test_kind_mismatch_and_old_version(self, relay_setup):
        _endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        assert relay.hello(upstream={"kind": "openai_compatible"}).status_code == 409
        resp = relay.hello(relay_version="0.9.0")
        assert resp.status_code == 426
        assert resp.json()["min_relay_version"] == "1.0.0"

    def test_probe_creates_no_session(self, relay_setup):
        endpoint, raw, _token = relay_setup
        doc = Relay(raw).hello(probe=True).json()
        assert doc["session_id"] is None
        assert doc["endpoint"]["slug"] == endpoint.slug
        assert not RelaySession.objects.filter(endpoint=endpoint).exists()

    def test_capacity_and_standby_promotion(self, relay_setup):
        _endpoint, raw, _token = relay_setup
        first = Relay(raw)
        assert first.hello(parallel=8).json()["role"] == "active"
        assert RelaySession.objects.get(pk=first.session_id).capacity == 2  # min(parallel, max_concurrency)
        second = Relay(raw, relay_id="22222222-2222-2222-2222-222222222222")
        assert second.hello().json()["role"] == "standby"
        # the same relay reconnecting replaces its old session instead
        again = Relay(raw)
        assert again.hello().json()["role"] == "active"
        assert RelaySession.objects.get(pk=first.session_id).ended_reason == "replaced"
        assert again.post("bye", {"inflight": []}).status_code == 200
        assert RelaySession.objects.get(pk=second.session_id).role == "active"

    def test_standby_is_promoted_when_active_times_out(self, relay_setup):
        _endpoint, raw, _token = relay_setup
        first = Relay(raw)
        first.hello()
        second = Relay(raw, relay_id="22222222-2222-2222-2222-222222222222")
        second.hello()
        RelaySession.objects.filter(pk=first.session_id).update(last_seen_at=timezone.now() - timedelta(minutes=5))
        assert second.poll().json()["role"] == "active"


@pytest.mark.django_db
class TestPoll:
    def test_claims_then_returns_empty_after_timeout(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        doc = relay.poll().json()
        assert [r["id"] for r in doc["requests"]] == [str(rid)]
        claim = doc["requests"][0]
        assert claim["upstream"] == {"method": "POST", "path": "/api/chat", "json": {"model": "m:7b", "i": 0}}
        row = LLMRequest.objects.get(pk=rid)
        assert row.lease_expires_at <= timezone.now() + timedelta(seconds=60)
        started = time.monotonic()
        empty = relay.poll(inflight=[{"id": str(rid), "claim_token": claim["claim_token"]}]).json()
        assert empty["requests"] == []
        assert 0.8 <= time.monotonic() - started < 5
        row.refresh_from_db()
        assert row.acked_at is not None
        assert row.lease_expires_at > timezone.now() + timedelta(seconds=900)

    def test_only_served_models_are_claimed(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id, models=(("m:7b", "sha256:aa"),))
        _enqueue(endpoint, model="m:7b", digest="sha256:bb")
        wanted = _enqueue(endpoint, model="m:7b", digest="sha256:aa")[0]
        assert [r["id"] for r in relay.poll().json()["requests"]] == [str(wanted)]

    def test_lost_claim_and_stale_token(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        claim = relay.poll().json()["requests"][0]
        # The poll response never reached the relay: the next poll does not list it.
        LLMRequest.objects.filter(pk=rid).update(leased_at=timezone.now() - timedelta(seconds=11))
        doc = relay.poll(free_slots=0).json()
        row = LLMRequest.objects.get(pk=rid)
        assert (row.status, row.attempt) == ("queued", 0)
        assert doc["requests"] == []
        # A later poll claims it again under a new token; the old pair is cancelled.
        new_claim = relay.poll().json()["requests"][0]
        assert new_claim["claim_token"] != claim["claim_token"]
        doc = relay.poll(inflight=[{"id": str(rid), "claim_token": claim["claim_token"]}], free_slots=0).json()
        assert doc["cancel"] == [{"id": str(rid), "claim_token": claim["claim_token"]}]

    def test_unacked_claim_expires(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        relay.poll()
        q.requeue_expired(timezone.now() + timedelta(seconds=61))
        assert LLMRequest.objects.get(pk=rid).status == "queued"

    def test_cancelled_request_is_listed(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        claim = relay.poll().json()["requests"][0]
        q.cancel_request(rid)
        doc = relay.poll(inflight=[{"id": str(rid), "claim_token": claim["claim_token"]}], free_slots=0).json()
        assert doc["cancel"] == [{"id": str(rid), "claim_token": claim["claim_token"]}]

    def test_invalid_session(self, relay_setup):
        _endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.session_id = "33333333-3333-3333-3333-333333333333"
        assert relay.poll().json()["session_valid"] is False

    def test_resync_flag(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        type(endpoint).objects.filter(pk=endpoint.pk).update(inventory_resync_requested=True)
        assert relay.poll(free_slots=0).json()["resync_inventory"] is True


@pytest.mark.django_db
class TestResultsAndBodies:
    def test_result_accept_and_stale(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        claim = relay.poll().json()["requests"][0]
        other = Relay(raw, relay_id="22222222-2222-2222-2222-222222222222")
        other.hello()
        payload = {"claim_token": claim["claim_token"], "ok": True, "status_code": 200, "json": {"message": {}}}
        assert other.post(f"requests/{rid}/result", payload).json() == {"accepted": False, "reason": "stale_claim"}
        assert relay.post(f"requests/{rid}/result", payload).json() == {"accepted": True}
        again = relay.post(f"requests/{rid}/result", payload).json()
        assert again == {"accepted": False, "reason": "already_done"}

    def test_gzip_request_body_and_plain_response(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        claim = relay.poll().json()["requests"][0]
        body = {"claim_token": claim["claim_token"], "ok": True, "status_code": 200, "json": {"x": "y" * 10000}}
        resp = relay.post(
            f"requests/{rid}/result",
            raw=gzip.compress(json.dumps(body).encode()),
            HTTP_CONTENT_ENCODING="gzip",
            HTTP_ACCEPT_ENCODING="gzip",
        )
        assert resp.json() == {"accepted": True}
        assert not resp.has_header("Content-Encoding")

    def test_body_caps(self, relay_setup, settings):
        _endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        settings.RELAY_MAX_BODY_BYTES = 1000
        assert relay.post("poll", raw=b"{" + b" " * 2000 + b"}").status_code == 413
        bomb = gzip.compress(b"[" + b"0," * (17 * 1024 * 1024) + b"0]")
        assert len(bomb) < 1_000_000
        settings.RELAY_MAX_BODY_BYTES = 8 * 1024 * 1024
        assert relay.post("poll", raw=bomb, HTTP_CONTENT_ENCODING="gzip").status_code == 413

    def test_json_bomb_bodies_are_refused_before_parsing(self, relay_setup):
        endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        # Small endpoints cap the decompressed size well below the 32 MB result cap.
        big = gzip.compress(b'{"x":"' + b"a" * (3 * 1024 * 1024) + b'"}')
        assert relay.post("poll", raw=big, HTTP_CONTENT_ENCODING="gzip").status_code == 413
        assert relay.post("hello", raw=big, HTTP_CONTENT_ENCODING="gzip").status_code == 413
        # Container-heavy documents (parsed size ~30x the bytes) are refused without json.loads.
        dicts = gzip.compress(b'{"x":[' + b"{}," * 200_000 + b"{}]}")
        with mock.patch.object(views, "json") as fake_json:
            assert relay.post("poll", raw=dicts, HTTP_CONTENT_ENCODING="gzip").status_code == 413
        fake_json.loads.assert_not_called()
        _serve(relay.session_id)
        rid = _enqueue(endpoint)[0]
        claim = relay.poll().json()["requests"][0]
        many = b'{"claim_token":"x","json":[' + b"{}," * 600_000 + b"{}]}"
        with mock.patch.object(views, "json") as fake_json:
            resp = relay.post(f"requests/{rid}/result", raw=gzip.compress(many), HTTP_CONTENT_ENCODING="gzip")
        assert resp.status_code == 413
        fake_json.loads.assert_not_called()
        # Deep nesting is a 400, not a 500.
        assert relay.post("poll", raw=b"[" * 40_000 + b"]" * 40_000).status_code in (400, 413)
        assert relay.post("poll", raw=b'{"a":' * 20_000 + b"1" + b"}" * 20_000).status_code == 400
        # A legitimate large, string-heavy result still goes through.
        body = {"claim_token": claim["claim_token"], "ok": True, "status_code": 200, "json": {"x": "y" * 4_000_000}}
        resp = relay.post(
            f"requests/{rid}/result",
            raw=gzip.compress(json.dumps(body).encode()),
            HTTP_CONTENT_ENCODING="gzip",
        )
        assert resp.json() == {"accepted": True}

    def test_inventory_caps(self, relay_setup, settings):
        _endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        relay.hello()
        settings.RELAY_INVENTORY_MAX_MODELS = 2
        many = {"models": [{"name": f"m{i}:1b", "digest": "sha256:aa"} for i in range(3)]}
        assert relay.post("inventory", many).status_code == 413
        assert relay.post("inventory", {"models": [{"name": "bad name!"}]}).status_code == 422
        ok = relay.post("inventory", {"models": [{"name": "m:1b", "digest": "sha256:" + "a" * 64}]})
        assert ok.status_code == 200
        assert ok.json()["need_show"] == ["m:1b"]

    def test_bye_and_revoke_requeue(self, relay_setup):
        endpoint, raw, token = relay_setup
        relay = Relay(raw)
        relay.hello()
        _serve(relay.session_id)
        _enqueue(endpoint, n=2)
        relay.poll()
        assert LLMRequest.objects.filter(status="leased").count() == 2
        result = relay_tokens.revoke(token)
        assert (result["sessions_ended"], result["requests_requeued"]) == (1, 2)
        assert set(LLMRequest.objects.values_list("status", flat=True)) == {"queued"}
        assert relay.poll().status_code == 401


@pytest.mark.django_db
class TestPlumbing:
    def test_client_ip_is_rightmost_untrusted_hop(self, relay_setup, settings):
        settings.TRUSTED_PROXY_CIDRS = ["127.0.0.0/8", "172.16.0.0/12"]
        _endpoint, raw, _token = relay_setup
        relay = Relay(raw)
        resp = relay.post(
            "hello",
            HELLO,
            HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9, 172.18.0.4",
            REMOTE_ADDR="127.0.0.1",
        )
        session = RelaySession.objects.get(pk=resp.json()["session_id"])
        assert session.client_ip == "203.0.113.9"

    def test_script_download_with_sha(self, client):
        views.relay_file.cache_clear()
        resp = client.get(BASE + "relay.py")
        assert resp.status_code == 200
        assert resp["X-Relay-Version"] == "1.0.0"
        assert resp["X-Relay-Sha256"] == hashlib.sha256(resp.content).hexdigest()
        docker = client.get(BASE + "Dockerfile")
        assert docker.status_code == 200
        assert b"COPY llmlab_relay.py" in docker.content
        unit = client.get(BASE + "llmlab-relay.service")
        assert unit.status_code == 200
        assert b"RestartPreventExitStatus=3" in unit.content
        assert unit["X-Relay-Sha256"] == hashlib.sha256(unit.content).hexdigest()

    def test_relay_views_are_non_atomic(self):
        for view in (
            views.hello,
            views.inventory,
            views.poll,
            views.result,
            views.bye,
            views.relay_script,
            views.relay_dockerfile,
            views.relay_systemd_unit,
        ):
            assert getattr(view, "_non_atomic_requests", None), view


@pytest.mark.django_db(transaction=True)
def test_poll_wakes_on_doorbell_and_holds_no_db_connection(owner, settings):
    settings.RELAY_POLL_TIMEOUT_S = 8
    endpoint = make_endpoint(owner, max_concurrency=2)
    raw, _token = relay_tokens.generate_token(endpoint)
    relay = Relay(raw)
    relay.hello()
    _serve(relay.session_id)
    connection_states: list[bool] = []
    original = views._wait_doorbell

    async def spy(endpoint_id, seconds):
        connection_states.append(await sync_to_async(lambda: connection.connection is None)())
        await original(endpoint_id, seconds)

    def later():
        time.sleep(0.7)
        try:
            _enqueue(endpoint)
        finally:
            connection.close()

    thread = threading.Thread(target=later)
    with mock.patch.object(views, "_wait_doorbell", spy):
        thread.start()
        started = time.monotonic()
        doc = relay.poll().json()
        elapsed = time.monotonic() - started
    thread.join()
    assert len(doc["requests"]) == 1
    assert elapsed < 4
    assert connection_states
    assert all(connection_states)


@pytest.mark.django_db
def test_session_events_are_redacted_on_the_public_channel(relay_setup, django_capture_on_commit_callbacks):
    _endpoint, raw, _token = relay_setup
    published = []
    with (
        mock.patch("backend.realtime.events.publish", side_effect=lambda ch, ev: published.append((ch, ev))),
        django_capture_on_commit_callbacks(execute=True),
    ):
        Relay(raw).hello()
    public = [ev for ch, ev in published if ch.endswith(":public")]
    owner = [ev for ch, ev in published if not ch.endswith(":public")]
    assert {"type": "session", "event": "connected"} in public
    assert any(ev.get("hostname") == "gpu-box-1" for ev in owner)
    assert any(ev["type"] == "endpoint" and ev["session_count"] == 1 for ev in public)
    assert "gpu-box-1" not in json.dumps(public)
    assert "127.0.0.1" not in json.dumps(public)
