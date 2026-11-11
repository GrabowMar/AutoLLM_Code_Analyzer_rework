"""OpenCode Go (host ``opencode.ai``): the transient ``[server_error]`` is retried (C1) and the policy's
reasoning effort is sent as ``reasoning_effort`` (C2). Every other endpoint keeps its old behaviour."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

import pytest
from django.db import connection

from backend.generation.services.call_telemetry import local_from_llm_request
from backend.inference import proxy_server
from backend.inference import pump
from backend.inference import requests as q
from backend.inference import security
from backend.inference import upstream
from backend.inference.access import LLMAccessError
from backend.inference.access import endpoint_access
from backend.inference.chat import REASONING_REJECTED_HINT
from backend.inference.chat import build_upstream_body
from backend.inference.chat import chat
from backend.inference.chat import raise_for_request
from backend.inference.gateway_wiring import proxy_token
from backend.inference.models import LLMRequest
from backend.inference.tests.conftest import make_agent_run
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.inference.tests.fake_opencode_go import MODERATION
from backend.inference.tests.fake_opencode_go import SERVER_ERROR
from backend.inference.tests.fake_opencode_go import FakeOpenCodeGo

pytestmark = pytest.mark.django_db(transaction=True)

GO_URL = "https://opencode.ai/zen/go"
CHAT = "/v1/chat/completions"


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(upstream, "OPENCODE_RETRY_BACKOFF_S", (0.01, 0.01))


@pytest.fixture
def go():
    server = FakeOpenCodeGo().start()
    yield server
    server.stop()


@pytest.fixture
def go_target(go):
    target = security.Target(scheme="http", host="opencode.ai", port=go.port, base_path="", addresses=("127.0.0.1",))
    with mock.patch("backend.inference.upstream.validate_direct_base_url", return_value=target):
        yield target


def _endpoint(owner, base_url: str = GO_URL):
    return make_endpoint(
        owner,
        kind="openai_compatible",
        api_flavor="openai",
        connection="direct",
        base_url=base_url,
        request_timeout_s=60,
    )


def _body(model: str, **extra) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "go"}],
        "max_tokens": 64,
        "stream": False,
        **extra,
    }


def _run_once(endpoint, model: str, **extra) -> LLMRequest:
    rid = q.enqueue(endpoint_access(endpoint, served_model_name=model), CHAT, _body(model, **extra), source="analysis")
    assert pump.run_one(endpoint.pk, "exec-test") is True
    return LLMRequest.objects.select_related("endpoint").get(pk=rid)


def _resp(status: int, payload, text: str = "") -> upstream.UpstreamResponse:
    return upstream.UpstreamResponse(status=status, payload=payload, text=text or json.dumps(payload), elapsed_ms=1)


# =====================================================================================
# Which answers are transient (C1)
# =====================================================================================


def test_only_opencode_server_errors_are_transient():
    error = {"error": {"message": SERVER_ERROR, "type": "server_error"}}
    assert upstream.transient_reason(GO_URL, _resp(400, error)) == upstream.TRANSIENT_REASON
    assert upstream.transient_reason(GO_URL, _resp(400, None, f"oops {SERVER_ERROR}")) == upstream.TRANSIENT_REASON
    # The same error inside a 200 body, or as an in-stream error event (stored as 502).
    assert upstream.transient_reason(GO_URL, _resp(200, error)) == upstream.TRANSIENT_REASON
    streamed = {**error, "llmlab_stream_error": True}
    assert upstream.transient_reason(GO_URL, _resp(502, streamed)) == upstream.TRANSIENT_REASON
    # Never: another host, a moderation refusal, other 4xx, a real answer that mentions the marker.
    assert upstream.transient_reason("https://openrouter.ai/api", _resp(400, error)) is None
    assert upstream.transient_reason("http://gpu.example.org:11434", _resp(400, error)) is None
    moderation = {"error": {"message": f"{MODERATION} [server_error]"}}
    assert upstream.transient_reason(GO_URL, _resp(400, moderation)) is None
    assert upstream.transient_reason(GO_URL, _resp(422, error)) is None
    assert upstream.transient_reason(GO_URL, _resp(429, error)) is None
    assert upstream.transient_reason(GO_URL, _resp(502, error)) is None  # a plain 5xx: the gateway's retry_on
    answer = {"choices": [{"message": {"content": "print('[server_error]')"}}], "error": None}
    assert upstream.transient_reason(GO_URL, _resp(200, answer)) is None


# =====================================================================================
# The pump re-submits, bounded, and records it (C1)
# =====================================================================================


def test_a_transient_400_is_resubmitted_and_the_retries_are_recorded(owner, go, go_target):
    go.fail_n = 2
    row = _run_once(_endpoint(owner), "flaky")
    assert len(go.received) == 3
    assert (row.status, row.response_status) == (LLMRequest.Status.DONE, 200)
    info = row.response[pump.RETRIES_KEY]
    assert (info["count"], info["reason"], info["exhausted"]) == (2, upstream.TRANSIENT_REASON, False)
    assert [a["status"] for a in info["attempts"]] == [400, 400]
    raise_for_request(row)  # an answer
    local = local_from_llm_request(row)
    assert (local["transient_retries"], local["transient_retry_reason"]) == (2, upstream.TRANSIENT_REASON)


def test_retries_are_bounded_then_the_call_is_a_retryable_502(owner, go, go_target):
    go.fail_n = 99
    row = _run_once(_endpoint(owner), "flaky")
    assert len(go.received) == 1 + upstream.OPENCODE_TRANSIENT_RETRIES
    assert (row.status, row.response_status) == (LLMRequest.Status.DONE, 502)
    assert row.response["error"]["code"] == "upstream_transient"
    assert row.response["error"]["upstream_status"] == 400
    assert row.response[pump.RETRIES_KEY]["exhausted"] is True
    assert row.response[pump.RETRIES_KEY]["count"] == 2
    assert proxy_server._transient_exhausted(row)
    with pytest.raises(LLMAccessError) as exc:
        raise_for_request(row)
    assert (exc.value.status_code, exc.value.retryable) == (502, True)
    assert "[server_error]" in str(exc.value)


@pytest.mark.parametrize("mode", ["200", "event"])
def test_the_error_inside_a_200_or_a_stream_is_retried_too(owner, go, go_target, mode):
    go.fail_n, go.fail_mode = 1, mode
    endpoint = _endpoint(owner)
    model = make_local_model(endpoint, name="flaky")
    run = make_agent_run(model, owner)
    rid = q.enqueue(
        endpoint_access(endpoint, served_model_name="flaky"),
        CHAT,
        _body("flaky"),
        job=run,
        source=LLMRequest.Source.AGENT_PROXY,
    )
    assert pump.run_one(endpoint.pk, "exec-test") is True
    row = LLMRequest.objects.get(pk=rid)
    assert len(go.received) == 2
    assert all(body["stream"] is True for body in go.received)  # agent calls are streamed upstream
    assert (row.status, row.response_status) == (LLMRequest.Status.DONE, 200)
    assert row.response["choices"][0]["message"]["content"] == "OK"
    assert row.response[pump.RETRIES_KEY]["count"] == 1


def test_a_moderation_refusal_is_never_retried(owner, go, go_target):
    row = _run_once(_endpoint(owner), "moderation")
    assert len(go.received) == 1
    assert (row.status, row.response_status) == (LLMRequest.Status.DONE, 400)
    assert pump.RETRIES_KEY not in row.response
    with pytest.raises(LLMAccessError) as exc:
        raise_for_request(row)
    assert (exc.value.status_code, exc.value.retryable) == (400, False)


def test_other_endpoints_keep_the_old_behaviour(owner, go, go_target):
    go.fail_n = 1
    row = _run_once(_endpoint(owner, base_url="https://llm.example.org"), "flaky")
    assert len(go.received) == 1
    assert (row.status, row.response_status) == (LLMRequest.Status.DONE, 400)
    assert pump.RETRIES_KEY not in row.response


def test_no_retry_once_the_per_call_wall_clock_is_short(owner, go, go_target, monkeypatch):
    go.fail_n = 99
    monkeypatch.setattr(upstream, "OPENCODE_RETRY_BACKOFF_S", (30.0, 30.0))
    endpoint = _endpoint(owner)
    model = make_local_model(endpoint, name="flaky")
    run = make_agent_run(model, owner)
    manifest = run.manifest.manifest
    manifest["resources"]["effective"] = {"content": {"time": {"llm_call_timeout_s": 20}}}
    type(run.manifest)._base_manager.filter(pk=run.manifest.pk).update(manifest=manifest)
    rid = q.enqueue(
        endpoint_access(endpoint, served_model_name="flaky"),
        CHAT,
        _body("flaky"),
        job=run,
        source=LLMRequest.Source.AGENT_PROXY,
    )
    started = time.monotonic()
    assert pump.run_one(endpoint.pk, "exec-test") is True
    assert time.monotonic() - started < 10
    assert len(go.received) == 1
    assert LLMRequest.objects.get(pk=rid).response_status == 502  # still handed to the gateway's retry


# =====================================================================================
# The effort reaches Go as reasoning_effort (C2)
# =====================================================================================


def test_the_reasoning_object_becomes_reasoning_effort_for_opencode_only():
    body = {"model": "m", "messages": [], "reasoning": {"effort": "high", "exclude": False}}
    assert upstream.opencode_reasoning(body) == {"model": "m", "messages": [], "reasoning_effort": "high"}
    assert upstream.opencode_reasoning({"model": "m", "reasoning": {"max_tokens": 900}}) == {"model": "m"}
    plain = {"model": "m"}
    assert upstream.opencode_reasoning(plain) is plain
    headers: dict[str, str] = {}
    assert upstream._provider_quirks("opencode.ai", body, headers)["reasoning_effort"] == "high"
    assert upstream._provider_quirks("api.example.com", body, {}) is body


def test_build_upstream_body_keeps_the_reasoning_object_only_for_opencode(owner):
    params = {"max_tokens": 64, "reasoning": {"effort": "high", "exclude": False}}
    go_access = endpoint_access(_endpoint(owner), served_model_name="m")
    _path, body = build_upstream_body(go_access, [{"role": "user", "content": "x"}], params, None)
    assert body["reasoning"] == {"effort": "high", "exclude": False}
    other = endpoint_access(_endpoint(owner, base_url="http://vllm.example.org"), served_model_name="m")
    _path, body = build_upstream_body(other, [{"role": "user", "content": "x"}], params, None)
    assert "reasoning" not in body
    assert "reasoning_effort" not in body


class _PumpLoop:
    def __init__(self, endpoint_id: int) -> None:
        self.endpoint_id = endpoint_id
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                if not pump.run_one(self.endpoint_id, "exec-test"):
                    time.sleep(0.02)
        finally:
            connection.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=10)


def test_a_rejected_effort_fails_loudly_and_is_not_retried(owner, go, go_target):
    endpoint = _endpoint(owner)
    access = endpoint_access(endpoint, served_model_name="reject-effort")
    with _PumpLoop(endpoint.pk), pytest.raises(LLMAccessError) as exc:
        chat(
            access,
            messages=[{"role": "user", "content": "x"}],
            params={"max_tokens": 64, "reasoning": {"effort": "high"}},
            seed=None,
            source="probe",
        )
    assert len(go.received) == 1
    assert go.received[0]["reasoning_effort"] == "high"
    assert "reasoning" not in go.received[0]
    assert exc.value.status_code == 400
    assert "reasoning_effort" in str(exc.value)
    assert REASONING_REJECTED_HINT in str(exc.value)
    assert exc.value.remediation == REASONING_REJECTED_HINT


# =====================================================================================
# Through the executor proxy (the gateway's view)
# =====================================================================================


@pytest.fixture
def proxy():
    proxy_server._auth_cache._jobs.clear()
    server = proxy_server.make_server("127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _go_run(owner, name: str, reasoning: dict | None):
    endpoint = _endpoint(owner)
    model = make_local_model(endpoint, name=name)
    run = make_agent_run(model, owner)
    if reasoning is not None:
        manifest = run.manifest.manifest
        manifest["resources"]["effective"]["content"]["reasoning"] = reasoning
        type(run.manifest)._base_manager.filter(pk=run.manifest.pk).update(manifest=manifest)
    return endpoint, run


def _post(base: str, run, **extra):
    body = {
        "model": "whatever",
        "messages": [{"role": "user", "content": "go"}],
        "max_tokens": 64,
        "usage": {"include": True},
        **extra,
    }
    request = urllib.request.Request(  # noqa: S310
        base + CHAT,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {proxy_token(run)}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_the_policy_effort_reaches_go_through_the_proxy(proxy, owner, go, go_target):
    endpoint, run = _go_run(owner, "ok", {"mode": "effort", "effort": "high", "exclude": False})
    with _PumpLoop(endpoint.pk):
        status, raw = _post(proxy, run, reasoning={"effort": "high", "exclude": False})
    assert status == 200, raw
    sent = go.received[-1]
    assert sent["reasoning_effort"] == "high"
    assert "reasoning" not in sent
    assert go.headers[-1]["x-opencode-session"].startswith("llmlab-")


def test_a_harness_reasoning_object_under_provider_default_never_reaches_go(proxy, owner, go, go_target):
    endpoint, run = _go_run(owner, "ok", {"mode": "provider_default"})
    with _PumpLoop(endpoint.pk):
        status, _raw = _post(proxy, run, reasoning={"effort": "max"})
    assert status == 200
    assert "reasoning" not in go.received[-1]
    assert "reasoning_effort" not in go.received[-1]


def test_an_exhausted_transient_error_is_a_503_the_gateway_retries(proxy, owner, go, go_target):
    go.fail_n = 3  # the pump's three tries all fail; the gateway's retry then succeeds
    endpoint, run = _go_run(owner, "flaky", None)
    with _PumpLoop(endpoint.pk):
        status, raw = _post(proxy, run)
        assert (status, json.loads(raw)["error"]["code"]) == (503, "upstream_transient")
        status, raw = _post(proxy, run)  # the gateway's retry: a new request, not the stored 502
    assert status == 200, raw
    assert len(go.received) == 4
    assert LLMRequest.objects.filter(job=run).count() == 2
    run.refresh_from_db()
    assert [e["code"] for e in run.execution["infra_errors"]] == ["upstream_transient"]


def test_a_streamed_call_gets_the_retried_answer_without_any_error_chunk(proxy, owner, go, go_target):
    go.fail_n = 2
    endpoint, run = _go_run(owner, "flaky", None)
    with _PumpLoop(endpoint.pk):
        status, raw = _post(proxy, run, stream=True)
    text = raw.decode()
    assert status == 200
    assert "error" not in text
    assert '"OK"' in text
    assert text.rstrip().endswith("data: [DONE]")
    assert len(go.received) == 3


# =====================================================================================
# Review fixes: the hint, the lease, retries of an attempt that failed in transport
# =====================================================================================


def _done_row(owner, status: int, message: str, base_url: str = GO_URL) -> LLMRequest:
    endpoint = _endpoint(owner, base_url=base_url)
    rid = q.enqueue(endpoint_access(endpoint, served_model_name="m"), CHAT, _body("m"), source="analysis")
    LLMRequest.objects.filter(pk=rid).update(
        status=LLMRequest.Status.DONE,
        response_status=status,
        response={"error": {"message": message}},
        body=None,
    )
    return LLMRequest.objects.select_related("endpoint").get(pk=rid)


def test_the_effort_hint_is_only_for_an_effort_rejection(owner):
    # DeepSeek's thinking-mode 400 also happens under provider default: no advice to unset the effort.
    thinking = "The `reasoning_content` in the thinking mode must be passed back to the API."
    with pytest.raises(LLMAccessError) as exc:
        raise_for_request(_done_row(owner, 400, thinking))
    assert REASONING_REJECTED_HINT not in str(exc.value)
    assert exc.value.remediation is None
    with pytest.raises(LLMAccessError) as exc:
        raise_for_request(_done_row(owner, 400, "Invalid value for reasoning_effort: 'high'"))
    assert exc.value.remediation == REASONING_REJECTED_HINT
    # Never for another host.
    with pytest.raises(LLMAccessError) as exc:
        raise_for_request(_done_row(owner, 400, "bad reasoning_effort", base_url="https://llm.example.org"))
    assert exc.value.remediation is None


def _claimed(endpoint) -> LLMRequest:
    q.enqueue(endpoint_access(endpoint, served_model_name="m"), CHAT, _body("m"), source="analysis")
    (row,) = q.claim(endpoint.pk, limit=1, pump_owner="exec-test")
    return row


def test_a_retry_renews_the_lease_and_respects_the_row_deadline(owner):
    from datetime import timedelta

    from django.utils import timezone

    endpoint = _endpoint(owner)  # request_timeout_s=60
    row = _claimed(endpoint)
    # The first try used almost all of the lease: the retry gets a fresh one, so the reconciler cannot requeue
    # the row (and another pump run it again) while the retry is in flight.
    LLMRequest.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() + timedelta(seconds=2))
    assert pump._may_retry_transient(row, endpoint, 0, None) is True
    lease = LLMRequest.objects.get(pk=row.pk).lease_expires_at
    assert lease > timezone.now() + timedelta(seconds=60)
    assert pump._may_retry_transient(row, endpoint, upstream.OPENCODE_TRANSIENT_RETRIES, None) is False
    # Too close to the row's deadline: no retry.
    row.deadline_at = timezone.now() + timedelta(seconds=0.5)
    assert pump._may_retry_transient(row, endpoint, 0, None) is False
    # Not leased to this claim any more (cancelled, or requeued by the reconciler): no retry.
    LLMRequest.objects.filter(pk=row.pk).update(status=LLMRequest.Status.DONE)  # free the endpoint's slot
    row = _claimed(endpoint)
    LLMRequest.objects.filter(pk=row.pk).update(status=LLMRequest.Status.CANCELLED)
    assert pump._may_retry_transient(row, endpoint, 0, None) is False
    row = _claimed(endpoint)
    LLMRequest.objects.filter(pk=row.pk).update(claim_token="someone-else")  # noqa: S106
    assert pump._may_retry_transient(row, endpoint, 0, None) is False


def test_retries_before_a_transport_failure_are_kept_and_carried_to_the_next_attempt(owner):
    endpoint = _endpoint(owner)
    rid = q.enqueue(endpoint_access(endpoint, served_model_name="m"), CHAT, _body("m"), source="analysis")
    transient = _resp(400, {"error": {"message": "Upstream request failed: [server_error] not valid JSON"}})
    timeout = upstream.UpstreamError("timeout", "read timed out")
    answer = _resp(200, {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 1}})
    with mock.patch.object(upstream, "call", side_effect=[transient, timeout]):
        assert pump.run_one(endpoint.pk, "exec-test") is True
    row = LLMRequest.objects.get(pk=rid)
    assert (row.status, row.attempt) == (LLMRequest.Status.QUEUED, 1)  # requeued
    assert "after 1 transient-error retries" in row.error
    info = row.response[pump.RETRIES_KEY]
    assert info["count"] == 1
    assert [a["status"] for a in info["attempts"]] == [400, None]
    assert info["attempts"][1]["error"].startswith("timeout:")
    # The next attempt succeeds at once: its answer still shows the earlier retry.
    with mock.patch.object(upstream, "call", side_effect=[answer]):
        assert pump.run_one(endpoint.pk, "exec-test") is True
    row = LLMRequest.objects.select_related("endpoint").get(pk=rid)
    assert (row.status, row.response_status) == (LLMRequest.Status.DONE, 200)
    assert row.response["choices"][0]["message"]["content"] == "OK"
    assert row.response[pump.RETRIES_KEY]["count"] == 1
    assert local_from_llm_request(row)["transient_retries"] == 1


def test_retries_before_a_final_transport_failure_reach_the_telemetry(owner):
    endpoint = _endpoint(owner)
    rid = q.enqueue(endpoint_access(endpoint, served_model_name="m"), CHAT, _body("m"), source="analysis")
    LLMRequest.objects.filter(pk=rid).update(max_attempts=1)
    transient = _resp(400, {"error": {"message": "Upstream request failed: [server_error] not valid JSON"}})
    with mock.patch.object(upstream, "call", side_effect=[transient, upstream.UpstreamError("connect", "refused")]):
        assert pump.run_one(endpoint.pk, "exec-test") is True
    row = LLMRequest.objects.select_related("endpoint").get(pk=rid)
    assert row.status == LLMRequest.Status.FAILED
    local = local_from_llm_request(row)
    assert (local["transient_retries"], local["transient_retry_reason"]) == (1, upstream.TRANSIENT_REASON)
