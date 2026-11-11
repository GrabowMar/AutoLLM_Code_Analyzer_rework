"""A fake OpenCode Go (``opencode.ai/zen/go``) for the C1 / C2 tests: the served model name picks the behaviour.

* ``ok``: a normal answer (SSE when ``stream`` is set, else one ``chat.completion``);
* ``flaky``: the first ``fail_n`` requests fail with Go's transient ``[server_error]`` (``fail_mode`` ``"400"``:
  a JSON 400; ``"event"``: a 200 stream whose first event is the error; ``"200"``: a JSON 200 error body),
  then it answers like ``ok``;
* ``moderation``: always ``400 [data_inspection_failed]`` (Alibaba moderation; never retried);
* ``reject-effort``: 400 when the body carries ``reasoning_effort``, else ``ok``.

Every request body and its headers are kept in ``received`` / ``headers``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any

SERVER_ERROR = "Upstream request failed: [server_error] Upstream response was not valid JSON"
MODERATION = "Upstream request failed: [data_inspection_failed] Input data may contain inappropriate content."
USAGE = {"prompt_tokens": 21, "completion_tokens": 3, "total_tokens": 24}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    @property
    def fake(self) -> FakeOpenCodeGo:
        return self.server.fake  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _events(self, events: list[Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for event in events:
            data = b"data: " + (event if isinstance(event, bytes) else json.dumps(event).encode()) + b"\n\n"
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _answer(self, body: dict[str, Any]) -> None:
        if body.get("stream"):
            chunk = {"id": "chatcmpl-go", "object": "chat.completion.chunk", "created": 1, "model": body["model"]}
            self._events(
                [
                    {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}}]},
                    {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    {**chunk, "choices": [], "usage": USAGE},
                    b"[DONE]",
                ],
            )
            return
        self._json(
            200,
            {
                "id": "chatcmpl-go",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
                "usage": USAGE,
            },
        )

    def _transient(self) -> None:
        error = {"error": {"message": SERVER_ERROR, "type": "server_error"}}
        if self.fake.fail_mode == "event":
            self._events([error, b"[DONE]"])
        elif self.fake.fail_mode == "200":
            self._json(200, error)
        else:
            self._json(400, error)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with self.fake.lock:
            self.fake.received.append(body)
            self.fake.headers.append(dict(self.headers.items()))
            n = len(self.fake.received)
        model = str(body.get("model") or "ok")
        if model == "flaky" and n <= self.fake.fail_n:
            self._transient()
        elif model == "moderation":
            self._json(400, {"error": {"message": MODERATION, "type": "data_inspection_failed"}})
        elif model == "reject-effort" and "reasoning_effort" in body:
            self._json(400, {"error": {"message": "Unsupported parameter: 'reasoning_effort'", "type": "invalid"}})
        else:
            self._answer(body)


class FakeOpenCodeGo:
    def __init__(self, *, fail_n: int = 0, fail_mode: str = "400") -> None:
        self.fail_n = fail_n
        self.fail_mode = fail_mode
        self.received: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.fake = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]

    def start(self) -> FakeOpenCodeGo:
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
