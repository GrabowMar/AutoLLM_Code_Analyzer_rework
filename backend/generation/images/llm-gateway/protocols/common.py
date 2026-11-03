"""Shared pieces of the gateway's front protocols: the run policy, hashing, SSE framing, cache markers.

Stdlib only. :class:`Policy` is the gateway's read of ``GATEWAY_CONFIG`` (the
``backend.generation.contracts.resources.GatewayConfig`` contract, ``config_version 1``); every front
module applies it to its own body shape.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import unicodedata
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from protocols import budget_rules

FRONT_PROTOCOLS = ("openai_chat", "openai_responses", "anthropic_messages", "gemini_generate")
PROTOCOL_MODES = ("native", "passthrough", "translate")
# Fronts that may run in each mode in this gateway version. Translators are wave 3: a config that
# asks for one is refused at startup (exit 78) instead of failing call by call.
SUPPORTED_MODES = {
    "openai_chat": ("native",),
    "anthropic_messages": ("passthrough",),
    "openai_responses": ("passthrough",),
    "gemini_generate": (),
}
# Sampling keys a front body may carry (stripped / forced by the sampling policy).
CHAT_SAMPLING_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "top_a",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
)
TTL_SECONDS = {"5m": 300, "1h": 3600}
MAX_CACHE_BREAKPOINTS = 4  # Anthropic's per-request limit on cache_control markers


class ConfigError(ValueError):
    """``GATEWAY_CONFIG`` cannot run in this gateway version."""


# =====================================================================================
# Hashing (mirrors backend.generation.identity.canonical_json for JSON values)
# =====================================================================================


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, Mapping):
        return {_normalize(str(k)): _normalize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha(value: Any) -> str:
    """``H(value)``: sha256 of the canonical JSON (equal to ``identity.H`` for JSON-native values)."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def text_sha(text: str) -> str:
    """sha256 of the NFC/LF-normalized UTF-8 text (equal to ``identity.text_sha``)."""
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


# =====================================================================================
# The policy
# =====================================================================================


@dataclass(frozen=True)
class Policy:
    """The run's frozen policy, read from a ``GatewayConfig`` mapping."""

    raw: Mapping[str, Any]
    run_id: str
    attempt: int
    session_id: str | None
    front: str
    mode: str
    request_class_source: str
    upstream_kind: str
    upstream_base_url: str
    upstream_model: str
    presented_names: tuple[str, ...]
    max_output: int | None
    supports_reasoning: bool
    forced: Mapping[str, Any]
    stripped: tuple[str, ...]
    seed: int | None
    forward_seed: bool
    reasoning_mode: str
    reasoning_effort: str | None
    reasoning_max_tokens: int | None
    reasoning_exclude: bool
    call_max_output_tokens: int
    call_max_input_tokens: int | None
    provider: Mapping[str, Any]
    serving_tier: str
    prompt_cache: str
    cache_ttl: str
    sticky_session: bool
    cache_control_providers: tuple[str, ...]
    upstream_retries: int
    backoff_s: float
    retry_on: tuple[str, ...]
    llm_call_timeout_s: float
    rpm_per_model: int | None
    bucket_key: str | None
    log_native_system_prompt: bool

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Policy:
        try:
            return cls._parse(config)
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"GATEWAY_CONFIG is malformed: {exc!r}"
            raise ConfigError(msg) from exc

    @classmethod
    def _parse(cls, c: Mapping[str, Any]) -> Policy:
        if int(c.get("config_version", 1)) != 1:
            msg = f"unsupported config_version {c.get('config_version')!r} (this gateway reads 1)"
            raise ConfigError(msg)
        model = c["model"]
        sampling = c.get("sampling") or {}
        reasoning = c["reasoning"]
        call = c["call"]
        routing = c.get("routing") or {}
        caching = c["caching"]
        retries = c["retries"]
        rate = c.get("rate_limit") or {}
        return cls(
            raw=copy.deepcopy(dict(c)),
            run_id=str(c["run_id"]),
            attempt=int(c["attempt"]),
            session_id=c.get("session_id") or None,
            front=str(c["front_protocol"]),
            mode=str(c["protocol_mode"]),
            request_class_source=str(c.get("request_class_source") or "default:main"),
            upstream_kind=str(c["upstream"]["kind"]),
            upstream_base_url=str(c["upstream"]["base_url"]),
            upstream_model=str(model["upstream_model"]),
            presented_names=tuple(str(n) for n in model.get("presented_names") or ()),
            max_output=int(model["max_output"]) if model.get("max_output") else None,
            supports_reasoning=bool(model.get("supports_reasoning")),
            forced=dict(sampling.get("forced") or {}),
            stripped=tuple(str(k) for k in sampling.get("stripped") or ()),
            seed=None if sampling.get("seed") is None else int(sampling["seed"]),
            forward_seed=bool(sampling.get("forward_seed", True)),
            reasoning_mode=str(reasoning["mode"]),
            reasoning_effort=reasoning.get("effort"),
            reasoning_max_tokens=None if reasoning.get("max_tokens") is None else int(reasoning["max_tokens"]),
            reasoning_exclude=bool(reasoning.get("exclude")),
            call_max_output_tokens=int(call["max_output_tokens"]),
            call_max_input_tokens=None if call.get("max_input_tokens") is None else int(call["max_input_tokens"]),
            provider=dict(routing.get("provider") or {}),
            serving_tier=str(routing.get("serving_tier") or "standard"),
            prompt_cache=str(caching["prompt_cache"]),
            cache_ttl=str(caching["ttl"]),
            sticky_session=bool(caching["sticky_session"]),
            cache_control_providers=tuple(str(p).lower() for p in caching.get("cache_control_providers") or ()),
            upstream_retries=int(retries["upstream_retries"]),
            backoff_s=float(retries["backoff_s"]),
            retry_on=tuple(str(s) for s in retries["retry_on"]),
            llm_call_timeout_s=float(retries["llm_call_timeout_s"]),
            rpm_per_model=int(rate["rpm_per_model"]) if rate.get("rpm_per_model") else None,
            bucket_key=rate.get("bucket_key") or None,
            log_native_system_prompt=bool(c.get("log_native_system_prompt", True)),
        )

    # -- derived values ------------------------------------------------------------------------------

    def problems(self) -> list[str]:
        """Why this config cannot run here (empty when it can)."""
        out: list[str] = []
        if self.front not in FRONT_PROTOCOLS:
            out.append(f"unknown front_protocol {self.front!r}")
        elif self.mode not in SUPPORTED_MODES[self.front]:
            if self.mode == "translate" or self.front == "gemini_generate":
                out.append(f"the {self.front} translator is not available in this gateway version (wave 3)")
            else:
                out.append(f"{self.front} does not run in protocol_mode {self.mode!r}")
        if self.mode == "passthrough" and self.upstream_kind != "openrouter":
            out.append("passthrough forwards native bodies to OpenRouter only (remote endpoints)")
        if self.reasoning_mode == "effort" and not self.reasoning_effort:
            out.append("reasoning.mode=effort needs reasoning.effort")
        if self.reasoning_mode == "max_tokens" and not self.reasoning_max_tokens:
            out.append("reasoning.mode=max_tokens needs reasoning.max_tokens")
        if self.prompt_cache not in ("auto", "on", "off"):
            out.append(f"unknown caching.prompt_cache {self.prompt_cache!r}")
        if self.serving_tier == "batch":
            out.append("serving_tier=batch is for single-shot slot calls, never a gateway run")
        return out

    @property
    def upstream_max_tokens(self) -> int:
        return budget_rules.upstream_max_tokens(self.raw)

    @property
    def model_for_upstream(self) -> str:
        """The pinned model, with OpenRouter's ``:floor`` suffix for flex serving."""
        model = self.upstream_model
        if self.serving_tier == "flex" and not model.endswith(":floor"):
            model += ":floor"
        return model

    @property
    def model_vendor(self) -> str:
        return self.upstream_model.split("/", 1)[0].lower() if "/" in self.upstream_model else ""

    def cache_control_applies(self) -> bool:
        """Whether explicit ``cache_control`` markers are meaningful for this run's provider."""
        if self.prompt_cache != "on":
            return False
        listed = set(self.cache_control_providers)
        if not listed:
            return False
        names = {self.model_vendor}
        names |= {str(p).lower() for p in self.provider.get("only") or ()}
        names |= {str(p).lower() for p in self.provider.get("order") or ()}
        return bool(names & listed)

    def retry_statuses(self) -> set[int]:
        out: set[int] = set()
        for item in self.retry_on:
            if item.endswith("xx"):
                base = int(item[0]) * 100
                out |= set(range(base, base + 100))
            else:
                out.add(int(item))
        return out

    @property
    def openrouter_reasoning(self) -> dict[str, Any] | None:
        """The OpenRouter ``reasoning`` object the policy forces (None: provider default)."""
        if self.reasoning_mode == "effort" and self.reasoning_effort:
            return {"effort": self.reasoning_effort, "exclude": self.reasoning_exclude}
        if self.reasoning_mode == "max_tokens" and self.reasoning_max_tokens:
            return {"max_tokens": self.reasoning_max_tokens, "exclude": self.reasoning_exclude}
        return None


class PolicyDiff:
    """What the policy changed in a body, recorded per call as ``policy_diff``."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def model(self, requested: Any) -> None:
        self.data["model"] = requested

    def stripped(self, key: str, value: Any) -> None:
        self.data.setdefault("stripped", {})[key] = value

    def forced(self, key: str, previous: Any) -> None:
        self.data.setdefault("forced", {})[key] = previous

    def note(self, key: str, value: Any) -> None:
        self.data[key] = value

    def count(self, key: str, n: int = 1) -> None:
        if n:
            self.data[key] = int(self.data.get(key, 0)) + n


def apply_sampling(body: dict[str, Any], policy: Policy, diff: PolicyDiff, *, allowed: tuple[str, ...]) -> None:
    """Strip, then force, the sampling keys this front accepts (in place, keeping key order)."""
    for key in policy.stripped:
        if key in body:
            diff.stripped(key, body.pop(key))
    for key, value in policy.forced.items():
        if key not in allowed:
            diff.note("forced_unsupported", sorted({*diff.data.get("forced_unsupported", []), key}))
            continue
        if body.get(key) != value:
            diff.forced(key, body.get(key))
        body[key] = value


def pin_model(body: dict[str, Any], policy: Policy, diff: PolicyDiff) -> None:
    requested = body.get("model")
    model = policy.model_for_upstream
    if requested != model:
        diff.model(requested)
    body["model"] = model


def force(body: dict[str, Any], key: str, value: Any, diff: PolicyDiff) -> None:
    """Set ``body[key] = value``, recording the previous value when it differs."""
    if body.get(key) != value:
        diff.forced(key, body.get(key))
    body[key] = value


# =====================================================================================
# cache_control markers
# =====================================================================================


def _walk_parts(value: Any) -> Iterator[dict[str, Any]]:
    """Every dict nested in ``value`` (content parts, tool definitions, messages)."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_parts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_parts(item)


def strip_cache_control(body: dict[str, Any]) -> int:
    """Remove every ``cache_control`` marker; returns how many were removed."""
    removed = 0
    for part in _walk_parts(body):
        if "cache_control" in part:
            del part["cache_control"]
            removed += 1
    return removed


def count_cache_control(body: Mapping[str, Any]) -> int:
    return sum(1 for part in _walk_parts(body) if "cache_control" in part)


def cache_marker(ttl: str) -> dict[str, Any]:
    marker: dict[str, Any] = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    return marker


def set_cache_ttl(body: dict[str, Any], ttl: str) -> int:
    """Rewrite every existing marker's TTL to the policy's; returns how many changed."""
    changed = 0
    for part in _walk_parts(body):
        marker = part.get("cache_control")
        if isinstance(marker, dict):
            want = cache_marker(ttl)
            if {k: marker.get(k) for k in ("type", "ttl")} != {k: want.get(k) for k in ("type", "ttl")}:
                rest = {k: v for k, v in marker.items() if k not in ("type", "ttl")}
                part["cache_control"] = {**rest, **want}
                changed += 1
    return changed


def mark_last_part(content: Any, ttl: str) -> tuple[Any, bool]:
    """``content`` with a marker on its last text-like part (a string becomes one text part)."""
    if isinstance(content, str):
        if not content:
            return content, False
        return [{"type": "text", "text": content, "cache_control": cache_marker(ttl)}], True
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        if "cache_control" not in content[-1]:
            content[-1]["cache_control"] = cache_marker(ttl)
            return content, True
    return content, False


# =====================================================================================
# Content checks
# =====================================================================================

IMAGE_PART_TYPES = frozenset({"image", "image_url", "input_image", "document", "input_file", "file"})


def contains_images(value: Any) -> bool:
    """Whether any content part is an image/file (rejected with 400 ``unsupported_content``)."""
    for part in _walk_parts(value):
        if part.get("type") in IMAGE_PART_TYPES:
            return True
        if "inlineData" in part or "inline_data" in part or "fileData" in part:
            return True
    return False


def content_text(content: Any) -> str:
    """Plain text of a content value (string, list of parts or a part)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(t for t in (content_text(p) for p in content) if t)
    if isinstance(content, Mapping):
        for key in ("text", "content", "input_text"):
            if isinstance(content.get(key), str | list):
                return content_text(content[key])
        return ""
    return str(content)


# =====================================================================================
# SSE
# =====================================================================================


class SSEParser:
    """Incremental ``text/event-stream`` parser yielding ``(event, data)`` per complete event."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[tuple[str | None, str]]:
        self._buffer += chunk.replace(b"\r\n", b"\n")
        events: list[tuple[str | None, str]] = []
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            name: str | None = None
            data: list[str] = []
            for raw in block.split(b"\n"):
                line = raw.decode("utf-8", errors="replace")
                if not line or line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if field == "event":
                    name = value
                elif field == "data":
                    data.append(value)
            if data or name:
                events.append((name, "\n".join(data)))
        return events


def json_or_none(text: str | bytes) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def sse_event(data: Mapping[str, Any] | str, event: str | None = None) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data)
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {payload}\n\n".encode()
