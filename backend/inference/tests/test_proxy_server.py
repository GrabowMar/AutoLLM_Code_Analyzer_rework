"""The internal LLM proxy (spec §D.3): auth, translation, dedupe, 504 re-attach, synthesized streaming."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.inference import proxy_server
from backend.inference import requests as q
from backend.inference.gateway_wiring import proxy_token
from backend.inference.models import LLMRequest
from backend.inference.snapshot import endpoint_section
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.inference.timeouts import Chain

pytestmark = pytest.mark.django_db(transaction=True)
TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]
NATIVE_TOOL_REPLY = {
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "bash", "arguments": {"c": 1}}}],
    },
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 11,
    "eval_count": 4,
}


@pytest.fixture
def proxy():
    proxy_server._auth_cache._jobs.clear()
    server = proxy_server.make_server("127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def agent_job(owner):
    endpoint = make_endpoint(owner, num_ctx_cap=8192)
    model = make_local_model(endpoint, context_window=4096)
    return GenerationJobFactory(
        model=model,
        created_by=owner,
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.RUNNING,
        phase=GenerationJob.Phase.RUNNING,
        attempt=1,
        priority=3,
        lease_expires_at=timezone.now() + timedelta(minutes=5),
        resolved_bundle={
            "mode": "agent",
            "endpoint": endpoint_section(model),
            "llm": {"model_slug": model.model_id},
            "agent": {"wall_clock_seconds": 600},
        },
    )


class Completer:
    """Plays the relay: claims the endpoint's requests and answers them after ``delay`` seconds."""

    def __init__(self, endpoint_id, *, payload=None, status=200, delay=0.0):
        self.endpoint_id = endpoint_id
        self.payload = payload or NATIVE_TOOL_REPLY
        self.status = status
        self.delay = delay
        self.stop = threading.Event()
        self.handled: list[LLMRequest] = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            while not self.stop.is_set():
                rows = q.claim(self.endpoint_id, limit=1, pump_owner="fake")
                if not rows:
                    time.sleep(0.05)
                    continue
                time.sleep(self.delay)
                row = rows[0]
                self.handled.append(row)
                q.complete(
                    row.pk,
                    row.claim_token,
                    pump_owner="fake",
                    ok=True,
                    status_code=self.status,
                    payload=self.payload,
                )
        finally:
            connection.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)


def _call(base, path, body=None, *, token=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _chat_body(**extra):
    return {
        "model": "whatever",
        "messages": [{"role": "user", "content": "go"}],
        "tools": TOOLS,
        "seed": 99,
        "max_tokens": 256,
        "temperature": 0.1,
        "usage": {"include": True},
        "provider": {"order": ["x"]},
        **extra,
    }


def test_health_and_auth(proxy, agent_job):
    assert _call(proxy, "/healthz")[0] == 200
    assert _call(proxy, "/v1/chat/completions", _chat_body())[0] == 401
    assert _call(proxy, "/v1/chat/completions", _chat_body(), token="garbage")[0] == 401  # noqa: S106
    status, raw = _call(proxy, "/v1/models", token=proxy_token(agent_job))
    assert (status, json.loads(raw)["data"][0]["id"]) == (200, agent_job.model.model_id)
    stale = GenerationJob(pk=agent_job.pk, attempt=0)
    assert _call(proxy, "/v1/models", token=proxy_token(stale))[0] == 401
    GenerationJob.objects.filter(pk=agent_job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    proxy_server._auth_cache._jobs.clear()
    assert _call(proxy, "/v1/models", token=proxy_token(agent_job))[0] == 401
    GenerationJob.objects.filter(pk=agent_job.pk).update(
        lease_expires_at=timezone.now() + timedelta(minutes=5),
        phase=GenerationJob.Phase.EVAL_QUEUED,
    )
    proxy_server._auth_cache._jobs.clear()
    assert _call(proxy, "/v1/models", token=proxy_token(agent_job))[0] == 401


def test_non_stream_round_trip_uses_the_frozen_snapshot(proxy, agent_job):
    from backend.llm_models.models import LLMModel

    # The live row changes after the snapshot froze num_ctx; the proxy must not follow it.
    LLMModel.objects.filter(pk=agent_job.model_id).update(num_ctx_override=1024)
    with Completer(agent_job.model.endpoint_id) as completer:
        status, raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=proxy_token(agent_job))
    assert status == 200, raw
    out = json.loads(raw)
    call = out["choices"][0]["message"]["tool_calls"][0]
    assert call["function"] == {"name": "bash", "arguments": '{"c": 1}'}
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert out["usage"]["cost"] == 0.0
    row = LLMRequest.objects.get(pk=completer.handled[0].pk)
    assert (row.source, row.job_id, row.job_attempt, row.priority) == ("agent_proxy", agent_job.pk, 1, 3)
    sent = completer.handled[0].body
    assert sent["options"]["num_ctx"] == 4096
    assert sent["options"]["seed"] == 99
    assert sent["options"]["num_predict"] == 256
    assert "provider" not in sent
    assert "usage" not in sent
    assert sent["tools"] == TOOLS
    assert row.body is None  # cleared on completion


def test_dedupe_reattaches_to_one_request(proxy, agent_job):
    token = proxy_token(agent_job)
    with Completer(agent_job.model.endpoint_id):
        first = _call(proxy, "/v1/chat/completions", _chat_body(), token=token)
        second = _call(proxy, "/v1/chat/completions", _chat_body(), token=token)
    assert first == second
    assert LLMRequest.objects.filter(job=agent_job).count() == 1


def test_wait_cap_answers_504_and_retry_reattaches(proxy, agent_job):
    token = proxy_token(agent_job)
    short = Chain(
        request_timeout_s=900,
        deadline_s=3300,
        gateway_upstream_s=1200,
        proxy_wait_s=1,
        harness_client_s=6060,
    )
    with mock.patch("backend.inference.timeouts.chain", return_value=short):
        status, raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=token)
        assert (status, json.loads(raw)["error"]["code"]) == (504, "proxy_wait")
        with Completer(agent_job.model.endpoint_id):
            status, _raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=token)
    assert status == 200
    assert LLMRequest.objects.filter(job=agent_job).count() == 1
    agent_job.refresh_from_db()
    assert not agent_job.execution.get("infra_errors")


def test_stream_synthesis_with_pings(proxy, agent_job):
    with (
        mock.patch.object(proxy_server, "PING_INTERVAL_S", 0.3),
        Completer(agent_job.model.endpoint_id, delay=1.0),
    ):
        status, raw = _call(proxy, "/v1/chat/completions", _chat_body(stream=True), token=proxy_token(agent_job))
    assert status == 200
    text = raw.decode()
    assert ": ping" in text
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
    assert events[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert events[1]["choices"][0]["finish_reason"] == "tool_calls"
    assert events[2]["usage"]["completion_tokens"] == 4
    assert text.rstrip().endswith("data: [DONE]")


def test_stream_error_after_headers_records_infra_error(proxy, agent_job):
    oom = {"error": "model requires more system memory (12 GiB) than is available"}
    with Completer(agent_job.model.endpoint_id, payload=oom, status=500):
        status, raw = _call(proxy, "/v1/chat/completions", _chat_body(stream=True), token=proxy_token(agent_job))
    assert status == 200
    text = raw.decode()
    assert '"upstream_oom"' in text
    assert text.rstrip().endswith("data: [DONE]")
    agent_job.refresh_from_db()
    assert [e["code"] for e in agent_job.execution["infra_errors"]] == ["upstream_oom"]
    assert agent_job.execution["infra_errors"][0]["status"] == 507


def test_upstream_4xx_passes_through_and_413(proxy, agent_job, settings):
    with Completer(agent_job.model.endpoint_id, payload={"error": "bad tool schema"}, status=400):
        status, raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=proxy_token(agent_job))
    assert status == 400
    assert "bad tool schema" in json.loads(raw)["error"]["message"]
    settings.LLM_PROXY_MAX_BODY_BYTES = 100
    status, _raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=proxy_token(agent_job))
    assert status == 413


def test_upstream_5xx_is_retried_not_reserved(proxy, agent_job):
    token = proxy_token(agent_job)
    with Completer(agent_job.model.endpoint_id, payload={"error": "boom"}, status=500):
        status, raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=token)
    assert (status, json.loads(raw)["error"]["code"]) == (503, "endpoint_unavailable")
    # The gateway retries a 503: that retry must reach the endpoint again, not re-read the 500.
    with Completer(agent_job.model.endpoint_id):
        status, _raw = _call(proxy, "/v1/chat/completions", _chat_body(), token=token)
    assert status == 200
    assert LLMRequest.objects.filter(job=agent_job).count() == 2


def test_bad_content_length_is_rejected(proxy, agent_job):
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(proxy)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.putrequest("POST", "/v1/chat/completions")
        conn.putheader("Authorization", f"Bearer {proxy_token(agent_job)}")
        conn.putheader("Content-Length", "-1")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 400
        assert json.loads(resp.read())["error"]["code"] == "bad_request"
    finally:
        conn.close()
