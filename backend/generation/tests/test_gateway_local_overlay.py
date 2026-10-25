"""The ``llm-gateway-local`` overlay on the frozen gateway (spec decision 5, §D.4), without Docker.

Runs the real ``images/llm-gateway/gateway.py`` through
``images/llm-gateway-local/local.py`` as a subprocess against a stub
upstream and checks what the overlay changes: plain-HTTP upstream with the
per-job bearer, no OpenRouter fields or headers, 500 not retried, 504
retried, and the call log still written.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

IMAGES = Path(__file__).resolve().parents[1] / "images"
GATEWAY_DIR = IMAGES / "llm-gateway"
LOCAL_SCRIPT = IMAGES / "llm-gateway-local" / "local.py"
COMPLETION = {
    "id": "c1",
    "object": "chat.completion",
    "model": "local-gpu/m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


class Upstream:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.statuses: list[int] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status = upstream.statuses.pop(0) if upstream.statuses else 200
                if status != 200:
                    data = json.dumps({"error": {"message": f"status {status}"}}).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for event in (
                        ": ping",
                        "data: " + json.dumps({"id": "c1", "choices": [{"index": 0, "delta": {"content": "hi"}}]}),
                        "data: " + json.dumps({"id": "c1", "choices": [], "usage": COMPLETION["usage"]}),
                        "data: [DONE]",
                    ):
                        chunk = (event + "\n\n").encode()
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    return
                data = json.dumps(COMPLETION).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def overlay(tmp_path):
    upstream = Upstream()
    port = _free_port()
    config = {
        "upstream": f"http://127.0.0.1:{upstream.port}/v1",
        "model": "local-gpu/m",
        "forced_params": {"seed": 7, "max_tokens": 256},
        "stripped_params": ["provider", "seed"],
        "budget": {"max_requests": 10},
        "port": port,
        "retry_backoff_s": 0.01,
        "log_path": str(tmp_path / "calls.jsonl"),
        "events_path": str(tmp_path / "events.jsonl"),
    }
    env = {
        **os.environ,
        "GATEWAY_CONFIG": json.dumps(config),
        "LLMLAB_UPSTREAM_TOKEN": "t",
        "OPENROUTER_API_KEY": "",
        "PYTHONPATH": str(GATEWAY_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",  # never leave a __pycache__ in the frozen image dir
    }
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-u", str(LOCAL_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/healthz", timeout=1).read()  # noqa: S310
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail(f"gateway did not start: {proc.stdout.read().decode()}")
    yield base, upstream, tmp_path
    proc.terminate()
    proc.wait(timeout=5)
    upstream.server.shutdown()


def _post(base: str, body: dict):
    request = urllib.request.Request(  # noqa: S310
        f"{base}/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-sandbox"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_non_stream_call(overlay):
    base, upstream, logs = overlay
    status, raw = _post(
        base,
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}], "provider": {"a": 1}},
    )
    assert status == 200
    assert json.loads(raw)["choices"][0]["message"]["content"] == "hi"
    call = upstream.calls[0]
    assert call["path"] == "/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer t"
    assert "HTTP-Referer" not in call["headers"]
    assert "X-Title" not in call["headers"]
    assert "usage" not in call["body"]
    assert "provider" not in call["body"]
    assert (call["body"]["model"], call["body"]["seed"], call["body"]["max_tokens"]) == ("local-gpu/m", 7, 256)
    records = _records(logs)
    assert records[0]["status"] == 200
    assert records[0]["response"]["usage"]["prompt_tokens"] == 3


def test_stream_call(overlay):
    base, upstream, logs = overlay
    status, raw = _post(base, {"model": "m", "stream": True, "messages": [{"role": "user", "content": "x"}]})
    assert status == 200
    assert b": ping" in raw
    assert b"[DONE]" in raw
    assert "usage" not in upstream.calls[0]["body"]
    assert upstream.calls[0]["body"]["stream_options"] == {"include_usage": True}
    record = _records(logs)[0]
    assert record["response"]["content"] == "hi"


def test_500_is_not_retried_but_504_is(overlay):
    base, upstream, _logs = overlay
    upstream.statuses = [500]
    status, _raw = _post(base, {"model": "m", "messages": []})
    assert status == 500
    assert len(upstream.calls) == 1
    upstream.statuses = [504, 504]
    status, _raw = _post(base, {"model": "m", "messages": []})
    assert status == 200
    assert len(upstream.calls) == 4


def test_frozen_gateway_dir_untouched():
    names = {p.name for p in GATEWAY_DIR.iterdir() if p.name != "__pycache__"}
    assert "local.py" not in names
    assert {"Dockerfile", "gateway.py"} <= names


def _records(logs: Path) -> list[dict]:
    path = logs / "calls.jsonl"
    deadline = time.monotonic() + 5
    # The gateway logs after the last byte reaches the client: wait for a
    # complete line, not just the file (it can exist while still empty).
    while time.monotonic() < deadline and not (path.exists() and path.read_text().endswith("\n")):
        time.sleep(0.05)
    return [json.loads(line) for line in path.read_text().splitlines()]
