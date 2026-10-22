"""Per-job LLM gateway between a sandboxed coding agent and OpenRouter.

The agent container sits on an internal Docker network whose only other
member is this gateway, so every byte it sends out goes through here. The
gateway

* holds the real OpenRouter key (the agent is configured with a dummy one),
* pins the model and applies the job's frozen sampling params / provider
  routing / seed / output cap to every request, so the harness cannot quietly
  switch to a cheaper "small model" or drift its params between runs,
* normalises sampling across harnesses and model families: under sampling
  policy ``provider_default`` it strips whatever sampling knobs the harness
  sets itself (OpenCode picks per-family temperatures, Aider sends 0, others
  send nothing), so only the job's explicit params ever reach the model,
* retries transient upstream failures itself (408/429/5xx, connection
  errors) with backoff, so harnesses with different retry logic see the same
  provider reliability, and only successful calls count toward the step
  budget -- a flaky provider never costs a model its steps,
* enforces the job's budget (requests, tokens, USD) and answers a
  non-retryable 400 once it is spent, which ends the agent's run cleanly,
* appends every exchange to ``/logs/calls.jsonl`` -- the run's authoritative
  LLM record, independent of whatever the harness chooses to log,
* optionally tunnels HTTPS ``CONNECT`` to an allowlist of package registries
  (network policy ``packages``); everything else is refused.

Stdlib only, configured by the ``GATEWAY_CONFIG`` env var (JSON).
"""

# Standalone script baked into its own image (no package, stdlib os.path on purpose).
# ruff: noqa: INP001, PTH103, PTH120, PTH123

from __future__ import annotations

import contextlib
import http.client
import json
import os
import select
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

CONFIG = json.loads(os.environ.get("GATEWAY_CONFIG", "{}"))
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
UPSTREAM = urlsplit(CONFIG.get("upstream", "https://openrouter.ai/api/v1"))
MODEL = CONFIG.get("model", "")
# Params forced onto every request (the job's frozen sampling config).
FORCED = CONFIG.get("forced_params") or {}
# Params the harness is not allowed to set itself (dropped before forwarding).
STRIPPED = set(CONFIG.get("stripped_params") or [])
BUDGET = CONFIG.get("budget") or {}
# Hard ceiling on harness requests including failed ones, so a harness stuck
# re-sending a rejected request cannot loop forever.
MAX_ATTEMPTS = int(BUDGET.get("max_attempts") or (2 * int(BUDGET.get("max_requests") or 500) + 20))
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
UPSTREAM_RETRIES = int(CONFIG.get("upstream_retries", 4))
RETRY_BACKOFF_S = float(CONFIG.get("retry_backoff_s", 2.0))
ALLOWED_CONNECT = {h.lower() for h in CONFIG.get("connect_allowlist") or []}
LOG_PATH = CONFIG.get("log_path", "/logs/calls.jsonl")
EVENTS_PATH = CONFIG.get("events_path", "/logs/gateway_events.jsonl")
APP_HEADERS = {
    "HTTP-Referer": CONFIG.get("referer", "https://llm-lab.local"),
    "X-Title": CONFIG.get("title", "LLM Lab agent run"),
}
UPSTREAM_TIMEOUT = float(CONFIG.get("upstream_timeout_s", 600))

_lock = threading.Lock()
_state = {
    "attempts": 0,  # every request the harness made
    "requests": 0,  # successful completions (what the step budget counts)
    "inflight": 0,
    "upstream_retries": 0,
    "llm_seconds": 0.0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "reasoning_tokens": 0,
    "cached_tokens": 0,
    "cost": 0.0,
    "errors": 0,
    "rejected": 0,
    "budget_exhausted": None,
}


def _append(path: str, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _lock, open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _event(kind: str, **fields) -> None:
    _append(EVENTS_PATH, {"ts": time.time(), "event": kind, **fields})


def _budget_reason() -> str | None:
    """Return why the budget is spent, or None while requests may continue."""
    s = _state
    if BUDGET.get("max_requests") and s["requests"] + s["inflight"] >= BUDGET["max_requests"]:
        return "max_requests"
    if s["attempts"] >= MAX_ATTEMPTS:
        return "max_attempts"
    total = s["prompt_tokens"] + s["completion_tokens"]
    if BUDGET.get("max_total_tokens") and total >= BUDGET["max_total_tokens"]:
        return "max_total_tokens"
    if BUDGET.get("max_cost_usd") and s["cost"] >= BUDGET["max_cost_usd"]:
        return "max_cost_usd"
    return None


def _account(usage: dict | None) -> None:
    if not isinstance(usage, dict):
        return
    with _lock:
        _state["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        _state["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        details = usage.get("completion_tokens_details") or {}
        _state["reasoning_tokens"] += int(details.get("reasoning_tokens") or 0)
        pdetails = usage.get("prompt_tokens_details") or {}
        _state["cached_tokens"] += int(pdetails.get("cached_tokens") or 0)
        _state["cost"] += float(usage.get("cost") or 0.0)


def _rewrite(body: dict) -> tuple[dict, dict]:
    """Apply the job's policy to an outgoing chat request; return (body, diff)."""
    diff: dict = {}
    requested_model = body.get("model")
    if requested_model != MODEL:
        diff["model"] = requested_model
    body["model"] = MODEL
    for key in STRIPPED:
        if key in body:
            diff.setdefault("stripped", {})[key] = body.pop(key)
    for key, value in FORCED.items():
        if body.get(key) != value:
            diff.setdefault("forced", {})[key] = body.get(key)
        body[key] = value
    # Usage accounting (tokens + USD cost) on every response, streamed or not.
    body["usage"] = {"include": True}
    if body.get("stream"):
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
    return body, diff


class _StreamCollector:
    """Rebuild a streamed completion from SSE chunks for the call log."""

    def __init__(self) -> None:
        self.buffer = b""
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict] = {}
        self.meta: dict = {}
        self.usage: dict | None = None
        self.finish_reason = None
        self.error = None

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            try:
                event = json.loads(data)
            except ValueError:
                continue
            self._event(event)

    def _event(self, event: dict) -> None:
        if "error" in event:
            self.error = event["error"]
        for key in ("id", "model", "provider", "created", "system_fingerprint"):
            if event.get(key) is not None:
                self.meta[key] = event[key]
        if event.get("usage"):
            self.usage = event["usage"]
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.content.append(delta["content"])
            if delta.get("reasoning"):
                self.reasoning.append(delta["reasoning"])
            for tc in delta.get("tool_calls") or []:
                slot = self.tool_calls.setdefault(
                    tc.get("index", 0),
                    {"id": None, "name": "", "arguments": ""},
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]

    def summary(self) -> dict:
        return {
            **self.meta,
            "content": "".join(self.content),
            "reasoning": "".join(self.reasoning),
            "tool_calls": [self.tool_calls[i] for i in sorted(self.tool_calls)],
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "error": self.error,
        }


def _response_summary(payload: dict) -> dict:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "id": payload.get("id"),
        "model": payload.get("model"),
        "provider": payload.get("provider"),
        "system_fingerprint": payload.get("system_fingerprint"),
        "content": message.get("content"),
        "reasoning": message.get("reasoning"),
        "tool_calls": [
            {
                "id": tc.get("id"),
                "name": (tc.get("function") or {}).get("name"),
                "arguments": (tc.get("function") or {}).get("arguments"),
            }
            for tc in message.get("tool_calls") or []
        ],
        "finish_reason": choice.get("finish_reason"),
        "usage": payload.get("usage"),
        "error": payload.get("error"),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llm-gateway"

    def log_message(self, fmt: str, *args) -> None:  # quiet default access log
        sys.stderr.write("gateway: " + (fmt % args) + "\n")

    # -- helpers ---------------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, code: str) -> None:
        self._send_json(status, {"error": {"message": message, "code": code, "type": code}})

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(200, {"ok": True})
        elif path == "/stats":
            with _lock:
                self._send_json(200, dict(_state))
        elif path.endswith("/models"):
            self._send_json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._error(404, "not found", "not_found")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not path.endswith("/chat/completions"):
            with _lock:
                _state["rejected"] += 1
            _event("rejected_path", path=path)
            self._error(404, f"gateway only serves chat completions, not {path}", "not_found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self._error(400, "request body is not JSON", "bad_request")
            return

        with _lock:
            reason = _budget_reason()
            if reason and not _state["budget_exhausted"]:
                _state["budget_exhausted"] = reason
                _event("budget_exhausted", reason=reason, state=dict(_state))
            if not reason:
                _state["attempts"] += 1
                _state["inflight"] += 1
                seq = _state["attempts"]
        if reason:
            # 400, not 429: harnesses retry 429s with backoff, which would only
            # burn wall-clock time on a run that is already over.
            self._error(400, f"Run budget exhausted ({reason}). Stop working and summarise.", "budget_exhausted")
            return

        body, diff = _rewrite(body)
        started = time.time()
        record = {"seq": seq, "ts": started, "request": body, "policy_diff": diff}
        try:
            status, summary, retries = self._forward(body)
        except Exception as exc:  # noqa: BLE001 - logged and surfaced to the agent
            with _lock:
                _state["errors"] += 1
            record |= {"status": 502, "error": repr(exc), "duration_s": round(time.time() - started, 3)}
            _append(LOG_PATH, record)
            with contextlib.suppress(OSError):
                self._error(502, f"upstream error: {exc}", "upstream_error")
            return
        finally:
            with _lock:
                _state["inflight"] -= 1
        duration = round(time.time() - started, 3)
        ok = status < 400 and not summary.get("error")
        with _lock:
            _state["llm_seconds"] += duration
            _state["upstream_retries"] += retries
            if ok:
                _state["requests"] += 1
            else:
                _state["errors"] += 1
        _account(summary.get("usage"))
        record |= {"status": status, "response": summary, "duration_s": duration, "upstream_retries": retries}
        _append(LOG_PATH, record)

    def _open_upstream(self, data: bytes, stream: bool):
        conn = http.client.HTTPSConnection(
            UPSTREAM.hostname,
            UPSTREAM.port or 443,
            timeout=UPSTREAM_TIMEOUT,
            context=ssl.create_default_context(),
        )
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            **APP_HEADERS,
        }
        conn.request("POST", UPSTREAM.path.rstrip("/") + "/chat/completions", body=data, headers=headers)
        return conn, conn.getresponse()

    def _forward(self, body: dict) -> tuple[int, dict, int]:
        """Send upstream (retrying transient failures before any byte reaches the harness)."""
        data = json.dumps(body).encode()
        retries = 0
        while True:
            try:
                conn, resp = self._open_upstream(data, bool(body.get("stream")))
            except (OSError, http.client.HTTPException):
                if retries >= UPSTREAM_RETRIES:
                    raise
            else:
                if resp.status not in RETRY_STATUSES or retries >= UPSTREAM_RETRIES:
                    break
                resp.read()
                conn.close()
            retries += 1
            _event("upstream_retry", attempt=retries)
            time.sleep(RETRY_BACKOFF_S * (2 ** (retries - 1)))
        status, summary = self._relay(body, conn, resp)
        return status, summary, retries

    def _relay(self, body: dict, conn, resp) -> tuple[int, dict]:
        ctype = resp.getheader("Content-Type", "application/json")
        streaming = body.get("stream") and "text/event-stream" in ctype and resp.status < 400

        if not streaming:
            payload_raw = resp.read()
            conn.close()
            self.send_response(resp.status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload_raw)))
            self.end_headers()
            self.wfile.write(payload_raw)
            try:
                payload = json.loads(payload_raw)
            except ValueError:
                return resp.status, {"error": payload_raw[:2000].decode(errors="replace")}
            return resp.status, _response_summary(payload) if isinstance(payload, dict) else {"error": "non-object"}

        self.send_response(resp.status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        collector = _StreamCollector()
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                collector.feed(chunk)
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        finally:
            conn.close()
        return resp.status, collector.summary()

    def do_CONNECT(self) -> None:
        host, _, port = self.path.partition(":")
        port = int(port or 443)
        if host.lower() not in ALLOWED_CONNECT or port != 443:
            with _lock:
                _state["rejected"] += 1
            _event("rejected_connect", target=self.path)
            self._error(403, f"egress to {self.path} is not allowed in this sandbox", "egress_denied")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError as exc:
            self._error(502, f"cannot reach {self.path}: {exc}", "upstream_error")
            return
        _event("connect", target=self.path)
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._tunnel(self.connection, upstream)
        self.close_connection = True

    @staticmethod
    def _tunnel(client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        try:
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 300)
                if errored or not readable:
                    return
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    (upstream if sock is client else client).sendall(data)
        except OSError:
            return
        finally:
            upstream.close()


def main() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    port = int(CONFIG.get("port", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - internal network only
    server.daemon_threads = True
    _event("started", model=MODEL, forced=FORCED, budget=BUDGET, connect_allowlist=sorted(ALLOWED_CONNECT))
    sys.stderr.write(f"gateway: listening on :{port} for model {MODEL}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
