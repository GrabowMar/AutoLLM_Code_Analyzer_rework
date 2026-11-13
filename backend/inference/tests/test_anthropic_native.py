"""OpenAI chat ↔ Anthropic native Messages translation (``inference.anthropic_native``): pure units, no DB."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from backend.inference import anthropic_native as an
from backend.inference.proxy_server import budget_rules

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a command",
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
    },
    {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}},
]
SYSTEM = "You are OpenCode. Build the app."


def _walk(value: Any, key: str) -> list[Any]:
    """Every value stored under ``key`` anywhere in a JSON-like structure."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key:
                found.append(v)
            found.extend(_walk(v, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk(item, key))
    return found


def _strip_markers(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_markers(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_strip_markers(v) for v in value]
    return value


def _turn1() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Create the todo app."},
    ]


def _assistant_calls(n: int, start: int = 0) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "Looking around.",
        "reasoning": "I should inspect the tree first.",
        "tool_calls": [
            {
                "id": f"toolu_{i}",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"cmd": f"ls {i}"})},
            }
            for i in range(start, start + n)
        ],
    }


def _results(n: int, start: int = 0) -> list[dict[str, Any]]:
    return [{"role": "tool", "tool_call_id": f"toolu_{i}", "content": f"out {i}"} for i in range(start, start + n)]


def _build(messages, *, model="claude-sonnet-5-5", params=None, **kwargs) -> dict[str, Any]:
    return an.to_messages_request(model, messages, params or {"max_tokens": 4000}, tools=TOOLS, **kwargs)


# =====================================================================================
# Request: system, messages, cache breakpoints
# =====================================================================================


def test_system_and_developer_messages_are_hoisted_with_the_first_breakpoint():
    body = _build(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "developer", "content": [{"type": "text", "text": "Use TypeScript."}]},
            {"role": "user", "content": "go"},
        ],
    )
    assert body["system"] == [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": "Use TypeScript.", "cache_control": {"type": "ephemeral"}},
    ]
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "go", "cache_control": {"type": "ephemeral"}}]},
    ]
    assert body["tools"][0] == {
        "name": "bash",
        "description": "Run a command",
        "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
    }
    assert body["tools"][1] == {"name": "read", "input_schema": {"type": "object"}}
    assert body["stream"] is True
    meta = body[an.META_KEY]
    assert meta["cache"] == {"prompt_cache": "auto", "breakpoints": 2, "ttl": "5m"}
    assert "system_hoisted" not in meta


def test_a_system_message_after_the_first_user_turn_is_hoisted_and_counted():
    body = _build([*_turn1(), {"role": "assistant", "content": "ok"}, {"role": "system", "content": "Hurry."}])
    assert [b["text"] for b in body["system"]] == [SYSTEM, "Hurry."]
    assert body[an.META_KEY]["system_hoisted"] == 1


def test_prompt_cache_off_sends_no_marker_anywhere():
    messages = [*_turn1(), _assistant_calls(2), *_results(2)]
    body = _build(messages, options=an.Options(prompt_cache="off"))
    assert _walk(body, "cache_control") == []
    assert body[an.META_KEY]["cache"]["breakpoints"] == 0


@pytest.mark.parametrize("parallel", [1, 3, 12])
def test_breakpoints_mark_system_previous_turn_and_last_block_never_more_than_four(parallel):
    messages = [*_turn1(), _assistant_calls(parallel), *_results(parallel)]
    body = _build(messages)
    markers = _walk(body, "cache_control")
    assert markers == [{"type": "ephemeral"}] * 3
    assert len(markers) <= 4
    assert "cache_control" in body["system"][-1]
    # The previous request's last block (the first user turn) is marked: an exact hit on what it wrote.
    assert "cache_control" in body["messages"][0]["content"][-1]
    assert "cache_control" in body["messages"][-1]["content"][-1]
    assert "cache_control" not in body["messages"][1]["content"][-1]
    assert body[an.META_KEY]["cache"]["breakpoints"] == 3


def test_a_policy_ttl_of_one_hour_is_recorded_and_five_minutes_sent():
    body = _build(_turn1(), options=an.Options(cache_ttl="1h"))
    assert {m.get("ttl") for m in _walk(body, "cache_control")} == {None}
    assert body[an.META_KEY]["cache"]["ttl_overridden"] == "1h->5m"


def test_tool_loop_groups_results_and_keeps_ids():
    messages = [
        *_turn1(),
        _assistant_calls(2),
        *_results(2),
        {"role": "user", "content": "Also add tests."},
    ]
    body = _build(messages)
    assistant = body["messages"][1]
    assert assistant["role"] == "assistant"
    assert _strip_markers(assistant["content"]) == [
        {"type": "text", "text": "Looking around."},
        {"type": "tool_use", "id": "toolu_0", "name": "bash", "input": {"cmd": "ls 0"}},
        {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"cmd": "ls 1"}},
    ]
    # Two tool results and the following user text become ONE user message, results first.
    assert len(body["messages"]) == 3
    user = _strip_markers(body["messages"][2])
    assert user == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_0", "content": "out 0"},
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "out 1"},
            {"type": "text", "text": "Also add tests."},
        ],
    }
    meta = body[an.META_KEY]
    assert meta["thinking_replay"] == "dropped"
    assert meta["reasoning_fields_dropped"] == 1
    assert _walk(body["messages"], "thinking") == []


def test_ids_are_sanitised_consistently_and_bad_arguments_become_empty_input():
    messages = [
        *_turn1(),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call:1/x", "function": {"name": "bash", "arguments": "{not json"}},
                {"id": "", "function": {"name": "read", "arguments": ""}},
            ],
        },
        {"role": "tool", "tool_call_id": "call:1/x", "content": ""},
        {"role": "tool", "tool_call_id": "", "content": [{"type": "text", "text": "file body"}]},
    ]
    body = _build(messages)
    uses = [b for b in body["messages"][1]["content"] if b["type"] == "tool_use"]
    results = [b for b in body["messages"][2]["content"] if b["type"] == "tool_result"]
    assert [u["id"] for u in uses] == ["call_1_x", "call_0"]
    assert [r["tool_use_id"] for r in results] == ["call_1_x", "call_0"]
    assert uses[0]["input"] == {}
    assert uses[1]["input"] == {}
    assert "content" not in results[0]
    assert _strip_markers(results[1]["content"]) == [{"type": "text", "text": "file body"}]
    assert body[an.META_KEY]["bad_tool_arguments"] == 1


def test_whitespace_only_tool_results_have_no_content_and_mappings_are_json():
    messages = [
        *_turn1(),
        _assistant_calls(2),
        {"role": "tool", "tool_call_id": "toolu_0", "content": "\n  \n"},
        {"role": "tool", "tool_call_id": "toolu_1", "content": {"ok": True, "path": "ü"}},
    ]
    results = [b for b in _build(messages)["messages"][2]["content"] if b["type"] == "tool_result"]
    assert "content" not in results[0]
    assert results[1]["content"] == '{"ok": true, "path": "ü"}'


def test_a_request_without_tools_renders_the_tool_history_as_text():
    """A compaction / summary call sends the transcript without tools: Anthropic would answer 400 to
    tool_use / tool_result blocks, so they become text (deterministically, and counted)."""
    messages = [*_turn1(), _assistant_calls(2), *_results(2), {"role": "user", "content": "Summarise."}]
    body = an.to_messages_request("claude-opus-5-5", messages, {"max_tokens": 1000})
    assert "tools" not in body
    assert "tool_choice" not in body
    kinds = {b["type"] for m in body["messages"] for b in m["content"]}
    assert kinds == {"text"}
    assistant = _strip_markers(body["messages"][1]["content"])
    assert assistant == [
        {"type": "text", "text": "Looking around."},
        {"type": "text", "text": '[tool call bash] {"cmd": "ls 0"}'},
        {"type": "text", "text": '[tool call bash] {"cmd": "ls 1"}'},
    ]
    user = _strip_markers(body["messages"][2]["content"])
    assert user == [
        {"type": "text", "text": "[tool result]\nout 0"},
        {"type": "text", "text": "[tool result]\nout 1"},
        {"type": "text", "text": "Summarise."},
    ]
    assert body[an.META_KEY]["tool_blocks_as_text"] == 4
    assert body == an.to_messages_request("claude-opus-5-5", messages, {"max_tokens": 1000})
    # With tools the blocks stay native.
    assert "tool_blocks_as_text" not in _build(messages)[an.META_KEY]


def test_blank_text_and_empty_assistant_messages_are_dropped_and_roles_merged():
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "   "},
        {"role": "user", "content": [{"type": "text", "text": "second"}, {"type": "text", "text": " "}]},
    ]
    body = _build(messages)
    assert _strip_markers(body["messages"]) == [
        {"role": "user", "content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]},
    ]
    assert body[an.META_KEY]["empty_messages_dropped"] == 1


def test_images_become_base64_or_url_sources():
    body = _build(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR"}},
                    {"type": "image_url", "image_url": {"url": "https://example.org/a.png"}},
                    {"type": "input_audio", "input_audio": {}},
                ],
            },
        ],
    )
    blocks = _strip_markers(body["messages"][0]["content"])
    assert blocks[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}}
    assert blocks[2] == {"type": "image", "source": {"type": "url", "url": "https://example.org/a.png"}}
    assert body[an.META_KEY]["unsupported_parts"] == 1


def test_each_turn_is_an_append_only_extension_of_the_previous_one():
    turn_n = [*_turn1(), _assistant_calls(2), *_results(2)]
    turn_n1 = [*turn_n, _assistant_calls(3, start=2), *_results(3, start=2)]
    first = _build(turn_n)
    second = _build(turn_n1)
    assert json.dumps(first["tools"]) == json.dumps(second["tools"])
    assert json.dumps(first["system"]) == json.dumps(second["system"])
    a = _strip_markers(first["messages"])
    b = _strip_markers(second["messages"])
    for i, message in enumerate(a):
        assert b[i]["role"] == message["role"]
        assert b[i]["content"][: len(message["content"])] == message["content"]
    # Only the last message of turn N can grow (with nothing here, since an assistant message follows it).
    assert b[: len(a)] == a
    # The breakpoint turn N wrote last is re-marked in turn N+1 (exact read), the new last block writes.
    assert first["messages"][-1]["content"][-1].get("cache_control") == {"type": "ephemeral"}
    assert second["messages"][len(a) - 1]["content"][-1].get("cache_control") == {"type": "ephemeral"}
    # Deterministic: the same history gives byte-identical requests.
    assert json.dumps(_build(turn_n1)) == json.dumps(second)


# =====================================================================================
# tool_choice, effort, stripped params, max_tokens
# =====================================================================================


@pytest.mark.parametrize(
    ("model", "choice", "expected"),
    [
        ("claude-opus-5-5", None, {"type": "auto"}),
        ("claude-opus-5-5", "auto", {"type": "auto"}),
        ("claude-opus-5-5", "none", {"type": "none"}),
        ("claude-opus-5-5", "required", {"type": "auto"}),
        ("claude-sonnet-5-5", "required", {"type": "auto"}),
        ("claude-fable-5-1", "required", {"type": "auto"}),
        ("claude-fable-5-1", {"type": "function", "function": {"name": "bash"}}, {"type": "auto"}),
        ("claude-sonnet-5-5", {"type": "function", "function": {"name": "bash"}}, {"type": "auto"}),
        ("claude-opus-5", "required", {"type": "any"}),
        ("claude-sonnet-5", "required", {"type": "any"}),
        ("claude-haiku-5-5", "required", {"type": "any"}),
        ("claude-opus-5", {"type": "function", "function": {"name": "bash"}}, {"type": "tool", "name": "bash"}),
        ("claude-sonnet-5", {"type": "function", "function": {"name": "bash"}}, {"type": "tool", "name": "bash"}),
        ("claude-unknown-9", "required", {"type": "auto"}),
    ],
)
def test_tool_choice_matrix(model, choice, expected):
    body = _build(_turn1(), model=model, tool_choice=choice)
    assert body["tool_choice"] == expected


def test_forced_tool_choice_downgrade_is_recorded_and_parallel_off_maps():
    body = _build(_turn1(), model="claude-opus-5-5", tool_choice="required", parallel_tool_calls=False)
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert body[an.META_KEY]["tool_choice"] == "auto (from required)"


def test_no_tools_no_tool_choice():
    body = an.to_messages_request("claude-opus-5-5", _turn1(), {"max_tokens": 10}, tool_choice="required")
    assert "tools" not in body
    assert "tool_choice" not in body


@pytest.mark.parametrize(
    ("params", "model", "effort", "source"),
    [
        ({}, "claude-opus-5-5", "high", "llmlab_default"),
        ({}, "claude-haiku-5-5", "high", "llmlab_default"),
        ({"reasoning_effort": "medium"}, "claude-opus-5-5", "medium", "harness"),
        ({"reasoning": {"effort": "xhigh"}, "reasoning_effort": "low"}, "claude-opus-5-5", "xhigh", "policy"),
        ({"reasoning": {"effort": "max", "exclude": False}}, "claude-fable-5-1", "max", "policy"),
        ({"reasoning_effort": "none"}, "claude-sonnet-5-5", "low", "harness"),
        ({"reasoning_effort": "minimal"}, "claude-sonnet-5-5", "low", "harness"),
        ({"reasoning": {"enabled": False}}, "claude-sonnet-5-5", "low", "policy (reasoning disabled)"),
        ({"reasoning_effort": "xhigh"}, "claude-opus-4-6", "high", "harness"),
        ({"reasoning_effort": "max"}, "claude-opus-4-5-20251101", "high", "harness"),
        (
            {"reasoning": {"max_tokens": 8000}},
            "claude-opus-5-5",
            "high",
            "llmlab_default (reasoning.max_tokens not expressible)",
        ),
    ],
)
def test_effort_precedence_and_mapping(params, model, effort, source):
    body = _build(_turn1(), model=model, params={"max_tokens": 1000, **params})
    assert body["output_config"]["effort"] == effort
    meta = body[an.META_KEY]
    assert meta["effort"] == effort
    assert meta["effort_source"] == source


def test_models_without_effort_send_no_output_config():
    body = _build(_turn1(), model="claude-haiku-4-5-20251001", params={"max_tokens": 1000, "reasoning_effort": "high"})
    assert "output_config" not in body
    assert "thinking" not in body
    assert body[an.META_KEY]["effort"] is None


@pytest.mark.parametrize("model", [*an._TRAITS, "claude-next-7"])
@pytest.mark.parametrize(
    "params",
    [{}, {"reasoning": {"enabled": False}}, {"reasoning": {"max_tokens": 4096}}, {"reasoning_effort": "none"}],
)
def test_thinking_is_never_disabled_or_budgeted(model, params):
    body = _build(_turn1(), model=model, params={"max_tokens": 2000, **params})
    thinking = body.get("thinking")
    assert thinking in (None, {"type": "adaptive"})
    assert _walk(body, "budget_tokens") == []
    assert _walk(body, "display") == []
    assert "fallbacks" not in body
    assert "betas" not in body
    effort = (body.get("output_config") or {}).get("effort")
    assert effort is None or effort in an.traits(model).efforts


def test_sampling_knobs_are_stripped_and_recorded():
    params = {
        "max_tokens": 500,
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 40,
        "presence_penalty": 0.1,
        "frequency_penalty": 0.1,
        "verbosity": "low",
        "stop": ["END", "  ", ""],
    }
    body = an.to_messages_request("claude-sonnet-5-5", _turn1(), {**params, "seed": 7}, tools=TOOLS)
    for key in ("temperature", "top_p", "top_k", "seed", "presence_penalty", "frequency_penalty", "verbosity"):
        assert key not in body
    assert body[an.META_KEY]["stripped"] == [
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "temperature",
        "top_k",
        "top_p",
        "verbosity",
    ]
    assert body["stop_sequences"] == ["END"]
    assert body["max_tokens"] == 500


@pytest.mark.parametrize(
    ("params", "model", "expected"),
    [
        ({}, "claude-opus-5-5", an.DEFAULT_MAX_TOKENS),
        ({"max_completion_tokens": 9000}, "claude-opus-5-5", 9000),
        ({"max_tokens": 200_000}, "claude-opus-5-5", 128_000),
        ({"max_tokens": 100_000}, "claude-sonnet-4-5-20250929", 64_000),
    ],
)
def test_max_tokens_default_and_cap(params, model, expected):
    body = an.to_messages_request(model, _turn1(), params)
    assert body["max_tokens"] == expected


def test_json_schema_response_format_becomes_output_config_format():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    body = an.to_messages_request(
        "claude-opus-5-5",
        _turn1(),
        {"max_tokens": 100, "response_format": {"type": "json_schema", "json_schema": {"schema": schema}}},
    )
    assert body["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": schema}}
    plain = an.to_messages_request("claude-opus-5-5", _turn1(), {"response_format": {"type": "json_object"}})
    assert "format" not in plain["output_config"]
    assert plain[an.META_KEY]["stripped"] == ["response_format"]


def test_traits_match_exact_names_and_dated_snapshots_only():
    assert an.model_key("claude-opus-4-5-20251101") == "claude-opus-4-5"
    assert an.model_key("claude-opus-5-5") == "claude-opus-5-5"
    assert an.model_key("claude-opus-5-7") is None
    assert an.traits("claude-opus-5-7") == an.DEFAULT_TRAITS
    assert not an.traits("claude-opus-5-5").forced_tool_choice
    assert an.traits("claude-opus-5").forced_tool_choice


# =====================================================================================
# Response: message -> chat.completion
# =====================================================================================

NATIVE_USAGE = {
    "input_tokens": 1000,
    "cache_creation_input_tokens": 10_000,
    "cache_read_input_tokens": 100_000,
    "output_tokens": 2000,
    "cache_creation": {"ephemeral_5m_input_tokens": 10_000, "ephemeral_1h_input_tokens": 0},
}


def _message(**extra) -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Part one, "},
            {"type": "text", "text": "same paragraph."},
            {"type": "tool_use", "id": "toolu_A", "name": "bash", "input": {"cmd": "ls ü"}},
            {"type": "text", "text": "After the call."},
            {"type": "tool_use", "id": "toolu_B", "name": "read", "input": {}},
        ],
        "stop_reason": "tool_use",
        "stop_details": None,
        "usage": dict(NATIVE_USAGE),
        **extra,
    }


def test_message_maps_to_a_chat_completion_with_the_cache_split():
    out = an.to_chat_completion(_message(), meta={"effort": "high"})
    choice = out["choices"][0]
    assert out["id"] == "chatcmpl-msg_01"
    assert out["object"] == "chat.completion"
    assert choice["finish_reason"] == "tool_calls"
    assert choice["native_finish_reason"] == "tool_use"
    message = choice["message"]
    assert message["content"] == "Part one, same paragraph.\n\nAfter the call."
    assert message["tool_calls"] == [
        {"id": "toolu_A", "type": "function", "function": {"name": "bash", "arguments": '{"cmd": "ls ü"}'}},
        {"id": "toolu_B", "type": "function", "function": {"name": "read", "arguments": "{}"}},
    ]
    usage = out["usage"]
    assert usage["prompt_tokens"] == 1000 + 10_000 + 100_000
    assert usage["completion_tokens"] == 2000
    assert usage["total_tokens"] == 113_000
    assert usage["prompt_tokens_details"] == {"cached_tokens": 100_000, "cache_write_tokens": 10_000}
    assert "cost" not in usage
    assert "completion_tokens_details" not in usage
    assert usage["anthropic"]["usage"] == NATIVE_USAGE
    assert usage["anthropic"]["request"] == {"effort": "high"}
    assert usage["anthropic"]["thinking_blocks"] == 1


@pytest.mark.parametrize(
    ("stop_reason", "finish"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("pause_turn", "stop"),
        ("tool_use", "tool_calls"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
        (None, "stop"),
    ],
)
def test_stop_reason_mapping(stop_reason, finish):
    out = an.to_chat_completion(_message(stop_reason=stop_reason, content=[{"type": "text", "text": "x"}]))
    assert out["choices"][0]["finish_reason"] == finish


def test_refusal_keeps_its_stop_details():
    details = {"type": "refusal", "category": "cyber", "explanation": "..."}
    out = an.to_chat_completion(_message(stop_reason="refusal", stop_details=details, content=[]))
    assert out["choices"][0]["finish_reason"] == "content_filter"
    assert out["choices"][0]["message"]["content"] == ""
    assert out["usage"]["anthropic"]["stop_details"] == details


def test_cost_of_a_cached_call_at_opus_5_5_list_prices():
    """uncached*in + writes*1.25*in + reads*read + out*out, through the gateway's own budget rules."""
    rules = budget_rules()
    usage = rules.chat_usage(an.to_chat_completion(_message())["usage"])
    pricing = rules.Pricing(
        input_per_mtok=4.0,
        output_per_mtok=20.0,
        cached_input_per_mtok=0.2,
        cache_write_per_mtok=5.0,
    )
    cost = pricing.price(
        usage["prompt_tokens"],
        usage["completion_tokens"],
        usage["cached_tokens"],
        usage["cache_write_tokens"],
    )
    assert cost == pytest.approx((1000 * 4 + 10_000 * 5 + 100_000 * 0.2 + 2000 * 20) / 1e6)
    assert cost == pytest.approx(0.114)
    assert usage["cost"] is None  # absent: priced from the table, never a stamped $0


# =====================================================================================
# SSE stream
# =====================================================================================


def _events() -> list[dict[str, Any]]:
    start_usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 10_000,
        "cache_read_input_tokens": 100_000,
        "output_tokens": 1,
    }
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "usage": start_usage,
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Part one, "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "same paragraph."}},
        {"type": "content_block_stop", "index": 1},
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "toolu_A", "name": "bash", "input": {}},
        },
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": ""}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"cmd": '}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"ls ü"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "content_block_start", "index": 3, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 3, "delta": {"type": "text_delta", "text": "After the call."}},
        {"type": "content_block_stop", "index": 3},
        {
            "type": "content_block_start",
            "index": 4,
            "content_block": {"type": "tool_use", "id": "toolu_B", "name": "read", "input": {}},
        },
        {"type": "content_block_stop", "index": 4},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 2000},
        },
        {"type": "message_stop"},
    ]


LIMITS = type("Limits", (), {"max_output_tokens": None, "wall_clock_s": None, "prompt_tokens_estimate": 7})()


def test_stream_reassembles_to_the_same_completion_as_the_message():
    acc = an.MessageStream({"effort": "high"})
    for event in _events():
        acc.feed(event)
    assert acc.done
    assert acc.finish_reason == "tool_calls"
    streamed = acc.completion(LIMITS)
    whole = an.to_chat_completion(_message(), meta={"effort": "high"})
    assert streamed["choices"] == whole["choices"]
    expected_usage = copy.deepcopy(whole["usage"])
    expected_usage["anthropic"]["usage"] = {**NATIVE_USAGE, "output_tokens": 2000}
    expected_usage["anthropic"]["usage"].pop("cache_creation")
    assert streamed["usage"] == expected_usage
    assert "llmlab_estimated" not in streamed["usage"]


def test_a_cut_stream_drops_unfinished_tool_calls_and_estimates_only_the_output():
    acc = an.MessageStream()
    for event in _events()[:12]:  # mid tool_use arguments
        acc.feed(event)
    out = acc.completion(LIMITS, cut="wall_clock", elapsed_s=3.2)
    choice = out["choices"][0]
    assert choice["finish_reason"] == "length"
    assert choice["native_finish_reason"] == "llmlab_cut:wall_clock"
    assert "tool_calls" not in choice["message"]
    assert choice["message"]["content"] == "Part one, same paragraph."
    usage = out["usage"]
    assert usage["llmlab_estimated"] is True
    assert usage["prompt_tokens"] == 111_000  # the exact input split from message_start
    assert usage["prompt_tokens_details"] == {"cached_tokens": 100_000, "cache_write_tokens": 10_000}
    assert usage["completion_tokens"] == acc.output_tokens()
    assert out["llmlab_cut"]["dropped_tool_calls"] == 1


def test_a_stream_without_final_usage_is_estimated():
    acc = an.MessageStream()
    events = [e for e in _events() if e["type"] != "message_delta"]
    for event in events:
        acc.feed(event)
    out = acc.completion(LIMITS)
    assert out["usage"]["llmlab_estimated"] is True
    assert out["usage"]["prompt_tokens"] == 111_000


def _max_tokens_stop_mid_tool_call() -> list[dict[str, Any]]:
    """Anthropic's own ``max_tokens`` stop in the middle of a tool call's ``input_json_delta``."""
    events = _events()[:12]  # message_start .. the tool_use's first fragment ('{"cmd": ')
    return [
        *events,
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 4000}},
        {"type": "message_stop"},
    ]


def test_a_tool_call_cut_by_anthropics_max_tokens_is_dropped_not_sent_with_empty_arguments():
    acc = an.MessageStream()
    for event in _max_tokens_stop_mid_tool_call():
        acc.feed(event)
    out = acc.completion(LIMITS)
    choice = out["choices"][0]
    assert choice["finish_reason"] == "length"
    assert "tool_calls" not in choice["message"]
    assert choice["message"]["content"] == "Part one, same paragraph."
    assert out["usage"]["anthropic"]["dropped_tool_calls"] == 1
    assert out["usage"]["completion_tokens"] == 4000  # Anthropic's final usage, not an estimate
    assert "llmlab_estimated" not in out["usage"]


def test_hidden_thinking_of_a_cut_stream_is_bounded_by_max_tokens():
    """A cut stream that had a thinking block books at least the request's max_tokens (thinking is billed but
    not streamed); without a thinking block, or without a known max_tokens, only the visible text counts."""
    events = _events()[:9]  # thinking block, then the first text block
    acc = an.MessageStream(max_tokens=8000)
    for event in events:
        acc.feed(event)
    out = acc.completion(LIMITS, cut="wall_clock", elapsed_s=900.0)
    assert out["usage"]["completion_tokens"] == 8000
    assert out["usage"]["llmlab_estimate_basis"] == "max_tokens (hidden thinking)"
    plain = an.MessageStream(max_tokens=8000)
    for event in [e for i, e in enumerate(events) if i not in (1, 2, 3)]:  # no thinking block
        plain.feed(event)
    out = plain.completion(LIMITS, cut="wall_clock", elapsed_s=900.0)
    assert out["usage"]["completion_tokens"] == plain.output_tokens()
    assert "llmlab_estimate_basis" not in out["usage"]


def test_a_started_thinking_block_counts_as_output():
    acc = an.MessageStream()
    acc.feed(_events()[0])
    assert not acc.has_output
    acc.feed(_events()[1])
    assert acc.has_output


def test_an_error_event_is_recorded():
    acc = an.MessageStream()
    acc.feed(_events()[0])
    acc.feed({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    assert acc.error == {"error": {"message": "Overloaded", "type": "overloaded_error", "code": "overloaded_error"}}


# =====================================================================================
# Errors
# =====================================================================================


@pytest.mark.parametrize(
    ("status", "etype", "message", "code"),
    [
        (
            400,
            "invalid_request_error",
            "prompt is too long: 1200000 tokens > 1000000 maximum",
            "context_length_exceeded",
        ),
        (400, "invalid_request_error", "tool_choice: type tool is not supported", "invalid_request_error"),
        (429, "rate_limit_error", "Number of request tokens has exceeded your rate limit", "rate_limit_error"),
        (529, "overloaded_error", "Overloaded", "overloaded_error"),
        (500, "api_error", "Internal server error", "api_error"),
    ],
)
def test_error_payload(status, etype, message, code):
    raw = {"type": "error", "error": {"type": etype, "message": message}}
    out = an.error_payload(status, raw, json.dumps(raw))
    assert out["error"] == {"message": message, "type": etype, "code": code}
    assert out["anthropic_error"] == raw


def test_error_payload_without_json_keeps_the_text():
    out = an.error_payload(502, None, "<html>bad gateway</html>")
    assert out == {"error": {"message": "<html>bad gateway</html>", "type": "http_502", "code": "http_502"}}


# =====================================================================================
# Telemetry
# =====================================================================================


def test_telemetry_records_the_cache_split_and_what_the_route_chose():
    from backend.generation.services import call_telemetry

    body = _build([*_turn1()], model="claude-opus-5-5", params={"max_tokens": 100, "temperature": 0.1})
    completion = an.to_chat_completion(_message(), meta=body[an.META_KEY])
    out = call_telemetry.from_chat_response(completion, started_at=1.0, ended_at=2.0)
    assert out["tokens"]["prompt"] == 111_000
    assert out["tokens"]["cached"] == 100_000
    assert out["tokens"]["cache_write"] == 10_000
    assert "cost" not in out
    upstream = out["upstream"]
    assert upstream["api"] == "anthropic_messages"
    assert upstream["effort"] == "high"
    assert upstream["effort_source"] == "llmlab_default"
    assert upstream["thinking_replay"] == "dropped"
    assert upstream["stripped"] == ["temperature"]
    assert upstream["cache"]["breakpoints"] == 2
    assert upstream["stop_reason"] == "tool_use"
    # Other providers' usage gets no ``upstream`` key.
    plain = call_telemetry.from_chat_response(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        started_at=1.0,
        ended_at=2.0,
    )
    assert "upstream" not in plain
