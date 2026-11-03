"""OpenAI Responses front (Codex): ``passthrough`` to OpenRouter's stateless native ``/responses``, or
``translate`` to canonical chat.

The policy is applied on the native body: the model pin, ``max_output_tokens``, ``reasoning`` (effort or
token budget; the harness's ``summary`` setting is kept), sampling, ``provider`` routing, ``session_id``,
``store: false`` and ``include: ["reasoning.encrypted_content"]`` (so reasoning replays statelessly).
``previous_response_id`` is refused: the gateway and OpenRouter's beta are stateless.

``translate`` (wave 3, local endpoints) maps the stateless Responses body to canonical chat:
``instructions`` and system/developer items -> system messages, ``function_call`` /
``function_call_output`` items -> tool calls and tool messages, custom (freeform) tools -> function tools
with one string parameter ``input`` (and back to ``custom_tool_call`` items), ``reasoning`` items ->
``reasoning_details`` (their ``encrypted_content`` is an ``llmlab1.`` token carrying the upstream's
details; a native blob is kept as a ``reasoning.encrypted`` detail of format ``openai-responses-v1``),
``text.format`` -> ``response_format``. Answers become a Responses object or the Responses SSE event
sequence (``response.created`` ... ``response.completed`` / ``incomplete`` / ``failed``).
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from protocols import budget_rules
from protocols.common import Policy
from protocols.common import PolicyDiff
from protocols.common import SSEParser
from protocols.common import apply_sampling
from protocols.common import content_text
from protocols.common import force
from protocols.common import json_or_none
from protocols.common import pin_model
from protocols.common import sse_event
from protocols.common import strip_cache_control
from protocols.translation import STREAM_ENDED_EARLY
from protocols.translation import ChatStreamReader
from protocols.translation import ChatTurn
from protocols.translation import Dropped
from protocols.translation import ToolCall
from protocols.translation import TranslationError
from protocols.translation import TurnBuilder
from protocols.translation import as_dropped
from protocols.translation import assistant_reasoning
from protocols.translation import chat_error_text
from protocols.translation import decode_token
from protocols.translation import detail_text
from protocols.translation import encode_token
from protocols.translation import finish_from_chat
from protocols.translation import iterate_stream
from protocols.translation import looks_like_context_overflow
from protocols.translation import merge_details
from protocols.translation import parse_arguments
from protocols.translation import synthetic_detail
from protocols.translation import text_of_parts
from protocols.translation import tool_call_id
from protocols.translation import turn_from_completion

NAME = "openai_responses"
UPSTREAM_PATH = "/responses"
PING = b": keepalive\n\n"
SAMPLING_KEYS = ("temperature", "top_p")
ENCRYPTED_REASONING = "reasoning.encrypted_content"
TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")


class TranslatorUnavailableError(NotImplementedError):
    """Kept for callers that guard on it; the translator is available since wave 3 (never raised)."""


class StatefulRequestError(ValueError):
    """The request depends on server-side state (``previous_response_id``)."""


def route(method: str, path: str) -> str | None:
    clean = path.rstrip("/")
    if method == "POST" and clean.endswith("/responses"):
        return "call"
    if method == "GET" and clean.endswith("/models"):
        return "models"
    return None


# =====================================================================================
# Policy on the native body
# =====================================================================================


def check_request(body: Mapping[str, Any]) -> None:
    if body.get("previous_response_id"):
        msg = "previous_response_id is not supported: this gateway is stateless (send the full input)"
        raise StatefulRequestError(msg)


def apply_policy_native(body: Mapping[str, Any], policy: Policy) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(dict(body))
    diff = PolicyDiff()
    pin_model(out, policy, diff)
    apply_sampling(out, policy, diff, allowed=SAMPLING_KEYS)
    if "max_tokens" in out:
        diff.stripped("max_tokens", out.pop("max_tokens"))
    force(out, "max_output_tokens", policy.upstream_max_tokens, diff)
    _apply_reasoning(out, policy, diff)
    if policy.seed is not None and policy.forward_seed:
        diff.note("seed_not_applicable", policy.seed)  # the Responses API has no seed
    force(out, "store", value=False, diff=diff)
    include = list(out.get("include") or [])
    if ENCRYPTED_REASONING not in include:
        include.append(ENCRYPTED_REASONING)
        diff.forced("include", out.get("include"))
    out["include"] = include
    if "provider" in out:
        diff.stripped("provider", out.pop("provider"))
    if policy.provider:
        out["provider"] = dict(policy.provider)
    if policy.sticky_session and policy.session_id:
        out["session_id"] = policy.session_id
    if policy.prompt_cache in ("off", "auto"):
        diff.count("cache_control_stripped", strip_cache_control(out))
    return out, diff.data


def _apply_reasoning(out: dict[str, Any], policy: Policy, diff: PolicyDiff) -> None:
    if policy.reasoning_exclude:
        diff.note("reasoning_exclude_ignored", value=True)  # encrypted reasoning must replay
    if policy.reasoning_mode == "provider_default":
        return
    current = out.get("reasoning") if isinstance(out.get("reasoning"), dict) else {}
    wanted = {k: v for k, v in current.items() if k == "summary"}
    if policy.reasoning_mode == "effort" and policy.reasoning_effort:
        wanted["effort"] = policy.reasoning_effort
    elif policy.reasoning_mode == "max_tokens" and policy.reasoning_max_tokens:
        wanted["max_tokens"] = policy.reasoning_max_tokens
    if current != wanted:
        diff.forced("reasoning", current or None)
    out["reasoning"] = wanted


def prompt_text(body: Mapping[str, Any]) -> str:
    return budget_rules.prompt_text(body)


def system_prompt(body: Mapping[str, Any]) -> str | None:
    parts: list[str] = []
    if body.get("instructions"):
        parts.append(content_text(body["instructions"]))
    for item in body.get("input") or [] if isinstance(body.get("input"), list) else []:
        if isinstance(item, Mapping) and item.get("role") in ("system", "developer"):
            parts.append(content_text(item.get("content")))
    return "\n\n".join(p for p in parts if p) or None


def requested_reasoning(body: Mapping[str, Any]) -> Any:
    return body.get("reasoning")


# =====================================================================================
# Errors
# =====================================================================================


def error(kind: str, message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    """The OpenAI error shape."""
    return status, {"error": {"message": message, "type": "invalid_request_error", "code": kind, "param": None}}


def context_error(est_tokens: int, cap: int) -> tuple[int, dict[str, Any]]:
    """The ``call.max_input_tokens`` refusal in OpenAI's context-window wording, so Codex compacts."""
    message = f"Your input exceeds the context window of this model ({est_tokens} tokens > {cap} maximum)."
    return 400, {"error": {"message": message, "type": "invalid_request_error", "code": "context_length_exceeded"}}


def stream_error(status: int, payload: Mapping[str, Any]) -> bytes:
    err = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    event = {"type": "error", "code": err.get("code"), "message": err.get("message"), "param": None}
    return sse_event(event, "error")


def models_payload(names: list[str]) -> dict[str, Any]:
    return {"object": "list", "data": [{"id": n, "object": "model", "owned_by": "llm-gateway"} for n in names]}


# =====================================================================================
# Responses
# =====================================================================================


def native_usage(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A Responses ``usage`` block, normalized (``input_tokens`` includes cached tokens)."""
    if not isinstance(usage, Mapping) or not usage:
        return None
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    return budget_rules.normalized_usage(
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
        reasoning_tokens=output_details.get("reasoning_tokens") if isinstance(output_details, Mapping) else None,
        cached_tokens=input_details.get("cached_tokens") if isinstance(input_details, Mapping) else None,
        cache_write_tokens=input_details.get("cache_write_tokens") if isinstance(input_details, Mapping) else None,
        cost=usage.get("cost"),
    )


def _finish(response: Mapping[str, Any], has_tools: bool) -> str | None:
    status = response.get("status")
    if status == "completed":
        return "tool_calls" if has_tools else "stop"
    if status == "incomplete":
        reason = (response.get("incomplete_details") or {}).get("reason")
        return "content_filter" if reason == "content_filter" else "length"
    if status == "failed":
        return "error"
    return None


def summarize(payload: Any) -> dict[str, Any]:
    """A Responses object -> the call record's ``response`` summary."""
    if not isinstance(payload, Mapping):
        return {"error": "non-object response"}
    if "error" in payload and payload.get("object") != "response":
        return {"error": payload.get("error"), "usage": None, "normalized_usage": None}
    text: list[str] = []
    reasoning: list[str] = []
    tools: list[dict[str, Any]] = []
    for item in payload.get("output") or []:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, Mapping) and part.get("type") == "output_text":
                    text.append(str(part.get("text") or ""))
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, Mapping):
                    reasoning.append(str(part.get("text") or ""))
        elif kind in ("function_call", "custom_tool_call"):
            tools.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "name": item.get("name"),
                    "arguments": item.get("arguments") if kind == "function_call" else item.get("input"),
                },
            )
    status = payload.get("status")
    return {
        "id": payload.get("id"),
        "model": payload.get("model"),
        "provider": payload.get("provider"),
        "system_fingerprint": None,
        "content": "".join(text),
        "reasoning": "".join(reasoning),
        "tool_calls": tools,
        "finish_reason": _finish(payload, bool(tools)),
        "native_finish_reason": status,
        "usage": payload.get("usage"),
        "normalized_usage": native_usage(payload.get("usage")),
        "error": payload.get("error"),
    }


class StreamAccumulator:
    """Keeps the terminal ``response.*`` event (it carries the whole response and its usage)."""

    def __init__(self) -> None:
        self._parser = SSEParser()
        self.response: dict[str, Any] | None = None
        self.created: dict[str, Any] = {}
        self.text: list[str] = []
        self.error: Any = None

    def feed(self, chunk: bytes) -> None:
        for name, data in self._parser.feed(chunk):
            event = json_or_none(data) if data else None
            if not isinstance(event, Mapping):
                continue
            kind = name or str(event.get("type"))
            if kind == "response.created":
                self.created = dict(event.get("response") or {})
            elif kind == "response.output_text.delta":
                self.text.append(str(event.get("delta") or ""))
            elif kind in TERMINAL_EVENTS:
                self.response = dict(event.get("response") or {})
            elif kind == "error":
                self.error = {k: event.get(k) for k in ("code", "message")}

    def summary(self) -> dict[str, Any]:
        if self.response is not None:
            out = summarize(self.response)
            if self.error is not None and not out.get("error"):
                out["error"] = self.error
            return out
        return {
            "id": self.created.get("id"),
            "model": self.created.get("model"),
            "provider": self.created.get("provider"),
            "system_fingerprint": None,
            "content": "".join(self.text),
            "reasoning": "",
            "tool_calls": [],
            "finish_reason": None,
            "native_finish_reason": None,
            "usage": None,
            "normalized_usage": None,
            "error": self.error or "stream ended without a terminal response event",
        }


# =====================================================================================
# Translator (translate mode)
# =====================================================================================

TRANSLATED_FORMAT = "openai-responses-v1"
# Request keys that never change what the model sees or generates (transport, storage, telemetry).
IGNORED_KEYS = frozenset(
    {
        "model",
        "stream",
        "stream_options",
        "store",
        "include",
        "metadata",
        "client_metadata",
        "user",
        "safety_identifier",
        "prompt_cache_key",
        "prompt_cache_retention",
        "service_tier",
        "background",
    },
)
TEXT_PART_TYPES = ("input_text", "output_text", "text", "summary_text", "reasoning_text")
CUSTOM_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"input": {"type": "string", "description": "The raw input of this freeform tool."}},
    "required": ["input"],
    "additionalProperties": False,
}


def _custom_tool_names(request_json: Mapping[str, Any] | None) -> frozenset[str]:
    tools = (request_json or {}).get("tools")
    return (
        frozenset(str(t.get("name")) for t in tools if isinstance(t, Mapping) and t.get("type") == "custom")
        if isinstance(tools, list)
        else frozenset()
    )


def _parts_text(content: Any) -> str:
    return text_of_parts(content, keys=("text", "refusal"))


def to_chat(request_json: Mapping[str, Any], **_options: Any) -> tuple[dict[str, Any], dict[str, int]]:
    """A Responses request -> ``(chat request, translation_dropped)``."""
    body = request_json
    if body.get("conversation"):
        msg = "conversation objects are not supported: this gateway is stateless (send the full input)"
        raise TranslationError(msg)
    dropped = Dropped()
    out: dict[str, Any] = {"model": body.get("model")}
    messages: list[dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": _parts_text(body["instructions"])})
    items = body.get("input")
    if isinstance(items, str):
        messages.append({"role": "user", "content": items})
    elif isinstance(items, list):
        messages.extend(_items_to_chat(items, dropped))
    out["messages"] = messages
    if body.get("max_output_tokens") is not None:
        out["max_tokens"] = body["max_output_tokens"]
    for key in SAMPLING_KEYS:
        if key in body:
            out[key] = body[key]
    tools = _tools_to_chat(body.get("tools"), dropped)
    if tools:
        out["tools"] = tools
    _tool_choice_to_chat(body.get("tool_choice"), out, dropped)
    if "parallel_tool_calls" in body:
        out["parallel_tool_calls"] = bool(body["parallel_tool_calls"])
    reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), Mapping) else {}
    if reasoning.get("effort"):
        out["reasoning"] = {"effort": reasoning["effort"]}
    elif reasoning.get("max_tokens"):
        out["reasoning"] = {"max_tokens": reasoning["max_tokens"]}
    _text_to_chat(body.get("text"), out, dropped)
    if body.get("truncation") not in (None, "disabled"):
        dropped.add("request.truncation")
    if body.get("stream"):
        out["stream"] = True
    handled = {"instructions", "input", "max_output_tokens", *SAMPLING_KEYS, "tools", "tool_choice"}
    handled |= {"parallel_tool_calls", "reasoning", "text", "truncation", "conversation", "previous_response_id"}
    for key in body:
        if key not in handled and key not in IGNORED_KEYS:
            dropped.add(f"request.{key}")
    return out, dropped.data


class _Turn:
    """The assistant turn being rebuilt from consecutive output items."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.details: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def __bool__(self) -> bool:
        return bool(self.texts or self.details or self.calls)

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.texts) if self.texts else None}
        assistant_reasoning(message, merge_details(self.details))
        if self.calls:
            message["tool_calls"] = self.calls
        return message


def _items_to_chat(items: list[Any], dropped: Dropped) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    turn = _Turn()

    def flush() -> None:
        nonlocal turn
        if turn:
            messages.append(turn.message())
        turn = _Turn()

    for item in items:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            role = item.get("role")
            if role == "assistant":
                turn.texts.append(_parts_text(item.get("content")))
                continue
            flush()
            content = item.get("content")
            text = content if isinstance(content, str) else _parts_text(content)
            messages.append({"role": "system" if role in ("system", "developer") else "user", "content": text})
        elif kind == "reasoning":
            turn.details.extend(_reasoning_item_details(item))
        elif kind in ("function_call", "custom_tool_call"):
            arguments = item.get("arguments") if kind == "function_call" else json.dumps({"input": item.get("input")})
            turn.calls.append(
                {
                    "id": str(item.get("call_id") or item.get("id") or f"call_{len(turn.calls)}"),
                    "type": "function",
                    "function": {"name": item.get("name"), "arguments": arguments or "{}"},
                },
            )
        elif kind in ("function_call_output", "custom_tool_call_output"):
            flush()
            output = item.get("output")
            text = output if isinstance(output, str) else _parts_text(output)
            messages.append({"role": "tool", "tool_call_id": str(item.get("call_id") or ""), "content": text})
        else:
            dropped.add(f"input.{kind}")
    flush()
    return messages


def _reasoning_item_details(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    carried = decode_token(item.get("encrypted_content"))
    if isinstance(carried, list):
        return [dict(d) for d in carried if isinstance(d, Mapping)]
    summary = _parts_text(item.get("summary"))
    content = _parts_text(item.get("content"))
    out: list[dict[str, Any]] = []
    if item.get("encrypted_content"):
        if summary:
            out.append({"type": "reasoning.summary", "summary": summary, "format": TRANSLATED_FORMAT})
        detail = {"type": "reasoning.encrypted", "data": item["encrypted_content"], "format": TRANSLATED_FORMAT}
        if item.get("id"):
            detail["id"] = item["id"]
        out.append(detail)
        return out
    text = content or summary
    return [synthetic_detail(text)] if text else []


def _tools_to_chat(tools: Any, dropped: Dropped) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools if isinstance(tools, list) else []:
        if not isinstance(tool, Mapping):
            continue
        kind = tool.get("type")
        if kind == "function":
            function: dict[str, Any] = {"name": tool.get("name")}
            if tool.get("description"):
                function["description"] = tool["description"]
            function["parameters"] = dict(tool.get("parameters") or {"type": "object", "properties": {}})
            if tool.get("strict") is not None:
                function["strict"] = bool(tool["strict"])
            out.append({"type": "function", "function": function})
        elif kind == "custom":
            fmt = tool.get("format") if isinstance(tool.get("format"), Mapping) else {}
            if fmt and fmt.get("type") not in (None, "text"):
                dropped.add("tools.custom.format")  # a grammar constraint chat cannot express
            function = {"name": tool.get("name"), "parameters": dict(CUSTOM_INPUT_SCHEMA)}
            if tool.get("description"):
                function["description"] = tool["description"]
            out.append({"type": "function", "function": function})
        else:
            dropped.add(f"tools.{kind}")  # hosted tools (web search, file search, local shell, MCP, ...)
    return out


def _tool_choice_to_chat(choice: Any, out: dict[str, Any], dropped: Dropped) -> None:
    if choice is None:
        return
    if choice in ("auto", "none", "required"):
        out["tool_choice"] = choice
    elif isinstance(choice, Mapping) and choice.get("type") in ("function", "custom") and choice.get("name"):
        out["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    else:
        dropped.add("tool_choice")


def _text_to_chat(text: Any, out: dict[str, Any], dropped: Dropped) -> None:
    if not isinstance(text, Mapping):
        return
    fmt = text.get("format") if isinstance(text.get("format"), Mapping) else {}
    if fmt.get("type") == "json_schema":
        schema = {k: fmt[k] for k in ("name", "schema", "strict", "description") if k in fmt}
        out["response_format"] = {"type": "json_schema", "json_schema": schema}
    elif fmt.get("type") == "json_object":
        out["response_format"] = {"type": "json_object"}
    elif fmt.get("type") not in (None, "text"):
        dropped.add("text.format")
    if text.get("verbosity"):
        out["verbosity"] = text["verbosity"]


# -- responses ---------------------------------------------------------------------------------------


def _usage_from_chat(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    norm = budget_rules.chat_usage(usage) or {}
    prompt = norm.get("prompt_tokens") or 0
    completion = norm.get("completion_tokens") or 0
    out: dict[str, Any] = {
        "input_tokens": prompt,
        "input_tokens_details": {"cached_tokens": norm.get("cached_tokens") or 0},
        "output_tokens": completion,
        "output_tokens_details": {"reasoning_tokens": norm.get("reasoning_tokens") or 0},
        "total_tokens": prompt + completion,
    }
    if norm.get("cost") is not None:
        out["cost"] = norm["cost"]
    return out


def _response_id(turn: ChatTurn) -> str:
    raw = turn.id or "llmlab"
    return raw if raw.startswith("resp_") else f"resp_{raw}"


def _reasoning_item(details: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    summary = [{"type": "summary_text", "text": t} for t in (detail_text(d) for d in details) if t]
    return {"type": "reasoning", "id": item_id, "summary": summary, "encrypted_content": encode_token(details)}


def _message_item(text: str, item_id: str, *, refusal: bool = False) -> dict[str, Any]:
    part = (
        {"type": "refusal", "refusal": text} if refusal else {"type": "output_text", "text": text, "annotations": []}
    )
    return {"type": "message", "id": item_id, "status": "completed", "role": "assistant", "content": [part]}


def _call_item(call: ToolCall, n: int, custom: frozenset[str], dropped: Dropped) -> dict[str, Any]:
    call_id = tool_call_id(call.id, "call_", n)
    if call.name in custom:
        value, ok = parse_arguments(call.arguments)
        if ok and isinstance(value, Mapping) and isinstance(value.get("input"), str):
            raw = value["input"]
        else:
            dropped.add("tool_call.invalid_arguments")
            raw = call.arguments
        return {
            "type": "custom_tool_call",
            "id": f"ctc_{n}_{call_id}",
            "call_id": call_id,
            "name": call.name,
            "input": raw,
            "status": "completed",
        }
    _, ok = parse_arguments(call.arguments)
    if not ok:
        dropped.add("tool_call.invalid_arguments")
    return {
        "type": "function_call",
        "id": f"fc_{n}_{call_id}",
        "call_id": call_id,
        "name": call.name,
        "arguments": call.arguments or "{}",
        "status": "completed",
    }


def _status(turn: ChatTurn) -> tuple[str, dict[str, Any] | None]:
    finish = finish_from_chat(turn)
    if finish == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    if finish == "content_filter":
        return "incomplete", {"reason": "content_filter"}
    if finish == "error":
        return "failed", None
    return "completed", None


def _response_object(
    turn: ChatTurn,
    output: list[dict[str, Any]],
    request_json: Mapping[str, Any] | None,
    *,
    status: str,
    incomplete: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    error_obj: Any = None,
) -> dict[str, Any]:
    request = request_json or {}
    return {
        "id": _response_id(turn),
        "object": "response",
        "created_at": turn.created or int(time.time()),
        "status": status,
        "model": turn.model,
        "output": output,
        "parallel_tool_calls": bool(request.get("parallel_tool_calls", True)),
        "tool_choice": request.get("tool_choice", "auto"),
        "tools": list(request.get("tools") or []),
        "incomplete_details": incomplete,
        "error": error_obj,
        "usage": usage,
    }


def _output_items(turn: ChatTurn, custom: frozenset[str], dropped: Dropped) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if turn.details:
        output.append(_reasoning_item(turn.details, "rs_0"))
    if turn.content:
        output.append(_message_item(turn.content, "msg_0"))
    if turn.refusal:
        output.append(_message_item(turn.refusal, "msg_1", refusal=True))
    output.extend(_call_item(call, n, custom, dropped) for n, call in enumerate(turn.tool_calls))
    return output


def from_chat(
    chat_response: Mapping[str, Any],
    request_json: Mapping[str, Any] | None = None,
    *,
    dropped: Dropped | dict[str, int] | None = None,
    **_options: Any,
) -> dict[str, Any]:
    """A chat completion -> a Responses object (an error payload -> the OpenAI error shape)."""
    drops = as_dropped(dropped)
    if chat_response.get("error") and not chat_response.get("choices"):
        message, code = chat_error_text(chat_response)
        return upstream_error(502, message, code)[1]
    turn = turn_from_completion(chat_response)
    output = _output_items(turn, _custom_tool_names(request_json), drops)
    status, incomplete = _status(turn)
    if isinstance(dropped, dict):
        dropped.update(drops.data)
    return _response_object(
        turn,
        output,
        request_json,
        status=status,
        incomplete=incomplete,
        usage=_usage_from_chat(turn.usage),
    )


def upstream_error(status: int, message: str, code: str | None = None) -> tuple[int, dict[str, Any]]:
    """An upstream chat error in the OpenAI shape; a context overflow keeps ``context_length_exceeded``."""
    if looks_like_context_overflow(message, code):
        return status, {
            "error": {"message": message, "type": "invalid_request_error", "code": "context_length_exceeded"},
        }
    return error(code or "upstream_error", message, status)


class StreamTranslator:
    """Chat SSE -> the Responses event stream.

    Reasoning summaries and output text stream live as items; tool calls are emitted whole when the turn
    ends. Reasoning details that arrive after their reasoning item was closed become one more reasoning
    item (the request side merges every reasoning item of a turn).
    """

    content_type = "text/event-stream"

    def __init__(
        self,
        request_json: Mapping[str, Any] | None = None,
        *,
        dropped: Dropped | dict[str, int] | None = None,
        **_options: Any,
    ) -> None:
        self._request = request_json or {}
        self._custom = _custom_tool_names(request_json)
        self._reader = ChatStreamReader()
        self._drops = dropped if isinstance(dropped, Dropped) else Dropped()
        self._sink = dropped if isinstance(dropped, dict) else None
        self._turn = TurnBuilder()
        self._seq = 0
        self._started = False
        self._finished = False
        self._items: list[dict[str, Any]] = []  # completed output items, in output order
        self._open: dict[str, Any] | None = None  # the item being streamed
        self._fragments: list[dict[str, Any]] = []
        self._late: list[dict[str, Any]] = []
        self._plain: list[str] = []

    def _emit(self, kind: str, **payload: Any) -> bytes:
        self._seq += 1
        return sse_event({"type": kind, "sequence_number": self._seq, **payload}, kind)

    def _start(self) -> bytes:
        if self._started:
            return b""
        self._started = True
        shell = _response_object(self._turn.turn, [], self._request, status="in_progress")
        return self._emit("response.created", response=shell) + self._emit("response.in_progress", response=shell)

    def _open_item(self, kind: str) -> bytes:
        out = self._close()
        index = len(self._items)
        if kind == "reasoning":
            item = {"type": "reasoning", "id": f"rs_{index}", "summary": []}
            self._open = {"kind": kind, "index": index, "item": item, "text": ""}
            out += self._emit("response.output_item.added", output_index=index, item=item)
            part = {"type": "summary_text", "text": ""}
            return out + self._emit(
                "response.reasoning_summary_part.added",
                item_id=item["id"],
                output_index=index,
                summary_index=0,
                part=part,
            )
        item = {"type": "message", "id": f"msg_{index}", "status": "in_progress", "role": "assistant", "content": []}
        self._open = {"kind": kind, "index": index, "item": item, "text": ""}
        out += self._emit("response.output_item.added", output_index=index, item=item)
        part = {"type": "output_text", "text": "", "annotations": []}
        return out + self._emit(
            "response.content_part.added",
            item_id=item["id"],
            output_index=index,
            content_index=0,
            part=part,
        )

    def _close(self) -> bytes:
        current, self._open = self._open, None
        if current is None:
            return b""
        index, item, text = current["index"], current["item"], current["text"]
        if current["kind"] == "reasoning":
            details = merge_details(self._fragments) or [synthetic_detail("".join(self._plain))]
            self._fragments, self._plain = [], []
            done = _reasoning_item(details, item["id"])
            out = self._emit(
                "response.reasoning_summary_text.done",
                item_id=item["id"],
                output_index=index,
                summary_index=0,
                text=text,
            )
            out += self._emit(
                "response.reasoning_summary_part.done",
                item_id=item["id"],
                output_index=index,
                summary_index=0,
                part={"type": "summary_text", "text": text},
            )
        else:
            done = _message_item(text, item["id"])
            part = done["content"][0]
            out = self._emit(
                "response.output_text.done",
                item_id=item["id"],
                output_index=index,
                content_index=0,
                text=text,
            )
            out += self._emit(
                "response.content_part.done",
                item_id=item["id"],
                output_index=index,
                content_index=0,
                part=part,
            )
        self._items.append(done)
        return out + self._emit("response.output_item.done", output_index=index, item=done)

    def feed(self, chunk: bytes) -> bytes:
        out = b""
        for event in self._reader.feed(chunk):
            self._turn.feed(event)
            out += self._event(event)
        return out

    def _reasoning_delta(self, text: str) -> bytes:
        out = b""
        if self._open is None or self._open["kind"] != "reasoning":
            out += self._open_item("reasoning")
        assert self._open is not None
        if text:
            self._open["text"] += text
            out += self._emit(
                "response.reasoning_summary_text.delta",
                item_id=self._open["item"]["id"],
                output_index=self._open["index"],
                summary_index=0,
                delta=text,
            )
        return out

    def _event(self, event: tuple[Any, ...]) -> bytes:
        kind = event[0]
        if kind in ("meta", "finish", "usage", "error", "tool"):
            return b""
        out = self._start()
        if kind == "reasoning":
            self._plain.append(event[1])
            return out + self._reasoning_delta(event[1])
        if kind == "details":
            for fragment in event[1]:
                text = detail_text(fragment)
                if not text and self._open is not None and self._open["kind"] != "reasoning":
                    self._late.append(fragment)  # a signature after the text started: emitted at the end
                    continue
                if self._open is None or self._open["kind"] != "reasoning":
                    out += self._open_item("reasoning")
                self._fragments.append(fragment)
                out += self._reasoning_delta(text)
            return out
        if kind in ("content", "refusal"):
            if self._open is None or self._open["kind"] != "message":
                out += self._open_item("message")
            assert self._open is not None
            self._open["text"] += event[1]
            return out + self._emit(
                "response.output_text.delta",
                item_id=self._open["item"]["id"],
                output_index=self._open["index"],
                content_index=0,
                delta=event[1],
            )
        return out

    def _call_events(self, item: dict[str, Any]) -> bytes:
        index = len(self._items)
        custom = item["type"] == "custom_tool_call"
        added = {**item, "status": "in_progress", ("input" if custom else "arguments"): ""}
        out = self._emit("response.output_item.added", output_index=index, item=added)
        if custom:
            out += self._emit(
                "response.custom_tool_call_input.delta",
                item_id=item["id"],
                output_index=index,
                delta=item["input"],
            )
            out += self._emit(
                "response.custom_tool_call_input.done",
                item_id=item["id"],
                output_index=index,
                input=item["input"],
            )
        else:
            out += self._emit(
                "response.function_call_arguments.delta",
                item_id=item["id"],
                output_index=index,
                delta=item["arguments"],
            )
            out += self._emit(
                "response.function_call_arguments.done",
                item_id=item["id"],
                output_index=index,
                arguments=item["arguments"],
            )
        self._items.append(item)
        return out + self._emit("response.output_item.done", output_index=index, item=item)

    def finish(self, failure: Any = None) -> bytes:
        """The closing events; ``failure`` is a transport error seen by the relay (stream broke)."""
        if self._finished:
            return b""
        self._finished = True
        turn = self._turn.finish()
        out = self._start() + self._close()
        if self._late:  # details that arrived after their reasoning item closed
            self._fragments = self._late
            out += self._open_item("reasoning") + self._close()
        problem = failure or turn.error
        if problem or turn.finish_reason in (None, "error"):
            message, code = chat_error_text({"error": problem}) if problem else (STREAM_ENDED_EARLY, None)
            failed = _response_object(
                turn,
                self._items,
                self._request,
                status="failed",
                usage=_usage_from_chat(turn.usage) if turn.usage else None,
                error_obj={"code": code or "upstream_error", "message": message},
            )
            return out + self._emit("response.failed", response=failed)
        for n, call in enumerate(turn.tool_calls):
            out += self._call_events(_call_item(call, n, self._custom, self._drops))
        if self._sink is not None:
            self._sink.update(self._drops.data)
        status, incomplete = _status(turn)
        final = _response_object(
            turn,
            self._items,
            self._request,
            status=status,
            incomplete=incomplete,
            usage=_usage_from_chat(turn.usage),
        )
        kind = {"completed": "response.completed", "incomplete": "response.incomplete"}.get(status, "response.failed")
        return out + self._emit(kind, response=final)

    def fail(self, status: int, payload: Mapping[str, Any]) -> bytes:
        """An upstream error after the stream started: the native in-stream error event."""
        self._finished = True
        return self._start() + self._close() + stream_error(status, payload)


def stream_from_chat(
    chat_sse_iter: Iterable[bytes | None],
    request_json: Mapping[str, Any] | None = None,
    **options: Any,
) -> Iterator[bytes]:
    """Chat SSE chunks -> Responses SSE bytes; a ``None`` item (upstream silence) yields a keepalive."""
    return iterate_stream(StreamTranslator(request_json, **options), chat_sse_iter, PING)
