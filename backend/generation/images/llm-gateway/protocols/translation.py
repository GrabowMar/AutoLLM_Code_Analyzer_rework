"""Shared machinery of the ``translate`` mode (spec 5.4 rule 2-4, 6.6): native <-> canonical chat.

The canonical internal format is OpenAI chat completions (OpenRouter's dialect). A front module's
translator maps its native request to a chat request (``to_chat``), a chat completion back to its native
response (``from_chat``) and a chat SSE stream to its native SSE stream (``StreamTranslator`` /
``stream_from_chat``). This module holds what the three translators share:

* :class:`Dropped`, the per-call ``translation_dropped`` counts (field -> count; only what the native
  shape could not carry, never what the policy changes afterwards);
* **reasoning continuity**: OpenRouter ``reasoning_details`` (thinking text, summaries, signatures,
  encrypted blobs) are carried through native fields that a harness replays verbatim (Anthropic
  ``thinking.signature`` / ``redacted_thinking.data``, Responses ``reasoning.encrypted_content``, Gemini
  ``thoughtSignature``) as ``llmlab1.<base64url(JSON)>`` tokens, so the next request restores exactly the
  details the upstream sent (:func:`encode_token` / :func:`decode_token`, :func:`merge_details`). A
  signature that is not one of ours (a native one) is kept as an encrypted detail of the front's format;
* :class:`ChatStreamReader`, which turns chat SSE bytes into normalized delta events, and
  :func:`completion_events`, which produces the same events from a non-streamed completion, so one
  translator state machine serves both;
* :class:`ChatTurn`, the aggregate of one assistant turn (text, reasoning, tool calls, finish, usage).

Stdlib only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from protocols.common import SSEParser
from protocols.common import json_or_none

TOKEN_PREFIX = "llmlab1."  # noqa: S105 - a format marker, not a secret
# Detail keys whose string values are concatenated when stream fragments of one detail are merged.
_CONCAT_KEYS = ("text", "summary")
# A detail synthesized from a plain ``reasoning`` string (no ``reasoning_details`` from the upstream):
# it is replayed as ``reasoning`` text, never as a detail.
SYNTHETIC = "synthetic"
# Finish reasons of the canonical format.
CHAT_FINISH = ("stop", "length", "tool_calls", "content_filter", "error")
STREAM_ENDED_EARLY = "upstream stream ended before the turn finished"
FINISHED_WITH_ERROR = "upstream ended the turn with finish_reason=error"


class TranslationError(ValueError):
    """A native request the translator cannot express in canonical chat (400 ``unsupported_request``)."""


# =====================================================================================
# Drop accounting
# =====================================================================================


class Dropped:
    """``translation_dropped``: counts by field of what the native shape could not carry."""

    def __init__(self, data: Mapping[str, int] | None = None) -> None:
        self.data: dict[str, int] = dict(data or {})

    def add(self, key: str, n: int = 1) -> None:
        if n > 0:
            self.data[key] = self.data.get(key, 0) + n

    def merge(self, other: Mapping[str, int] | None) -> None:
        for key, n in (other or {}).items():
            self.add(key, int(n))

    def __bool__(self) -> bool:
        return bool(self.data)


def as_dropped(value: Dropped | Mapping[str, int] | None) -> Dropped:
    if isinstance(value, Dropped):
        return value
    return Dropped(value)


# =====================================================================================
# Reasoning tokens and details
# =====================================================================================


def encode_token(value: Any) -> str:
    """``llmlab1.<base64url(compact JSON)>``: an opaque native signature that carries ``value``."""
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return TOKEN_PREFIX + base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def decode_token(token: Any) -> Any:
    """The value carried by an :func:`encode_token` token, or None for anything else."""
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
        return None
    raw = token[len(TOKEN_PREFIX) :]
    try:
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError):
        return None
    return json_or_none(data)


def merge_details(fragments: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge streamed ``reasoning_details`` fragments into whole details.

    Fragments with the same ``index`` and ``type`` belong to one detail: ``text`` / ``summary`` are
    concatenated, other keys (``signature``, ``data``, ``id``, ``format``) take the latest non-empty
    value. Fragments without an ``index`` merge with the previous detail of the same type when that one
    has no index either; otherwise they start a new detail. Order of first appearance is kept.
    """
    out: list[dict[str, Any]] = []
    by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for fragment in fragments:
        if not isinstance(fragment, Mapping):
            continue
        kind = fragment.get("type")
        index = fragment.get("index")
        target: dict[str, Any] | None = None
        if index is not None:
            target = by_key.get((kind, index))
        elif out and out[-1].get("type") == kind and out[-1].get("index") is None:
            # An index-less continuation only merges into a detail that has not been closed by a signature.
            last = out[-1]
            if not last.get("signature") and not last.get("data"):
                target = last
        if target is None:
            target = {}
            out.append(target)
            if index is not None:
                by_key[(kind, index)] = target
        for key, value in fragment.items():
            if key in _CONCAT_KEYS and isinstance(value, str):
                target[key] = str(target.get(key) or "") + value
            elif value is not None and value != "":
                target[key] = value
            else:
                target.setdefault(key, value)
    return out


def detail_text(detail: Mapping[str, Any]) -> str:
    """The human-readable reasoning of a detail (thinking text or summary; empty for encrypted)."""
    for key in ("text", "summary"):
        value = detail.get(key)
        if isinstance(value, str):
            return value
    return ""


def details_text(details: Iterable[Mapping[str, Any]]) -> str:
    return "".join(detail_text(d) for d in details)


def synthetic_detail(text: str) -> dict[str, Any]:
    return {"type": "reasoning.text", "text": text, SYNTHETIC: True}


def assistant_reasoning(message: dict[str, Any], details: list[dict[str, Any]]) -> None:
    """Put reasoning on a canonical assistant message: real details as ``reasoning_details``; when only
    synthetic ones exist (a plain ``reasoning`` string came back), their text as ``reasoning``."""
    real = [{k: v for k, v in d.items() if k != SYNTHETIC} for d in details if not d.get(SYNTHETIC)]
    if real:
        message["reasoning_details"] = real
        return
    text = details_text(details)
    if text:
        message["reasoning"] = text


# =====================================================================================
# Tool calls
# =====================================================================================


def parse_arguments(arguments: Any) -> tuple[Any, bool]:
    """``(value, ok)``: a tool call's JSON ``arguments`` string as a value; ``ok`` False when it is
    not valid JSON (the raw string is then returned)."""
    if isinstance(arguments, Mapping | list):
        return arguments, True
    if arguments is None or arguments == "":
        return {}, True
    parsed = json_or_none(arguments) if isinstance(arguments, str) else None
    if parsed is None and arguments != "null":
        return arguments, False
    return parsed, True


def dump_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps({} if value is None else value, ensure_ascii=False)


@dataclass
class ToolCall:
    index: int
    id: str | None = None
    name: str = ""
    arguments: str = ""


@dataclass
class ChatTurn:
    """One assistant turn of the canonical format, as a whole."""

    id: str | None = None
    model: str | None = None
    provider: str | None = None
    created: int | None = None
    content: str = ""
    refusal: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    error: Any = None

    @property
    def reasoning_text(self) -> str:
        return details_text(self.details)


def tool_call_id(call_id: Any, prefix: str, n: int) -> str:
    """The call's id, or a deterministic one (``<prefix><n>``) when the upstream sent none."""
    return str(call_id) if call_id else f"{prefix}{n}"


# =====================================================================================
# Canonical stream events
# =====================================================================================

# Event kinds produced by ChatStreamReader and completion_events, as tuples:
# "meta" + {id, model, provider, created} (first sighting of response metadata); "reasoning" + text
# (plain reasoning, no details in that chunk); "details" + [fragments] (reasoning_details); "content" +
# text; "refusal" + text; "tool" + index, id or None, name or None, arguments fragment; "finish" +
# finish_reason; "usage" + usage; "error" + error.


class ChatStreamReader:
    """Chat completion SSE bytes -> canonical stream events (see the list above)."""

    def __init__(self) -> None:
        self._parser = SSEParser()
        self._meta_sent = False
        self.done = False

    def feed(self, chunk: bytes) -> list[tuple[Any, ...]]:
        events: list[tuple[Any, ...]] = []
        for _name, data in self._parser.feed(chunk):
            if not data:
                continue
            if data.strip() == "[DONE]":
                self.done = True
                continue
            payload = json_or_none(data)
            if isinstance(payload, Mapping):
                events.extend(self._payload(payload))
        return events

    def _payload(self, payload: Mapping[str, Any]) -> list[tuple[Any, ...]]:
        events: list[tuple[Any, ...]] = []
        if not self._meta_sent and (payload.get("id") or payload.get("model")):
            self._meta_sent = True
            events.append(("meta", {k: payload.get(k) for k in ("id", "model", "provider", "created")}))
        if payload.get("error"):
            events.append(("error", payload["error"]))
        for choice in payload.get("choices") or []:
            if not isinstance(choice, Mapping) or int(choice.get("index") or 0) != 0:
                continue
            delta = choice.get("delta") or choice.get("message") or {}
            events.extend(delta_events(delta))
            if choice.get("error") and not payload.get("error"):
                events.append(("error", choice["error"]))  # OpenRouter's mid-generation error on the choice
            if choice.get("finish_reason"):
                events.append(("finish", choice["finish_reason"]))
        if payload.get("usage"):
            events.append(("usage", dict(payload["usage"])))
        return events


def delta_events(delta: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """The events of one chat delta (or a whole message: it has the same keys)."""
    events: list[tuple[Any, ...]] = []
    details = delta.get("reasoning_details")
    if isinstance(details, list) and details:
        events.append(("details", [dict(d) for d in details if isinstance(d, Mapping)]))
    else:
        text = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(text, str) and text:
            events.append(("reasoning", text))
    if isinstance(delta.get("content"), str) and delta["content"]:
        events.append(("content", delta["content"]))
    if isinstance(delta.get("refusal"), str) and delta["refusal"]:
        events.append(("refusal", delta["refusal"]))
    for position, tc in enumerate(delta.get("tool_calls") or []):
        if not isinstance(tc, Mapping):
            continue
        fn = tc.get("function") or {}
        index = tc.get("index")
        arguments = fn.get("arguments")
        if arguments is not None and not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        events.append(
            ("tool", position if index is None else int(index), tc.get("id"), fn.get("name"), arguments or ""),
        )
    return events


def completion_events(payload: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    """A non-streamed chat completion as the same events a stream of it would produce."""
    events: list[tuple[Any, ...]] = [("meta", {k: payload.get(k) for k in ("id", "model", "provider", "created")})]
    if payload.get("error"):
        events.append(("error", payload["error"]))
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
    events.extend(delta_events(choice.get("message") or {}))
    if choice.get("error") and not payload.get("error"):
        events.append(("error", choice["error"]))
    if choice.get("finish_reason"):
        events.append(("finish", choice["finish_reason"]))
    if payload.get("usage"):
        events.append(("usage", dict(payload["usage"])))
    return events


class TurnBuilder:
    """Aggregates canonical events into a :class:`ChatTurn`."""

    def __init__(self) -> None:
        self.turn = ChatTurn()
        self._fragments: list[dict[str, Any]] = []
        self._plain: list[str] = []
        self._tools: dict[int, ToolCall] = {}

    def feed(self, event: tuple[Any, ...]) -> None:
        kind = event[0]
        t = self.turn
        if kind == "meta":
            meta = event[1]
            t.id = t.id or meta.get("id")
            t.model = t.model or meta.get("model")
            t.provider = t.provider or meta.get("provider")
            t.created = t.created or meta.get("created")
        elif kind == "reasoning":
            self._plain.append(event[1])
        elif kind == "details":
            self._fragments.extend(event[1])
        elif kind == "content":
            t.content += event[1]
        elif kind == "refusal":
            t.refusal += event[1]
        elif kind == "tool":
            _, index, call_id, name, arguments = event
            call = self._tools.get(index)
            if call is None:
                call = self._tools[index] = ToolCall(index=index)
            if call_id:
                call.id = call_id
            if name:
                call.name += name
            call.arguments += arguments or ""
        elif kind == "finish":
            t.finish_reason = event[1]
        elif kind == "usage":
            t.usage = event[1]
        elif kind == "error":
            t.error = event[1]

    def finish(self) -> ChatTurn:
        t = self.turn
        t.details = merge_details(self._fragments)
        if not t.details and self._plain:
            t.details = [synthetic_detail("".join(self._plain))]
        t.tool_calls = [self._tools[i] for i in sorted(self._tools)]
        return t


def turn_from_completion(payload: Mapping[str, Any]) -> ChatTurn:
    builder = TurnBuilder()
    for event in completion_events(payload):
        builder.feed(event)
    return builder.finish()


def iterate_stream(translator: Any, chat_sse_iter: Iterable[bytes | None], ping: bytes) -> Iterator[bytes]:
    """Drive a ``StreamTranslator`` over an iterator of chat SSE chunks (``stream_from_chat``).

    A ``None`` item means "the upstream has been silent for one ping interval": the native keepalive is
    yielded (always between native events, since the translator emits whole events only).
    """
    for chunk in chat_sse_iter:
        if chunk is None:
            yield ping
            continue
        out = translator.feed(chunk)
        if out:
            yield out
    tail = translator.finish()
    if tail:
        yield tail


# =====================================================================================
# Small helpers shared by the fronts
# =====================================================================================


def chat_error_text(payload: Any) -> tuple[str, str | None]:
    """``(message, code)`` of a chat error payload (``{"error": {...}}`` or a bare error object)."""
    err = payload.get("error") if isinstance(payload, Mapping) and "error" in payload else payload
    if isinstance(err, Mapping):
        message = err.get("message") or json.dumps(dict(err))[:500]
        code = err.get("code")
        return str(message), None if code is None else str(code)
    if isinstance(err, str):
        return err, None
    return "upstream error", None


def completion_failure(payload: Any) -> tuple[str, str | None] | None:
    """``(message, code)`` when a non-streamed chat completion is a failed turn, else ``None``.

    A failed turn is an error object (top level, or OpenRouter's ``choices[0].error``) or
    ``finish_reason == "error"``, even with partial content: every front answers it with its native error
    (as a stream that ends that way does), never with a normal-looking answer."""
    if not isinstance(payload, Mapping):
        return None
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
    err = payload.get("error") or choice.get("error")
    if err:
        return chat_error_text({"error": err})
    if choice.get("finish_reason") == "error":
        native = choice.get("native_finish_reason")
        message = f"{FINISHED_WITH_ERROR} ({native})" if native and native != "error" else FINISHED_WITH_ERROR
        return message, None
    return None


CONTEXT_OVERFLOW_HINTS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "reduce the length",
    "input is too long",
)


def looks_like_context_overflow(message: str, code: str | None) -> bool:
    """Whether an upstream error says the prompt does not fit (mapped to the front's own wording so the
    harness compacts, as it would against the native API)."""
    if code and "context_length" in code:
        return True
    low = message.lower()
    return any(hint in low for hint in CONTEXT_OVERFLOW_HINTS)


def text_of_parts(parts: Any, *, keys: tuple[str, ...] = ("text",)) -> str:
    """Concatenated text of a list of native content parts (or a string)."""
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, Mapping):
            for key in keys:
                if isinstance(part.get(key), str):
                    out.append(part[key])
                    break
    return "".join(out)


def turn_failure(turn: ChatTurn, failure: Any = None) -> tuple[str, str | None] | None:
    """``(message, code)`` when a streamed turn failed (transport ``failure``, an error event,
    ``finish_reason == "error"`` or no finish at all), else ``None``."""
    problem = failure or turn.error
    if problem:
        return chat_error_text({"error": problem})
    if turn.finish_reason == "error":
        return FINISHED_WITH_ERROR, None
    if turn.finish_reason is None:
        return STREAM_ENDED_EARLY, None
    return None


def finish_from_chat(turn: ChatTurn) -> str:
    """The canonical finish of a turn (tool calls make an unspecified or ``stop`` finish ``tool_calls``)."""
    finish = turn.finish_reason or ("error" if turn.error else "stop")
    if turn.tool_calls and finish in ("stop", None):
        return "tool_calls"
    return finish


def completion_sse(payload: Mapping[str, Any]) -> bytes:
    """A whole chat completion as the chat SSE stream of it (one chunk, then ``[DONE]``)."""
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
    chunk: dict[str, Any] = {
        k: payload[k] for k in ("id", "model", "provider", "created") if payload.get(k) is not None
    }
    out_choice: dict[str, Any] = {
        "index": 0,
        "delta": dict(choice.get("message") or {}),
        "finish_reason": choice.get("finish_reason"),
    }
    if choice.get("error"):
        out_choice["error"] = choice["error"]
    chunk["choices"] = [out_choice]
    if payload.get("usage"):
        chunk["usage"] = payload["usage"]
    if payload.get("error"):
        chunk["error"] = payload["error"]
    return b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"
