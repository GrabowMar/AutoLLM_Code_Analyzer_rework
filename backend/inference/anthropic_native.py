"""OpenAI chat ↔ Anthropic's native Messages API (endpoint ``api_flavor="anthropic"``).

Claude models on the user's own Anthropic key used to go through Anthropic's OpenAI-compatibility layer,
which has no prompt caching, ignores ``reasoning_effort`` and reports no cache split. Endpoints with
``api_flavor="anthropic"`` speak ``POST {base}/v1/messages`` instead:

- :func:`to_messages_request` turns the canonical chat body the gateway sends into the exact Messages
  request (built at enqueue time, stored as the ``LLMRequest`` body);
- :class:`MessageStream` reads the Messages SSE stream the pump always requests and re-assembles it into
  the OpenAI ``chat.completion`` every consumer already understands (``translate``, the budgets, the
  ledger, telemetry and the gateway); :func:`to_chat_completion` does the same for one ``message`` JSON;
- :func:`error_payload` maps an Anthropic error answer to the OpenAI error shape, status unchanged.

Decisions (study validity):

- **Effort**: ``output_config.effort`` comes from the policy's pinned OpenRouter ``reasoning.effort``, else
  the harness's ``reasoning_effort``, else :data:`DEFAULT_EFFORT` (``high``), never the model's own default
  (Opus 5.5 and Haiku 5.5 default to ``medium``, the others to ``high``), so conditions are comparable.
  Thinking can never be disabled on Claude 5.x: ``thinking.type=disabled`` and ``budget_tokens`` are never
  sent. The effort actually sent and its source are recorded on every call (``usage.anthropic.request``).
- **Thinking is not replayed**: assistant ``reasoning`` / ``reasoning_content`` / ``reasoning_details``
  are dropped from the history, as the compat layer did. Claude re-thinks every turn from the visible
  transcript; history edits by the harness can never trip the preserved-thinking 400.
- **Prompt caching** (unless the run's ``caching.prompt_cache`` is ``off``): 5-minute ephemeral breakpoints
  on the last system block (tools render before system, so tools and system are cached), on the last block
  of the previous turn's final message (an exact hit on what the previous request wrote, whatever the
  20-block lookback) and on the last block of the last message (writes this turn's prefix). At most 3 of
  Anthropic's 4. The translation is deterministic and never adds volatile text, so each request of an
  append-only agent loop is a byte extension of the previous one.
- **Sampling knobs** (temperature, top_p, top_k, seed, penalties, ...) are not sent (400 on current
  models, no seed in the API) and are listed in ``usage.anthropic.request.stripped``.
- **Unfinished tool calls are dropped**: a ``tool_use`` whose JSON did not complete (llmlab's cut or Anthropic's
  ``max_tokens`` stop) never reaches the harness, counted in ``usage.anthropic.dropped_tool_calls``.
- **A request without tools** renders the history's ``tool_use`` / ``tool_result`` blocks as text (Anthropic
  answers 400 otherwise), counted in ``tool_blocks_as_text``.
- **Hidden thinking past the wall clock** is a ``length`` cut (a started thinking block is output, as streamed
  reasoning is on the OpenAI route), booked with the exact input split and ``max_tokens`` output tokens.
- **Cost**: the completion carries no ``usage.cost`` (Anthropic reports none); the budget code prices
  ``prompt_tokens = input + cache writes + cache reads`` with ``prompt_tokens_details.cached_tokens`` (reads)
  and ``prompt_tokens_details.cache_write_tokens`` (writes) at the manifest's list prices.

Stdlib only.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MESSAGES_PATH = "/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# The documented study default when neither the policy nor the harness asks for an effort.
DEFAULT_EFFORT = "high"
# Messages requires ``max_tokens``; gateway runs always send one (the policy pin).
DEFAULT_MAX_TOKENS = 32_000
# Private request metadata kept in the stored body; ``upstream.call`` pops it before sending.
META_KEY = "_llmlab"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
CACHE_TTL = "5m"
EPHEMERAL = {"type": "ephemeral"}
# Output tokens of a cut stream (or one without final usage) are estimated like ``upstream.ChatStream``.
OUTPUT_CHARS_PER_TOKEN = 3.6
# Chat params that have a Messages counterpart; every other param present is recorded as stripped.
_CONSUMED_PARAMS = frozenset(
    {"max_tokens", "max_completion_tokens", "stop", "reasoning", "reasoning_effort", "response_format", "tools"},
)
_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "refusal": "content_filter",
}
_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_DATA_URL_RE = re.compile(r"^data:(?P<media>[\w.+/-]+);base64,(?P<data>.*)$", re.DOTALL)


# =====================================================================================
# Model traits
# =====================================================================================


@dataclass(frozen=True)
class ModelTraits:
    """What a Claude model accepts on the Messages API."""

    adaptive: bool  # send thinking {"type": "adaptive"} (on 5.x identical to omitting it)
    efforts: tuple[str, ...]  # accepted output_config.effort values (empty: effort is not sent)
    forced_tool_choice: bool  # tool_choice any / tool allowed (Opus 5.5, Sonnet 5.5, Fable 5.1 answer 400)
    max_output: int


_ALL = EFFORT_LEVELS
_TRAITS: dict[str, ModelTraits] = {
    "claude-fable-5-1": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=False, max_output=128_000),
    "claude-mythos-5-1": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=False, max_output=128_000),
    "claude-opus-5-5": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=False, max_output=128_000),
    "claude-sonnet-5-5": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=False, max_output=128_000),
    "claude-fable-5": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=True, max_output=128_000),
    "claude-opus-5": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=True, max_output=128_000),
    "claude-sonnet-5": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=True, max_output=128_000),
    "claude-haiku-5-5": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=True, max_output=128_000),
    "claude-opus-4-8": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=True, max_output=128_000),
    "claude-opus-4-7": ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=True, max_output=128_000),
    "claude-opus-4-6": ModelTraits(
        adaptive=True,
        efforts=("low", "medium", "high", "max"),
        forced_tool_choice=True,
        max_output=128_000,
    ),
    "claude-sonnet-4-6": ModelTraits(
        adaptive=True,
        efforts=("low", "medium", "high", "max"),
        forced_tool_choice=True,
        max_output=128_000,
    ),
    "claude-opus-4-5": ModelTraits(
        adaptive=False,
        efforts=("low", "medium", "high"),
        forced_tool_choice=True,
        max_output=64_000,
    ),
    "claude-sonnet-4-5": ModelTraits(adaptive=False, efforts=(), forced_tool_choice=True, max_output=64_000),
    "claude-haiku-4-5": ModelTraits(adaptive=False, efforts=(), forced_tool_choice=True, max_output=64_000),
}
# An unknown (newer) Claude model: the conservative 5.x surface (no forced tool use).
DEFAULT_TRAITS = ModelTraits(adaptive=True, efforts=_ALL, forced_tool_choice=False, max_output=64_000)


def model_key(served_name: str) -> str | None:
    """The :data:`_TRAITS` key of a served name: exact, or with a dated snapshot suffix (``-20251101``)."""
    name = str(served_name or "").strip().lower()
    if name in _TRAITS:
        return name
    base = _DATE_SUFFIX_RE.sub("", name)
    return base if base in _TRAITS else None


def traits(served_name: str) -> ModelTraits:
    """The :class:`ModelTraits` of a served model name (:data:`DEFAULT_TRAITS` when unknown)."""
    key = model_key(served_name)
    return _TRAITS[key] if key is not None else DEFAULT_TRAITS


# =====================================================================================
# Request: chat body -> Messages request
# =====================================================================================


@dataclass(frozen=True)
class Options:
    """Per-call knobs that do not come from the chat body (the run's frozen policy)."""

    prompt_cache: str = "auto"  # manifest ``caching.prompt_cache``: "off" sends no breakpoint
    cache_ttl: str = CACHE_TTL  # manifest ``caching.ttl``; always sent as 5m (recorded when it differs)
    default_effort: str = DEFAULT_EFFORT

    @property
    def ttl_differs(self) -> bool:
        """A policy TTL other than 5 minutes (1-hour writes cost 2x and ``Pricing`` has one write price)."""
        return bool(self.cache_ttl) and self.cache_ttl != CACHE_TTL


def _text_of(content: Any) -> list[str]:
    """The text parts of an OpenAI message content (string or parts array)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
                out.append(part["text"])
        return out
    return [str(content)]


def _image_block(part: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = part.get("image_url")
    url = raw.get("url") if isinstance(raw, Mapping) else raw
    if not isinstance(url, str) or not url:
        return None
    match = _DATA_URL_RE.match(url)
    if match:
        source = {"type": "base64", "media_type": match.group("media"), "data": match.group("data")}
        return {"type": "image", "source": source}
    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _content_blocks(content: Any, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Text and image blocks of a user / tool content; blank text is dropped (Anthropic rejects it)."""
    if content is None:
        return []
    if not isinstance(content, list):
        text = content if isinstance(content, str) else str(content)
        return [{"type": "text", "text": text}] if text.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part.strip():
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, Mapping):
            continue
        kind = part.get("type")
        if kind == "text" or (kind is None and isinstance(part.get("text"), str)):
            text = str(part.get("text") or "")
            if text.strip():
                blocks.append({"type": "text", "text": text})
        elif kind == "image_url":
            block = _image_block(part)
            if block is not None:
                blocks.append(block)
            else:
                _bump(meta, "unsupported_parts")
        else:
            _bump(meta, "unsupported_parts")
    return blocks


def _bump(meta: dict[str, Any], key: str, by: int = 1) -> None:
    meta[key] = int(meta.get(key) or 0) + by


class _Ids:
    """Tool-call ids made safe for Anthropic (``^[A-Za-z0-9_-]+$``), the same mapping for use and result."""

    def __init__(self) -> None:
        self.generated = 0
        self.pending: list[str] = []  # generated ids of the last assistant message, for id-less results

    def use(self, raw: Any) -> str:
        text = str(raw or "")
        if text:
            return _ID_RE.sub("_", text)
        new = f"call_{self.generated}"
        self.generated += 1
        self.pending.append(new)
        return new

    def result(self, raw: Any) -> str:
        text = str(raw or "")
        if text:
            return _ID_RE.sub("_", text)
        if self.pending:
            return self.pending.pop(0)
        new = f"call_{self.generated}"
        self.generated += 1
        return new


def _tool_input(arguments: Any, meta: dict[str, Any]) -> dict[str, Any]:
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if arguments is None or (isinstance(arguments, str) and not arguments.strip()):
        return {}
    try:
        value = json.loads(arguments) if isinstance(arguments, str) else None
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value
    _bump(meta, "bad_tool_arguments")
    return {}


def _convert_messages(
    messages: list[Any],
    meta: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(system blocks, messages)``: system / developer messages hoisted, roles merged, tool results grouped."""
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    ids = _Ids()
    seen_user = False
    reasoning_dropped = 0

    def append(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for message in messages or []:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role in ("system", "developer"):
            texts = [t for t in _text_of(message.get("content")) if t.strip()]
            system.extend({"type": "text", "text": t} for t in texts)
            if seen_user:
                _bump(meta, "system_hoisted")
            continue
        if role == "user":
            seen_user = True
            blocks = _content_blocks(message.get("content"), meta)
            if blocks:
                append("user", blocks)
            else:
                _bump(meta, "empty_messages_dropped")
            continue
        if role == "assistant":
            blocks = [{"type": "text", "text": t} for t in _text_of(message.get("content")) if t.strip()]
            ids.pending = []
            for call in message.get("tool_calls") or []:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": ids.use(call.get("id")),
                        "name": str(function.get("name") or ""),
                        "input": _tool_input(function.get("arguments"), meta),
                    },
                )
            reasoning_dropped += sum(
                1 for key in ("reasoning", "reasoning_content", "reasoning_details") if message.get(key)
            )
            if blocks:
                append("assistant", blocks)
            else:
                _bump(meta, "empty_messages_dropped")
            continue
        if role == "tool":
            result: dict[str, Any] = {"type": "tool_result", "tool_use_id": ids.result(message.get("tool_call_id"))}
            content = message.get("content")
            if isinstance(content, list):
                blocks = _content_blocks(content, meta)
                if blocks:
                    result["content"] = blocks
            elif isinstance(content, str):
                # A whitespace-only result (a command that printed "\n") is sent without content: Anthropic
                # rejects blank text, and an empty tool_result is valid.
                if content.strip():
                    result["content"] = content
            elif content is not None:
                text = json.dumps(content, ensure_ascii=False) if isinstance(content, Mapping) else str(content)
                if text.strip():
                    result["content"] = text
            append("user", [result])
            continue
        _bump(meta, "unsupported_messages")
    for message in out:
        if message["role"] == "user":
            # Anthropic wants the tool results first in the user message that answers a tool call.
            message["content"].sort(key=lambda block: block.get("type") != "tool_result")
    if reasoning_dropped:
        meta["reasoning_fields_dropped"] = reasoning_dropped
    return system, out


def _tool_blocks_as_text(messages: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    """Rewrite ``tool_use`` / ``tool_result`` blocks as text blocks in place (a request without tools).

    ``tool_use`` becomes ``[tool call <name>] <input JSON>``; ``tool_result`` becomes ``[tool result]``
    followed by its text (its image blocks are kept). Deterministic, so a repeated tool-less request still
    reads its own cache. Counted in ``meta["tool_blocks_as_text"]``.
    """
    converted = 0
    for message in messages:
        blocks: list[dict[str, Any]] = []
        for block in message["content"]:
            kind = block.get("type")
            if kind == "tool_use":
                arguments = json.dumps(block.get("input") or {}, ensure_ascii=False, sort_keys=True)
                blocks.append({"type": "text", "text": f"[tool call {block.get('name') or ''}] {arguments}"})
                converted += 1
            elif kind == "tool_result":
                content = block.get("content")
                parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
                texts = [str(p.get("text") or "") for p in parts if p.get("type") == "text"]
                body = "\n".join(t for t in texts if t.strip())
                blocks.append({"type": "text", "text": f"[tool result]\n{body}" if body else "[tool result]"})
                blocks.extend(p for p in parts if p.get("type") == "image")
                converted += 1
            else:
                blocks.append(block)
        message["content"] = blocks
    if converted:
        meta["tool_blocks_as_text"] = converted


def _tools(tools: Any) -> list[dict[str, Any]]:
    out = []
    for tool in tools or []:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
        name = function.get("name")
        if not name:
            continue
        schema = function.get("parameters")
        if not isinstance(schema, Mapping):
            schema = function.get("input_schema")
        entry: dict[str, Any] = {"name": str(name)}
        if function.get("description"):
            entry["description"] = str(function["description"])
        entry["input_schema"] = dict(schema) if isinstance(schema, Mapping) else {"type": "object", "properties": {}}
        out.append(entry)
    return out


def _tool_choice(
    choice: Any,
    parallel: Any,
    model_traits: ModelTraits,
    meta: dict[str, Any],
) -> dict[str, Any] | None:
    if choice in (None, "", "auto"):
        out: dict[str, Any] = {"type": "auto"}
        meta["tool_choice"] = "auto"
    elif choice == "none":
        meta["tool_choice"] = "none"
        return {"type": "none"}
    elif choice in ("required", "any"):
        if model_traits.forced_tool_choice:
            out = {"type": "any"}
            meta["tool_choice"] = "any"
        else:
            out = {"type": "auto"}
            meta["tool_choice"] = "auto (from required)"
    elif isinstance(choice, Mapping):
        function = choice.get("function") if isinstance(choice.get("function"), Mapping) else {}
        name = function.get("name") or choice.get("name")
        if not name:
            out = {"type": "auto"}
            meta["tool_choice"] = "auto"
        elif model_traits.forced_tool_choice:
            out = {"type": "tool", "name": str(name)}
            meta["tool_choice"] = f"tool:{name}"
        else:
            out = {"type": "auto"}
            meta["tool_choice"] = f"auto (from tool:{name})"
    else:
        out = {"type": "auto"}
        meta["tool_choice"] = f"auto (unknown {choice!r})"
    if parallel is False:
        out["disable_parallel_tool_use"] = True
    return out


def _requested_effort(params: Mapping[str, Any], default: str) -> tuple[str | None, str]:
    """``(requested effort, source)``: the policy's reasoning pin, then the harness, then the default."""
    reasoning = params.get("reasoning")
    if isinstance(reasoning, Mapping):
        if reasoning.get("effort"):
            return str(reasoning["effort"]).lower(), "policy"
        if reasoning.get("enabled") is False:
            return "none", "policy (reasoning disabled)"
        if reasoning.get("max_tokens"):
            return default, "llmlab_default (reasoning.max_tokens not expressible)"
    effort = params.get("reasoning_effort")
    if isinstance(effort, str) and effort:
        return effort.lower(), "harness"
    return default, "llmlab_default"


def _effort(requested: str | None, model_traits: ModelTraits, meta: dict[str, Any]) -> str | None:
    """The effort the model accepts: none/minimal become low (thinking cannot be disabled), an unsupported
    level takes the nearest lower supported one; None for a model without effort."""
    if not model_traits.efforts:
        return None
    value = requested or DEFAULT_EFFORT
    if value in ("none", "minimal", "off"):
        meta["effort_clamped"] = f"{value}->low"
        value = "low"
    if value not in EFFORT_LEVELS:
        meta["effort_clamped"] = f"{value}->{DEFAULT_EFFORT}"
        value = DEFAULT_EFFORT
    if value in model_traits.efforts:
        return value
    rank = EFFORT_LEVELS.index(value)
    lower = [e for e in model_traits.efforts if EFFORT_LEVELS.index(e) <= rank]
    chosen = lower[-1] if lower else model_traits.efforts[0]
    meta["effort_clamped"] = f"{value}->{chosen}"
    return chosen


def _mark_last(blocks: list[dict[str, Any]]) -> bool:
    if not blocks:
        return False
    blocks[-1]["cache_control"] = dict(EPHEMERAL)
    return True


def _apply_cache(system: list[dict[str, Any]], messages: list[dict[str, Any]], options: Options) -> dict[str, Any]:
    """The breakpoints (see the module doc); returns ``meta.cache``."""
    info: dict[str, Any] = {"prompt_cache": options.prompt_cache, "breakpoints": 0, "ttl": CACHE_TTL}
    if options.ttl_differs:
        info["ttl_overridden"] = f"{options.cache_ttl}->{CACHE_TTL}"
    if options.prompt_cache == "off":
        return info
    marked = 0
    if _mark_last(system):
        marked += 1
    if messages and _mark_last(messages[-1]["content"]):
        marked += 1
    # The previous request ended with the message just before the last assistant turn: mark it too, so the
    # read hits that exact prefix even when this turn added more blocks than the 20-block lookback.
    last_assistant = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    if 1 <= last_assistant < len(messages) - 1 and _mark_last(messages[last_assistant - 1]["content"]):
        marked += 1
    info["breakpoints"] = marked
    return info


def not_sent(params: Any) -> list[str]:
    """The chat params (names) the native route never sends to Anthropic (sampling knobs, seed, ...)."""
    return sorted({str(k) for k in params or ()} - _CONSUMED_PARAMS)


def planned_settings(served_name: str, *, reasoning: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What the native route will send for ``served_name``, frozen in the run manifest at plan time
    (``model.endpoint.native``), so a run records its Claude settings even after its call rows are purged.

    ``reasoning`` is the policy's ``ReasoningSettings`` (``mode``/``effort``/``max_tokens``): a pinned effort
    wins; otherwise the harness's own ``reasoning_effort`` (if it sends one) and then :data:`DEFAULT_EFFORT`
    are used at call time, and every call records the effort actually sent (``usage.anthropic.request``).
    """
    model_traits = traits(served_name)
    params: dict[str, Any] = {}
    if isinstance(reasoning, Mapping) and reasoning.get("mode") == "effort" and reasoning.get("effort"):
        params["reasoning"] = {"effort": reasoning["effort"]}
    elif isinstance(reasoning, Mapping) and reasoning.get("mode") == "max_tokens" and reasoning.get("max_tokens"):
        params["reasoning"] = {"max_tokens": reasoning["max_tokens"]}
    meta: dict[str, Any] = {}
    requested, source = _requested_effort(params, DEFAULT_EFFORT)
    effort = _effort(requested, model_traits, meta)
    out: dict[str, Any] = {
        "api": "anthropic_messages",
        "model_key": model_key(served_name) or "default",
        "effort": effort,
        "effort_source": source if effort is not None else "model has no effort",
        "effort_precedence": "policy reasoning.effort > harness reasoning_effort > llmlab_default",
        "default_effort": DEFAULT_EFFORT,
        "thinking": "adaptive" if model_traits.adaptive else "omitted",
        "thinking_replay": "dropped",
        "forced_tool_choice": model_traits.forced_tool_choice,
        "max_output_tokens": model_traits.max_output,
        "cache_ttl": CACHE_TTL,
    }
    if meta.get("effort_clamped"):
        out["effort_clamped"] = meta["effort_clamped"]
    return out


def to_messages_request(
    model: str,
    messages: list[dict[str, Any]],
    params: Mapping[str, Any] | None,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    parallel_tool_calls: Any = None,
    options: Options | None = None,
) -> dict[str, Any]:
    """The Messages request for a canonical chat body (``params`` = sampling/LLM params, seed included).

    The returned body carries :data:`META_KEY` (what was mapped, stripped and chosen), which
    ``upstream.call`` pops before sending and copies into ``usage.anthropic.request``.
    """
    options = options or Options()
    params = dict(params or {})
    model_traits = traits(model)
    meta: dict[str, Any] = {"api": "anthropic_messages", "model_key": model_key(model) or "default"}
    system, converted = _convert_messages(messages, meta)
    body: dict[str, Any] = {"model": model}
    tool_defs = _tools(tools if tools is not None else params.get("tools"))
    if tool_defs:
        body["tools"] = tool_defs
    else:
        # Anthropic answers 400 to tool_use / tool_result blocks in a request that defines no tools (a
        # harness's compaction or summary call sends the transcript without tools): render them as text.
        _tool_blocks_as_text(converted, meta)
    if system:
        body["system"] = system
    body["messages"] = converted
    wanted = params.get("max_tokens") or params.get("max_completion_tokens") or DEFAULT_MAX_TOKENS
    try:
        wanted = int(wanted)
    except (TypeError, ValueError):
        wanted = DEFAULT_MAX_TOKENS
    body["max_tokens"] = max(1, min(wanted, model_traits.max_output))
    if body["max_tokens"] != wanted:
        meta["max_tokens_clamped"] = f"{wanted}->{body['max_tokens']}"
    stop = params.get("stop")
    stops = [stop] if isinstance(stop, str) else list(stop or []) if isinstance(stop, (list, tuple)) else []
    stops = [s for s in stops if isinstance(s, str) and s.strip()]
    if stops:
        body["stop_sequences"] = stops
    if tool_defs:
        body["tool_choice"] = _tool_choice(tool_choice, parallel_tool_calls, model_traits, meta)
    if model_traits.adaptive:
        body["thinking"] = {"type": "adaptive"}
        meta["thinking"] = "adaptive"
    else:
        meta["thinking"] = "omitted"
    meta["thinking_replay"] = "dropped"
    requested, source = _requested_effort(params, options.default_effort)
    effort = _effort(requested, model_traits, meta)
    output_config: dict[str, Any] = {}
    if effort is not None:
        output_config["effort"] = effort
    meta["effort"] = effort
    meta["effort_source"] = source if effort is not None else "model has no effort"
    stripped = sorted(k for k, v in params.items() if k not in _CONSUMED_PARAMS and v is not None)
    response_format = params.get("response_format")
    if isinstance(response_format, Mapping):
        schema = (response_format.get("json_schema") or {}).get("schema")
        if response_format.get("type") == "json_schema" and isinstance(schema, Mapping):
            output_config["format"] = {"type": "json_schema", "schema": dict(schema)}
        else:
            stripped.append("response_format")
    if output_config:
        body["output_config"] = output_config
    if stripped:
        meta["stripped"] = sorted(stripped)
    meta["cache"] = _apply_cache(system, converted, options)
    body["stream"] = True
    body[META_KEY] = meta
    return body


# =====================================================================================
# Response: Messages message -> chat.completion
# =====================================================================================


def finish_reason(stop_reason: Any) -> str:
    """The OpenAI ``finish_reason`` of an Anthropic ``stop_reason`` (unknown or missing: ``stop``)."""
    return _STOP_REASONS.get(str(stop_reason or ""), "stop")


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def chat_usage(native: Mapping[str, Any] | None, *, completion_tokens: int | None = None) -> dict[str, Any]:
    """The OpenAI-shaped usage of a native ``usage``: ``prompt_tokens`` counts every input token (uncached,
    cache writes and cache reads); reads go to ``cached_tokens`` and writes to ``cache_write_tokens``. No
    ``cost`` and no ``reasoning_tokens``: Anthropic reports neither, and absent means unknown, never zero."""
    native = native or {}
    uncached = _int(native.get("input_tokens"))
    writes = _int(native.get("cache_creation_input_tokens"))
    reads = _int(native.get("cache_read_input_tokens"))
    output = _int(native.get("output_tokens")) if completion_tokens is None else int(completion_tokens)
    prompt = uncached + writes + reads
    return {
        "prompt_tokens": prompt,
        "completion_tokens": output,
        "total_tokens": prompt + output,
        "prompt_tokens_details": {"cached_tokens": reads, "cache_write_tokens": writes},
    }


def _message_out(blocks: list[Mapping[str, Any]]) -> tuple[dict[str, Any], int]:
    """``(assistant message, thinking blocks)`` of native content blocks."""
    groups: list[str] = []
    joined = False  # the previous block was text: concatenate
    calls = []
    thinking = 0
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            text = str(block.get("text") or "")
            if joined and groups:
                groups[-1] += text
            else:
                groups.append(text)
            joined = True
            continue
        joined = False
        if kind == "tool_use":
            calls.append(
                {
                    "id": str(block.get("id") or f"call_{len(calls)}"),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                },
            )
        elif kind in ("thinking", "redacted_thinking"):
            thinking += 1
    content = "\n\n".join(g for g in groups if g)
    message: dict[str, Any] = {"role": "assistant", "content": content or (None if calls else "")}
    if calls:
        message["tool_calls"] = calls
    return message, thinking


def to_chat_completion(
    message: Mapping[str, Any],
    *,
    meta: Mapping[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A native Messages ``message`` as an OpenAI ``chat.completion``; the raw native usage, the request
    metadata, ``stop_reason`` and ``stop_details`` are kept under ``usage.anthropic``. ``usage`` overrides
    the OpenAI-shaped usage (a cut or estimated stream)."""
    blocks = [b for b in message.get("content") or [] if isinstance(b, Mapping)]
    out_message, thinking = _message_out(blocks)
    stop_reason = message.get("stop_reason")
    native_usage = dict(message.get("usage") or {})
    out_usage = usage if usage is not None else chat_usage(native_usage)
    out_usage["anthropic"] = {
        "id": message.get("id"),
        "usage": native_usage,
        "request": dict(meta or {}),
        "stop_reason": stop_reason,
        "stop_details": message.get("stop_details"),
        "thinking_blocks": thinking,
    }
    choice: dict[str, Any] = {
        "index": 0,
        "message": out_message,
        "finish_reason": finish_reason(stop_reason),
        "native_finish_reason": stop_reason,
    }
    out: dict[str, Any] = {"object": "chat.completion", "choices": [choice], "usage": out_usage}
    if message.get("id"):
        out["id"] = f"chatcmpl-{message['id']}"
    if message.get("model"):
        out["model"] = message["model"]
    return out


# =====================================================================================
# SSE stream
# =====================================================================================


def _complete_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class MessageStream:
    """Re-assembles a Messages SSE stream into one ``chat.completion``.

    Same interface as ``upstream.ChatStream`` (``feed``, ``has_output``, ``chars``, ``finish_reason``,
    ``done``, ``error``, ``completion``, ``estimated_usage``), so ``upstream._read_chat_stream`` applies the
    same cut, wall-clock, size and read-timeout rules. Thinking text counts as output (it is empty while
    ``display`` is omitted), and so does a started thinking block: like a streamed reasoning model on the
    OpenAI route, a call that thinks past the wall clock is a ``length`` cut (model-attributed), not an
    infrastructure timeout. ``max_tokens`` (the request's) bounds the hidden thinking of a cut stream.
    """

    def __init__(self, meta: Mapping[str, Any] | None = None, *, max_tokens: int | None = None) -> None:
        self.request_meta = dict(meta or {})
        self.max_tokens = max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else None
        self.thinking_blocks = 0
        self.message: dict[str, Any] = {}
        self.blocks: dict[int, dict[str, Any]] = {}
        self.json_parts: dict[int, list[str]] = {}
        self.usage: dict[str, Any] = {}
        self.final_usage = False
        self.stop_reason: str | None = None
        self.stop_details: Any = None
        self.finish_reason: str | None = None
        self.error: dict[str, Any] | None = None
        self.chars = 0
        self.thinking_chars = 0
        self.done = False

    @property
    def has_output(self) -> bool:
        return self.chars > 0 or self.thinking_blocks > 0

    def output_tokens(self) -> int:
        return math.ceil(self.chars / OUTPUT_CHARS_PER_TOKEN)

    def feed(self, event: Any) -> None:
        if not isinstance(event, Mapping):
            return
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") if isinstance(event.get("message"), Mapping) else {}
            self.message = {k: message.get(k) for k in ("id", "model") if message.get(k) is not None}
            if isinstance(message.get("usage"), Mapping):
                self.usage = dict(message["usage"])
        elif kind == "content_block_start":
            index = _int(event.get("index"))
            block = dict(event.get("content_block") or {})
            if block.get("type") == "tool_use":
                block["input"] = {}
                self.json_parts[index] = []
            elif block.get("type") == "text":
                self.chars += len(str(block.get("text") or ""))
            elif block.get("type") in ("thinking", "redacted_thinking"):
                self.thinking_blocks += 1
            self.blocks[index] = block
        elif kind == "content_block_delta":
            index = _int(event.get("index"))
            block = self.blocks.setdefault(index, {"type": "text", "text": ""})
            delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = str(delta.get("text") or "")
                block["text"] = str(block.get("text") or "") + text
                self.chars += len(text)
            elif dtype == "input_json_delta":
                part = str(delta.get("partial_json") or "")
                self.json_parts.setdefault(index, []).append(part)
                self.chars += len(part)
            elif dtype == "thinking_delta":
                text = str(delta.get("thinking") or "")
                self.thinking_chars += len(text)
                self.chars += len(text)
        elif kind == "content_block_stop":
            index = _int(event.get("index"))
            block = self.blocks.get(index)
            if block is not None and block.get("type") == "tool_use":
                parsed = _complete_json("".join(self.json_parts.get(index) or []))
                block["input"] = parsed if parsed is not None else {}
                block["_complete"] = parsed is not None
        elif kind == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), Mapping) else {}
            if delta.get("stop_reason"):
                self.stop_reason = str(delta["stop_reason"])
                self.finish_reason = finish_reason(self.stop_reason)
            if delta.get("stop_details") is not None:
                self.stop_details = delta["stop_details"]
            usage = event.get("usage")
            if isinstance(usage, Mapping):
                self.usage.update({k: v for k, v in usage.items() if v is not None})
                if usage.get("output_tokens") is not None:
                    self.final_usage = True
        elif kind == "message_stop":
            self.done = True
        elif kind == "error":
            error = event.get("error") if isinstance(event.get("error"), Mapping) else {}
            etype = str(error.get("type") or "stream_error")
            self.error = {"error": {"message": str(error.get("message") or etype), "type": etype, "code": etype}}

    def _content(self) -> tuple[list[dict[str, Any]], int]:
        """The finished content blocks and how many tool calls were dropped.

        A tool call whose arguments did not complete (llmlab's own cut, or Anthropic's ``max_tokens`` stop in
        the middle of an ``input_json_delta``) is dropped, never handed to the harness with ``{}`` arguments:
        the harness would run a call the model never finished and replay it in the history.
        """
        out = []
        dropped = 0
        for index in sorted(self.blocks):
            block = dict(self.blocks[index])
            if block.get("type") == "tool_use":
                complete = block.pop("_complete", None)
                if complete is None:  # never stopped: parse what arrived
                    parsed = _complete_json("".join(self.json_parts.get(index) or []))
                    complete = parsed is not None
                    block["input"] = parsed or {}
                if not complete:
                    dropped += 1
                    continue
            out.append(block)
        return out, dropped

    def estimated_completion_tokens(self) -> int:
        """Output tokens of a stream without final usage: the visible text estimate, or, once a thinking block
        started, the request's ``max_tokens`` (thinking is billed but not streamed while ``display`` is
        omitted, so the visible text is only a lower bound; the bound keeps the budgets from under-booking,
        the same rule as ``budget_rules`` settling a usage-less call at its reservation)."""
        visible = self.output_tokens()
        if self.thinking_blocks and self.max_tokens is not None:
            return max(visible, self.max_tokens)
        return visible

    def estimated_usage(self, limits: Any) -> dict[str, Any]:
        """Usage with the exact input split from ``message_start`` (the limits' prompt estimate when there
        was none) and :meth:`estimated_completion_tokens`, marked ``llmlab_estimated``."""
        completion = self.estimated_completion_tokens()
        if any(k in self.usage for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            usage = chat_usage(self.usage, completion_tokens=completion)
        else:
            prompt = int(getattr(limits, "prompt_tokens_estimate", 0) or 0)
            usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
        usage["llmlab_estimated"] = True
        if self.thinking_blocks and completion > self.output_tokens():
            usage["llmlab_estimate_basis"] = "max_tokens (hidden thinking)"
        return usage

    def completion(self, limits: Any, cut: str | None = None, elapsed_s: float = 0.0) -> dict[str, Any]:
        """The ``chat.completion``; ``cut`` (``max_output_tokens`` | ``wall_clock``) ends it at ``length``."""
        content, dropped = self._content()
        native = {
            **self.message,
            "content": content,
            "stop_reason": self.stop_reason,
            "stop_details": self.stop_details,
            "usage": dict(self.usage),
        }
        usage = self.estimated_usage(limits) if cut or not self.final_usage else None
        payload = to_chat_completion(native, meta=self.request_meta, usage=usage)
        choice = payload["choices"][0]
        if dropped:
            payload["usage"]["anthropic"]["dropped_tool_calls"] = dropped
            if choice["finish_reason"] == "tool_calls" and not choice["message"].get("tool_calls"):
                choice["finish_reason"] = "length"
        if cut:
            choice["finish_reason"] = "length"
            choice["native_finish_reason"] = f"llmlab_cut:{cut}"
            payload["llmlab_cut"] = {
                "reason": cut,
                "elapsed_s": round(elapsed_s, 3),
                "max_output_tokens": getattr(limits, "max_output_tokens", None),
                "wall_clock_s": getattr(limits, "wall_clock_s", None),
                "est_completion_tokens": payload["usage"]["completion_tokens"],
                "output_chars": self.chars,
                "dropped_tool_calls": dropped,
            }
        return payload


# =====================================================================================
# Errors
# =====================================================================================


def error_payload(status: int, payload: Any, text: str) -> dict[str, Any]:
    """An Anthropic error answer in the OpenAI error shape (the HTTP status is kept by the caller).

    ``{"type": "error", "error": {"type", "message"}}`` → ``{"error": {"message", "type", "code"},
    "anthropic_error": <raw>}``. A 400 ``prompt is too long`` gets ``code="context_length_exceeded"``.
    """
    error = payload.get("error") if isinstance(payload, Mapping) else None
    error = error if isinstance(error, Mapping) else {}
    etype = str(error.get("type") or f"http_{status}")
    message = str(error.get("message") or (text or "")[:2000] or etype)
    code = etype
    if status == 400 and message.lower().startswith("prompt is too long"):
        code = "context_length_exceeded"
    out: dict[str, Any] = {"error": {"message": message, "type": etype, "code": code}}
    if payload is not None:
        out["anthropic_error"] = payload
    return out
