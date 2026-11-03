"""Anthropic Messages front (Claude Code): ``passthrough`` to OpenRouter's native ``/messages``.

Passthrough forwards the harness's native body after applying the run policy **on the native body**:
the model pin, ``max_tokens``, ``thinking`` / ``output_config.effort`` for the reasoning knob, sampling,
``provider`` routing, ``cache_control`` handling (kept, TTL set, injected for listed providers; stripped
under ``off``/``auto``) and ``session_id``. Everything else, thinking blocks and their signatures
included, is forwarded byte-for-byte in content. Usage comes from the native response.

The stdlib translator to canonical chat (``to_chat`` / ``from_chat`` / ``stream_from_chat``, needed for
local endpoints) is wave 3; until then a ``translate`` config is refused at startup.
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
    """The Anthropic <-> canonical chat translator ships in wave 3."""


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
# Translator seams (wave 3)
# =====================================================================================


def to_chat(request_json: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    raise TranslatorUnavailableError(NAME)


def from_chat(chat_response: Mapping[str, Any]) -> dict[str, Any]:
    raise TranslatorUnavailableError(NAME)


def stream_from_chat(chat_sse_iter: Iterable[bytes]) -> Iterator[bytes]:
    raise TranslatorUnavailableError(NAME)
