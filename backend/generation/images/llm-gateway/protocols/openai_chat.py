"""OpenAI chat completions: the canonical format and the ``native`` front (opencode, mini-swe-agent,
aider, goose, qwen-code).

The policy is applied in the same key order as the v2 gateway's ``_rewrite`` (model in place, strip,
force sampling, seed, provider, ``max_tokens``, then the v3 additions, then ``usage`` and
``stream_options``), so a ported harness's upstream bytes stay identical apart from new policy fields.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from protocols import budget_rules
from protocols.common import CHAT_SAMPLING_KEYS
from protocols.common import Policy
from protocols.common import PolicyDiff
from protocols.common import SSEParser
from protocols.common import apply_sampling
from protocols.common import content_text
from protocols.common import count_cache_control
from protocols.common import force
from protocols.common import json_or_none
from protocols.common import mark_last_part
from protocols.common import pin_model
from protocols.common import set_cache_ttl
from protocols.common import strip_cache_control

NAME = "openai_chat"
UPSTREAM_PATH = "/chat/completions"
PING = b": ping\n\n"
# OpenRouter routing fields the harness may not set (the policy owns routing).
ROUTING_KEYS = ("provider", "models", "route", "transforms")
REASONING_KEYS = ("reasoning", "reasoning_effort", "include_reasoning")


def route(method: str, path: str) -> str | None:
    """``"call"`` / ``"models"`` for paths this front serves, else None."""
    if method == "POST" and path.rstrip("/").endswith("/chat/completions"):
        return "call"
    if method == "GET" and path.rstrip("/").endswith("/models"):
        return "models"
    return None


# =====================================================================================
# Policy
# =====================================================================================


def apply_policy_native(body: Mapping[str, Any], policy: Policy) -> tuple[dict[str, Any], dict[str, Any]]:
    """The upstream chat body with the run's policy applied, and the ``policy_diff``."""
    out = copy.deepcopy(dict(body))
    diff = PolicyDiff()
    pin_model(out, policy, diff)
    apply_sampling(out, policy, diff, allowed=(*CHAT_SAMPLING_KEYS, *_extra_forced(policy)))
    if "seed" in out:
        diff.stripped("seed", out.pop("seed"))
    if policy.seed is not None and policy.forward_seed:
        out["seed"] = policy.seed
    for key in ROUTING_KEYS:
        if key in out:
            diff.stripped(key, out.pop(key))
    if policy.provider:
        out["provider"] = dict(policy.provider)
    if "max_completion_tokens" in out:
        diff.stripped("max_completion_tokens", out.pop("max_completion_tokens"))
    force(out, "max_tokens", policy.upstream_max_tokens, diff)
    reasoning = policy.openrouter_reasoning
    if reasoning is not None:
        for key in REASONING_KEYS:
            if key in out:
                diff.stripped(key, out.pop(key))
        out["reasoning"] = reasoning
    if policy.sticky_session and policy.session_id:
        out["session_id"] = policy.session_id
    _apply_cache_policy(out, policy, diff)
    out["usage"] = {"include": True}
    if out.get("stream"):
        out["stream_options"] = {**(out.get("stream_options") or {}), "include_usage": True}
    return out, diff.data


def _extra_forced(policy: Policy) -> tuple[str, ...]:
    """Forced keys outside the sampling set that chat still accepts verbatim (e.g. ``verbosity``)."""
    return tuple(k for k in policy.forced if k not in ("model", "messages", "tools", "stream", "max_tokens"))


def _apply_cache_policy(out: dict[str, Any], policy: Policy, diff: PolicyDiff) -> None:
    if policy.prompt_cache in ("off", "auto"):
        diff.count("cache_control_stripped", strip_cache_control(out))
        return
    if not policy.cache_control_applies():
        return
    diff.count("cache_control_ttl_set", set_cache_ttl(out, policy.cache_ttl))
    messages = out.get("messages")
    if not isinstance(messages, list):
        return
    targets = []
    systems = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    if systems:
        targets.append(systems[-1])
    tools = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    if tools:
        targets.append(tools[-1])
    injected = 0
    for message in targets:
        if count_cache_control(out) >= 4:
            break
        message["content"], added = mark_last_part(message.get("content"), policy.cache_ttl)
        injected += int(added)
    diff.count("cache_control_injected", injected)


def prompt_text(body: Mapping[str, Any]) -> str:
    return budget_rules.prompt_text(body)


def system_prompt(body: Mapping[str, Any]) -> str | None:
    parts = [
        content_text(m.get("content"))
        for m in body.get("messages") or []
        if isinstance(m, Mapping) and m.get("role") in ("system", "developer")
    ]
    return "\n\n".join(parts) if parts else None


def requested_reasoning(body: Mapping[str, Any]) -> Any:
    found = {k: body[k] for k in REASONING_KEYS if k in body}
    return found or None


# =====================================================================================
# Errors
# =====================================================================================


def error(kind: str, message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    """The OpenAI error shape."""
    return status, {"error": {"message": message, "type": kind, "code": kind, "param": None}}


def context_error(est_tokens: int, cap: int) -> tuple[int, dict[str, Any]]:
    """The ``call.max_input_tokens`` refusal, worded as the provider's own context-length error so the
    harness compacts its history (that is the cap's purpose)."""
    message = (
        f"This model's maximum context length is {cap} tokens. However, your messages resulted in "
        f"{est_tokens} tokens. Please reduce the length of the messages."
    )
    return 400, {"error": {"message": message, "type": "invalid_request_error", "code": "context_length_exceeded"}}


def stream_error(status: int, payload: Mapping[str, Any]) -> bytes:
    """An error inside an already-started SSE stream."""
    return f"data: {json.dumps(dict(payload))}\n\ndata: [DONE]\n\n".encode()


def models_payload(names: list[str]) -> dict[str, Any]:
    return {"object": "list", "data": [{"id": n, "object": "model", "owned_by": "llm-gateway"} for n in names]}


# =====================================================================================
# Responses
# =====================================================================================


def summarize(payload: Any) -> dict[str, Any]:
    """A non-streamed chat completion -> the call record's ``response`` summary."""
    if not isinstance(payload, Mapping):
        return {"error": "non-object response"}
    choices = payload.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], Mapping) else {}
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
            if isinstance(tc, Mapping)
        ],
        "finish_reason": choice.get("finish_reason"),
        "native_finish_reason": choice.get("native_finish_reason") or choice.get("finish_reason"),
        "usage": payload.get("usage"),
        "normalized_usage": budget_rules.chat_usage(payload.get("usage")),
        "error": payload.get("error"),
    }


class StreamAccumulator:
    """Rebuilds a streamed chat completion from its SSE chunks (for the call record)."""

    def __init__(self) -> None:
        self._parser = SSEParser()
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.meta: dict[str, Any] = {}
        self.usage: dict[str, Any] | None = None
        self.finish_reason: str | None = None
        self.native_finish_reason: str | None = None
        self.error: Any = None

    def feed(self, chunk: bytes) -> None:
        for _name, data in self._parser.feed(chunk):
            if not data or data == "[DONE]":
                continue
            event = json_or_none(data)
            if isinstance(event, Mapping):
                self._event(event)

    def _event(self, event: Mapping[str, Any]) -> None:
        if "error" in event:
            self.error = event["error"]
        for key in ("id", "model", "provider", "created", "system_fingerprint"):
            if event.get(key) is not None:
                self.meta[key] = event[key]
        if event.get("usage"):
            self.usage = dict(event["usage"])
        for choice in event.get("choices") or []:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.content.append(str(delta["content"]))
            if delta.get("reasoning"):
                self.reasoning.append(str(delta["reasoning"]))
            for tc in delta.get("tool_calls") or []:
                slot = self.tool_calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
                self.native_finish_reason = choice.get("native_finish_reason") or choice["finish_reason"]

    def summary(self) -> dict[str, Any]:
        return {
            **{k: self.meta.get(k) for k in ("id", "model", "provider", "system_fingerprint")},
            "content": "".join(self.content),
            "reasoning": "".join(self.reasoning),
            "tool_calls": [self.tool_calls[i] for i in sorted(self.tool_calls)],
            "finish_reason": self.finish_reason,
            "native_finish_reason": self.native_finish_reason,
            "usage": self.usage,
            "normalized_usage": budget_rules.chat_usage(self.usage),
            "error": self.error,
        }


# =====================================================================================
# Translator seams (the canonical format: identity)
# =====================================================================================


def to_chat(request_json: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    return copy.deepcopy(dict(request_json)), {}


def from_chat(chat_response: Mapping[str, Any]) -> dict[str, Any]:
    return dict(chat_response)
