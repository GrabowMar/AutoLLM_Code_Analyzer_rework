"""A fake Anthropic Messages server (``POST /v1/messages``, SSE): the requested model picks the behaviour.

* ``normal``: ``message_start`` (cached usage), an empty thinking block, text deltas, ``end_turn``, the
  final ``message_delta`` usage and ``message_stop`` (``event:`` lines included, as Anthropic sends them);
* ``tools``: text, then one ``tool_use`` whose input arrives in three ``input_json_delta`` fragments;
* ``slow``: pings, then a text delta every 50 ms until the client goes away;
* ``thinking-only``: pings and nothing visible until the client goes away;
* ``hidden-thinking``: a thinking block starts (``display`` omitted: no thinking text), then pings until the
  client goes away;
* ``error-event``: some text, then an in-stream ``overloaded_error``;
* ``json``: ignores ``stream`` and answers one ``message`` JSON;
* ``http-529`` / ``http-429`` / ``http-400-too-long``: a JSON error answer with that status;
* ``GET /v1/models``: Anthropic's model list (``max_input_tokens``) of ``models``.

Every request body is kept in ``received`` and its headers in ``headers``; ``disconnects`` counts streams
the client closed.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any

USAGE = {"input_tokens": 12, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 4000, "output_tokens": 1}
OUTPUT_TOKENS = 25


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    @property
    def fake(self) -> FakeAnthropic:
        return self.server.fake  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.flush()

    def _event(self, payload: dict[str, Any]) -> None:
        data = f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _end(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _message_start(self, model: str) -> None:
        message = {"id": "msg_fake", "type": "message", "role": "assistant", "model": model, "content": []}
        self._event({"type": "message_start", "message": {**message, "usage": dict(USAGE)}})

    def _text(self, index: int, *pieces: str) -> None:
        self._event({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
        for piece in pieces:
            self._event(
                {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": piece}},
            )
        self._event({"type": "content_block_stop", "index": index})

    def _finish(self, stop_reason: str) -> None:
        self._event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": OUTPUT_TOKENS},
            },
        )
        self._event({"type": "message_stop"})

    def do_GET(self) -> None:
        with self.fake.lock:
            self.fake.headers.append({k.lower(): v for k, v in self.headers.items()})
            self.fake.paths.append(self.path)
        if self.path.rstrip("/") != "/v1/models":
            self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "not found"}})
            return
        data = [
            {"type": "model", "id": name, "display_name": name, "max_input_tokens": 1_000_000, "max_tokens": 128_000}
            for name in self.fake.models
        ]
        self._json(200, {"data": data, "has_more": False, "first_id": data[0]["id"], "last_id": data[-1]["id"]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with self.fake.lock:
            self.fake.received.append(body)
            self.fake.headers.append({k.lower(): v for k, v in self.headers.items()})
            self.fake.paths.append(self.path)
        model = str(body.get("model") or "normal")
        if model.startswith("http-"):
            status = int(model.split("-")[1])
            kind, message = {
                529: ("overloaded_error", "Overloaded"),
                429: ("rate_limit_error", "Number of request tokens has exceeded your per-minute rate limit"),
                400: ("invalid_request_error", "prompt is too long: 1200000 tokens > 1000000 maximum"),
            }[status]
            self._json(status, {"type": "error", "error": {"type": kind, "message": message}})
            return
        if model == "json":
            self._json(
                200,
                {
                    "id": "msg_json",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": "plain"}],
                    "stop_reason": "end_turn",
                    "usage": {**USAGE, "output_tokens": 3},
                },
            )
            return
        self._start()
        try:
            self._message_start(model)
            if model == "normal":
                self._event(
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
                )
                self._event({"type": "ping"})
                self._event(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "signature_delta", "signature": "s"},
                    },
                )
                self._event({"type": "content_block_stop", "index": 0})
                self._text(1, "Hello", ", ", "world")
                self._finish("end_turn")
            elif model == "tools":
                self._text(0, "Writing it.")
                block = {"type": "tool_use", "id": "toolu_01", "name": "write", "input": {}}
                self._event({"type": "content_block_start", "index": 1, "content_block": block})
                for part in ('{"path": ', '"a.txt", ', '"text": "hi"}'):
                    self._event(
                        {
                            "type": "content_block_delta",
                            "index": 1,
                            "delta": {"type": "input_json_delta", "partial_json": part},
                        },
                    )
                self._event({"type": "content_block_stop", "index": 1})
                self._finish("tool_use")
            elif model in ("slow", "thinking-only", "hidden-thinking"):
                hidden = model == "hidden-thinking"
                block = {"type": "thinking", "thinking": ""} if hidden else {"type": "text", "text": ""}
                self._event({"type": "content_block_start", "index": 0, "content_block": block})
                while not self.fake.stopped.is_set():
                    self._event({"type": "ping"})
                    if model == "slow":
                        delta = {"type": "text_delta", "text": "more text "}
                        self._event({"type": "content_block_delta", "index": 0, "delta": delta})
                    time.sleep(0.05)
            elif model == "error-event":
                self._text(0, "partial")
                self._event({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
            self._end()
        except OSError:
            with self.fake.lock:
                self.fake.disconnects += 1


class FakeAnthropic:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.paths: list[str] = []
        self.models = ["claude-opus-5-5", "claude-haiku-4-5-20251001", "normal", "tools"]
        self.disconnects = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.fake = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeAnthropic:
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self) -> None:
        self.stopped.set()
        self._server.shutdown()
        self._server.server_close()
