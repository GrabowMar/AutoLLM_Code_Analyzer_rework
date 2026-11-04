"""Shared helpers for the gateway tests: the real image code in-process, a scripted upstream.

The gateway image is stdlib-only and not a package of this project, so the tests put its directory on
``sys.path`` and import ``gateway`` and ``protocols`` from there (exactly the files the Dockerfile
copies). ``Upstream`` is a scripted OpenRouter stand-in that records every request it receives.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import sys
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from backend.generation.testing.fake_gateway import gateway_config

IMAGES = Path(__file__).resolve().parents[2] / "images"
GATEWAY_DIR = IMAGES / "llm-gateway"
LOCAL_DIR = IMAGES / "llm-gateway-local"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))
sys.dont_write_bytecode = True  # never leave a __pycache__ in the image build context

import gateway as gateway_module  # noqa: E402
from protocols import anthropic_messages  # noqa: E402
from protocols import budget_rules  # noqa: E402
from protocols import common  # noqa: E402
from protocols import gemini_generate  # noqa: E402
from protocols import openai_chat  # noqa: E402
from protocols import openai_responses  # noqa: E402
from protocols import translation  # noqa: E402

__all__ = [
    "anthropic_messages",
    "budget_rules",
    "common",
    "gateway_module",
    "gemini_generate",
    "openai_chat",
    "openai_responses",
    "translation",
]

# =====================================================================================
# Scripted upstream
# =====================================================================================


@dataclass
class Reply:
    """One scripted upstream answer: a JSON body, or SSE ``events`` (raw byte chunks) with delays."""

    status: int = 200
    body: Any = None
    events: Sequence[bytes] | None = None
    delay_s: float = 0.0  # before the response headers
    chunk_delay_s: float = 0.0  # between SSE chunks
    content_type: str | None = None
    hold: threading.Event | None = None  # block before answering until set
    drop: bool = False  # cut the connection after the events/headers instead of finishing the body


@dataclass
class Received:
    path: str
    headers: dict[str, str]
    raw: bytes

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.raw)


@dataclass
class Upstream:
    replies: list[Reply] = field(default_factory=list)
    default: Callable[[Received], Reply] | None = None
    received: list[Received] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    server: ThreadingHTTPServer | None = None

    def start(self) -> Upstream:
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                got = Received(self.path, {k.lower(): v for k, v in self.headers.items()}, raw)
                with upstream.lock:
                    upstream.received.append(got)
                    reply = upstream.replies.pop(0) if upstream.replies else None
                if reply is None:
                    reply = upstream.default(got) if upstream.default else Reply(body=chat_completion())
                if reply.hold is not None:
                    reply.hold.wait(timeout=30)
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                if reply.events is not None:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", reply.content_type or "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for chunk in reply.events:
                        if reply.chunk_delay_s:
                            time.sleep(reply.chunk_delay_s)
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    if reply.drop:
                        self._drop()
                        return
                    self.wfile.write(b"0\r\n\r\n")
                    return
                data = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
                self.send_response(reply.status)
                self.send_header("Content-Type", reply.content_type or "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if reply.drop:  # headers and half the body, then the connection dies
                    self.wfile.write(data[: len(data) // 2])
                    self._drop()
                    return
                self.wfile.write(data)

            def _drop(self) -> None:
                self.wfile.flush()
                self.close_connection = True
                with contextlib.suppress(OSError):
                    self.connection.shutdown(socket.SHUT_RDWR)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


# =====================================================================================
# Canned upstream payloads
# =====================================================================================


def chat_completion(
    content: str = "Done.",
    *,
    prompt: int = 100,
    completion: int = 20,
    cost: float | None = 0.0001,
    finish: str = "stop",
    model: str = "deepseek/deepseek-v4.1-flash",
    provider: str = "DeepInfra",
    cached: int | None = None,
    reasoning: int | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if cost is not None:
        usage["cost"] = cost
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "model": model,
        "provider": provider,
        "system_fingerprint": "fp_1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage,
    }


def chat_sse(
    content: str = "Hi there",
    *,
    prompt: int = 50,
    completion: int = 5,
    cost: float = 0.00002,
) -> list[bytes]:
    base = {"id": "gen-s", "object": "chat.completion.chunk", "model": "deepseek/deepseek-v4.1-flash", "provider": "P"}
    events = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content[:3]}}]},
        {**base, "choices": [{"index": 0, "delta": {"content": content[3:]}, "finish_reason": "stop"}]},
        {
            **base,
            "choices": [],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "cost": cost},
        },
    ]
    return (
        [b": OPENROUTER PROCESSING\n\n"]
        + [f"data: {json.dumps(e)}\n\n".encode() for e in events]
        + [b"data: [DONE]\n\n"]
    )


# =====================================================================================
# The gateway under test
# =====================================================================================


def config_dict(target: Upstream | str, /, **overrides: Any) -> dict[str, Any]:
    """A ``GatewayConfig`` (validated by the WP-01 contract) as the JSON the sandbox passes."""
    base_url = target if isinstance(target, str) else target.base_url
    upstream_section = {"kind": "openrouter", "base_url": base_url, **overrides.pop("upstream", {})}
    cfg = gateway_config(upstream=upstream_section, **overrides)
    return cfg.model_dump(mode="json")


@dataclass
class Running:
    gateway: Any
    server: ThreadingHTTPServer
    upstream: Upstream
    logs: Path

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def calls(self, n: int | None = None, timeout: float = 5.0) -> list[dict[str, Any]]:
        """The call records; with ``n``, wait until at least ``n`` are written (a record is appended
        just after the harness has its answer)."""
        deadline = time.monotonic() + timeout
        while True:
            records = read_jsonl(self.logs / "calls.jsonl")
            if n is None or len(records) >= n or time.monotonic() > deadline:
                return records
            time.sleep(0.01)

    def settled(self, timeout: float = 5.0) -> dict[str, Any]:
        """``state.json`` once nothing is in flight."""
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            if state["inflight"] == 0 or time.monotonic() > deadline:
                return state
            time.sleep(0.01)

    def events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.logs / "gateway_events.jsonl")

    def state(self) -> dict[str, Any]:
        return json.loads((self.logs / "state.json").read_text())

    def request(
        self,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        timeout: float = 30,
    ) -> Response:
        return http_request(self.port, path, body, method=method, headers=headers, timeout=timeout)


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    raw: bytes
    chunks: list[tuple[float, bytes]]  # (seconds since the request, chunk) as the client read them

    def json(self) -> Any:
        return json.loads(self.raw)


def http_request(
    port: int,
    path: str,
    body: Mapping[str, Any] | None = None,
    *,
    method: str = "POST",
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> Response:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    data = json.dumps(body).encode() if body is not None else None
    sent = {"Content-Type": "application/json", "Authorization": "Bearer sk-dummy", **(headers or {})}
    started = time.monotonic()
    conn.request(method, path, body=data, headers=sent)
    resp = conn.getresponse()
    chunks: list[tuple[float, bytes]] = []
    if method == "HEAD":
        raw = b""
    else:
        parts = []
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read()
            if not chunk:
                break
            chunks.append((time.monotonic() - started, chunk))
            parts.append(chunk)
        raw = b"".join(parts)
    out = Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, raw, chunks)
    conn.close()
    return out


@contextmanager
def run_gateway(
    tmp_path: Path,
    *,
    replies: Sequence[Reply] = (),
    default: Callable[[Received], Reply] | None = None,
    config: Mapping[str, Any] | None = None,
    ping_interval_s: float = 5.0,
    **overrides: Any,
) -> Iterator[Running]:
    upstream = Upstream(list(replies), default).start()
    cfg = dict(config) if config is not None else config_dict(upstream, **overrides)
    if config is not None:
        cfg["upstream"] = {**cfg["upstream"], "base_url": upstream.base_url}
    logs = tmp_path / "logs"
    gw = gateway_module.Gateway(cfg, log_dir=logs, api_key="sk-real", ping_interval_s=ping_interval_s)
    server = gw.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield Running(gw, server, upstream, logs)
    finally:
        server.shutdown()
        server.server_close()
        upstream.stop()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def chat_body(text: str = "Build the app.", **extra: Any) -> dict[str, Any]:
    return {"model": "whatever-the-harness-says", "messages": [{"role": "user", "content": text}], **extra}
