"""The ``llm-gateway-local`` overlay: the real ``local.py`` + ``gateway.py`` as a subprocess, as in the image.

It must keep the canonical policy and budget path, talk plain HTTP to the executor proxy with the per-run
bearer, drop the OpenRouter-only fields, send the request class and call index for the proxy's budget
backstop, never retry 500 and always retry 504.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from backend.generation.tests.wp06_gateway.support import GATEWAY_DIR
from backend.generation.tests.wp06_gateway.support import LOCAL_DIR
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import Upstream
from backend.generation.tests.wp06_gateway.support import chat_body
from backend.generation.tests.wp06_gateway.support import chat_completion
from backend.generation.tests.wp06_gateway.support import chat_sse
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import http_request
from backend.generation.tests.wp06_gateway.support import read_jsonl


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _env(cfg: dict, tmp_path, port: int) -> dict[str, str]:
    return {
        **os.environ,
        "GATEWAY_CONFIG": json.dumps(cfg),
        "GATEWAY_LOG_DIR": str(tmp_path / "logs"),
        "GATEWAY_PORT": str(port),
        "LLMLAB_UPSTREAM_TOKEN": "proxy-token",
        "OPENROUTER_API_KEY": "",
        "PYTHONPATH": str(GATEWAY_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@pytest.fixture
def overlay(tmp_path):
    upstream = Upstream().start()
    port = _free_port()
    cfg = config_dict(
        upstream.base_url.removesuffix("/api/v1") + "/v1",
        upstream={"kind": "local_proxy", "endpoint_id": 7},
        sampling={"forced": {"temperature": 0.2}, "seed": 7},
        routing={"provider": {"sort": "price"}, "serving_tier": "standard"},
        caching={"prompt_cache": "on", "ttl": "5m", "sticky_session": True, "cache_control_providers": ["qwen"]},
        retries={
            "upstream_retries": 2,
            "backoff_s": 0.01,
            "retry_on": ["408", "429", "5xx"],
            "llm_call_timeout_s": 30,
        },
    )
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-u", "-B", str(LOCAL_DIR / "local.py")],
        env=_env(cfg, tmp_path, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail(f"gateway did not start: {proc.stdout.read().decode() if proc.stdout else ''}")
    yield port, upstream, tmp_path / "logs"
    proc.terminate()
    proc.wait(timeout=5)
    upstream.stop()


def test_overlay_wiring_and_body(overlay):
    port, upstream, logs = overlay
    body = chat_body(
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "go", "cache_control": {"type": "ephemeral"}}]},
        ],
    )
    upstream.replies.append(Reply(body=chat_completion(cost=0.0)))
    resp = http_request(port, "/api/v1/chat/completions", body)
    assert resp.status == 200
    got = upstream.received[0]
    assert got.path == "/v1/chat/completions"
    assert got.headers["authorization"] == "Bearer proxy-token"
    assert got.headers["x-llmlab-request-class"] == "main"
    assert got.headers["x-llmlab-call-index"] == "1"
    assert "http-referer" not in got.headers
    sent = got.body
    for key in ("usage", "provider", "session_id"):
        assert key not in sent
    assert "cache_control" not in json.dumps(sent)
    assert (sent["seed"], sent["temperature"], sent["max_tokens"]) == (7, 0.2, 1024)
    deadline = time.monotonic() + 5
    while not read_jsonl(logs / "calls.jsonl") and time.monotonic() < deadline:
        time.sleep(0.05)
    record = read_jsonl(logs / "calls.jsonl")[0]
    assert record["status"] == 200
    assert record["request"] == sent
    assert json.loads((logs / "state.json").read_text())["requests_by_class"]["main"] == 1


def test_overlay_never_retries_500_and_always_retries_504(overlay):
    port, upstream, _logs = overlay
    upstream.replies += [Reply(status=500, body={"error": {"message": "model failed to load"}})]
    assert http_request(port, "/api/v1/chat/completions", chat_body()).status == 500
    assert len(upstream.received) == 1
    upstream.replies += [Reply(status=504, body={"error": {"code": "proxy_wait"}}), Reply(events=chat_sse())]
    resp = http_request(port, "/api/v1/chat/completions", chat_body(stream=True))
    assert resp.status == 200
    assert len(upstream.received) == 3
    # The retry of one call keeps its call index, so the proxy re-attaches instead of reserving twice.
    assert upstream.received[1].headers["x-llmlab-call-index"] == upstream.received[2].headers["x-llmlab-call-index"]


def test_overlay_refuses_passthrough_on_a_local_endpoint(tmp_path):
    cfg = config_dict(
        "http://127.0.0.1:9/v1",
        upstream={"kind": "local_proxy"},
        front_protocol="anthropic_messages",
        protocol_mode="passthrough",
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-B", str(LOCAL_DIR / "local.py")],
        env=_env(cfg, tmp_path, _free_port()),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 78
    assert b"OpenRouter only" in proc.stderr
