"""The wave-3 translators as pure functions (spec 5.4 rules 2-4, 6.6): native <-> canonical chat.

Inputs are recorded fixtures: the harnesses' own native requests (WP-07 smoke sessions of the pinned
Claude Code and Codex binaries, the Gemini CLI session recorded through the gateway) and chat answers in
the shapes OpenRouter returns for each model family (``fixtures/translate/chat_answers.json``: Anthropic
thinking + signature, OpenAI summary + encrypted blob, open-weights reasoning without a signature, Google
thought signature bound to a tool call, and the executor proxy's plain ``reasoning`` for local models).

The central property is **reasoning continuity**: whatever ``reasoning_details`` the upstream returned
come back, unchanged, in the next canonical request after the harness replays the translated answer the
way it really does (Claude Code: the content blocks; Codex: the output items; Gemini CLI: the parts,
minus ``thought`` parts). It is checked for every front x family x (JSON, stream).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.generation.tests.wp06_gateway.support import FIXTURES
from backend.generation.tests.wp06_gateway.support import anthropic_messages
from backend.generation.tests.wp06_gateway.support import common
from backend.generation.tests.wp06_gateway.support import gemini_generate
from backend.generation.tests.wp06_gateway.support import openai_chat
from backend.generation.tests.wp06_gateway.support import openai_responses
from backend.generation.tests.wp06_gateway.support import translation

ANSWERS = json.loads((FIXTURES / "translate" / "chat_answers.json").read_text())
FAMILIES = sorted(ANSWERS)
WP07_GATEWAY = Path(__file__).resolve().parents[1] / "wp07_harnesses" / "fixtures" / "gateway"
FRONTS = {
    "anthropic_messages": anthropic_messages,
    "openai_responses": openai_responses,
    "gemini_generate": gemini_generate,
}


def _events(family: str) -> list[bytes]:
    return [e.encode() for e in ANSWERS[family]["events"]]


def _expected_details(family: str) -> list[dict[str, Any]]:
    return ANSWERS[family]["expect_details"]


def _sse_events(raw: bytes) -> list[tuple[str | None, dict[str, Any]]]:
    out = []
    for name, data in common.SSEParser().feed(raw):
        payload = json.loads(data)
        out.append((name, payload))
    return out


# =====================================================================================
# Harness-side replays: how each CLI turns an answer into the next request's history
# =====================================================================================


def _anthropic_blocks_from_stream(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rebuild the assistant content blocks from Anthropic SSE as the SDK's accumulator does."""
    blocks: dict[int, dict[str, Any]] = {}
    partial: dict[int, str] = {}
    final: dict[str, Any] = {}
    for name, event in _sse_events(raw):
        kind = name or event.get("type")
        if kind == "content_block_start":
            blocks[event["index"]] = dict(event["content_block"])
        elif kind == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            if delta["type"] == "text_delta":
                block["text"] += delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = delta["signature"]
            elif delta["type"] == "input_json_delta":
                partial[event["index"]] = partial.get(event["index"], "") + delta["partial_json"]
        elif kind == "message_delta":
            final = {"stop_reason": event["delta"]["stop_reason"], "usage": event["usage"]}
        elif kind == "error":
            final = {"error": event["error"]}
    for index, text in partial.items():
        blocks[index]["input"] = json.loads(text) if text else {}
    return [blocks[i] for i in sorted(blocks)], final


def _responses_items_from_stream(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Codex keeps the ``response.output_item.done`` items as history."""
    items, final = [], {}
    for name, event in _sse_events(raw):
        kind = name or event.get("type")
        if kind == "response.output_item.done":
            items.append(event["item"])
        elif kind in ("response.completed", "response.incomplete", "response.failed"):
            final = event["response"]
    return items, final


def _gemini_parts_from_stream(raw: bytes, *, sse: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    chunks: list[dict[str, Any]]
    if sse:
        chunks = [payload for _name, payload in _sse_events(raw.replace(b"\r\n", b"\n"))]
    else:
        chunks = json.loads(raw)
    parts: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    for chunk in chunks:
        candidate = chunk["candidates"][0]
        parts.extend(candidate["content"]["parts"])
        if candidate.get("finishReason"):
            final = {"finishReason": candidate["finishReason"], "usageMetadata": chunk.get("usageMetadata")}
    return parts, final


def _gemini_history(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gemini CLI's history: ``thought`` parts dropped, adjacent plain text parts merged."""
    out: list[dict[str, Any]] = []
    for part in parts:
        if part.get("thought"):
            continue
        if out and set(part) == {"text"} and set(out[-1]) == {"text"}:
            out[-1] = {"text": out[-1]["text"] + part["text"]}
        else:
            out.append(dict(part))
    return out


def _native_turn(front: str, family: str, *, stream: bool, request: dict[str, Any] | None = None):
    """The translated answer of ``family`` and the assistant turn the harness would replay."""
    module = FRONTS[front]
    request = request or {}
    if stream:
        options = {"sse": True} if front == "gemini_generate" else {}
        raw = b"".join(module.stream_from_chat(iter(_events(family)), request, **options))
        if front == "anthropic_messages":
            blocks, final = _anthropic_blocks_from_stream(raw)
            return {"role": "assistant", "content": blocks}, final
        if front == "openai_responses":
            return _responses_items_from_stream(raw)
        parts, final = _gemini_parts_from_stream(raw)
        return {"role": "model", "parts": _gemini_history(parts)}, final
    native = module.from_chat(ANSWERS[family]["completion"], request)
    if front == "anthropic_messages":
        return {"role": "assistant", "content": native["content"]}, native
    if front == "openai_responses":
        return native["output"], native
    candidate = native["candidates"][0]
    return {"role": "model", "parts": _gemini_history(candidate["content"]["parts"])}, native


def _next_request(front: str, turn: Any) -> dict[str, Any]:
    if front == "anthropic_messages":
        return {
            "model": "claude",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": "Create hello.txt"}, turn],
        }
    if front == "openai_responses":
        return {"model": "codex", "input": [{"role": "user", "content": "Create hello.txt"}, *turn]}
    return {"contents": [{"role": "user", "parts": [{"text": "Create hello.txt"}]}, turn]}


def _assistant(chat: dict[str, Any]) -> dict[str, Any]:
    [message] = [m for m in chat["messages"] if m["role"] == "assistant"]
    return message


# =====================================================================================
# Reasoning continuity and tool calls: every front x family x framing
# =====================================================================================


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("front", sorted(FRONTS))
def test_reasoning_and_tool_calls_round_trip(front, family, stream):
    turn, _final = _native_turn(front, family, stream=stream)
    chat, dropped = FRONTS[front].to_chat(_next_request(front, turn))
    assert dropped == {}
    message = _assistant(chat)
    answer = ANSWERS[family]["completion"]["choices"][0]["message"]
    assert message.get("reasoning_details", []) == _expected_details(family)
    if ANSWERS[family].get("expect_reasoning"):
        assert message["reasoning"] == ANSWERS[family]["expect_reasoning"]
    expected_calls = [
        (tc["id"], tc["function"]["name"], json.loads(tc["function"]["arguments"]))
        for tc in answer.get("tool_calls") or []
    ]
    calls = [
        (tc["id"], tc["function"]["name"], json.loads(tc["function"]["arguments"]))
        for tc in message.get("tool_calls") or []
    ]
    assert calls == expected_calls
    assert (message.get("content") or "") == (answer.get("content") or "")


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("front", sorted(FRONTS))
def test_stream_and_json_answers_agree(front, family):
    """The streamed translation and the whole-answer translation give the harness the same turn."""
    streamed, _ = _native_turn(front, family, stream=True)
    whole, _ = _native_turn(front, family, stream=False)
    a, _ = FRONTS[front].to_chat(_next_request(front, streamed))
    b, _ = FRONTS[front].to_chat(_next_request(front, whole))
    assert _assistant(a) == _assistant(b)


def test_native_signatures_are_kept_as_details():
    """A signature the gateway did not mint (a native one) still reaches the upstream, tagged with the
    front's format."""
    chat, _ = anthropic_messages.to_chat(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "plan", "signature": "EqNativeSig"},
                        {"type": "redacted_thinking", "data": "EncNative"},
                        {"type": "text", "text": "ok"},
                    ],
                },
            ],
        },
    )
    assert _assistant(chat)["reasoning_details"] == [
        {"type": "reasoning.text", "text": "plan", "signature": "EqNativeSig", "format": "anthropic-claude-v1"},
        {"type": "reasoning.encrypted", "data": "EncNative", "format": "anthropic-claude-v1"},
    ]
    chat, _ = openai_responses.to_chat(
        {
            "input": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [{"type": "summary_text", "text": "S"}],
                    "encrypted_content": "gAAA",
                },
                {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{}"},
            ],
        },
    )
    assert _assistant(chat)["reasoning_details"] == [
        {"type": "reasoning.summary", "summary": "S", "format": "openai-responses-v1"},
        {"type": "reasoning.encrypted", "data": "gAAA", "format": "openai-responses-v1", "id": "rs_1"},
    ]
    chat, _ = gemini_generate.to_chat(
        {
            "contents": [
                {"role": "model", "parts": [{"functionCall": {"name": "ls", "args": {}}, "thoughtSignature": "CiQB"}]},
            ],
        },
    )
    message = _assistant(chat)
    assert message["reasoning_details"] == [
        {"type": "reasoning.encrypted", "data": "CiQB", "format": "google-gemini-v1", "id": "call_0_0"},
    ]
    assert message["tool_calls"][0]["id"] == "call_0_0"


def test_signature_tokens_survive_any_text_and_reject_foreign_values():
    details = [{"type": "reasoning.text", "text": 'é\n"quoted" ☃', "signature": "s", "index": 0}]
    token = translation.encode_token(details)
    assert token.startswith("llmlab1.")
    assert translation.decode_token(token) == details
    assert translation.decode_token("EqNative") is None
    assert translation.decode_token("llmlab1.%%%") is None


def test_stream_fragments_merge_into_whole_details():
    merged = translation.merge_details(
        [
            {"type": "reasoning.text", "text": "a", "index": 0, "format": "f"},
            {"type": "reasoning.text", "text": "b", "index": 0},
            {"type": "reasoning.encrypted", "data": "X", "index": 0},
            {"type": "reasoning.text", "signature": "S", "index": 0},
        ],
    )
    assert merged == [
        {"type": "reasoning.text", "text": "ab", "index": 0, "format": "f", "signature": "S"},
        {"type": "reasoning.encrypted", "data": "X", "index": 0},
    ]


# =====================================================================================
# Anthropic Messages (Claude Code)
# =====================================================================================


def _recorded(name: str) -> dict[str, Any]:
    return json.loads((WP07_GATEWAY / f"{name}.json").read_text())


def test_anthropic_recorded_claude_code_requests_translate_without_loss():
    session = _recorded("claude-code")
    first, second = (anthropic_messages.to_chat(call["body"]) for call in session["calls"])
    chat, dropped = first
    assert dropped == {}
    assert chat["messages"][0]["role"] == "system"
    # The billing-header block, the identity block and the env block stay separate text parts (markers kept).
    assert [p["text"][:20] for p in chat["messages"][0]["content"]] == [
        "x-anthropic-billing-",
        "You are a Claude age",
        "CWD: /workspace\nDate",
    ]
    assert chat["messages"][1]["role"] == "user"
    assert [t["function"]["name"] for t in chat["tools"]] == ["Bash", "Edit", "Read"]
    assert chat["tools"][0]["function"]["parameters"]["properties"]["command"]["type"] == "string"
    assert chat["reasoning"] == {"effort": "low"}  # thinking adaptive + output_config.effort
    assert chat["max_tokens"] == 8192
    assert chat["stream"] is True
    chat, dropped = second
    assert dropped == {}
    roles = [m["role"] for m in chat["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    call = chat["messages"][2]["tool_calls"][0]
    assert chat["messages"][3]["tool_call_id"] == call["id"]
    assert json.loads(call["function"]["arguments"])["command"]


def test_anthropic_tool_results_precede_user_text_and_errors_are_counted():
    chat, dropped = anthropic_messages.to_chat(
        {
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "and then?"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": "boom"}],
                            "is_error": True,
                        },
                    ],
                },
            ],
        },
    )
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "tool", "user"]
    assert chat["messages"][2] == {"role": "tool", "tool_call_id": "t1", "content": "boom"}
    assert dropped == {"tool_result.is_error": 1}


def test_anthropic_request_mapping_tool_choice_stop_reasoning_and_unknown_fields():
    chat, dropped = anthropic_messages.to_chat(
        {
            "model": "claude",
            "max_tokens": 50,
            "system": "S",
            "messages": [{"role": "user", "content": "q"}],
            "stop_sequences": ["END"],
            "temperature": 0.2,
            "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
            "tools": [
                {"name": "t", "input_schema": {"type": "object"}},
                {"type": "web_search_20250305", "name": "web_search"},
            ],
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "context_management": {"edits": []},
        },
    )
    assert chat["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}]
    assert chat["stop"] == ["END"]
    assert chat["temperature"] == 0.2
    assert chat["max_tokens"] == 50
    assert chat["tool_choice"] == "required"
    assert chat["parallel_tool_calls"] is False
    assert chat["reasoning"] == {"max_tokens": 1024}
    assert [t["function"]["name"] for t in chat["tools"]] == ["t"]
    assert dropped == {"tools.web_search_20250305": 1, "request.context_management": 1}
    chat, _ = anthropic_messages.to_chat(
        {"messages": [], "thinking": {"type": "disabled"}, "tool_choice": {"type": "tool", "name": "t"}},
    )
    assert chat["reasoning"] == {"enabled": False}
    assert chat["tool_choice"] == {"type": "function", "function": {"name": "t"}}


@pytest.mark.parametrize(
    ("finish", "stop_reason"),
    [("stop", "end_turn"), ("length", "max_tokens"), ("tool_calls", "tool_use"), ("content_filter", "refusal")],
)
def test_anthropic_stop_reasons(finish, stop_reason):
    completion = json.loads(json.dumps(ANSWERS["open_weights"]["completion"]))
    completion["choices"][0]["finish_reason"] = finish
    assert anthropic_messages.from_chat(completion)["stop_reason"] == stop_reason


def test_anthropic_usage_splits_cache_reads_and_writes_out_of_the_prompt():
    native = anthropic_messages.from_chat(ANSWERS["anthropic"]["completion"])
    assert native["usage"] == {
        "input_tokens": 5210 - 4096 - 900,
        "cache_creation_input_tokens": 900,
        "cache_read_input_tokens": 4096,
        "output_tokens": 96,
        "cost": 0.0168,
    }
    assert native["id"] == "msg_gen-1762000000-anthropic"
    assert [b["type"] for b in native["content"]] == ["thinking", "text", "tool_use"]


def test_anthropic_stream_is_a_well_formed_event_sequence():
    raw = b"".join(anthropic_messages.stream_from_chat(iter(_events("anthropic"))))
    names = [name for name, _ in _sse_events(raw)]
    assert names[0] == "message_start"
    assert names[-2:] == ["message_delta", "message_stop"]
    starts = [e for n, e in _sse_events(raw) if n == "content_block_start"]
    assert [s["content_block"]["type"] for s in starts] == ["thinking", "text", "tool_use"]
    assert [s["index"] for s in starts] == [0, 1, 2]
    _, final = _anthropic_blocks_from_stream(raw)
    assert final["stop_reason"] == "tool_use"
    assert final["usage"]["cache_read_input_tokens"] == 4096


def test_anthropic_stream_errors_and_early_ends_are_native_error_events():
    broken = _events("anthropic")[:4]  # no finish, no usage
    raw = b"".join(anthropic_messages.stream_from_chat(iter(broken)))
    name, event = _sse_events(raw)[-1]
    assert name == "error"
    assert event["error"]["type"] == "api_error"
    chunk = {
        "id": "g",
        "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "error"}],
        "error": {"message": "provider died", "code": 502},
    }
    mid = [b"data: " + json.dumps(chunk).encode() + b"\n\n"]
    raw = b"".join(anthropic_messages.stream_from_chat(iter(mid)))
    name, event = _sse_events(raw)[-1]
    assert name == "error"
    assert "provider died" in event["error"]["message"]


def test_anthropic_upstream_context_overflow_uses_the_native_wording():
    status, payload = anthropic_messages.upstream_error(
        400,
        "This model's maximum context length is 8192 tokens",
        "context_length_exceeded",
    )
    assert status == 400
    assert payload["error"]["message"].startswith("prompt is too long")


def test_anthropic_plain_reasoning_from_local_models_replays_as_reasoning_text():
    turn, _ = _native_turn("anthropic_messages", "local", stream=True)
    assert turn["content"][0]["type"] == "thinking"
    assert turn["content"][0]["thinking"] == "I need a shell call."
    chat, _ = anthropic_messages.to_chat(_next_request("anthropic_messages", turn))
    message = _assistant(chat)
    assert "reasoning_details" not in message
    assert message["reasoning"] == "I need a shell call."


# =====================================================================================
# OpenAI Responses (Codex)
# =====================================================================================


def test_responses_recorded_codex_requests_translate_without_loss():
    session = _recorded("codex")
    first, second = (openai_responses.to_chat(call["body"]) for call in session["calls"])
    chat, dropped = first
    assert dropped == {}
    assert [m["role"] for m in chat["messages"]] == ["system", "system", "user", "user"]
    assert [t["function"]["name"] for t in chat["tools"]] == [
        "exec_command",
        "write_stdin",
        "request_user_input",
        "view_image",
    ]
    assert chat["reasoning"] == {"effort": "low"}
    assert chat["stream"] is True
    assert chat["parallel_tool_calls"] is False or chat["parallel_tool_calls"] is True
    chat, dropped = second
    assert dropped == {}
    assert [m["role"] for m in chat["messages"]][-2:] == ["assistant", "tool"]
    assert chat["messages"][-1]["tool_call_id"] == chat["messages"][-2]["tool_calls"][0]["id"]


def test_responses_custom_tools_are_single_string_functions_both_ways():
    request = {
        "input": "patch it",
        "tools": [
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch",
                "format": {"type": "grammar", "syntax": "lark", "definition": "start: x"},
            },
        ],
        "tool_choice": {"type": "custom", "name": "apply_patch"},
    }
    chat, dropped = openai_responses.to_chat(request)
    assert chat["tools"][0]["function"]["parameters"]["required"] == ["input"]
    assert chat["tool_choice"] == {"type": "function", "function": {"name": "apply_patch"}}
    assert dropped == {"tools.custom.format": 1}
    completion = {
        "id": "g",
        "created": 1,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c9",
                            "type": "function",
                            "function": {"name": "apply_patch", "arguments": json.dumps({"input": "*** Begin Patch"})},
                        },
                    ],
                },
            },
        ],
    }
    native = openai_responses.from_chat(completion, request)
    item = native["output"][0]
    assert item["type"] == "custom_tool_call"
    assert item["input"] == "*** Begin Patch"
    assert item["call_id"] == "c9"
    chat, _ = openai_responses.to_chat(
        {"input": [item, {"type": "custom_tool_call_output", "call_id": "c9", "output": "Done!"}]},
    )
    assert json.loads(chat["messages"][0]["tool_calls"][0]["function"]["arguments"]) == {"input": "*** Begin Patch"}
    assert chat["messages"][1] == {"role": "tool", "tool_call_id": "c9", "content": "Done!"}


def test_responses_request_mapping_and_dropped_fields():
    chat, dropped = openai_responses.to_chat(
        {
            "model": "codex",
            "instructions": "Be brief.",
            "input": [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "D"}]}],
            "max_output_tokens": 77,
            "text": {
                "format": {"type": "json_schema", "name": "x", "schema": {"type": "object"}, "strict": True},
                "verbosity": "low",
            },
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "f", "parameters": {"type": "object"}, "strict": True},
            ],
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": "k",
            "truncation": "auto",
            "top_logprobs": 3,
        },
    )
    assert chat["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "system", "content": "D"}]
    assert chat["max_tokens"] == 77
    assert chat["verbosity"] == "low"
    assert chat["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "x", "schema": {"type": "object"}, "strict": True},
    }
    assert chat["tools"] == [
        {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}, "strict": True}},
    ]
    assert dropped == {"tools.web_search": 1, "request.truncation": 1, "request.top_logprobs": 1}


@pytest.mark.parametrize(
    ("finish", "status", "reason"),
    [
        ("stop", "completed", None),
        ("length", "incomplete", "max_output_tokens"),
        ("content_filter", "incomplete", "content_filter"),
    ],
)
def test_responses_status_follows_the_finish_reason(finish, status, reason):
    completion = json.loads(json.dumps(ANSWERS["open_weights"]["completion"]))
    completion["choices"][0]["finish_reason"] = finish
    native = openai_responses.from_chat(completion)
    assert native["status"] == status
    assert (native["incomplete_details"] or {}).get("reason") == reason


def test_responses_stream_event_sequence_and_usage():
    raw = b"".join(openai_responses.stream_from_chat(iter(_events("openai"))))
    events = _sse_events(raw)
    names = [n for n, _ in events]
    assert names[:2] == ["response.created", "response.in_progress"]
    assert names[-1] == "response.completed"
    assert [e["sequence_number"] for _, e in events] == list(range(1, len(events) + 1))
    items, final = _responses_items_from_stream(raw)
    assert [i["type"] for i in items] == ["reasoning", "function_call", "function_call"]
    assert final["output"] == items
    assert final["usage"] == {
        "input_tokens": 3021,
        "input_tokens_details": {"cached_tokens": 2944},
        "output_tokens": 210,
        "output_tokens_details": {"reasoning_tokens": 128},
        "total_tokens": 3231,
        "cost": 0.00589,
    }
    assert "response.reasoning_summary_text.delta" in names


def test_responses_stream_failure_is_response_failed():
    raw = b"".join(openai_responses.stream_from_chat(iter(_events("openai")[:3])))
    name, event = _sse_events(raw)[-1]
    assert name == "response.failed"
    assert event["response"]["status"] == "failed"
    assert event["response"]["error"]["message"]


def test_responses_conversation_state_is_refused():
    with pytest.raises(translation.TranslationError):
        openai_responses.to_chat({"conversation": "conv_1", "input": "x"})


# =====================================================================================
# Gemini generateContent (Gemini CLI)
# =====================================================================================

GEMINI_REQUEST = {
    "contents": [
        {"role": "user", "parts": [{"text": "Create hello.txt"}]},
        {
            "role": "model",
            "parts": [
                {"text": "Sure."},
                {"functionCall": {"name": "run_shell_command", "args": {"command": "ls"}}},
                {"functionCall": {"name": "read_file", "args": {"path": "a"}}},
            ],
        },
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "run_shell_command", "response": {"output": "a\n"}}},
                {"functionResponse": {"name": "read_file", "response": {"output": "x"}}},
            ],
        },
    ],
    "systemInstruction": {"role": "user", "parts": [{"text": "You are Gemini CLI."}]},
    "tools": [
        {
            "functionDeclarations": [
                {
                    "name": "run_shell_command",
                    "description": "Run",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {"command": {"type": "STRING"}},
                        "required": ["command"],
                    },
                },
                {
                    "name": "read_file",
                    "parametersJsonSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            ],
        },
    ],
    "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
    "generationConfig": {
        "temperature": 0,
        "topP": 1,
        "maxOutputTokens": 1000,
        "thinkingConfig": {"includeThoughts": True, "thinkingBudget": 8192},
    },
}


def test_gemini_request_mapping():
    chat, dropped = gemini_generate.to_chat(GEMINI_REQUEST, stream=True, model="gemini-2.5-pro")
    assert dropped == {}
    assert chat["model"] == "gemini-2.5-pro"
    assert chat["stream"] is True
    assert [m["role"] for m in chat["messages"]] == ["system", "user", "assistant", "tool", "tool"]
    calls = chat["messages"][2]["tool_calls"]
    assert [c["id"] for c in calls] == ["call_1_0", "call_1_1"]
    # Unidentified function responses are matched to the calls by name, in order.
    assert [m["tool_call_id"] for m in chat["messages"][3:]] == ["call_1_0", "call_1_1"]
    assert json.loads(chat["messages"][3]["content"]) == {"output": "a\n"}
    assert chat["tools"][0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    assert chat["tools"][1]["function"]["parameters"]["properties"]["path"]["type"] == "string"
    assert chat["tool_choice"] == "auto"
    assert chat["temperature"] == 0
    assert chat["top_p"] == 1
    assert chat["max_tokens"] == 1000
    assert chat["reasoning"] == {"max_tokens": 8192}


@pytest.mark.parametrize(
    ("config", "reasoning"),
    [
        ({"thinkingBudget": 0}, {"enabled": False}),
        ({"thinkingBudget": -1}, {"enabled": True}),
        ({"thinkingLevel": "HIGH"}, {"effort": "high"}),
        ({"thinkingBudget": 512, "includeThoughts": False}, {"max_tokens": 512, "exclude": True}),
    ],
)
def test_gemini_thinking_config(config, reasoning):
    chat, _ = gemini_generate.to_chat({"contents": [], "generationConfig": {"thinkingConfig": config}})
    assert chat["reasoning"] == reasoning


def test_gemini_dropped_and_tool_config():
    chat, dropped = gemini_generate.to_chat(
        {
            "contents": [{"role": "user", "parts": [{"text": "q"}]}],
            "tools": [{"googleSearch": {}}, {"functionDeclarations": [{"name": "f"}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["f"]}},
            "safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}],
            "generationConfig": {
                "candidateCount": 2,
                "responseMimeType": "application/json",
                "responseLogprobs": True,
            },
        },
    )
    assert chat["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    assert chat["response_format"] == {"type": "json_object"}
    assert dropped == {
        "tools.googleSearch": 1,
        "request.safetySettings": 1,
        "generationConfig.candidateCount": 1,
        "generationConfig.responseLogprobs": 1,
    }


def test_gemini_answer_shape_usage_and_thoughts():
    request = {"generationConfig": {"thinkingConfig": {"includeThoughts": True}}}
    native = gemini_generate.from_chat(ANSWERS["google"]["completion"], request)
    candidate = native["candidates"][0]
    parts = candidate["content"]["parts"]
    assert parts[0] == {"text": "**Planning** I'll run one command.", "thought": True}
    assert parts[1]["functionCall"] == {
        "id": "tool_run_shell_command_k3",
        "name": "run_shell_command",
        "args": {"command": "ls"},
    }
    assert parts[1]["thoughtSignature"].startswith("llmlab1.")
    assert candidate["finishReason"] == "STOP"
    assert native["usageMetadata"] == {
        "promptTokenCount": 8000,
        "candidatesTokenCount": 30,
        "totalTokenCount": 8150,
        "thoughtsTokenCount": 120,
    }
    plain = gemini_generate.from_chat(ANSWERS["google"]["completion"], {})
    assert not any(p.get("thought") for p in plain["candidates"][0]["content"]["parts"])


def test_gemini_text_only_answer_carries_the_signature_on_its_text():
    native = gemini_generate.from_chat(ANSWERS["open_weights"]["completion"], {})
    parts = native["candidates"][0]["content"]["parts"]
    assert parts == [
        {"text": "Done. hello.txt contains a greeting.", "thoughtSignature": parts[0]["thoughtSignature"]},
    ]


def _two_call_answer() -> dict[str, Any]:
    completion = json.loads(json.dumps(ANSWERS["google"]["completion"]))
    message = completion["choices"][0]["message"]
    message["tool_calls"] = [
        {"id": "call_abc", "type": "function", "function": {"name": "run_shell_command", "arguments": "{}"}},
        {"id": "call_def", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
    ]
    message["reasoning_details"] = [
        {"type": "reasoning.text", "text": "plan", "format": "google-gemini-v1", "index": 0},
        {"type": "reasoning.encrypted", "data": "SIG_A", "id": "call_abc", "format": "google-gemini-v1"},
        {"type": "reasoning.encrypted", "data": "SIG_B", "id": "call_def", "format": "google-gemini-v1"},
    ]
    return completion


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
@pytest.mark.parametrize(
    ("rewrite_a", "rewrite_b"),
    [
        (lambda i: f"run_shell_command__{i}", lambda i: f"read_file__{i}"),  # Gemini CLI prefixes the id
        (lambda _i: "run_shell_command_1793724095801_0", lambda _i: "read_file_1793724095801_1"),  # or mints one
        (lambda i: i, lambda i: i),  # or keeps it
    ],
    ids=["prefixed", "fresh", "kept"],
)
def test_gemini_signatures_follow_call_ids_gemini_cli_rewrites(stream, rewrite_a, rewrite_b):
    completion = _two_call_answer()
    if stream:
        message = completion["choices"][0]["message"]
        chunk = {
            "id": completion["id"],
            "model": completion["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        **message,
                        "tool_calls": [{**c, "index": n} for n, c in enumerate(message["tool_calls"])],
                    },
                    "finish_reason": "tool_calls",
                },
            ],
            "usage": completion["usage"],
        }
        body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        raw = b"".join(gemini_generate.stream_from_chat(iter([None, body]), {}, sse=True))
        parts, _ = _gemini_parts_from_stream(raw)
    else:
        parts = gemini_generate.from_chat(completion, {})["candidates"][0]["content"]["parts"]
    history = _gemini_history(parts)
    calls = [p for p in history if "functionCall" in p]
    assert [c["functionCall"]["id"] for c in calls] == ["call_abc", "call_def"]
    for part, rewrite in zip(calls, (rewrite_a, rewrite_b), strict=True):
        part["functionCall"]["id"] = rewrite(part["functionCall"]["id"])
    chat, _ = gemini_generate.to_chat(
        {"contents": [{"role": "user", "parts": [{"text": "go"}]}, {"role": "model", "parts": history}]},
    )
    replayed = _assistant(chat)
    ids = [c["id"] for c in replayed["tool_calls"]]
    assert ids == [rewrite_a("call_abc"), rewrite_b("call_def")]
    encrypted = {d["data"]: d.get("id") for d in replayed["reasoning_details"] if d["type"] == "reasoning.encrypted"}
    assert encrypted == {"SIG_A": ids[0], "SIG_B": ids[1]}


@pytest.mark.parametrize("sse", [True, False], ids=["alt_sse", "json_array"])
def test_gemini_stream_framings(sse):
    raw = b"".join(gemini_generate.stream_from_chat(iter([None, *_events("google")]), {}, sse=sse))
    if sse:
        assert raw.startswith(b"\r\n")
        assert b"data: " in raw
    else:
        assert json.loads(raw)  # whitespace pings are valid JSON whitespace
    parts, final = _gemini_parts_from_stream(raw, sse=sse)
    assert final["finishReason"] == "STOP"
    assert final["usageMetadata"]["thoughtsTokenCount"] == 120
    assert any("functionCall" in p for p in parts)


def test_gemini_stream_errors_are_bare_error_objects_the_sdk_raises():
    raw = b"".join(gemini_generate.stream_from_chat(iter(_events("google")[:2]), {}, sse=True))
    tail = raw.rsplit(b"\r\n\r\n", 1)[-1].strip()
    payload = json.loads(tail)
    assert payload["error"]["code"] == 502
    assert payload["error"]["status"] == "UNAVAILABLE"


def test_gemini_routes_options_and_local_answers():
    assert gemini_generate.route("POST", "/v1beta/models/gemini-2.5-pro:streamGenerateContent") == "call"
    assert gemini_generate.route("POST", "/v1beta/models/gemini-2.5-pro:countTokens") == "count_tokens"
    assert gemini_generate.request_options("/v1beta/models/gemini-2.5-pro:streamGenerateContent", "alt=sse") == {
        "stream": True,
        "sse": True,
        "model": "gemini-2.5-pro",
    }
    assert gemini_generate.request_options("/v1beta/models/m:generateContent")["stream"] is False
    counted = gemini_generate.count_tokens({"generateContentRequest": GEMINI_REQUEST})
    assert counted["totalTokens"] > 0
    assert counted["estimate"] is True
    assert gemini_generate.system_prompt(GEMINI_REQUEST) == "You are Gemini CLI."
    assert gemini_generate.requested_reasoning(GEMINI_REQUEST) == {"includeThoughts": True, "thinkingBudget": 8192}
    status, payload = gemini_generate.context_error(9000, 8000)
    assert status == 400
    assert payload["error"]["status"] == "INVALID_ARGUMENT"


def test_gemini_unmatched_function_responses_are_counted():
    chat, dropped = gemini_generate.to_chat(
        {"contents": [{"role": "user", "parts": [{"functionResponse": {"name": "x", "response": {"output": "?"}}}]}]},
    )
    assert chat["messages"][0]["tool_call_id"] == "call_unmatched_1"
    assert dropped == {"functionResponse.unmatched": 1}


# =====================================================================================
# The canonical policy applies to translated bodies exactly as to native chat
# =====================================================================================


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_translated_bodies_take_the_chat_policy(front):
    from backend.generation.tests.wp06_gateway.test_policy import policy

    p = policy(
        front_protocol=front,
        protocol_mode="translate",
        model={"upstream_model": "deepseek/deepseek-v4.1-flash", "max_output": 32000},
        call={"max_output_tokens": 4096},
        reasoning={"mode": "effort", "effort": "high"},
        routing={"provider": {"only": ["deepinfra"]}},
    )
    native = {
        "anthropic_messages": {
            "model": "claude",
            "max_tokens": 99,
            "messages": [{"role": "user", "content": "q"}],
            "thinking": {"type": "enabled", "budget_tokens": 50},
        },
        "openai_responses": {"model": "codex", "input": "q", "max_output_tokens": 99, "reasoning": {"effort": "low"}},
        "gemini_generate": {
            "contents": [{"role": "user", "parts": [{"text": "q"}]}],
            "generationConfig": {"maxOutputTokens": 99},
        },
    }[front]
    chat, _ = FRONTS[front].to_chat(native)
    body, diff = openai_chat.apply_policy_native(chat, p)
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["max_tokens"] == 4096
    assert diff["forced"]["max_tokens"] == 99
    assert body["reasoning"] == {"effort": "high", "exclude": False}
    assert body["provider"] == {"only": ["deepinfra"]}
    assert body["usage"] == {"include": True}
