"""A scripted, offline stand-in for the LLM gateway, used by the WP-07 harness smoke runs.

Stdlib only: it runs inside the agent-base image on an ``--internal`` docker network under the alias
``llm-gateway``, so a harness under test reaches it exactly where the real gateway sidecar would be
(``http://llm-gateway:8080``) and has no other egress. It never calls a real model. It speaks the four
front protocols of spec section 5.1 (``openai_chat``, ``anthropic_messages``, ``openai_responses`` and
``gemini_generate``), streaming and non-streaming, and logs every request verbatim to ``--log`` (one
JSON object per line), which is how the native system prompts and tool specs of each harness are
captured (``harnesses/captured/``).

Script, per conversation (decided from the request alone, so it needs no state):

1. no shell-like tool offered (Aider, auxiliary calls): a plain text reply, or an Aider search/replace
   block that creates ``hello.txt`` when the system prompt asks for SEARCH/REPLACE edits;
2. no tool result in the conversation yet: one shell tool call writing ``hello.txt`` into the workspace;
3. one tool result: for mini-swe-agent (its instance prompt names the submit sentinel) the submit command,
   otherwise a final text answer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

WRITE_CMD = "printf 'hello from the scripted upstream\\n' > /workspace/hello.txt && ls /workspace"
SUBMIT_SENTINEL = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
FINAL_TEXT = "Done. I created hello.txt in the workspace."
AIDER_EDIT = "hello.txt\n```text\n<<<<<<< SEARCH\n=======\nhello from the scripted upstream\n>>>>>>> REPLACE\n```\n"
_SHELL_NAME = re.compile(r"(^|_)(bash|shell|run_shell_command|exec_command|execute_bash)$", re.IGNORECASE)
_LOCK = threading.Lock()


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                out.append(str(part.get("text") or ""))
            else:
                out.append(str(part))
        return "\n".join(out)
    return "" if content is None else str(content)


# -- request inspection per protocol ------------------------------------------------------------------


def _tools(protocol: str, body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(name, JSON schema of the parameters) of every function tool in the request."""
    out: list[tuple[str, dict[str, Any]]] = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        if protocol == "gemini_generate":
            for decl in tool.get("functionDeclarations") or tool.get("function_declarations") or []:
                schema = decl.get("parameters") or decl.get("parametersJsonSchema") or {}
                out.append((str(decl.get("name")), schema))
        elif protocol == "openai_chat":
            fn = tool.get("function") or {}
            out.append((str(fn.get("name")), fn.get("parameters") or {}))
        elif protocol == "anthropic_messages":
            out.append((str(tool.get("name")), tool.get("input_schema") or {}))
        else:  # openai_responses
            out.append((str(tool.get("name") or tool.get("type")), tool.get("parameters") or {}))
    return out


def _system(protocol: str, body: dict[str, Any]) -> str:
    if protocol == "anthropic_messages":
        return _text(body.get("system"))
    if protocol == "openai_responses":
        parts = [_text(body.get("instructions"))]
        for item in body.get("input") or []:
            if isinstance(item, dict) and item.get("role") in {"system", "developer"}:
                parts.append(_text(item.get("content")))
        return "\n".join(parts)
    if protocol == "gemini_generate":
        sys_inst = body.get("systemInstruction") or body.get("system_instruction") or {}
        return _text(sys_inst.get("parts") if isinstance(sys_inst, dict) else sys_inst)
    return "\n".join(
        _text(m.get("content")) for m in body.get("messages") or [] if m.get("role") in {"system", "developer"}
    )


def _tool_results(protocol: str, body: dict[str, Any]) -> int:
    if protocol == "openai_chat":
        return sum(1 for m in body.get("messages") or [] if m.get("role") == "tool")
    if protocol == "anthropic_messages":
        return sum(
            1
            for m in body.get("messages") or []
            if isinstance(m.get("content"), list)
            for part in m["content"]
            if isinstance(part, dict) and part.get("type") == "tool_result"
        )
    if protocol == "openai_responses":
        return sum(
            1
            for item in body.get("input") or []
            if isinstance(item, dict) and str(item.get("type", "")).endswith("call_output")
        )
    return sum(
        1
        for content in body.get("contents") or []
        for part in content.get("parts") or []
        if isinstance(part, dict) and ("functionResponse" in part or "function_response" in part)
    )


def _shell_call(tools: list[tuple[str, dict[str, Any]]], command: str) -> tuple[str, dict[str, Any]] | None:
    for name, schema in tools:
        short = name.rsplit("__", 1)[-1]
        if not _SHELL_NAME.search(short):
            continue
        props = schema.get("properties") or {}
        key = next((k for k in ("command", "cmd") if k in props), None)
        if key is None:
            continue
        kind = (props[key] or {}).get("type")
        args: dict[str, Any] = {key: ["bash", "-lc", command] if kind == "array" else command}
        for req in schema.get("required") or []:
            if req in args:
                continue
            ptype = (props.get(req) or {}).get("type")
            if ptype == "string":
                args[req] = "write a file" if "desc" in req else "/workspace"
            elif ptype in {"integer", "number"}:
                args[req] = 60000 if "timeout" in req else 1
            elif ptype == "boolean":
                args[req] = False
        return name, args
    return None


def decide(protocol: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"text": str}`` or ``{"tool": name, "args": dict}`` for this request."""
    tools = _tools(protocol, body)
    system = _system(protocol, body)
    results = _tool_results(protocol, body)
    if not tools:
        if "SEARCH" in system and "REPLACE" in system and results == 0:
            return {"text": AIDER_EDIT}
        return {"text": FINAL_TEXT}
    if results == 0:
        call = _shell_call(tools, WRITE_CMD)
    elif results == 1 and SUBMIT_SENTINEL in json.dumps(body):
        call = _shell_call(tools, f"echo {SUBMIT_SENTINEL}")
    else:
        call = None
    if call is None:
        return {"text": FINAL_TEXT}
    return {"tool": call[0], "args": call[1]}


# -- response encoding per protocol ---------------------------------------------------------------------

USAGE_IN, USAGE_OUT = 1000, 50


def _sse(handler: BaseHTTPRequestHandler, events: list[tuple[str | None, dict[str, Any] | str]]) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()
    for name, data in events:
        chunk = ""
        if name:
            chunk += f"event: {name}\n"
        chunk += "data: " + (data if isinstance(data, str) else json.dumps(data)) + "\n\n"
        handler.wfile.write(chunk.encode())
        handler.wfile.flush()
    handler.close_connection = True


def _json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def openai_chat(handler: BaseHTTPRequestHandler, body: dict[str, Any], plan: dict[str, Any], n: int) -> None:
    model = body.get("model", "scripted")
    usage = {"prompt_tokens": USAGE_IN, "completion_tokens": USAGE_OUT, "total_tokens": USAGE_IN + USAGE_OUT}
    if "tool" in plan:
        call = {
            "id": f"call_{n}",
            "type": "function",
            "function": {"name": plan["tool"], "arguments": json.dumps(plan["args"])},
        }
        message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": [call]}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": plan["text"]}
        finish = "stop"
    base = {"id": f"chatcmpl-{n}", "created": int(time.time()), "model": model}
    if not body.get("stream"):
        _json(
            handler,
            200,
            {
                **base,
                "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            },
        )
        return
    delta: dict[str, Any] = {"role": "assistant"}
    if "tool" in plan:
        delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]
    else:
        delta["content"] = plan["text"]
    chunk = {**base, "object": "chat.completion.chunk"}
    events: list[tuple[str | None, dict[str, Any] | str]] = [
        (None, {**chunk, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
        (None, {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}),
        (None, {**chunk, "choices": [], "usage": usage}),
        (None, "[DONE]"),
    ]
    _sse(handler, events)


def anthropic_messages(handler: BaseHTTPRequestHandler, body: dict[str, Any], plan: dict[str, Any], n: int) -> None:
    model = body.get("model", "scripted")
    if "tool" in plan:
        block: dict[str, Any] = {
            "type": "tool_use",
            "id": f"toolu_{n:04d}",
            "name": plan["tool"],
            "input": plan["args"],
        }
        stop = "tool_use"
    else:
        block = {"type": "text", "text": plan["text"]}
        stop = "end_turn"
    usage = {"input_tokens": USAGE_IN, "output_tokens": USAGE_OUT, "cache_read_input_tokens": 0}
    message = {
        "id": f"msg_{n:04d}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [block],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": usage,
    }
    if not body.get("stream"):
        _json(handler, 200, message)
        return
    start_block = dict(block, input={}) if block["type"] == "tool_use" else {"type": "text", "text": ""}
    delta = (
        {"type": "input_json_delta", "partial_json": json.dumps(plan["args"])}
        if "tool" in plan
        else {"type": "text_delta", "text": plan["text"]}
    )
    events: list[tuple[str | None, dict[str, Any] | str]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {**message, "content": [], "stop_reason": None, "usage": {**usage, "output_tokens": 1}},
            },
        ),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start_block}),
        ("ping", {"type": "ping"}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": USAGE_OUT},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    _sse(handler, events)


def openai_responses(handler: BaseHTTPRequestHandler, body: dict[str, Any], plan: dict[str, Any], n: int) -> None:
    model = body.get("model", "scripted")
    if "tool" in plan:
        item: dict[str, Any] = {
            "type": "function_call",
            "id": f"fc_{n}",
            "call_id": f"call_{n}",
            "name": plan["tool"],
            "arguments": json.dumps(plan["args"]),
            "status": "completed",
        }
    else:
        item = {
            "type": "message",
            "id": f"msg_{n}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": plan["text"], "annotations": []}],
        }
    usage = {
        "input_tokens": USAGE_IN,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": USAGE_OUT,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": USAGE_IN + USAGE_OUT,
    }
    response = {
        "id": f"resp_{n}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": [item],
        "usage": usage,
    }
    if not body.get("stream"):
        _json(handler, 200, response)
        return
    in_progress = {**response, "status": "in_progress", "output": [], "usage": None}
    events: list[tuple[str | None, dict[str, Any] | str]] = [
        ("response.created", {"type": "response.created", "sequence_number": 0, "response": in_progress}),
        ("response.in_progress", {"type": "response.in_progress", "sequence_number": 1, "response": in_progress}),
        (
            "response.output_item.added",
            {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0, "item": item},
        ),
    ]
    if "tool" not in plan:
        events.append(
            (
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 3,
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": plan["text"],
                },
            ),
        )
    events += [
        (
            "response.output_item.done",
            {"type": "response.output_item.done", "sequence_number": 4, "output_index": 0, "item": item},
        ),
        ("response.completed", {"type": "response.completed", "sequence_number": 5, "response": response}),
    ]
    _sse(handler, events)


def gemini_generate(
    handler: BaseHTTPRequestHandler,
    body: dict[str, Any],
    plan: dict[str, Any],
    n: int,
    *,
    stream: bool,
) -> None:
    part = {"functionCall": {"name": plan["tool"], "args": plan["args"]}} if "tool" in plan else {"text": plan["text"]}
    payload = {
        "candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {
            "promptTokenCount": USAGE_IN,
            "candidatesTokenCount": USAGE_OUT,
            "totalTokenCount": USAGE_IN + USAGE_OUT,
        },
        "modelVersion": "scripted",
        "responseId": f"resp_{n}",
    }
    if stream:
        _sse(handler, [(None, payload)])
    else:
        _json(handler, 200, payload)


# -- server ---------------------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    log_path = "/logs/upstream.jsonl"
    counter = 0

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("upstream: " + (format % args) + "\n")

    def _record(self, protocol: str | None, body: Any, plan: dict[str, Any] | None) -> int:
        with _LOCK:
            Handler.counter += 1
            n = Handler.counter
            entry = {
                "n": n,
                "ts": time.time(),
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items() if k.lower() not in {"authorization"}},
                "protocol": protocol,
                "body": body,
                "plan": plan,
            }
            with Path(self.log_path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return n

    def do_HEAD(self) -> None:
        self._record(None, None, None)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._record(None, None, None)
        path = urlparse(self.path).path
        if path.endswith("/models"):
            _json(self, 200, {"object": "list", "data": [{"id": "scripted", "object": "model", "type": "model"}]})
            return
        _json(self, 404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        path = urlparse(self.path).path
        if path.endswith(("/count_tokens", ":countTokens")):
            self._record("count_tokens", body, None)
            _json(self, 200, {"input_tokens": USAGE_IN, "totalTokens": USAGE_IN})
            return
        if path.endswith("/chat/completions"):
            protocol = "openai_chat"
        elif path.endswith("/messages"):
            protocol = "anthropic_messages"
        elif path.endswith("/responses"):
            protocol = "openai_responses"
        elif ":generateContent" in path or ":streamGenerateContent" in path:
            protocol = "gemini_generate"
        else:
            self._record(None, body, None)
            _json(self, 404, {"error": {"message": f"no route {path}", "type": "not_found"}})
            return
        plan = decide(protocol, body)
        n = self._record(protocol, body, plan)
        if protocol == "openai_chat":
            openai_chat(self, body, plan, n)
        elif protocol == "anthropic_messages":
            anthropic_messages(self, body, plan, n)
        elif protocol == "openai_responses":
            openai_responses(self, body, plan, n)
        else:
            gemini_generate(self, body, plan, n, stream=":streamGenerateContent" in path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log", default="/logs/upstream.jsonl")
    args = parser.parse_args()
    Handler.log_path = args.log
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)  # noqa: S104 - internal docker network only
    server.serve_forever()


if __name__ == "__main__":
    main()
