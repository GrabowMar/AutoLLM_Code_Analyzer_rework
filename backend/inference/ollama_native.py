"""OpenAI chat format ↔ Ollama native ``/api/chat`` (spec §D.2 "Message conversion").

Pure functions, unit-tested on request fixtures captured from the harnesses
(opencode, mini-swe-agent, aider):

- content arrays are flattened to text; ``image_url`` parts carrying base64
  data become ``images``; any other part (remote image URLs, audio) is
  rejected,
- assistant ``tool_calls[].function.arguments`` (a JSON string) become
  objects (invalid JSON → ``{"_raw": s}``),
- ``role: "tool"`` messages carry ``tool_name``, looked up from the matching
  call id,
- responses become the OpenAI ``chat.completion`` shape with synthetic
  tool-call ids ``call_<rid8>_<i>`` and arguments re-encoded as JSON strings.
"""

from __future__ import annotations

import json
import time
from typing import Any


class ConversionError(ValueError):
    """A message cannot be represented in the native format."""


def _flatten_content(content: Any) -> tuple[str, list[str]]:
    """``(text, base64 images)`` from an OpenAI message content (string or parts array)."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text"):
            texts.append(str(part.get("text") or ""))
        elif kind == "image_url":
            url = part.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
                msg = "Only inline base64 images can be sent to a local model"
                raise ConversionError(msg)
            images.append(url.split(";base64,", 1)[1])
        else:
            msg = f"Unsupported message content part {kind!r} for a local model"
            raise ConversionError(msg)
    return "".join(texts) if len(texts) == 1 else "\n".join(t for t in texts if t), images


def _parse_arguments(arguments: Any) -> Any:
    if isinstance(arguments, (dict, list)):
        return arguments
    if arguments in (None, ""):
        return {}
    try:
        return json.loads(arguments)
    except (TypeError, ValueError):
        return {"_raw": arguments}


def to_native_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI chat messages → Ollama native messages."""
    call_names: dict[str, str] = {}
    native: list[dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        text, images = _flatten_content(message.get("content"))
        out: dict[str, Any] = {"role": role, "content": text}
        if images:
            out["images"] = images
        if role == "assistant":
            calls = []
            for call in message.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                name = str(fn.get("name") or "")
                if call.get("id"):
                    call_names[str(call["id"])] = name
                calls.append({"function": {"name": name, "arguments": _parse_arguments(fn.get("arguments"))}})
            if calls:
                out["tool_calls"] = calls
            thinking = message.get("reasoning") or message.get("reasoning_content")
            if isinstance(thinking, str) and thinking:
                out["thinking"] = thinking
        elif role == "tool":
            name = message.get("name") or call_names.get(str(message.get("tool_call_id") or ""), "")
            if name:
                out["tool_name"] = name
        native.append(out)
    return native


def _tool_call_out(call: dict[str, Any], index: int, rid8: str) -> dict[str, Any]:
    fn = call.get("function") or {}
    arguments = fn.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
    return {
        "id": f"call_{rid8}_{index}",
        "type": "function",
        "function": {"name": str(fn.get("name") or ""), "arguments": arguments},
    }


def _finish_reason(done_reason: Any, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_calls"
    if done_reason == "length":
        return "length"
    return "stop"


def from_native_response(
    payload: dict[str, Any],
    *,
    request_id: str,
    served_model: str,
    provider: str,
) -> dict[str, Any]:
    """Ollama ``/api/chat`` (non-stream) response → OpenAI ``chat.completion``."""
    message = payload.get("message") or {}
    rid8 = str(request_id).replace("-", "")[:8]
    calls = [_tool_call_out(c, i, rid8) for i, c in enumerate(message.get("tool_calls") or []) if isinstance(c, dict)]
    out_message: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if calls:
        out_message["tool_calls"] = calls
    if message.get("thinking"):
        out_message["reasoning"] = message["thinking"]
    prompt_tokens = int(payload.get("prompt_eval_count") or 0)
    completion_tokens = int(payload.get("eval_count") or 0)
    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": served_model,
        "provider": provider,
        "choices": [
            {
                "index": 0,
                "message": out_message,
                "finish_reason": _finish_reason(payload.get("done_reason"), bool(calls)),
            },
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": 0.0,
        },
    }


def from_openai_response(
    payload: dict[str, Any],
    *,
    request_id: str,
    served_model: str,
    provider: str,
) -> dict[str, Any]:
    """An upstream OpenAI-compatible response, normalised (``provider``, ``model``, ``usage.cost = 0``)."""
    out = dict(payload)
    usage = dict(out.get("usage") or {})
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    usage.update(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": int(usage.get("total_tokens") or prompt + completion),
            "cost": 0.0,
        },
    )
    out["usage"] = usage
    out["provider"] = provider
    out["model"] = served_model
    out.setdefault("id", f"chatcmpl-{request_id}")
    out.setdefault("object", "chat.completion")
    out.setdefault("created", int(time.time()))
    rid8 = str(request_id).replace("-", "")[:8]
    choices = []
    for raw_choice in out.get("choices") or []:
        choice = dict(raw_choice)
        message = dict(choice.get("message") or {})
        calls = message.get("tool_calls") or []
        if calls:
            message["tool_calls"] = [
                {**_tool_call_out(c, i, rid8), **({"id": c["id"]} if c.get("id") else {})}
                for i, c in enumerate(calls)
                if isinstance(c, dict)
            ]
        choice["message"] = message
        choices.append(choice)
    out["choices"] = choices
    return out


def served_model_label(served_model_name: str, digest: str) -> str:
    """``f"{served}@{digest[7:19]}"`` (the digest without its ``sha256:`` prefix, 12 chars)."""
    return f"{served_model_name}@{digest[7:19]}" if digest else served_model_name


def stream_chunks(completion: dict[str, Any]) -> list[dict[str, Any]]:
    """A completed ``chat.completion`` as the ``chat.completion.chunk`` sequence a streaming client expects.

    One chunk with the whole message delta (role, content, tool calls with
    their index), one with ``finish_reason``, and a final usage chunk with
    empty ``choices`` (the ``stream_options.include_usage`` shape).
    """
    base = {
        "id": completion.get("id"),
        "object": "chat.completion.chunk",
        "created": completion.get("created") or int(time.time()),
        "model": completion.get("model"),
        "provider": completion.get("provider"),
    }
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    delta: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("reasoning"):
        delta["reasoning"] = message["reasoning"]
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
    return [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason") or "stop"}]},
        {**base, "choices": [], "usage": completion.get("usage") or {}},
    ]
