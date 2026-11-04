"""E2E-17: a non-stream harness call whose model reasons forever ends as a model truncation, not infra.

The whole local path with real parts: harness -> ``llm-gateway-local`` (subprocess) -> executor proxy ->
``LLMRequest`` -> the direct pump -> a fake OpenAI upstream that streams reasoning forever. The pump streams
the call upstream, cuts it at the policy-pinned ``max_tokens`` and answers ``finish_reason=length``; the
gateway records a truncated call (``call_truncations``), and the harness gets a well-formed answer.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from unittest import mock

import pytest
from django.db import connection

from backend.generation.tests.wp06_gateway.support import GATEWAY_DIR
from backend.generation.tests.wp06_gateway.support import LOCAL_DIR
from backend.generation.tests.wp06_gateway.support import chat_body
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import http_request
from backend.generation.tests.wp06_gateway.support import read_jsonl
from backend.inference import proxy_server
from backend.inference import pump
from backend.inference import security
from backend.inference.tests.fake_openai_stream import FakeOpenAIStream

CHAT = "/api/v1/chat/completions"
CAP = 96


@pytest.fixture
def proxy(settings):
    settings.INFERENCE_REDIS_PREFIX = f"test-{uuid.uuid4().hex[:10]}:"
    proxy_server._auth_cache._jobs.clear()
    proxy_server._budgets.clear()
    server = proxy_server.make_server("127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def fake():
    server = FakeOpenAIStream().start()
    tgt = security.Target(
        scheme="http",
        host="llm.example.org",
        port=server.port,
        base_path="",
        addresses=("127.0.0.1",),
    )
    with mock.patch("backend.inference.upstream.validate_direct_base_url", return_value=tgt):
        yield server
    server.stop()


def _pump(endpoint_id: int, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            if not pump.run_one(endpoint_id, "exec-test"):
                time.sleep(0.05)
    finally:
        connection.close()


@pytest.mark.django_db(transaction=True)
def test_a_runaway_non_stream_call_is_a_truncation_through_the_whole_local_path(proxy, fake, user, tmp_path):
    from backend.generation.models import RunManifest
    from backend.inference.gateway_wiring import proxy_token
    from backend.inference.models import LLMRequest
    from backend.inference.tests.conftest import make_agent_run
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model

    endpoint = make_endpoint(
        user,
        kind="openai_compatible",
        api_flavor="openai",
        connection="direct",
        base_url="http://llm.example.org",
        request_timeout_s=900,
    )
    model = make_local_model(endpoint, name="runaway")
    run = make_agent_run(model, user)
    manifest = RunManifest._base_manager.get(run=run)
    content = manifest.manifest
    content["resources"]["effective"]["content"]["time"]["llm_call_timeout_s"] = 300
    RunManifest._base_manager.filter(pk=manifest.pk).update(manifest=content)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = config_dict(
        proxy + "/v1",
        run_id=str(run.pk),
        session_id=str(run.pk),
        upstream={"kind": "local_proxy", "endpoint_id": endpoint.pk},
        model={"upstream_model": model.model_id},
        call={"max_output_tokens": CAP},
        budget={"max_llm_requests": 5, "max_aux_requests": 0, "cost_basis": "free"},
    )
    env = {
        **os.environ,
        "GATEWAY_CONFIG": json.dumps(cfg),
        "GATEWAY_PORT": str(port),
        "GATEWAY_LOG_DIR": str(tmp_path / "logs"),
        "LLMLAB_UPSTREAM_TOKEN": proxy_token(run),
        "PYTHONPATH": str(GATEWAY_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.Popen([sys.executable, "-B", str(LOCAL_DIR / "local.py")], env=env)  # noqa: S603
    stop = threading.Event()
    worker = threading.Thread(target=_pump, args=(endpoint.pk, stop), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        started = time.monotonic()
        # aider's shape: one non-streamed call with its own max_tokens (the gateway pins the policy's).
        answer = http_request(
            port,
            CHAT,
            chat_body("write the app", temperature=0, seed=1, max_tokens=8192),
            timeout=60,
        )
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        worker.join(timeout=10)
        proc.terminate()
        proc.wait(timeout=10)
    assert answer.status == 200, answer.raw
    assert elapsed < 30
    choice = answer.json()["choices"][0]
    assert choice["finish_reason"] == "length"
    assert choice["message"]["content"] == ""
    sent = fake.received[-1]
    assert sent["stream"] is True
    assert sent["max_tokens"] == CAP
    row = LLMRequest.objects.get(job=run)
    assert (row.source, row.status, row.response_status) == ("agent_proxy", "done", 200)
    assert row.completion_tokens >= CAP
    record = read_jsonl(tmp_path / "logs" / "calls.jsonl")[0]
    assert record["status"] == 200
    assert record["transport_error"] is False
    assert record["finish_reason"] == "length"
    assert record["native_finish_reason"] == "llmlab_cut:max_output_tokens"
    assert record["truncated"] is True
    assert record["usage"]["completion_tokens"] == row.completion_tokens
    state = json.loads((tmp_path / "logs" / "state.json").read_text())
    assert state["call_truncations"] == 1
