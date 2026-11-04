"""A fake OpenAI-compatible server for streamed-call tests (E2E-17): the served model name picks the behaviour.

* ``normal``: reasoning, then content, ``finish_reason=stop`` and a usage chunk (``include_usage``);
* ``runaway``: reasoning chunks forever (OpenCode Go's qwen3.8-flash ignoring ``max_tokens``), until the
  client goes away;
* ``slow-runaway``: the same, one chunk every 50 ms;
* ``silent``: the 200 headers, then nothing;
* ``json``: ignores ``stream`` and answers one ``chat.completion``;
* ``tools``: one complete tool call split over chunks, then ``tool_calls`` finish and usage;
* ``tools-runaway``: a tool call whose arguments never end;
* ``broken``: some content, then the connection closes without a finish;
* ``error``: an in-stream error object;
* ``http-500``: a JSON 500;
* ``verbose``: 560 chars of content (5.6 chars per token), the provider's own ``length`` finish, then its
  usage chunk (100 completion tokens) and ``[DONE]``;
* ``no-usage``: ``hello``, ``stop`` and ``[DONE]``, never a usage chunk (``include_usage`` ignored);
* ``finish-then-stall``: content and a ``stop`` finish, then nothing (no usage, no ``[DONE]``).

Every request body is kept in ``received``; ``disconnects`` counts streams the client closed.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any

PROMPT_TOKENS = 31
REASONING_PIECE = "Let me think about this once more. "  # 35 chars
VERBOSE_PIECE = "configurationRepositoryFactoryImpl xxxxx"  # 40 chars


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    @property
    def fake(self) -> FakeOpenAIStream:
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
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.flush()

    def _event(self, payload: Any) -> None:
        data = b"data: " + (payload if isinstance(payload, bytes) else json.dumps(payload).encode()) + b"\n\n"
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _end(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    @staticmethod
    def _chunk(delta: dict[str, Any] | None = None, finish: str | None = None, **extra: Any) -> dict[str, Any]:
        choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "fake-model",
            "choices": choices,
            **extra,
        }

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.fake.received.append(body)
        model = str(body.get("model") or "normal")
        if model == "http-500":
            self._json(500, {"error": {"message": "boom"}})
            return
        if model == "json" or not body.get("stream"):
            self._json(
                200,
                {
                    "id": "chatcmpl-json",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "fake-model",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "plain"}, "finish_reason": "stop"},
                    ],
                    "usage": {
                        "prompt_tokens": PROMPT_TOKENS,
                        "completion_tokens": 2,
                        "total_tokens": PROMPT_TOKENS + 2,
                    },
                },
            )
            return
        self._start()
        try:
            if model == "normal":
                self._event(self._chunk({"role": "assistant", "content": ""}))
                for _ in range(3):
                    self._event(self._chunk({"reasoning_content": "hmm "}))
                for piece in ("Hello", ", ", "world"):
                    self._event(self._chunk({"content": piece}))
                self._event(self._chunk({}, "stop"))
                self._event(
                    self._chunk(
                        None,
                        usage={
                            "prompt_tokens": PROMPT_TOKENS,
                            "completion_tokens": 9,
                            "total_tokens": PROMPT_TOKENS + 9,
                            "completion_tokens_details": {"reasoning_tokens": 6},
                            "cost": 0,
                        },
                    ),
                )
                self._event(b"[DONE]")
            elif model in ("runaway", "slow-runaway"):
                pause = 0.05 if model == "slow-runaway" else 0.002
                self._event(self._chunk({"role": "assistant", "content": ""}))
                while not self.fake.stopped.is_set():
                    self._event(self._chunk({"reasoning_content": REASONING_PIECE}))
                    time.sleep(pause)
            elif model == "silent":
                while not self.fake.stopped.is_set():
                    time.sleep(0.05)
                    self.wfile.write(b"")  # nothing on the wire
            elif model == "tools":
                self._event(
                    self._chunk(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "write", "arguments": ""},
                                },
                            ],
                        },
                    ),
                )
                for part in ('{"path": ', '"a.txt", ', '"text": "hi"}'):
                    self._event(self._chunk({"tool_calls": [{"index": 0, "function": {"arguments": part}}]}))
                self._event(self._chunk({}, "tool_calls"))
                self._event(
                    self._chunk(
                        None,
                        usage={
                            "prompt_tokens": PROMPT_TOKENS,
                            "completion_tokens": 12,
                            "total_tokens": PROMPT_TOKENS + 12,
                        },
                    ),
                )
                self._event(b"[DONE]")
            elif model == "tools-runaway":
                self._event(
                    self._chunk(
                        {
                            "role": "assistant",
                            "content": "Writing it.",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "write", "arguments": ""},
                                },
                            ],
                        },
                    ),
                )
                self._event(self._chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"text": "'}}]}))
                while not self.fake.stopped.is_set():
                    self._event(self._chunk({"tool_calls": [{"index": 0, "function": {"arguments": "x" * 40}}]}))
                    time.sleep(0.002)
            elif model == "broken":
                self._event(self._chunk({"role": "assistant", "content": "partial"}))
                self.close_connection = True
                return  # no terminating chunk: the reader sees the connection close mid-stream
            elif model == "verbose":
                self._event(self._chunk({"role": "assistant", "content": ""}))
                for _ in range(14):
                    self._event(self._chunk({"content": VERBOSE_PIECE}))
                self._event(self._chunk({}, "length"))
                self._event(
                    self._chunk(
                        None,
                        usage={
                            "prompt_tokens": PROMPT_TOKENS,
                            "completion_tokens": 100,
                            "total_tokens": PROMPT_TOKENS + 100,
                        },
                    ),
                )
                self._event(b"[DONE]")
            elif model == "no-usage":
                self._event(self._chunk({"role": "assistant", "content": "hello"}))
                self._event(self._chunk({}, "stop"))
                self._event(b"[DONE]")
            elif model == "finish-then-stall":
                self._event(self._chunk({"role": "assistant", "content": "done here"}))
                self._event(self._chunk({}, "stop"))
                while not self.fake.stopped.is_set():
                    time.sleep(0.05)
            elif model == "error":
                self._event(self._chunk({"role": "assistant", "content": "x"}))
                self._event({"error": {"message": "provider overloaded", "code": 502}})
                self._event(b"[DONE]")
            self._end()
        except OSError:
            with self.fake.lock:
                self.fake.disconnects += 1


class FakeOpenAIStream:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.disconnects = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.fake = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeOpenAIStream:
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self) -> None:
        self.stopped.set()
        self._server.shutdown()
        self._server.server_close()

    def wait_disconnects(self, n: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.disconnects >= n:
                    return True
            time.sleep(0.02)
        return False
