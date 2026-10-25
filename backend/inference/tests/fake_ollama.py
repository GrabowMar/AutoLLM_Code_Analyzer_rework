"""A deterministic stand-in for an Ollama server (stdlib only), for relay and e2e tests.

Run in-process (``FakeOllama(...).start()``) or as a script
(``python fake_ollama.py --port 0 --models 4 --record out.json``). It serves

- ``GET /api/version``, ``GET /api/tags`` (N models with digests), ``POST /api/show``
  (``context_length`` 8192, capabilities ``completion`` + ``tools``),
  ``GET /api/ps`` (honours ``max_loaded`` with LRU eviction and counts loads),
- ``POST /api/generate`` (warm-up / unload only: an empty prompt),
- ``POST /api/chat`` and ``POST /v1/chat/completions``: deterministic per seed,
  a tool call when ``tools`` are present and the last message is not a tool
  result, a configurable latency and cold-load delay.

It records the maximum concurrency, the received ``options``, load events
and the request order (``stats``; optionally dumped to a JSON file). A
client that disconnects mid-request is recorded in ``aborted``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any

FAMILY = "qwen2"


def model_names(count: int) -> list[str]:
    return [f"fake-coder-{i}:7b" for i in range(count)]


def digest_of(name: str, generation: int = 0) -> str:
    return "sha256:" + hashlib.sha256(f"{name}#{generation}".encode()).hexdigest()


class FakeOllama:
    def __init__(
        self,
        *,
        models: int = 4,
        max_loaded: int = 1,
        latency_s: float = 0.0,
        load_delay_s: float = 0.0,
        context_length: int = 8192,
        record_path: str | None = None,
        port: int = 0,
    ) -> None:
        self.names = model_names(models)
        self.generation = dict.fromkeys(self.names, 0)
        self.max_loaded = max_loaded
        self.latency_s = latency_s
        self.load_delay_s = load_delay_s
        self.context_length = context_length
        self.record_path = record_path
        self.lock = threading.Lock()
        self.loaded: OrderedDict[str, float] = OrderedDict()
        self.stats: dict[str, Any] = {
            "max_concurrency": 0,
            "concurrency": 0,
            "loads": [],
            "options": [],
            "order": [],
            "aborted": 0,
            "chat_calls": 0,
            "max_resident": 0,
            "requests": [],
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True
        self.thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakeOllama:
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.dump()

    def dump(self) -> None:
        if self.record_path:
            with open(self.record_path, "w", encoding="utf-8") as fh:  # noqa: PTH123
                json.dump(self.snapshot(), fh)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.stats))

    def bump_digest(self, name: str) -> str:
        """Simulate ``ollama pull`` replacing a model's weights."""
        with self.lock:
            self.generation[name] += 1
        return digest_of(name, self.generation[name])

    # -- model state -----------------------------------------------------

    def _ensure_loaded(self, name: str) -> float:
        """Load ``name`` (LRU-evicting beyond ``max_loaded``); returns the load delay applied."""
        with self.lock:
            if name in self.loaded:
                self.loaded.move_to_end(name)
                self.loaded[name] = time.time()
                return 0.0
            while len(self.loaded) >= self.max_loaded:
                self.loaded.popitem(last=False)
            self.loaded[name] = time.time()
            self.stats["loads"].append(name)
            self.stats["max_resident"] = max(self.stats["max_resident"], len(self.loaded))
        if self.load_delay_s:
            time.sleep(self.load_delay_s)
        return self.load_delay_s

    def _unload(self, name: str) -> None:
        with self.lock:
            self.loaded.pop(name, None)

    def _completion(self, name: str, messages: list, seed: Any, tools: Any) -> dict[str, Any]:
        text = json.dumps([name, messages, seed], sort_keys=True, default=str)
        token = hashlib.sha256(text.encode()).hexdigest()[:12]
        last_role = (messages[-1] or {}).get("role") if messages else None
        if tools and last_role != "tool":
            fn = ((tools[0] or {}).get("function") or {}).get("name") or "tool"
            return {"tool_calls": [{"function": {"name": fn, "arguments": {"cmd": f"echo {token}"}}}], "content": ""}
        return {"content": f"answer {token} (seed {seed})"}

    # -- HTTP --------------------------------------------------------------

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _json(self, status: int, payload: Any) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    with fake.lock:
                        fake.stats["aborted"] += 1

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    return json.loads(raw or b"{}")
                except ValueError:
                    return {}

            def do_GET(self):
                if self.path == "/api/version":
                    self._json(200, {"version": "0.12.3-fake"})
                elif self.path == "/api/tags":
                    models = [
                        {
                            "name": n,
                            "model": n,
                            "digest": digest_of(n, fake.generation[n])[len("sha256:") :],
                            "size": 4_000_000_000,
                            "modified_at": "2026-10-01T00:00:00Z",
                            "details": {"family": FAMILY, "parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
                        }
                        for n in fake.names
                    ]
                    self._json(200, {"models": models})
                elif self.path == "/api/ps":
                    with fake.lock:
                        loaded = list(fake.loaded)
                    self._json(
                        200,
                        {
                            "models": [
                                {
                                    "name": n,
                                    "digest": digest_of(n, fake.generation[n])[len("sha256:") :],
                                    "size": 5_000_000_000,
                                    "size_vram": 5_000_000_000,
                                    "expires_at": "2026-10-01T00:30:00Z",
                                    "context_length": fake.context_length,
                                }
                                for n in loaded
                            ],
                        },
                    )
                elif self.path == "/v1/models":
                    self._json(200, {"object": "list", "data": [{"id": n, "object": "model"} for n in fake.names]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                body = self._body()
                if self.path == "/api/show":
                    name = body.get("model") or body.get("name")
                    if name not in fake.generation:
                        self._json(404, {"error": f"model {name!r} not found"})
                        return
                    self._json(
                        200,
                        {
                            "template": "{{ .Prompt }}",
                            "capabilities": ["completion", "tools"],
                            "model_info": {f"{FAMILY}.context_length": fake.context_length},
                            "details": {"family": FAMILY},
                        },
                    )
                elif self.path == "/api/generate":
                    name = body.get("model")
                    if body.get("prompt"):
                        self._json(400, {"error": "fake server only supports empty-prompt generate"})
                        return
                    if body.get("keep_alive") in (0, "0"):
                        fake._unload(name)
                        self._json(200, {"model": name, "done": True, "done_reason": "unload"})
                        return
                    load = fake._ensure_loaded(name)
                    self._json(200, {"model": name, "done": True, "load_duration": int(load * 1e9)})
                elif self.path in ("/api/chat", "/v1/chat/completions"):
                    self._chat(body, native=self.path == "/api/chat")
                else:
                    self._json(404, {"error": "not found"})

            def _chat(self, body: dict[str, Any], *, native: bool) -> None:
                name = body.get("model")
                if name not in fake.generation:
                    self._json(404, {"error": f"model {name!r} not found"})
                    return
                options = body.get("options") or {}
                seed = options.get("seed") if native else body.get("seed")
                with fake.lock:
                    fake.stats["concurrency"] += 1
                    fake.stats["max_concurrency"] = max(fake.stats["max_concurrency"], fake.stats["concurrency"])
                    fake.stats["chat_calls"] += 1
                    fake.stats["order"].append(name)
                    fake.stats["options"].append(options if native else {"seed": seed})
                    fake.stats["requests"].append({"model": name, "native": native, "body": body})
                try:
                    load = fake._ensure_loaded(name)
                    deadline = time.monotonic() + fake.latency_s
                    while time.monotonic() < deadline:
                        # Notice a client that went away (relay cancel) like Ollama does.
                        if _peer_closed(self.connection):
                            with fake.lock:
                                fake.stats["aborted"] += 1
                            return
                        time.sleep(0.05)
                    reply = fake._completion(name, body.get("messages") or [], seed, body.get("tools"))
                    if native:
                        payload = {
                            "model": name,
                            "created_at": "2026-10-01T00:00:00Z",
                            "message": {"role": "assistant", **reply},
                            "done": True,
                            "done_reason": "stop",
                            "prompt_eval_count": 42,
                            "eval_count": 7,
                            "load_duration": int(load * 1e9),
                            "eval_duration": 70_000_000,
                            "total_duration": int((fake.latency_s + load) * 1e9),
                        }
                    else:
                        message = {"role": "assistant", "content": reply.get("content") or ""}
                        if reply.get("tool_calls"):
                            message["tool_calls"] = [
                                {
                                    "id": f"call_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": c["function"]["name"],
                                        "arguments": json.dumps(c["function"]["arguments"]),
                                    },
                                }
                                for i, c in enumerate(reply["tool_calls"])
                            ]
                        payload = {
                            "id": "chatcmpl-fake",
                            "object": "chat.completion",
                            "model": name,
                            "choices": [
                                {
                                    "index": 0,
                                    "message": message,
                                    "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop",
                                },
                            ],
                            "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
                        }
                    self._json(200, payload)
                finally:
                    with fake.lock:
                        fake.stats["concurrency"] -= 1

        return Handler


def _peer_closed(sock: socket.socket) -> bool:
    try:
        sock.settimeout(0.0)
        try:
            data = sock.recv(1, socket.MSG_PEEK)
        finally:
            sock.settimeout(None)
    except BlockingIOError:
        return False
    except OSError:
        return True
    return data == b""


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Ollama server for LLM Lab relay tests")
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--models", type=int, default=4)
    parser.add_argument("--max-loaded", type=int, default=1)
    parser.add_argument("--latency", type=float, default=0.0)
    parser.add_argument("--load-delay", type=float, default=0.0)
    parser.add_argument("--record", default=None)
    args = parser.parse_args()
    fake = FakeOllama(
        models=args.models,
        max_loaded=args.max_loaded,
        latency_s=args.latency,
        load_delay_s=args.load_delay,
        record_path=args.record,
        port=args.port,
    ).start()
    print(fake.url, flush=True)  # noqa: T201
    try:
        while True:
            time.sleep(1)
            fake.dump()
    except KeyboardInterrupt:
        fake.stop()


if __name__ == "__main__":
    main()
