"""Gemini ``generateContent`` front (Gemini CLI): ``translate`` only (wave 3).

There is no OpenRouter-native Gemini endpoint, so this front always translates to canonical chat:

* routes: ``POST .../models/{m}:generateContent`` (one answer), ``:streamGenerateContent`` (``?alt=sse``:
  ``data: {...}\\r\\n\\r\\n`` events; without it a streamed JSON array), ``:countTokens`` answered locally
  (an estimate, never a billable call) and ``GET .../models``;
* request: ``systemInstruction`` -> system message, ``contents`` (``user`` / ``model`` turns with
  ``text``, ``thought`` parts, ``functionCall`` / ``functionResponse``) -> chat messages, ``tools[].
  functionDeclarations`` -> function tools (OpenAPI-style upper-case types lowered), ``toolConfig`` ->
  ``tool_choice``, ``generationConfig`` -> sampling, ``max_tokens``, ``stop``, ``seed``,
  ``response_format`` and ``thinkingConfig`` -> ``reasoning``;
* reasoning continuity: the turn's ``reasoning_details`` and the emitted call ids travel in a
  ``thoughtSignature`` (an ``llmlab1.`` token of ``{"details", "calls"}``) on the first ``functionCall``
  part (else the first answer text part, else an empty text part), which Gemini CLI keeps in its history
  even though it drops ``thought`` parts; on replay detail ids are rebound by position to the call ids
  Gemini CLI kept (it rewrites them); a native signature is kept as a ``reasoning.encrypted`` detail of
  format ``google-gemini-v1``;
* answers: ``candidates[0].content.parts`` (thought text only when ``includeThoughts``), ``finishReason``,
  ``usageMetadata`` (``candidatesTokenCount`` excludes thoughts, as Gemini reports it).

Errors use Google's shape ``{"error": {"code", "message", "status"}}``. In a started stream an error is
written as that bare JSON object, which the Gemini SDK detects in the chunk and raises as an API error.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import unquote

from protocols import budget_rules
from protocols.common import content_text
from protocols.translation import STREAM_ENDED_EARLY
from protocols.translation import ChatStreamReader
from protocols.translation import ChatTurn
from protocols.translation import Dropped
from protocols.translation import ToolCall
from protocols.translation import TurnBuilder
from protocols.translation import as_dropped
from protocols.translation import assistant_reasoning
from protocols.translation import chat_error_text
from protocols.translation import decode_token
from protocols.translation import details_text
from protocols.translation import encode_token
from protocols.translation import finish_from_chat
from protocols.translation import iterate_stream
from protocols.translation import looks_like_context_overflow
from protocols.translation import merge_details
from protocols.translation import parse_arguments
from protocols.translation import synthetic_detail
from protocols.translation import tool_call_id
from protocols.translation import turn_from_completion

NAME = "gemini_generate"
UPSTREAM_PATH = None  # translate-only: the upstream is canonical chat
PING = b"\r\n"  # whitespace between events: valid in both the SSE and the JSON-array framing
TRANSLATED_FORMAT = "google-gemini-v1"
_STATUS = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    413: "INVALID_ARGUMENT",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    502: "UNAVAILABLE",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}
CHAT_TO_FINISH = {
    "stop": "STOP",
    "tool_calls": "STOP",
    "length": "MAX_TOKENS",
    "content_filter": "SAFETY",
    "error": "OTHER",
}
_MODEL_IN_PATH = re.compile(r"/models/([^/:]+):")
# generationConfig keys mapped to chat keys verbatim (renamed).
_GENERATION_KEYS = {
    "temperature": "temperature",
    "topP": "top_p",
    "topK": "top_k",
    "maxOutputTokens": "max_tokens",
    "seed": "seed",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
}
# generationConfig keys that never change what the model generates as text.
_IGNORED_GENERATION_KEYS = frozenset({"responseModalities", "mediaResolution", "candidateCount"})
# Request keys that never change what the model sees (routing, labels, telemetry).
IGNORED_KEYS = frozenset({"model", "labels"})


class TranslatorUnavailableError(NotImplementedError):
    """Kept for callers that guard on it; the translator is available since wave 3 (never raised)."""


def route(method: str, path: str) -> str | None:
    clean = path.rstrip("/")
    if method == "POST" and clean.endswith(":countTokens"):
        return "count_tokens"
    if method == "POST" and clean.endswith((":generateContent", ":streamGenerateContent")):
        return "call"
    if method == "GET" and clean.endswith("/models"):
        return "models"
    return None


def request_options(path: str, query: str = "") -> dict[str, Any]:
    """What the URL says about the call: ``stream``, the SSE framing (``alt=sse``) and the model."""
    clean = path.rstrip("/")
    match = _MODEL_IN_PATH.search(clean + (":" if not clean.endswith(":") else ""))
    alt = (parse_qs(query).get("alt") or [""])[0]
    return {
        "stream": clean.endswith(":streamGenerateContent"),
        "sse": alt == "sse",
        "model": unquote(match.group(1)) if match else None,
    }


# =====================================================================================
# Errors and local answers
# =====================================================================================


def error(kind: str, message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    """Google's error shape (the status string follows the HTTP status; ``kind`` is logged, not sent)."""
    return status, {"error": {"code": status, "message": message, "status": _STATUS.get(status, "UNKNOWN")}}


def context_error(est_tokens: int, cap: int) -> tuple[int, dict[str, Any]]:
    """The ``call.max_input_tokens`` refusal in Gemini's own wording, so the CLI compresses its history."""
    message = f"The input token count ({est_tokens}) exceeds the maximum number of tokens allowed ({cap})."
    return error("context_budget", message, 400)


def upstream_error(status: int, message: str, code: str | None = None) -> tuple[int, dict[str, Any]]:
    """An upstream chat error in Google's shape; a context overflow in Gemini's wording."""
    if looks_like_context_overflow(message, code) and "input token count" not in message:
        message = f"The input token count exceeds the maximum number of tokens allowed: {message}"
    return error("upstream_error", message, status)


def stream_error(status: int, payload: Mapping[str, Any]) -> bytes:
    """An error inside a started stream: the bare JSON error object (the Gemini SDK raises it)."""
    return b"\r\n" + json.dumps(dict(payload)).encode() + b"\r\n"


def count_tokens(body: Mapping[str, Any]) -> dict[str, Any]:
    """``:countTokens``, answered locally: an estimate, never a billable call."""
    request = body.get("generateContentRequest") if isinstance(body.get("generateContentRequest"), Mapping) else body
    return {"totalTokens": budget_rules.estimate_tokens(budget_rules.prompt_text(request)), "estimate": True}


def models_payload(names: list[str]) -> dict[str, Any]:
    return {
        "models": [
            {
                "name": f"models/{n}",
                "displayName": n,
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"],
            }
            for n in names
        ],
    }


def prompt_text(body: Mapping[str, Any]) -> str:
    return budget_rules.prompt_text(body)


def system_prompt(body: Mapping[str, Any]) -> str | None:
    system = body.get("systemInstruction") or body.get("system_instruction")
    if not system:
        return None
    if isinstance(system, Mapping):
        return _parts_text(system.get("parts")) or None
    return content_text(system) or None


def requested_reasoning(body: Mapping[str, Any]) -> Any:
    config = _generation_config(body)
    thinking = config.get("thinkingConfig") or config.get("thinking_config")
    return dict(thinking) if isinstance(thinking, Mapping) else None


def summarize(payload: Any) -> dict[str, Any]:
    """A native Gemini answer -> a record summary (used only for diagnostics; calls are accounted on the
    canonical chat answer in translate mode)."""
    if not isinstance(payload, Mapping):
        return {"error": "non-object response"}
    candidate = (payload.get("candidates") or [{}])[0]
    parts = ((candidate or {}).get("content") or {}).get("parts") or []
    usage = payload.get("usageMetadata") or {}
    return {
        "id": payload.get("responseId"),
        "model": payload.get("modelVersion"),
        "content": "".join(str(p.get("text") or "") for p in parts if isinstance(p, Mapping) and not p.get("thought")),
        "finish_reason": (candidate or {}).get("finishReason"),
        "usage": usage or None,
        "normalized_usage": budget_rules.normalized_usage(
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=(usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
            if usage
            else None,
            reasoning_tokens=usage.get("thoughtsTokenCount"),
            cached_tokens=usage.get("cachedContentTokenCount"),
        )
        if usage
        else None,
        "error": payload.get("error"),
    }


# =====================================================================================
# Request: Gemini -> chat
# =====================================================================================


def _generation_config(body: Mapping[str, Any]) -> Mapping[str, Any]:
    config = body.get("generationConfig") or body.get("generation_config") or {}
    return config if isinstance(config, Mapping) else {}


def _parts_text(parts: Any) -> str:
    if isinstance(parts, str):
        return parts
    texts = [str(p.get("text") or "") for p in parts or [] if isinstance(p, Mapping) and "text" in p]
    return "".join(texts)


def _lower_types(schema: Any) -> Any:
    """OpenAPI-style schema (``"type": "OBJECT"``) -> JSON Schema types (``"object"``)."""
    if isinstance(schema, Mapping):
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                out[key] = value.lower()
            elif key == "type" and isinstance(value, list):
                out[key] = [v.lower() if isinstance(v, str) else v for v in value]
            else:
                out[key] = _lower_types(value)
        return out
    if isinstance(schema, list):
        return [_lower_types(v) for v in schema]
    return schema


def to_chat(
    request_json: Mapping[str, Any],
    *,
    stream: bool = False,
    model: str | None = None,
    **_options: Any,
) -> tuple[dict[str, Any], dict[str, int]]:
    """A ``generateContent`` request -> ``(chat request, translation_dropped)``."""
    body = request_json
    dropped = Dropped()
    out: dict[str, Any] = {"model": model or body.get("model")}
    messages: list[dict[str, Any]] = []
    system = system_prompt(body)
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(_contents_to_chat(body.get("contents"), dropped))
    out["messages"] = messages
    tools = _tools_to_chat(body.get("tools"), dropped)
    if tools:
        out["tools"] = tools
    _tool_config_to_chat(body.get("toolConfig") or body.get("tool_config"), out, dropped)
    _generation_to_chat(_generation_config(body), out, dropped)
    if body.get("safetySettings") or body.get("safety_settings"):
        dropped.add("request.safetySettings")
    if body.get("cachedContent") or body.get("cached_content"):
        dropped.add("request.cachedContent")
    if stream:
        out["stream"] = True
    handled = {"contents", "systemInstruction", "system_instruction", "tools", "toolConfig", "tool_config"}
    handled |= {"generationConfig", "generation_config", "safetySettings", "safety_settings"}
    handled |= {"cachedContent", "cached_content"}
    for key in body:
        if key not in handled and key not in IGNORED_KEYS:
            dropped.add(f"request.{key}")
    return out, dropped.data


def _contents_to_chat(contents: Any, dropped: Dropped) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending: dict[str, list[str]] = {}  # function name -> ids of the last model turn's calls, unanswered
    synthetic_ids = 0
    for turn_no, content in enumerate(contents if isinstance(contents, list) else []):
        if not isinstance(content, Mapping):
            continue
        parts = [p for p in content.get("parts") or [] if isinstance(p, Mapping)]
        if content.get("role") == "model":
            message, pending = _model_turn(parts, turn_no, dropped)
            messages.append(message)
            continue
        texts: list[str] = []
        for part in parts:
            if "functionResponse" in part or "function_response" in part:
                response = part.get("functionResponse") or part.get("function_response") or {}
                name = str(response.get("name") or "")
                call_id = response.get("id")
                if not call_id:
                    ids = pending.get(name) or []
                    if ids:
                        call_id = ids.pop(0)
                    else:
                        synthetic_ids += 1
                        call_id = f"call_unmatched_{synthetic_ids}"
                        dropped.add("functionResponse.unmatched")
                else:
                    ids = pending.get(name) or []
                    if call_id in ids:
                        ids.remove(call_id)
                value = response.get("response")
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": str(call_id), "content": text})
            elif "text" in part:
                if part.get("thought"):
                    dropped.add("user.thought")
                    continue
                texts.append(str(part.get("text") or ""))
            else:
                dropped.add(f"user.{_part_kind(part)}")
        if texts:
            messages.append({"role": "user", "content": "".join(texts)})
    return messages


def _part_kind(part: Mapping[str, Any]) -> str:
    keys = [k for k in part if k not in ("thoughtSignature", "thought_signature", "thought")]
    return keys[0] if keys else "empty"


def _model_turn(parts: list[Mapping[str, Any]], turn_no: int, dropped: Dropped) -> tuple[dict, dict]:
    texts: list[str] = []
    thoughts: list[str] = []
    # (signature, functionCall id of its part, index into ``calls`` of the first call it may cover)
    signatures: list[tuple[str, str | None, int]] = []
    calls: list[dict[str, Any]] = []
    pending: dict[str, list[str]] = {}
    for part in parts:
        signature = part.get("thoughtSignature") or part.get("thought_signature")
        call = part.get("functionCall") or part.get("function_call")
        call_id = None
        if isinstance(call, Mapping):
            name = str(call.get("name") or "")
            call_id = str(call.get("id") or f"call_{turn_no}_{len(calls)}")
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False)},
                },
            )
            pending.setdefault(name, []).append(call_id)
        elif "text" in part:
            (thoughts if part.get("thought") else texts).append(str(part.get("text") or ""))
        elif not signature:
            dropped.add(f"model.{_part_kind(part)}")
        if signature:
            start = len(calls) - 1 if call_id is not None else len(calls)
            signatures.append((str(signature), call_id, start))
    details: list[dict[str, Any]] = []
    for signature, call_id, start in signatures:
        carried = decode_token(signature)
        if isinstance(carried, Mapping) and isinstance(carried.get("details"), list):
            details.extend(_rebind_detail_ids(carried, [c["id"] for c in calls[start:]]))
        elif isinstance(carried, list):
            details.extend(dict(d) for d in carried if isinstance(d, Mapping))
        else:
            detail = {"type": "reasoning.encrypted", "data": signature, "format": TRANSLATED_FORMAT}
            if call_id:
                detail["id"] = call_id
            details.append(detail)
    if not details and thoughts:
        details = [synthetic_detail("".join(thoughts))]
    message: dict[str, Any] = {"role": "assistant", "content": "".join(texts) if texts else None}
    assistant_reasoning(message, merge_details(details))
    if calls:
        message["tool_calls"] = calls
    return message, pending


def _rebind_detail_ids(carried: Mapping[str, Any], replayed_ids: list[str]) -> list[dict[str, Any]]:
    """The carried details, with each detail ``id`` that named an emitted tool call rewritten to the id
    the client replayed for that call (matched by position).

    Gemini CLI does not keep the ``functionCall.id`` we send: it prefixes it (``<name>__<id>``) or mints
    its own (``<name>_<ms>_<n>``). The upstream binds a reasoning detail (Gemini's thought signature) to
    its tool call by id, so the detail must name the id the replayed ``tool_calls`` entry carries."""
    emitted = carried.get("calls") if isinstance(carried.get("calls"), list) else []
    rebind = {str(old): new for old, new in zip(emitted, replayed_ids, strict=False) if old}
    out: list[dict[str, Any]] = []
    for carried_detail in carried.get("details") or []:
        if not isinstance(carried_detail, Mapping):
            continue
        detail = dict(carried_detail)
        if detail.get("id") is not None and str(detail["id"]) in rebind:
            detail["id"] = rebind[str(detail["id"])]
        out.append(detail)
    return out


def _tools_to_chat(tools: Any, dropped: Dropped) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools if isinstance(tools, list) else []:
        if not isinstance(tool, Mapping):
            continue
        for key, value in tool.items():
            if key not in ("functionDeclarations", "function_declarations"):
                dropped.add(f"tools.{key}")  # googleSearch, codeExecution, urlContext, ...
                continue
            for decl in value or []:
                if not isinstance(decl, Mapping):
                    continue
                schema = decl.get("parametersJsonSchema") or decl.get("parameters_json_schema")
                if schema is None:
                    schema = _lower_types(decl.get("parameters") or {"type": "object", "properties": {}})
                function: dict[str, Any] = {"name": decl.get("name")}
                if decl.get("description"):
                    function["description"] = decl["description"]
                function["parameters"] = dict(schema)
                out.append({"type": "function", "function": function})
    return out


def _tool_config_to_chat(config: Any, out: dict[str, Any], dropped: Dropped) -> None:
    calling = (config or {}).get("functionCallingConfig") if isinstance(config, Mapping) else None
    if not isinstance(calling, Mapping):
        return
    mode = str(calling.get("mode") or "AUTO").upper()
    allowed = list(calling.get("allowedFunctionNames") or [])
    if mode == "NONE":
        out["tool_choice"] = "none"
    elif mode in ("ANY", "VALIDATED"):
        if len(allowed) == 1:
            out["tool_choice"] = {"type": "function", "function": {"name": allowed[0]}}
        else:
            out["tool_choice"] = "required"
            if allowed:
                dropped.add("toolConfig.allowedFunctionNames")
    else:
        out["tool_choice"] = "auto"


def _generation_to_chat(config: Mapping[str, Any], out: dict[str, Any], dropped: Dropped) -> None:
    for key, target in _GENERATION_KEYS.items():
        if config.get(key) is not None:
            out[target] = config[key]
    if config.get("stopSequences"):
        out["stop"] = list(config["stopSequences"])
    if int(config.get("candidateCount") or 1) != 1:
        dropped.add("generationConfig.candidateCount")
    mime = config.get("responseMimeType")
    schema = config.get("responseJsonSchema") or config.get("responseSchema")
    if mime == "application/json":
        if schema is not None:
            out["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": _lower_types(schema)},
            }
        else:
            out["response_format"] = {"type": "json_object"}
    elif mime not in (None, "text/plain"):
        dropped.add("generationConfig.responseMimeType")
    thinking = config.get("thinkingConfig") or config.get("thinking_config")
    if isinstance(thinking, Mapping):
        reasoning = _thinking_to_reasoning(thinking)
        if reasoning:
            out["reasoning"] = reasoning
    handled = {*_GENERATION_KEYS, "stopSequences", "candidateCount", "responseMimeType", "responseJsonSchema"}
    handled |= {"responseSchema", "thinkingConfig", "thinking_config"}
    for key in config:
        if key not in handled and key not in _IGNORED_GENERATION_KEYS:
            dropped.add(f"generationConfig.{key}")


def _thinking_to_reasoning(thinking: Mapping[str, Any]) -> dict[str, Any] | None:
    reasoning: dict[str, Any] = {}
    budget = thinking.get("thinkingBudget")
    level = thinking.get("thinkingLevel")
    if level:
        reasoning["effort"] = str(level).lower()
    elif isinstance(budget, int) and budget == 0:
        reasoning["enabled"] = False
    elif isinstance(budget, int) and budget > 0:
        reasoning["max_tokens"] = budget
    elif budget is not None:
        reasoning["enabled"] = True  # -1: dynamic thinking
    if thinking.get("includeThoughts") is False and reasoning.get("enabled") is not False:
        reasoning["exclude"] = True
    return reasoning or None


def _include_thoughts(request_json: Mapping[str, Any] | None) -> bool:
    thinking = (_generation_config(request_json or {})).get("thinkingConfig") or {}
    return bool(isinstance(thinking, Mapping) and thinking.get("includeThoughts"))


# =====================================================================================
# Answers: chat -> Gemini
# =====================================================================================


def _usage_metadata(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    norm = budget_rules.chat_usage(usage) or {}
    prompt = norm.get("prompt_tokens") or 0
    completion = norm.get("completion_tokens") or 0
    thoughts = norm.get("reasoning_tokens") or 0
    out: dict[str, Any] = {
        "promptTokenCount": prompt,
        "candidatesTokenCount": max(0, completion - thoughts),
        "totalTokenCount": prompt + completion,
    }
    if thoughts:
        out["thoughtsTokenCount"] = thoughts
    if norm.get("cached_tokens"):
        out["cachedContentTokenCount"] = norm["cached_tokens"]
    return out


def _function_call_part(call: ToolCall, n: int, dropped: Dropped) -> dict[str, Any]:
    value, ok = parse_arguments(call.arguments)
    if not ok or not isinstance(value, Mapping):
        dropped.add("tool_call.invalid_arguments")
        value = {}
    return {"functionCall": {"id": tool_call_id(call.id, "call_", n), "name": call.name, "args": dict(value)}}


def _final_parts(turn: ChatTurn, dropped: Dropped, *, text_parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The function-call parts of the turn, with the reasoning token on the first one; without calls,
    the token goes on the first answer text part (or a final empty text part)."""
    parts = [_function_call_part(call, n, dropped) for n, call in enumerate(turn.tool_calls)]
    if turn.details:
        # The emitted call ids ride along so a replay can rebind detail ids to the ids the client kept.
        calls = [part["functionCall"]["id"] for part in parts]
        token = encode_token({"details": list(turn.details), "calls": calls})
        if parts:
            parts[0]["thoughtSignature"] = token
        elif text_parts:
            text_parts[0]["thoughtSignature"] = token
        else:
            parts.append({"text": "", "thoughtSignature": token})
    return parts


def _answer(
    turn: ChatTurn,
    parts: list[dict[str, Any]],
    *,
    finish: bool,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}, "index": 0}
    if finish:
        candidate["finishReason"] = CHAT_TO_FINISH.get(finish_from_chat(turn), "OTHER")
    out: dict[str, Any] = {"candidates": [candidate]}
    if finish:
        out["usageMetadata"] = _usage_metadata(turn.usage)
    if turn.model:
        out["modelVersion"] = turn.model
    if turn.id:
        out["responseId"] = turn.id
    return out


def from_chat(
    chat_response: Mapping[str, Any],
    request_json: Mapping[str, Any] | None = None,
    *,
    dropped: Dropped | dict[str, int] | None = None,
    **_options: Any,
) -> dict[str, Any]:
    """A chat completion -> a ``GenerateContentResponse`` (an error payload -> Google's error shape)."""
    drops = as_dropped(dropped)
    if chat_response.get("error") and not chat_response.get("choices"):
        message, code = chat_error_text(chat_response)
        return upstream_error(502, message, code)[1]
    turn = turn_from_completion(chat_response)
    parts: list[dict[str, Any]] = []
    if _include_thoughts(request_json) and details_text(turn.details):
        parts.append({"text": details_text(turn.details), "thought": True})
    text_parts = [{"text": t} for t in (turn.content, turn.refusal) if t]
    tail = _final_parts(turn, drops, text_parts=text_parts)
    if isinstance(dropped, dict):
        dropped.update(drops.data)
    return _answer(turn, parts + text_parts + tail, finish=True)


class StreamTranslator:
    """Chat SSE -> Gemini stream chunks (``alt=sse`` events, or the elements of a JSON array).

    Answer text (and thought text when ``includeThoughts``) streams live; function calls, the reasoning
    token, ``finishReason`` and ``usageMetadata`` come in the final chunk.
    """

    def __init__(
        self,
        request_json: Mapping[str, Any] | None = None,
        *,
        dropped: Dropped | dict[str, int] | None = None,
        sse: bool = True,
        **_options: Any,
    ) -> None:
        self.sse = sse
        self.content_type = "text/event-stream" if sse else "application/json"
        self._thoughts = _include_thoughts(request_json)
        self._reader = ChatStreamReader()
        self._drops = dropped if isinstance(dropped, Dropped) else Dropped()
        self._sink = dropped if isinstance(dropped, dict) else None
        self._turn = TurnBuilder()
        self._count = 0
        self._finished = False

    def _frame(self, payload: Mapping[str, Any]) -> bytes:
        data = json.dumps(dict(payload), ensure_ascii=False)
        self._count += 1
        if self.sse:
            return f"data: {data}\r\n\r\n".encode()
        return (b"[" if self._count == 1 else b",\r\n") + data.encode()

    def feed(self, chunk: bytes) -> bytes:
        out = b""
        for event in self._reader.feed(chunk):
            self._turn.feed(event)
            kind = event[0]
            turn = self._turn.turn
            if kind in ("content", "refusal") and event[1]:
                out += self._frame(_answer(turn, [{"text": event[1]}], finish=False))
            elif kind == "reasoning" and self._thoughts and event[1]:
                out += self._frame(_answer(turn, [{"text": event[1], "thought": True}], finish=False))
            elif kind == "details" and self._thoughts:
                text = details_text(event[1])
                if text:
                    out += self._frame(_answer(turn, [{"text": text, "thought": True}], finish=False))
        return out

    def _close_array(self) -> bytes:
        if self.sse:
            return b""
        return b"]" if self._count else b"[]"

    def finish(self, failure: Any = None) -> bytes:
        """The final chunk; ``failure`` is a transport error seen by the relay (stream broke)."""
        if self._finished:
            return b""
        self._finished = True
        turn = self._turn.finish()
        problem = failure or turn.error
        if problem or turn.finish_reason in (None, "error"):
            message, code = chat_error_text({"error": problem}) if problem else (STREAM_ENDED_EARLY, None)
            return self.fail(502, upstream_error(502, message, code)[1])
        parts = _final_parts(turn, self._drops, text_parts=[])
        if self._sink is not None:
            self._sink.update(self._drops.data)
        return self._frame(_answer(turn, parts or [{"text": ""}], finish=True)) + self._close_array()

    def fail(self, status: int, payload: Mapping[str, Any]) -> bytes:
        """An upstream error after the stream started: the bare JSON error object (the SDK raises it)."""
        self._finished = True
        if self.sse:
            return stream_error(status, payload)
        return self._frame(dict(payload)) + self._close_array()


def stream_from_chat(
    chat_sse_iter: Iterable[bytes | None],
    request_json: Mapping[str, Any] | None = None,
    **options: Any,
) -> Iterator[bytes]:
    """Chat SSE chunks -> Gemini stream bytes; a ``None`` item (upstream silence) yields whitespace."""
    return iterate_stream(StreamTranslator(request_json, **options), chat_sse_iter, PING)
