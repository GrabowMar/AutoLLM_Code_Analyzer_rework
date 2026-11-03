"""Policy application on every front's native body (spec 6.6), without a server."""

from __future__ import annotations

import json

import pytest

from backend.generation import identity
from backend.generation.tests.wp06_gateway.support import anthropic_messages
from backend.generation.tests.wp06_gateway.support import common
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import openai_chat
from backend.generation.tests.wp06_gateway.support import openai_responses

UP = "https://openrouter.invalid/api/v1"
PROVIDER = {"sort": "price", "allow_fallbacks": False, "only": ["deepinfra"], "require_parameters": True}


def policy(**overrides):
    return common.Policy.from_config(config_dict(UP, **overrides))


def test_canonical_sha_equals_identity_h():
    body = {"b": [1, 2.5, None, True], "a": "Café\r\nx", "c": {"z": "ü", "y": []}}
    assert common.sha(body) == identity.H(body)
    assert common.text_sha("a\r\nb́") == identity.text_sha("a\r\nb́")


# =====================================================================================
# openai_chat
# =====================================================================================


def test_chat_policy_pins_model_sampling_seed_routing_and_cap():
    p = policy(
        sampling={"forced": {"temperature": 0.7}, "stripped": ["top_p", "stop"], "seed": 99},
        routing={"provider": PROVIDER},
        call={"max_output_tokens": 2048},
        model={"upstream_model": "deepseek/deepseek-v4.1-flash", "max_output": 32000},
    )
    body = {
        "model": "small-model",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.1,
        "top_p": 0.5,
        "stop": ["x"],
        "seed": 1,
        "provider": {"order": ["cheap"]},
        "max_completion_tokens": 100_000,
        "max_tokens": 50_000,
        "stream": True,
    }
    out, diff = openai_chat.apply_policy_native(body, p)
    assert out["model"] == "deepseek/deepseek-v4.1-flash"
    assert out["temperature"] == 0.7
    for key in ("top_p", "stop", "max_completion_tokens"):
        assert key not in out
    assert out["seed"] == 99
    assert out["provider"] == PROVIDER
    assert out["max_tokens"] == 2048  # never the harness's own cap
    assert out["usage"] == {"include": True}
    assert out["stream_options"] == {"include_usage": True}
    assert out["session_id"] == p.session_id
    assert diff["model"] == "small-model"
    assert diff["stripped"]["provider"] == {"order": ["cheap"]}
    assert body["model"] == "small-model"  # the native body is not mutated


def test_chat_reasoning_modes():
    effort = policy(reasoning={"mode": "effort", "effort": "low", "max_tokens": None, "exclude": False})
    out, _ = openai_chat.apply_policy_native({"messages": [], "reasoning_effort": "high"}, effort)
    assert out["reasoning"] == {"effort": "low", "exclude": False}
    assert "reasoning_effort" not in out
    budget = policy(
        reasoning={"mode": "max_tokens", "effort": None, "max_tokens": 4000, "exclude": True},
        call={"max_output_tokens": 8000},
        model={"upstream_model": "m/x", "max_output": 32000},
    )
    out, _ = openai_chat.apply_policy_native({"messages": []}, budget)
    assert out["reasoning"] == {"max_tokens": 4000, "exclude": True}
    assert out["max_tokens"] == 12000  # call cap + reasoning budget
    default = policy()
    out, _ = openai_chat.apply_policy_native({"messages": [], "reasoning": {"effort": "high"}}, default)
    assert out["reasoning"] == {"effort": "high"}  # provider_default leaves the harness's choice


def test_flex_serving_uses_the_floor_suffix():
    out, _ = openai_chat.apply_policy_native({"messages": []}, policy(routing={"serving_tier": "flex"}))
    assert out["model"].endswith(":floor")


def test_session_id_only_when_sticky():
    out, _ = openai_chat.apply_policy_native({"messages": []}, policy(caching={"sticky_session": False}))
    assert "session_id" not in out


CACHE_BODY = {
    "messages": [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": [{"type": "text", "text": "task", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "ls"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "a\nb"},
    ],
}


def test_cache_control_injected_only_for_listed_providers():
    listed = policy(
        model={"upstream_model": "anthropic/claude-sonnet-4.6"},
        caching={"prompt_cache": "on", "ttl": "1h", "cache_control_providers": ["anthropic"]},
    )
    out, diff = openai_chat.apply_policy_native(CACHE_BODY, listed)
    messages = out["messages"]
    assert messages[0]["content"] == [
        {"type": "text", "text": "You are an agent.", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    assert messages[3]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert messages[1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}  # TTL from the policy
    assert diff["cache_control_injected"] == 2
    unlisted = policy(caching={"prompt_cache": "on", "cache_control_providers": ["anthropic"]})
    out, _ = openai_chat.apply_policy_native(CACHE_BODY, unlisted)
    assert out["messages"][0]["content"] == "You are an agent."  # deepseek: nothing injected
    assert out["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}  # harness markers kept


def test_pinned_provider_counts_for_cache_control():
    p = policy(
        model={"upstream_model": "qwen/qwen3-coder"},
        routing={"provider": {"only": ["alibaba"]}},
        caching={"prompt_cache": "on", "cache_control_providers": ["alibaba"]},
    )
    assert p.cache_control_applies()


@pytest.mark.parametrize("mode", ["off", "auto"])
def test_cache_control_stripped_when_off_or_auto(mode):
    p = policy(
        model={"upstream_model": "anthropic/claude-sonnet-4.6"},
        caching={"prompt_cache": mode, "cache_control_providers": ["anthropic"]},
    )
    out, diff = openai_chat.apply_policy_native(CACHE_BODY, p)
    assert "cache_control" not in json.dumps(out)
    assert diff["cache_control_stripped"] == 1


def test_never_more_than_four_breakpoints():
    body = {
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": f"s{i}", "cache_control": {"type": "ephemeral"}}]}
            for i in range(4)
        ]
        + [{"role": "tool", "tool_call_id": "1", "content": "x"}],
    }
    p = policy(
        model={"upstream_model": "anthropic/claude-x"},
        caching={"prompt_cache": "on", "cache_control_providers": ["anthropic"]},
    )
    out, _ = openai_chat.apply_policy_native(body, p)
    assert common.count_cache_control(out) == 4


def test_images_are_detected():
    assert common.contains_images({"messages": [{"content": [{"type": "image_url", "image_url": {"url": "x"}}]}]})
    assert common.contains_images({"messages": [{"content": [{"type": "image", "source": {}}]}]})
    assert common.contains_images({"input": [{"content": [{"type": "input_image"}]}]})
    assert not common.contains_images(CACHE_BODY)


# =====================================================================================
# anthropic_messages, in passthrough
# =====================================================================================

CLAUDE_BODY = {
    "model": "claude-sonnet-4-5",
    "max_tokens": 32000,
    "system": [
        {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
        {"type": "text", "text": "Instructions...", "cache_control": {"type": "ephemeral"}},
    ],
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "Build the app."}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me look.", "signature": "EqoBCkgIARABGAIiQ..."},
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "app\n"}]},
    ],
    "tools": [{"name": "Bash", "description": "Run", "input_schema": {"type": "object"}}],
    "thinking": {"type": "enabled", "budget_tokens": 31999},
    "temperature": 1,
    "metadata": {"user_id": "user_abc_session_x"},
    "stream": True,
}


def anthropic_policy(**overrides):
    base = {
        "front_protocol": "anthropic_messages",
        "protocol_mode": "passthrough",
        "request_class_source": "x-claude-code-request-class",
        "model": {"upstream_model": "anthropic/claude-sonnet-4.6", "max_output": 64000},
    }
    base.update(overrides)
    return policy(**base)


def test_anthropic_passthrough_keeps_everything_but_policy_fields():
    p = anthropic_policy(
        caching={"prompt_cache": "on", "ttl": "5m", "sticky_session": True, "cache_control_providers": ["anthropic"]},
        routing={"provider": PROVIDER},
        call={"max_output_tokens": 16384},
    )
    out, diff = anthropic_messages.apply_policy_native(CLAUDE_BODY, p)
    assert out["model"] == "anthropic/claude-sonnet-4.6"
    assert out["max_tokens"] == 16384
    assert out["provider"] == PROVIDER
    assert out["session_id"] == p.session_id
    # Thinking blocks and their signatures reach the upstream untouched.
    assert out["messages"][1] == CLAUDE_BODY["messages"][1]
    assert out["thinking"] == CLAUDE_BODY["thinking"]  # provider_default: the harness's own thinking
    assert out["messages"][2]["content"][0]["cache_control"] == {"type": "ephemeral"}  # last tool result
    assert diff["cache_control_injected"] == 1
    untouched = {"tools", "metadata", "stream", "temperature", "system"}
    for key in untouched - {"system"}:
        assert out[key] == CLAUDE_BODY[key]
    assert out["system"] == CLAUDE_BODY["system"]  # already marked; nothing added there


def test_anthropic_reasoning_budget_and_effort():
    budget = anthropic_policy(
        reasoning={"mode": "max_tokens", "effort": None, "max_tokens": 4096, "exclude": True},
        call={"max_output_tokens": 16384},
    )
    out, diff = anthropic_messages.apply_policy_native({**CLAUDE_BODY, "output_config": {"effort": "high"}}, budget)
    assert out["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert out["max_tokens"] == 16384 + 4096
    assert "output_config" not in out
    assert diff["reasoning_exclude_ignored"] is True
    effort = anthropic_policy(reasoning={"mode": "effort", "effort": "xhigh", "max_tokens": None, "exclude": False})
    out, diff = anthropic_messages.apply_policy_native(CLAUDE_BODY, effort)
    assert out["output_config"] == {"effort": "high"}
    assert diff["effort_mapped"] == {"xhigh": "high"}
    off = anthropic_policy(reasoning={"mode": "effort", "effort": "none", "max_tokens": None, "exclude": False})
    out, _ = anthropic_messages.apply_policy_native(CLAUDE_BODY, off)
    assert out["thinking"] == {"type": "disabled"}


def test_anthropic_cache_off_strips_and_ttl_is_rewritten():
    off = anthropic_policy(caching={"prompt_cache": "off"})
    out, _ = anthropic_messages.apply_policy_native(CLAUDE_BODY, off)
    assert "cache_control" not in json.dumps(out)
    hour = anthropic_policy(caching={"prompt_cache": "on", "ttl": "1h", "cache_control_providers": ["anthropic"]})
    out, _ = anthropic_messages.apply_policy_native(CLAUDE_BODY, hour)
    assert out["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_anthropic_count_tokens_is_a_local_estimate():
    got = anthropic_messages.count_tokens(CLAUDE_BODY)
    assert got["estimate"] is True
    assert got["input_tokens"] == pytest.approx(len(anthropic_messages.prompt_text(CLAUDE_BODY)) / 3.6, abs=1)


def test_anthropic_usage_normalization_counts_cache_reads_and_writes_as_input():
    usage = {"input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 90, "output_tokens": 5}
    got = anthropic_messages.native_usage(usage)
    assert got["prompt_tokens"] == 1000
    assert (got["cached_tokens"], got["cache_write_tokens"], got["completion_tokens"]) == (900, 90, 5)
    assert got["reasoning_tokens"] is None  # not reported separately: unknown, never 0
    assert got["cost"] is None


# =====================================================================================
# openai_responses, in passthrough
# =====================================================================================

CODEX_BODY = {
    "model": "gpt-5-codex",
    "instructions": "You are Codex, based on GPT-5.",
    "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Build it."}]},
        {"type": "reasoning", "summary": [], "encrypted_content": "gAAAAB..."},
        {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"command":["ls"]}'},
        {"type": "function_call_output", "call_id": "c1", "output": "app"},
    ],
    "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
    "tool_choice": "auto",
    "parallel_tool_calls": False,
    "reasoning": {"effort": "medium", "summary": "auto"},
    "store": False,
    "stream": True,
    "include": ["reasoning.encrypted_content"],
    "prompt_cache_key": "abc",
}


def responses_policy(**overrides):
    base = {
        "front_protocol": "openai_responses",
        "protocol_mode": "passthrough",
        "model": {"upstream_model": "openai/gpt-5.1-codex", "max_output": 128000},
    }
    base.update(overrides)
    return policy(**base)


def test_responses_passthrough_policy():
    p = responses_policy(
        reasoning={"mode": "effort", "effort": "low", "max_tokens": None, "exclude": True},
        call={"max_output_tokens": 8192},
        routing={"provider": PROVIDER},
    )
    out, diff = openai_responses.apply_policy_native({**CODEX_BODY, "store": True, "include": []}, p)
    assert out["model"] == "openai/gpt-5.1-codex"
    assert out["max_output_tokens"] == 8192
    assert out["reasoning"] == {"summary": "auto", "effort": "low"}
    assert out["store"] is False
    assert out["include"] == ["reasoning.encrypted_content"]
    assert out["provider"] == PROVIDER
    assert out["input"] == CODEX_BODY["input"]  # encrypted reasoning replays untouched
    assert diff["reasoning_exclude_ignored"] is True


def test_responses_refuses_server_side_state():
    with pytest.raises(openai_responses.StatefulRequestError):
        openai_responses.check_request({**CODEX_BODY, "previous_response_id": "resp_1"})


def test_responses_usage_and_finish():
    payload = {
        "id": "resp_1",
        "object": "response",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]}],
        "usage": {
            "input_tokens": 1200,
            "input_tokens_details": {"cached_tokens": 1000},
            "output_tokens": 8192,
            "output_tokens_details": {"reasoning_tokens": 8192},
            "cost": 0.01,
        },
    }
    got = openai_responses.summarize(payload)
    assert got["finish_reason"] == "length"
    assert got["normalized_usage"] == {
        "prompt_tokens": 1200,
        "completion_tokens": 8192,
        "reasoning_tokens": 8192,
        "cached_tokens": 1000,
        "cache_write_tokens": None,
        "cost": 0.01,
    }


# =====================================================================================
# Startup refusals (wave-3 seams)
# =====================================================================================


@pytest.mark.parametrize(
    ("front", "mode", "upstream", "needle"),
    [
        ("gemini_generate", "translate", "openrouter", "translator"),
        ("anthropic_messages", "translate", "openrouter", "translator"),
        ("openai_responses", "native", "openrouter", "does not run"),
        ("openai_chat", "passthrough", "openrouter", "does not run"),
        ("anthropic_messages", "passthrough", "local_proxy", "OpenRouter only"),
    ],
)
def test_unsupported_configs_are_refused_at_startup(front, mode, upstream, needle):
    p = policy(front_protocol=front, protocol_mode=mode, upstream={"kind": upstream})
    assert any(needle in problem for problem in p.problems())
