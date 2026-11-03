"""Anthropic Messages front (Claude Code): ``passthrough`` to OpenRouter's native ``/messages``, or
``translate`` to canonical chat (local endpoints).

Passthrough forwards the harness's native body after applying the run policy **on the native body**:
the model pin, ``max_tokens``, ``thinking`` / ``output_config.effort`` for the reasoning knob, sampling,
``provider`` routing, ``cache_control`` handling (kept, TTL set, injected for listed providers; stripped
under ``off``/``auto``) and ``session_id``. Everything else, thinking blocks and their signatures
included, is forwarded byte-for-byte in content. Usage comes from the native response.

``translate`` (wave 3) maps the native body to canonical chat (``to_chat``), the chat answer back
(``from_chat``, ``StreamTranslator`` / ``stream_from_chat``): system blocks, text, ``tool_use`` /
``tool_result``, tool definitions and ``tool_choice``, ``thinking`` / ``output_config.effort`` ->
``reasoning``, stop reasons, usage (cache reads/writes split out of ``prompt_tokens``) and native error
shapes. Thinking blocks round-trip: each block's ``signature`` (or ``redacted_thinking.data``) is an
``llmlab1.`` token carrying the upstream's ``reasoning_details`` entry (see ``translation.py``); a
native signature is kept as a ``reasoning.text`` detail of format ``anthropic-claude-v1``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any

from protocols import budget_rules
from protocols.common import Policy
from protocols.common import PolicyDiff
from protocols.common import SSEParser
from protocols.common import apply_sampling
from protocols.common import cache_marker
from protocols.common import content_text
from protocols.common import count_cache_control
from protocols.common import force
from protocols.common import json_or_none
from protocols.common import pin_model
from protocols.common import set_cache_ttl
from protocols.common import sse_event
from protocols.common import strip_cache_control
from protocols.translation import ChatStreamReader
from protocols.translation import ChatTurn
from protocols.translation import Dropped
from protocols.translation import ToolCall
from protocols.translation import TurnBuilder
from protocols.translation import as_dropped
from protocols.translation import assistant_reasoning
from protocols.translation import completion_failure
from protocols.translation import decode_token
from protocols.translation import detail_text
from protocols.translation import dump_arguments
from protocols.translation import encode_token
from protocols.translation import finish_from_chat
from protocols.translation import iterate_stream
from protocols.translation import looks_like_context_overflow
from protocols.translation import merge_details
from protocols.translation import parse_arguments
from protocols.translation import synthetic_detail
from protocols.translation import tool_call_id
from protocols.translation import turn_failure
from protocols.translation import turn_from_completion

NAME = "anthropic_messages"
UPSTREAM_PATH = "/messages"
PING = b'event: ping\ndata: {"type": "ping"}\n\n'
SAMPLING_KEYS = ("temperature", "top_p", "top_k")
# Request headers that shape the native request's semantics; forwarded in passthrough.
FORWARD_HEADERS = ("anthropic-version", "anthropic-beta")
# Claude Code attribution headers, logged per call.
ATTRIBUTION_HEADERS = (
    "x-claude-code-request-class",
    "x-claude-code-session-id",
    "x-claude-code-agent-id",
    "x-claude-code-prompt-id",
    "x-claude-code-compaction",
)
# Canonical effort -> Anthropic ``output_config.effort`` (Anthropic has low / medium / high).
EFFORT_MAP = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high"}
STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


class TranslatorUnavailableError(NotImplementedError):
    """Kept for callers that guard on it; the translator is available since wave 3 (never raised)."""


def route(method: str, path: str) -> str | None:
    clean = path.rstrip("/")
    if method == "POST" and clean.endswith("/messages/count_tokens"):
        return "count_tokens"
    if method == "POST" and clean.endswith("/messages"):
        return "call"
    if method == "GET" and clean.endswith("/models"):
        return "models"
    return None


# =====================================================================================
# Policy on the native body
# =====================================================================================


def apply_policy_native(body: Mapping[str, Any], policy: Policy) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(dict(body))
    diff = PolicyDiff()
    pin_model(out, policy, diff)
    apply_sampling(out, policy, diff, allowed=SAMPLING_KEYS)
    force(out, "max_tokens", policy.upstream_max_tokens, diff)
    _apply_reasoning(out, policy, diff)
    if policy.seed is not None and policy.forward_seed:
        diff.note("seed_not_applicable", policy.seed)  # the Messages API has no seed
    if "provider" in out:
        diff.stripped("provider", out.pop("provider"))
    if policy.provider:
        out["provider"] = dict(policy.provider)
    if policy.sticky_session and policy.session_id:
        out["session_id"] = policy.session_id
    _apply_cache_policy(out, policy, diff)
    return out, diff.data


def _apply_reasoning(out: dict[str, Any], policy: Policy, diff: PolicyDiff) -> None:
    if policy.reasoning_exclude:
        diff.note("reasoning_exclude_ignored", value=True)  # thinking signatures must replay
    if policy.reasoning_mode == "max_tokens" and policy.reasoning_max_tokens:
        force(out, "thinking", {"type": "enabled", "budget_tokens": policy.reasoning_max_tokens}, diff)
        _drop_effort(out, diff)
    elif policy.reasoning_mode == "effort" and policy.reasoning_effort:
        if policy.reasoning_effort == "none":
            force(out, "thinking", {"type": "disabled"}, diff)
            _drop_effort(out, diff)
            return
        effort = EFFORT_MAP.get(policy.reasoning_effort, "medium")
        if effort != policy.reasoning_effort:
            diff.note("effort_mapped", {policy.reasoning_effort: effort})
        config = dict(out.get("output_config") or {})
        if config.get("effort") != effort:
            diff.forced("output_config.effort", config.get("effort"))
        config["effort"] = effort
        out["output_config"] = config


def _drop_effort(out: dict[str, Any], diff: PolicyDiff) -> None:
    config = out.get("output_config")
    if isinstance(config, dict) and "effort" in config:
        diff.stripped("output_config.effort", config.pop("effort"))
        if not config:
            out.pop("output_config")


def _apply_cache_policy(out: dict[str, Any], policy: Policy, diff: PolicyDiff) -> None:
    if policy.prompt_cache in ("off", "auto"):
        diff.count("cache_control_stripped", strip_cache_control(out))
        return
    diff.count("cache_control_ttl_set", set_cache_ttl(out, policy.cache_ttl))
    if not policy.cache_control_applies():
        return
    injected = 0
    system = out.get("system")
    if count_cache_control(out) < 4 and system:
        if isinstance(system, str):
            out["system"] = [{"type": "text", "text": system, "cache_control": cache_marker(policy.cache_ttl)}]
            injected += 1
        elif isinstance(system, list) and system and isinstance(system[-1], dict):
            if "cache_control" not in system[-1]:
                system[-1]["cache_control"] = cache_marker(policy.cache_ttl)
                injected += 1
    last_result = _last_tool_result(out.get("messages"))
    if last_result is not None and count_cache_control(out) < 4 and "cache_control" not in last_result:
        last_result["cache_control"] = cache_marker(policy.cache_ttl)
        injected += 1
    diff.count("cache_control_injected", injected)


def _last_tool_result(messages: Any) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            for part in reversed(content):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    return part
    return None


def prompt_text(body: Mapping[str, Any]) -> str:
    return budget_rules.prompt_text(body)


def system_prompt(body: Mapping[str, Any]) -> str | None:
    system = body.get("system")
    if not system:
        return None
    return content_text(system)


def requested_reasoning(body: Mapping[str, Any]) -> Any:
    found: dict[str, Any] = {}
    if "thinking" in body:
        found["thinking"] = body["thinking"]
    effort = (
        (body.get("output_config") or {}).get("effort") if isinstance(body.get("output_config"), Mapping) else None
    )
    if effort is not None:
        found["effort"] = effort
    return found or None


def count_tokens(body: Mapping[str, Any]) -> dict[str, Any]:
    """``/v1/messages/count_tokens``, answered locally: an estimate, never a billable call."""
    return {"input_tokens": budget_rules.estimate_tokens(prompt_text(body)), "estimate": True}


# =====================================================================================
# Errors and probes
# =====================================================================================

_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
}


def error(kind: str, message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    """The Anthropic error shape (the type follows the status; ``kind`` is logged, not sent)."""
    return status, {"type": "error", "error": {"type": _ERROR_TYPES.get(status, "api_error"), "message": message}}


def context_error(est_tokens: int, cap: int) -> tuple[int, dict[str, Any]]:
    """The ``call.max_input_tokens`` refusal in Anthropic's own wording, so Claude Code compacts."""
    return error("context_budget", f"prompt is too long: {est_tokens} tokens > {cap} maximum", 400)


def stream_error(status: int, payload: Mapping[str, Any]) -> bytes:
    return sse_event(dict(payload), "error")


def models_payload(names: list[str]) -> dict[str, Any]:
    data = [
        {"type": "model", "id": n, "display_name": n, "created_at": "2026-01-01T00:00:00Z"}
        for n in names  # one entry per presented name; every one maps to the pin
    ]
    return {
        "data": data,
        "has_more": False,
        "first_id": names[0] if names else None,
        "last_id": names[-1] if names else None,
    }


# =====================================================================================
# Responses
# =====================================================================================


def native_usage(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """An Anthropic ``usage`` block, normalized. ``input_tokens`` excludes cache reads and writes."""
    if not isinstance(usage, Mapping) or not usage:
        return None
    uncached = usage.get("input_tokens")
    read = usage.get("cache_read_input_tokens")
    write = usage.get("cache_creation_input_tokens")
    prompt = None
    if uncached is not None or read is not None or write is not None:
        prompt = int(uncached or 0) + int(read or 0) + int(write or 0)
    return budget_rules.normalized_usage(
        prompt_tokens=prompt,
        completion_tokens=usage.get("output_tokens"),
        reasoning_tokens=usage.get("reasoning_tokens"),  # Anthropic folds thinking into output_tokens
        cached_tokens=read,
        cache_write_tokens=write,
        cost=usage.get("cost"),
    )


def _blocks_summary(blocks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    text: list[str] = []
    thinking: list[str] = []
    tools: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            text.append(str(block.get("text") or ""))
        elif kind == "thinking":
            thinking.append(str(block.get("thinking") or ""))
        elif kind == "tool_use":
            arguments = block.get("input")
            tools.append(
                {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                },
            )
    return {"content": "".join(text), "reasoning": "".join(thinking), "tool_calls": tools}


def summarize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"error": "non-object response"}
    if payload.get("type") == "error":
        return {"error": payload.get("error"), "usage": None, "normalized_usage": None}
    blocks = [b for b in payload.get("content") or [] if isinstance(b, Mapping)]
    stop = payload.get("stop_reason")
    return {
        "id": payload.get("id"),
        "model": payload.get("model"),
        "provider": payload.get("provider"),
        "system_fingerprint": None,
        **_blocks_summary(blocks),
        "finish_reason": STOP_REASONS.get(stop, stop),
        "native_finish_reason": stop,
        "usage": payload.get("usage"),
        "normalized_usage": native_usage(payload.get("usage")),
        "error": None,
    }


class StreamAccumulator:
    """Rebuilds a streamed Messages response (``message_start`` ... ``message_stop``)."""

    def __init__(self) -> None:
        self._parser = SSEParser()
        self.message: dict[str, Any] = {}
        self.usage: dict[str, Any] = {}
        self.blocks: dict[int, dict[str, Any]] = {}
        self._json_parts: dict[int, list[str]] = {}
        self.stop_reason: str | None = None
        self.error: Any = None

    def feed(self, chunk: bytes) -> None:
        for name, data in self._parser.feed(chunk):
            event = json_or_none(data) if data else None
            if isinstance(event, Mapping):
                self._event(name or str(event.get("type")), event)

    def _event(self, name: str, event: Mapping[str, Any]) -> None:
        if name == "message_start":
            self.message = dict(event.get("message") or {})
            self.usage.update(self.message.get("usage") or {})
        elif name == "content_block_start":
            self.blocks[int(event.get("index", 0))] = dict(event.get("content_block") or {})
        elif name == "content_block_delta":
            index = int(event.get("index", 0))
            block = self.blocks.setdefault(index, {"type": "text", "text": ""})
            delta = event.get("delta") or {}
            kind = delta.get("type")
            if kind == "text_delta":
                block["text"] = str(block.get("text") or "") + str(delta.get("text") or "")
            elif kind == "thinking_delta":
                block["thinking"] = str(block.get("thinking") or "") + str(delta.get("thinking") or "")
            elif kind == "input_json_delta":
                self._json_parts.setdefault(index, []).append(str(delta.get("partial_json") or ""))
        elif name == "message_delta":
            delta = event.get("delta") or {}
            if delta.get("stop_reason"):
                self.stop_reason = delta["stop_reason"]
            self.usage.update({k: v for k, v in (event.get("usage") or {}).items() if v is not None})
        elif name == "error":
            self.error = event.get("error") or dict(event)

    def summary(self) -> dict[str, Any]:
        blocks = []
        for index in sorted(self.blocks):
            block = dict(self.blocks[index])
            if index in self._json_parts:
                block["input"] = "".join(self._json_parts[index])
            blocks.append(block)
        usage = dict(self.usage) or None
        return {
            "id": self.message.get("id"),
            "model": self.message.get("model"),
            "provider": self.message.get("provider"),
            "system_fingerprint": None,
            **_blocks_summary(blocks),
            "finish_reason": STOP_REASONS.get(self.stop_reason, self.stop_reason) if self.stop_reason else None,
            "native_finish_reason": self.stop_reason,
            "usage": usage,
            "normalized_usage": native_usage(usage),
            "error": self.error,
        }


# =====================================================================================
# Translator (translate mode: local endpoints, or any chat upstream)
# =====================================================================================

TRANSLATED_FORMAT = "anthropic-claude-v1"
# Native request keys that never change what the model sees or generates (transport, telemetry).
IGNORED_KEYS = frozenset({"model", "stream", "metadata", "service_tier", "anthropic_version", "container"})
# Chat finish -> Anthropic stop_reason.
CHAT_TO_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
    "error": "end_turn",
}


def to_chat(request_json: Mapping[str, Any], **_options: Any) -> tuple[dict[str, Any], dict[str, int]]:
    """An Anthropic Messages request -> ``(chat request, translation_dropped)``."""
    body = request_json
    dropped = Dropped()
    out: dict[str, Any] = {"model": body.get("model")}
    messages: list[dict[str, Any]] = []
    if body.get("system"):
        messages.append({"role": "system", "content": _text_content(body["system"], dropped, "system")})
    for message in body.get("messages") or []:
        if isinstance(message, Mapping):
            messages.extend(_message_to_chat(message, dropped))
    out["messages"] = messages
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    for key in SAMPLING_KEYS:
        if key in body:
            out[key] = body[key]
    if body.get("stop_sequences"):
        out["stop"] = list(body["stop_sequences"])
    tools = _tools_to_chat(body.get("tools"), dropped)
    if tools:
        out["tools"] = tools
    _tool_choice_to_chat(body.get("tool_choice"), out, dropped)
    reasoning = _reasoning_to_chat(body, dropped)
    if reasoning is not None:
        out["reasoning"] = reasoning
    if body.get("stream"):
        out["stream"] = True
    handled = {"system", "messages", "max_tokens", *SAMPLING_KEYS, "stop_sequences", "tools", "tool_choice"}
    handled |= {"thinking", "output_config"}
    for key in body:
        if key not in handled and key not in IGNORED_KEYS:
            dropped.add(f"request.{key}")
    return out, dropped.data


def _text_content(value: Any, dropped: Dropped, where: str) -> Any:
    """System / user / tool-result content as chat content: a string, or text parts when there are
    several blocks or ``cache_control`` markers to keep."""
    if isinstance(value, str):
        return value
    parts: list[dict[str, Any]] = []
    for block in value if isinstance(value, list) else []:
        if isinstance(block, str):
            parts.append({"type": "text", "text": block})
            continue
        if not isinstance(block, Mapping):
            continue
        if block.get("type") not in (None, "text"):
            dropped.add(f"{where}.{block.get('type')}")
            continue
        part: dict[str, Any] = {"type": "text", "text": str(block.get("text") or "")}
        if block.get("cache_control"):
            part["cache_control"] = dict(block["cache_control"])
        parts.append(part)
    if len(parts) == 1 and "cache_control" not in parts[0]:
        return parts[0]["text"]
    return parts


def _message_to_chat(message: Mapping[str, Any], dropped: Dropped) -> list[dict[str, Any]]:
    role = message.get("role")
    content = message.get("content")
    if role == "assistant":
        return [_assistant_to_chat(content, dropped)]
    if isinstance(content, str):
        return [{"role": "user", "content": content}]
    tool_messages: list[dict[str, Any]] = []
    text_blocks: list[Any] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            if block.get("is_error"):
                dropped.add("tool_result.is_error")
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": _tool_result_content(block, dropped),
                },
            )
        elif kind == "text":
            text_blocks.append(block)
        else:
            dropped.add(f"user.{kind}")
    out = list(tool_messages)
    if text_blocks:
        out.append({"role": "user", "content": _text_content(text_blocks, dropped, "user")})
    return out


def _tool_result_content(block: Mapping[str, Any], dropped: Dropped) -> Any:
    content = block.get("content")
    if content is None:
        return ""
    value = _text_content(content, dropped, "tool_result")
    if block.get("cache_control"):
        parts = [{"type": "text", "text": value}] if isinstance(value, str) else list(value)
        if parts:
            parts[-1]["cache_control"] = dict(block["cache_control"])
        return parts
    return value


def _assistant_to_chat(content: Any, dropped: Dropped) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant"}
    if isinstance(content, str):
        message["content"] = content
        return message
    texts: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "text":
            part = {"type": "text", "text": str(block.get("text") or "")}
            if block.get("cache_control"):
                part["cache_control"] = dict(block["cache_control"])
            texts.append(part)
        elif kind in ("thinking", "redacted_thinking"):
            details.extend(_thinking_to_details(block))
        elif kind == "tool_use":
            if block.get("cache_control"):
                dropped.add("cache_control")
            calls.append(
                {
                    "id": str(block.get("id") or f"toolu_{len(calls)}"),
                    "type": "function",
                    "function": {"name": block.get("name"), "arguments": dump_arguments(block.get("input"))},
                },
            )
        else:
            dropped.add(f"assistant.{kind}")
    if texts:
        if any("cache_control" in t for t in texts):
            message["content"] = texts
        else:
            message["content"] = "".join(t["text"] for t in texts)
    else:
        message["content"] = None
    assistant_reasoning(message, merge_details(details))
    if calls:
        message["tool_calls"] = calls
    return message


def _thinking_to_details(block: Mapping[str, Any]) -> list[dict[str, Any]]:  # noqa: PLR0911
    """A thinking / redacted_thinking block -> reasoning details (our tokens decoded, native ones kept)."""
    if block.get("type") == "redacted_thinking":
        carried = decode_token(block.get("data"))
        if isinstance(carried, list):
            return [dict(d) for d in carried if isinstance(d, Mapping)]
        if isinstance(carried, Mapping):
            return [dict(carried)]
        return [{"type": "reasoning.encrypted", "data": block.get("data"), "format": TRANSLATED_FORMAT}]
    text = str(block.get("thinking") or "")
    signature = block.get("signature")
    carried = decode_token(signature)
    if isinstance(carried, list):  # several details in one block: the token holds them whole
        return [dict(d) for d in carried if isinstance(d, Mapping)]
    if isinstance(carried, Mapping):
        detail = dict(carried)
        detail["summary" if detail.get("type") == "reasoning.summary" else "text"] = text
        return [detail]
    if not signature:
        return [synthetic_detail(text)] if text else []
    return [{"type": "reasoning.text", "text": text, "signature": signature, "format": TRANSLATED_FORMAT}]


def _tools_to_chat(tools: Any, dropped: Dropped) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools if isinstance(tools, list) else []:
        if not isinstance(tool, Mapping):
            continue
        if tool.get("type") not in (None, "custom"):
            dropped.add(f"tools.{tool.get('type')}")  # server tools (web search, code execution, ...)
            continue
        if tool.get("cache_control"):
            dropped.add("cache_control")
        function: dict[str, Any] = {"name": tool.get("name")}
        if tool.get("description"):
            function["description"] = tool["description"]
        function["parameters"] = dict(tool.get("input_schema") or {"type": "object", "properties": {}})
        out.append({"type": "function", "function": function})
    return out


def _tool_choice_to_chat(choice: Any, out: dict[str, Any], dropped: Dropped) -> None:
    if not isinstance(choice, Mapping):
        return
    kind = choice.get("type")
    if kind == "auto":
        out["tool_choice"] = "auto"
    elif kind == "any":
        out["tool_choice"] = "required"
    elif kind == "tool":
        out["tool_choice"] = {"type": "function", "function": {"name": choice.get("name")}}
    elif kind == "none":
        out["tool_choice"] = "none"
    else:
        dropped.add("tool_choice")
    if choice.get("disable_parallel_tool_use"):
        out["parallel_tool_calls"] = False


def _reasoning_to_chat(body: Mapping[str, Any], dropped: Dropped) -> dict[str, Any] | None:
    """``thinking`` / ``output_config.effort`` -> OpenRouter ``reasoning`` (the policy may replace it)."""
    thinking = body.get("thinking") if isinstance(body.get("thinking"), Mapping) else {}
    config = body.get("output_config") if isinstance(body.get("output_config"), Mapping) else {}
    for key in config:
        if key != "effort":
            dropped.add(f"output_config.{key}")
    kind = thinking.get("type")
    if kind == "disabled":
        return {"enabled": False}
    if kind == "enabled" and thinking.get("budget_tokens"):
        return {"max_tokens": int(thinking["budget_tokens"])}
    if config.get("effort"):
        return {"effort": config["effort"]}
    if kind in ("enabled", "adaptive"):
        return {"enabled": True}
    return None


# -- responses ---------------------------------------------------------------------------------------


def _usage_from_chat(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    norm = budget_rules.chat_usage(usage) or {}
    prompt = norm.get("prompt_tokens") or 0
    cached = norm.get("cached_tokens") or 0
    written = norm.get("cache_write_tokens") or 0
    out: dict[str, Any] = {
        "input_tokens": max(0, prompt - cached - written),
        "cache_creation_input_tokens": written,
        "cache_read_input_tokens": cached,
        "output_tokens": norm.get("completion_tokens") or 0,
    }
    if norm.get("cost") is not None:
        out["cost"] = norm["cost"]
    return out


def _message_id(turn: ChatTurn) -> str:
    raw = turn.id or "llmlab"
    return raw if raw.startswith("msg_") else f"msg_{raw}"


def _thinking_blocks(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reasoning details -> thinking / redacted_thinking blocks whose signature carries each detail."""
    blocks: list[dict[str, Any]] = []
    for detail in details:
        if detail.get("type") == "reasoning.encrypted":
            blocks.append({"type": "redacted_thinking", "data": encode_token(detail)})
            continue
        text_key = "summary" if detail.get("type") == "reasoning.summary" else "text"
        carried = {k: v for k, v in detail.items() if k != text_key}
        blocks.append({"type": "thinking", "thinking": detail_text(detail), "signature": encode_token(carried)})
    return blocks


def _tool_input(call: ToolCall, dropped: Dropped) -> dict[str, Any]:
    value, ok = parse_arguments(call.arguments)
    if not ok or not isinstance(value, Mapping):
        dropped.add("tool_call.invalid_arguments")
        return {}
    return dict(value)


def from_chat(
    chat_response: Mapping[str, Any],
    request_json: Mapping[str, Any] | None = None,
    *,
    dropped: Dropped | dict[str, int] | None = None,
    **_options: Any,
) -> dict[str, Any]:
    """A chat completion -> an Anthropic ``message`` (an error payload -> the Anthropic error shape)."""
    drops = as_dropped(dropped)
    failure = completion_failure(chat_response)
    if failure is not None:  # an error payload or a failed turn (finish_reason=error): the native error
        return upstream_error(502, *failure)[1]
    turn = turn_from_completion(chat_response)
    content = _thinking_blocks(turn.details)
    if turn.content:
        content.append({"type": "text", "text": turn.content})
    if turn.refusal:
        content.append({"type": "text", "text": turn.refusal})
    for n, call in enumerate(turn.tool_calls):
        content.append(
            {
                "type": "tool_use",
                "id": tool_call_id(call.id, "toolu_", n),
                "name": call.name,
                "input": _tool_input(call, drops),
            },
        )
    if isinstance(dropped, dict):
        dropped.update(drops.data)
    return {
        "id": _message_id(turn),
        "type": "message",
        "role": "assistant",
        "model": turn.model,
        "content": content,
        "stop_reason": CHAT_TO_STOP.get(finish_from_chat(turn), "end_turn"),
        "stop_sequence": None,
        "usage": _usage_from_chat(turn.usage),
    }


def upstream_error(status: int, message: str, code: str | None = None) -> tuple[int, dict[str, Any]]:
    """An upstream chat error in Anthropic's shape; a context overflow in Anthropic's own wording."""
    if looks_like_context_overflow(message, code) and "prompt is too long" not in message:
        message = f"prompt is too long: {message}"
    return error("upstream_error", message, status)


class StreamTranslator:
    """Chat SSE -> Anthropic Messages SSE (``message_start`` ... ``message_stop``).

    Thinking and text stream live as content blocks; tool calls are emitted whole when the turn ends
    (their arguments are validated JSON then). Reasoning details that arrive after their thinking block
    closed (late signatures) become one more thinking block; the request side merges them back.
    """

    content_type = "text/event-stream"

    def __init__(
        self,
        request_json: Mapping[str, Any] | None = None,
        *,
        dropped: Dropped | dict[str, int] | None = None,
        **_options: Any,
    ) -> None:
        self._reader = ChatStreamReader()
        self._drops = dropped if isinstance(dropped, Dropped) else Dropped()
        self._sink = dropped if isinstance(dropped, dict) else None
        self._turn = TurnBuilder()
        self._started = False
        self._index = -1
        self._open: str | None = None  # "thinking" | "text"
        self._block_fragments: list[dict[str, Any]] = []
        self._late: list[dict[str, Any]] = []
        self._synthetic = False
        self._finished = False

    # -- framing ------------------------------------------------------------------------------------

    def _start(self) -> bytes:
        if self._started:
            return b""
        self._started = True
        t = self._turn.turn
        message = {
            "id": _message_id(t),
            "type": "message",
            "role": "assistant",
            "model": t.model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        return sse_event({"type": "message_start", "message": message}, "message_start")

    def _open_block(self, block: dict[str, Any], kind: str) -> bytes:
        out = self._close()
        self._index += 1
        self._open = kind
        return out + sse_event(
            {"type": "content_block_start", "index": self._index, "content_block": block},
            "content_block_start",
        )

    def _delta(self, delta: dict[str, Any]) -> bytes:
        return sse_event({"type": "content_block_delta", "index": self._index, "delta": delta}, "content_block_delta")

    def _stop(self) -> bytes:
        return sse_event({"type": "content_block_stop", "index": self._index}, "content_block_stop")

    def _close(self) -> bytes:
        if self._open is None:
            return b""
        out = b""
        if self._open == "thinking":
            details = merge_details(self._block_fragments)
            if self._synthetic and not details:
                carried: Any = {"type": "reasoning.text", "synthetic": True}
            elif len(details) == 1 and details[0].get("type") != "reasoning.encrypted":
                key = "summary" if details[0].get("type") == "reasoning.summary" else "text"
                carried = {k: v for k, v in details[0].items() if k != key}
            else:
                carried = details
            out += self._delta({"type": "signature_delta", "signature": encode_token(carried)})
            self._block_fragments = []
            self._synthetic = False
        out += self._stop()
        self._open = None
        return out

    # -- events -------------------------------------------------------------------------------------

    def feed(self, chunk: bytes) -> bytes:
        out = b""
        for event in self._reader.feed(chunk):
            self._turn.feed(event)
            out += self._event(event)
        return out

    def _event(self, event: tuple[Any, ...]) -> bytes:
        kind = event[0]
        if kind in ("meta", "finish", "usage", "error", "tool"):
            return b""
        out = self._start()
        if kind == "reasoning":
            if self._open != "thinking" or not self._synthetic:
                out += self._open_block({"type": "thinking", "thinking": "", "signature": ""}, "thinking")
                self._synthetic = True
            return out + self._delta({"type": "thinking_delta", "thinking": event[1]})
        if kind == "details":
            for fragment in event[1]:
                out += self._detail(fragment)
            return out
        if kind in ("content", "refusal"):
            if self._open != "text":
                out += self._open_block({"type": "text", "text": ""}, "text")
            return out + self._delta({"type": "text_delta", "text": event[1]})
        return out

    def _detail(self, fragment: dict[str, Any]) -> bytes:
        out = b""
        if fragment.get("type") == "reasoning.encrypted":
            out += self._close()
            self._index += 1
            block = {"type": "redacted_thinking", "data": encode_token(fragment)}
            out += sse_event(
                {"type": "content_block_start", "index": self._index, "content_block": block},
                "content_block_start",
            )
            return out + self._stop()
        text = detail_text(fragment)
        if not text and self._open == "text":
            self._late.append(fragment)  # a signature after the text started: one more block at the end
            return out
        if self._open != "thinking" or self._synthetic:
            out += self._open_block({"type": "thinking", "thinking": "", "signature": ""}, "thinking")
        self._block_fragments.append(fragment)
        if text:
            out += self._delta({"type": "thinking_delta", "thinking": text})
        return out

    def finish(self, failure: Any = None) -> bytes:
        """The closing events; ``failure`` is a transport error seen by the relay (stream broke)."""
        if self._finished:
            return b""
        self._finished = True
        turn = self._turn.finish()
        out = self._start() + self._close()
        if self._late:
            out += self._open_block({"type": "thinking", "thinking": "", "signature": ""}, "thinking")
            self._block_fragments = self._late
            out += self._close()
        broken = turn_failure(turn, failure)
        if broken is not None:
            message, code = broken
            return out + stream_error(502, upstream_error(502, message, code)[1])
        for n, call in enumerate(turn.tool_calls):
            self._index += 1
            block = {"type": "tool_use", "id": tool_call_id(call.id, "toolu_", n), "name": call.name, "input": {}}
            out += sse_event(
                {"type": "content_block_start", "index": self._index, "content_block": block},
                "content_block_start",
            )
            out += self._delta(
                {"type": "input_json_delta", "partial_json": json.dumps(_tool_input(call, self._drops))},
            )
            out += self._stop()
        if self._sink is not None:
            self._sink.update(self._drops.data)
        delta = {"stop_reason": CHAT_TO_STOP.get(finish_from_chat(turn), "end_turn"), "stop_sequence": None}
        out += sse_event(
            {"type": "message_delta", "delta": delta, "usage": _usage_from_chat(turn.usage)},
            "message_delta",
        )
        return out + sse_event({"type": "message_stop"}, "message_stop")

    def fail(self, status: int, payload: Mapping[str, Any]) -> bytes:
        """An upstream error after the stream started: the native in-stream error event."""
        self._finished = True
        return self._start() + self._close() + stream_error(status, payload)


def stream_from_chat(
    chat_sse_iter: Iterable[bytes | None],
    request_json: Mapping[str, Any] | None = None,
    **options: Any,
) -> Iterator[bytes]:
    """Chat SSE chunks -> Anthropic SSE bytes; a ``None`` item (upstream silence) yields a ping."""
    return iterate_stream(StreamTranslator(request_json, **options), chat_sse_iter, PING)
