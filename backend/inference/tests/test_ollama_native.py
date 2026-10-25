"""OpenAI ↔ Ollama native translation (spec §D.2), on request shapes the harnesses send."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.inference.chat import build_upstream_body
from backend.inference.ollama_native import ConversionError
from backend.inference.ollama_native import from_native_response
from backend.inference.ollama_native import from_openai_response
from backend.inference.ollama_native import stream_chunks
from backend.inference.ollama_native import to_native_messages

# opencode (AI SDK): system + user content parts, assistant tool call, tool result.
OPENCODE_MESSAGES = [
    {"role": "system", "content": "You are opencode."},
    {"role": "user", "content": [{"type": "text", "text": "Build the app."}]},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call_abc", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}},
        ],
    },
    {"role": "tool", "tool_call_id": "call_abc", "content": "app.py\n"},
]
# mini-swe-agent (litellm): plain strings, a bash tool call, a tool result.
MINI_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant that can interact with a computer."},
    {"role": "user", "content": "Please solve this issue."},
    {
        "role": "assistant",
        "content": "THOUGHT: list files",
        "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "bash", "arguments": "not json"}}],
    },
    {"role": "tool", "tool_call_id": "t1", "name": "bash", "content": "<returncode>0</returncode>"},
]
# aider (litellm, --no-stream): system + user/assistant edit-format turns, no tools.
AIDER_MESSAGES = [
    {"role": "system", "content": "Act as an expert software developer."},
    {"role": "user", "content": "Change hello to goodbye"},
    {"role": "assistant", "content": "app.py\n<<<<<<< SEARCH\nhello\n=======\ngoodbye\n>>>>>>> REPLACE"},
]
TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]


def _access(**overrides):
    base = {
        "kind": "ollama",
        "api_flavor": "native",
        "served_model_name": "qwen2.5-coder:7b",
        "digest": "sha256:" + "ab" * 32,
        "num_ctx": 8192,
        "keep_alive": "30m",
        "capabilities": ("completion", "tools"),
        "endpoint": SimpleNamespace(slug="gpu"),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_opencode_messages_round_trip():
    native = to_native_messages(OPENCODE_MESSAGES)
    assert native[1] == {"role": "user", "content": "Build the app."}
    assert native[2]["tool_calls"] == [{"function": {"name": "bash", "arguments": {"command": "ls"}}}]
    assert native[3] == {"role": "tool", "content": "app.py\n", "tool_name": "bash"}


def test_mini_swe_agent_invalid_arguments_are_kept_raw():
    native = to_native_messages(MINI_MESSAGES)
    assert native[2]["tool_calls"][0]["function"]["arguments"] == {"_raw": "not json"}
    assert native[3]["tool_name"] == "bash"


def test_aider_messages_pass_through():
    assert to_native_messages(AIDER_MESSAGES) == [{"role": m["role"], "content": m["content"]} for m in AIDER_MESSAGES]


def test_inline_images_become_images_and_remote_ones_are_rejected():
    msg = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ],
        },
    ]
    assert to_native_messages(msg)[0]["images"] == ["QUJD"]
    with pytest.raises(ConversionError):
        to_native_messages([{"role": "user", "content": [{"type": "image_url", "image_url": "https://x/y.png"}]}])


def test_native_body_options_and_dropped_params():
    path, body = build_upstream_body(
        _access(),
        AIDER_MESSAGES,
        {
            "max_tokens": 512,
            "temperature": 0.2,
            "top_k": 40,
            "repetition_penalty": 1.1,
            "provider": {"order": ["x"]},
            "stop": ["###"],
            "response_format": {"type": "json_object"},
            "reasoning": {"enabled": True},
        },
        1234,
        tools=TOOLS,
    )
    assert path == "/api/chat"
    assert body["stream"] is False
    assert body["keep_alive"] == "30m"
    assert body["options"] == {
        "num_ctx": 8192,
        "num_predict": 512,
        "temperature": 0.2,
        "top_k": 40,
        "repeat_penalty": 1.1,
        "seed": 1234,
        "stop": ["###"],
    }
    assert body["format"] == "json"
    assert body["tools"] == TOOLS
    assert "provider" not in body
    # think only when the model reports the "thinking" capability
    assert "think" not in body
    _path, thinking = build_upstream_body(
        _access(capabilities=("completion", "thinking")),
        AIDER_MESSAGES,
        {"reasoning": {"effort": "none"}},
        None,
    )
    assert thinking["think"] is False


def test_openai_flavor_restricts_params():
    path, body = build_upstream_body(
        _access(api_flavor="openai"),
        AIDER_MESSAGES,
        {"max_tokens": 64, "top_k": 40, "min_p": 0.1, "temperature": 0},
        7,
    )
    assert path == "/v1/chat/completions"
    assert body["seed"] == 7
    assert body["max_tokens"] == 64
    assert "top_k" not in body
    assert "min_p" not in body
    _path, vllm = build_upstream_body(
        _access(kind="openai_compatible", api_flavor="openai"),
        AIDER_MESSAGES,
        {"top_k": 40, "min_p": 0.1, "repetition_penalty": 1.05},
        None,
    )
    assert (vllm["top_k"], vllm["min_p"], vllm["repetition_penalty"]) == (40, 0.1, 1.05)


def test_native_response_to_openai_shape():
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "bash", "arguments": {"command": "ls"}}}],
        },
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    out = from_native_response(payload, request_id="1234abcd-0000", served_model="m@abc", provider="gpu")
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["id"] == "call_1234abcd_0"
    assert json.loads(call["function"]["arguments"]) == {"command": "ls"}
    assert out["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.0}
    assert (out["provider"], out["model"]) == ("gpu", "m@abc")
    length = from_native_response(
        {"message": {"content": "x"}, "done_reason": "length"},
        request_id="r",
        served_model="m",
        provider="gpu",
    )
    assert length["choices"][0]["finish_reason"] == "length"


def test_openai_response_normalised_and_streamed():
    payload = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
    out = from_openai_response(payload, request_id="r1", served_model="m@x", provider="gpu")
    assert out["usage"]["cost"] == 0.0
    assert out["usage"]["total_tokens"] == 4
    chunks = stream_chunks(out)
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": "hi"}
    assert chunks[1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[2]["choices"] == []
    assert chunks[2]["usage"]["prompt_tokens"] == 3
