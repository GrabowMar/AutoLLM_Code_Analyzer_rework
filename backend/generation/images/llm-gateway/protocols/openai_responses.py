"""OpenAI Responses front (Codex): ``passthrough`` to OpenRouter's stateless native ``/responses``.

The policy is applied on the native body: the model pin, ``max_output_tokens``, ``reasoning`` (effort or
token budget; the harness's ``summary`` setting is kept), sampling, ``provider`` routing, ``session_id``,
``store: false`` and ``include: ["reasoning.encrypted_content"]`` (so reasoning replays statelessly).
``previous_response_id`` is refused: the gateway and OpenRouter's beta are stateless.

The stdlib translator to canonical chat (custom/freeform tools as single-string function tools) is
wave 3; until then a ``translate`` config is refused at startup.
"""

from __future__ import annotations

import copy
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

NAME = "openai_responses"
UPSTREAM_PATH = "/responses"
PING = b": keepalive\n\n"
SAMPLING_KEYS = ("temperature", "top_p")
ENCRYPTED_REASONING = "reasoning.encrypted_content"
TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")


class TranslatorUnavailableError(NotImplementedError):
    """The Responses <-> canonical chat translator ships in wave 3."""


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
# Translator seams (wave 3)
# =====================================================================================


def to_chat(request_json: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    raise TranslatorUnavailableError(NAME)


def from_chat(chat_response: Mapping[str, Any]) -> dict[str, Any]:
    raise TranslatorUnavailableError(NAME)


def stream_from_chat(chat_sse_iter: Iterable[bytes]) -> Iterator[bytes]:
    raise TranslatorUnavailableError(NAME)
