"""Translate mode through the real gateway over HTTP with the scripted (fake) upstream (spec 5.4, 6.6).

A translated call must be indistinguishable, for budgets, request classes and telemetry, from the same
conversation spoken natively in chat: the upstream gets canonical chat with the chat policy, the call
record carries the canonical usage and finish, the ledger fills identically and exhausts at the same call;
only the bytes the harness sees are native (answers, errors, refusals, keepalives).
"""

from __future__ import annotations

import json
import threading

import pytest

from backend.generation.tests.wp06_gateway.support import FIXTURES
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import budget_rules
from backend.generation.tests.wp06_gateway.support import chat_completion
from backend.generation.tests.wp06_gateway.support import common
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import run_gateway
from backend.generation.tests.wp06_gateway.test_translators import ANSWERS
from backend.generation.tests.wp06_gateway.test_translators import WP07_GATEWAY
from backend.generation.tests.wp06_gateway.test_translators import _anthropic_blocks_from_stream
from backend.generation.tests.wp06_gateway.test_translators import _gemini_parts_from_stream
from backend.generation.tests.wp06_gateway.test_translators import _responses_items_from_stream

CHAT = "/api/v1/chat/completions"
GEMINI_STREAM = "/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse"
GEMINI_JSON = "/v1beta/models/gemini-2.5-pro:generateContent"
FRONT_CALLS = {
    "openai_chat": (CHAT, lambda text: {"model": "m", "messages": [{"role": "user", "content": text}]}),
    "anthropic_messages": (
        "/v1/messages?beta=true",
        lambda text: {"model": "claude", "max_tokens": 64, "messages": [{"role": "user", "content": text}]},
    ),
    "openai_responses": ("/api/v1/responses", lambda text: {"model": "codex", "input": text}),
    "gemini_generate": (GEMINI_JSON, lambda text: {"contents": [{"role": "user", "parts": [{"text": text}]}]}),
}
MODE = {"openai_chat": "native"}


def _cfg(front: str, **extra):
    return {"front_protocol": front, "protocol_mode": MODE.get(front, "translate"), **extra}


def _stream_body(front: str, text: str = "go") -> tuple[str, dict]:
    path, make = FRONT_CALLS[front]
    body = make(text)
    if front == "gemini_generate":
        return GEMINI_STREAM, body
    return path, {**body, "stream": True}


# =====================================================================================
# The upstream sees canonical chat with the chat policy
# =====================================================================================


@pytest.mark.parametrize("front", ["anthropic_messages", "openai_responses", "gemini_generate"])
def test_translated_call_reaches_the_chat_upstream_with_the_policy(tmp_path, front):
    path, make = FRONT_CALLS[front]
    reply = Reply(body=ANSWERS["anthropic"]["completion"])
    overrides = _cfg(
        front,
        model={"upstream_model": "anthropic/claude-sonnet-4.6", "max_output": 64000},
        call={"max_output_tokens": 2048},
        routing={"provider": {"order": ["anthropic"], "allow_fallbacks": False}},
        caching={"prompt_cache": "off", "ttl": "5m", "sticky_session": True, "cache_control_providers": []},
    )
    with run_gateway(tmp_path, replies=[reply], **overrides) as gw:
        resp = gw.request(path, make("Build it."))
        [record] = gw.calls(1)
        [received] = gw.upstream.received
    assert resp.status == 200
    assert received.path == "/api/v1/chat/completions"
    sent = received.body
    assert sent["model"] == "anthropic/claude-sonnet-4.6"
    assert sent["max_tokens"] == 2048
    assert sent["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
    assert sent["messages"][-1] == {"role": "user", "content": "Build it."}
    assert sent["usage"] == {"include": True}
    assert sent["session_id"] == record["request"]["session_id"]
    assert received.headers["authorization"] == "Bearer sk-real"
    # The record is the canonical one.
    assert record["front_protocol"] == front
    assert record["protocol_mode"] == "translate"
    assert record["request"] == sent
    assert record["finish_reason"] == "tool_calls"
    assert record["usage"]["prompt_tokens"] == 5210
    assert record["usage"]["cached_tokens"] == 4096
    assert record["usage"]["cost"] == 0.0168
    assert record["cost_source"] == "usage.cost"
    assert record["served_provider"] == "Anthropic"
    assert record["response_id"] == "gen-1762000000-anthropic"
    assert record["translation_dropped"] == {}
    assert record["native_request_sha"] == common.sha(make("Build it."))
    # The harness got its native answer.
    payload = resp.json()
    if front == "anthropic_messages":
        assert payload["type"] == "message"
        assert payload["stop_reason"] == "tool_use"
    elif front == "openai_responses":
        assert payload["object"] == "response"
        assert payload["output"][-1]["type"] == "function_call"
    else:
        assert payload["candidates"][0]["content"]["parts"][-1]["functionCall"]["name"] == "Bash"
    if front == "gemini_generate":
        assert record["requested_model"] == "gemini-2.5-pro"


@pytest.mark.parametrize("front", ["anthropic_messages", "openai_responses", "gemini_generate"])
def test_translated_streams_reach_the_harness_natively(tmp_path, front):
    events = [e.encode() for e in ANSWERS["openai"]["events"]]
    path, body = _stream_body(front)
    with run_gateway(tmp_path, replies=[Reply(events=events)], **_cfg(front)) as gw:
        resp = gw.request(path, body)
        [record] = gw.calls(1)
        sent = gw.upstream.received[0].body
    assert resp.status == 200
    assert resp.headers["content-type"] == "text/event-stream"
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert record["stream"] is True
    assert record["usage"]["completion_tokens"] == 210
    assert record["usage"]["reasoning_tokens"] == 128
    assert record["ttft_ms"] is not None
    if front == "anthropic_messages":
        blocks, final = _anthropic_blocks_from_stream(resp.raw)
        assert [b["type"] for b in blocks] == ["thinking", "redacted_thinking", "tool_use", "tool_use"]
        assert final["stop_reason"] == "tool_use"
    elif front == "openai_responses":
        items, final = _responses_items_from_stream(resp.raw)
        assert final["status"] == "completed"
        assert len(items) == 3
    else:
        parts, final = _gemini_parts_from_stream(resp.raw)
        assert final["finishReason"] == "STOP"
        assert [p["functionCall"]["name"] for p in parts if "functionCall" in p] == ["exec_command", "exec_command"]


def test_gemini_stream_without_alt_sse_is_a_json_array(tmp_path):
    events = [e.encode() for e in ANSWERS["open_weights"]["events"]]
    body = FRONT_CALLS["gemini_generate"][1]("go")
    path = "/v1beta/models/gemini-2.5-pro:streamGenerateContent"
    with run_gateway(tmp_path, replies=[Reply(events=events)], **_cfg("gemini_generate")) as gw:
        resp = gw.request(path, body)
    assert resp.headers["content-type"] == "application/json"
    chunks = json.loads(resp.raw)
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"


# =====================================================================================
# Budgets, request classes and telemetry are identical across fronts
# =====================================================================================

SCRIPT = [chat_completion(f"answer {i}", prompt=400 + i, completion=30, cost=0.002) for i in range(6)]


def _run_script(tmp_path, front: str):
    path, make = FRONT_CALLS[front]
    overrides = _cfg(front, budget={"max_cost_usd": 1.0, "max_llm_requests": 4, "max_aux_requests": 2})
    with run_gateway(tmp_path / front, replies=[Reply(body=b) for b in SCRIPT], **overrides) as gw:
        statuses = [gw.request(path, make(f"step {i} " + "x" * 200)).status for i in range(6)]
        records = gw.calls(4)
        state = gw.settled()
        events = gw.events()
    return statuses, records, state, events


def test_the_same_conversation_fills_the_ledger_identically_on_every_front(tmp_path):
    runs = {front: _run_script(tmp_path, front) for front in FRONT_CALLS}
    baseline_statuses, baseline_records, baseline_state, _ = runs["openai_chat"]
    assert baseline_statuses == [200, 200, 200, 200, 400, 400]
    assert baseline_state["budget_exhausted"] == "budget_exhausted:requests"
    keys = ("index", "request_class", "finish_reason", "usage", "cost", "cost_source", "list_cost", "truncated")
    for front, (statuses, records, state, events) in runs.items():
        assert statuses == baseline_statuses, front
        assert {k: v for k, v in state.items() if k not in ("updated", "last_call_end")} == {
            k: v for k, v in baseline_state.items() if k not in ("updated", "last_call_end")
        }, front
        assert [{k: r[k] for k in keys} for r in records] == [{k: r[k] for k in keys} for r in baseline_records], front
        assert [r["upstream_max_tokens"] for r in records] == [r["upstream_max_tokens"] for r in baseline_records]
        assert sum(1 for e in events if e["event"] == "budget_exhausted") == 1


@pytest.mark.parametrize("front", ["anthropic_messages", "openai_responses", "gemini_generate"])
def test_budget_refusals_are_native_and_never_read_as_context_overflow(tmp_path, front):
    from backend.generation.tests.wp06_gateway.test_gateway_http import CONTEXT_OVERFLOW

    path, make = FRONT_CALLS[front]
    overrides = _cfg(front, budget={"max_cost_usd": 1.0, "max_llm_requests": 1, "max_aux_requests": 0})
    with run_gateway(tmp_path, replies=[Reply(body=SCRIPT[0])], **overrides) as gw:
        assert gw.request(path, make("one")).status == 200
        refused = gw.request(path, make("two"))
        assert len(gw.upstream.received) == 1
    assert refused.status == 400
    payload = refused.json()
    message = payload["error"]["message"]
    assert message == budget_rules.BUDGET_MESSAGE
    assert not CONTEXT_OVERFLOW.search(message)
    if front == "anthropic_messages":
        assert payload["type"] == "error"
    elif front == "gemini_generate":
        assert payload["error"]["status"] == "INVALID_ARGUMENT"
        assert payload["error"]["code"] == 400


def test_claude_code_request_classes_count_against_the_aux_cap_in_translate_mode(tmp_path):
    path, make = FRONT_CALLS["anthropic_messages"]
    overrides = _cfg(
        "anthropic_messages",
        request_class_source="x-claude-code-request-class",
        budget={"max_cost_usd": 1.0, "max_llm_requests": 5, "max_aux_requests": 1},
    )
    with run_gateway(tmp_path, default=lambda _r: Reply(body=SCRIPT[0]), **overrides) as gw:
        main = gw.request(path, make("a"))
        aux = gw.request(path, make("b"), headers={"x-claude-code-request-class": "compaction"})
        aux2 = gw.request(path, make("c"), headers={"x-claude-code-request-class": "side_reply"})
        records = gw.calls(2)
        state = gw.settled()
    assert (main.status, aux.status, aux2.status) == (200, 200, 400)
    assert [r["request_class"] for r in records] == ["main", "compaction"]
    assert state["budget_exhausted"] == "budget_exhausted:aux_requests"


def test_call_input_cap_answers_in_the_front_wording(tmp_path):
    path, make = FRONT_CALLS["gemini_generate"]
    with run_gateway(
        tmp_path,
        **_cfg("gemini_generate", call={"max_output_tokens": 1000, "max_input_tokens": 10}),
    ) as gw:
        resp = gw.request(path, make("y" * 400))
        assert gw.upstream.received == []
        state = gw.settled()
    assert resp.status == 400
    assert "input token count" in resp.json()["error"]["message"]
    assert state["budget_exhausted"] is None


# =====================================================================================
# Errors, broken streams, keepalives
# =====================================================================================


@pytest.mark.parametrize("front", ["anthropic_messages", "openai_responses", "gemini_generate"])
def test_upstream_errors_keep_their_status_in_the_native_shape(tmp_path, front):
    path, make = FRONT_CALLS[front]
    error = {"error": {"message": "Rate limit exceeded: free-models-per-min", "code": 429}}
    with run_gateway(
        tmp_path,
        replies=[Reply(status=429, body=error)],
        **_cfg(front, retries={"upstream_retries": 0, "backoff_s": 0, "retry_on": ["5xx"], "llm_call_timeout_s": 30}),
    ) as gw:
        resp = gw.request(path, make("x"))
        [record] = gw.calls(1)
    assert resp.status == 429
    assert "Rate limit exceeded" in resp.json()["error"]["message"]
    assert record["status"] == 429
    assert record["usage"] is None
    assert record["cost"] == 0


def test_upstream_context_overflow_is_worded_for_the_harness_to_compact(tmp_path):
    path, make = FRONT_CALLS["anthropic_messages"]
    error = {
        "error": {
            "message": "This model's maximum context length is 131072 tokens.",
            "code": "context_length_exceeded",
        },
    }
    with run_gateway(tmp_path, replies=[Reply(status=400, body=error)], **_cfg("anthropic_messages")) as gw:
        resp = gw.request(path, make("x"))
    assert resp.status == 400
    assert resp.json()["error"]["message"].startswith("prompt is too long")


@pytest.mark.parametrize("front", ["anthropic_messages", "openai_responses", "gemini_generate"])
def test_a_stream_that_breaks_mid_way_ends_with_a_native_error_and_is_charged(tmp_path, front):
    events = [e.encode() for e in ANSWERS["anthropic"]["events"][:5]]
    path, body = _stream_body(front)
    with run_gateway(tmp_path, replies=[Reply(events=events, drop=True)], **_cfg(front)) as gw:
        resp = gw.request(path, body)
        [record] = gw.calls(1)
    assert resp.status == 200
    tail = resp.raw.rstrip().rsplit(b"\n", 1)[-1]
    if front == "anthropic_messages":
        assert b'"type": "error"' in tail
    elif front == "openai_responses":
        assert b"response.failed" in tail
    else:
        assert json.loads(tail)["error"]["code"] == 502
    assert record["usage_estimated"] is True
    assert record["cost_source"] == "reservation"


def test_upstream_silence_keeps_the_translated_stream_alive_with_native_pings(tmp_path):
    gate = threading.Event()
    events = [e.encode() for e in ANSWERS["open_weights"]["events"]]
    timer = threading.Timer(0.9, gate.set)
    path, body = _stream_body("anthropic_messages")
    with run_gateway(
        tmp_path,
        replies=[Reply(events=events, hold=gate)],
        ping_interval_s=0.25,
        **_cfg("anthropic_messages"),
    ) as gw:
        timer.start()
        resp = gw.request(path, body)
    pings = [c for t, c in resp.chunks if b"event: ping" in c and t < 0.9]
    assert len(pings) >= 2
    blocks, final = _anthropic_blocks_from_stream(resp.raw)
    assert final["stop_reason"] == "end_turn"
    assert blocks[-1]["text"].startswith("Done.")


def test_a_json_answer_to_a_stream_request_is_re_encoded_as_a_native_stream(tmp_path):
    path, body = _stream_body("openai_responses")
    reply = Reply(body=ANSWERS["openai"]["completion"])
    with run_gateway(tmp_path, replies=[reply], **_cfg("openai_responses")) as gw:
        resp = gw.request(path, body)
        [record] = gw.calls(1)
    items, final = _responses_items_from_stream(resp.raw)
    assert final["status"] == "completed"
    assert next(i["type"] for i in items) == "reasoning"
    assert record["usage"]["prompt_tokens"] == 3021


# =====================================================================================
# translation_dropped, local answers, unsupported requests, recorded sessions
# =====================================================================================


def test_translation_dropped_is_recorded_and_moot_cache_markers_are_not(tmp_path):
    body = {
        "model": "claude",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "q"}],
        "tools": [
            {"name": "t", "input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral"}},
            {"type": "bash_20250124", "name": "bash"},
        ],
    }
    path = FRONT_CALLS["anthropic_messages"][0]
    off = {"prompt_cache": "off", "ttl": "5m", "sticky_session": False, "cache_control_providers": []}
    on = {**off, "prompt_cache": "on"}
    records = {}
    for name, caching in (("off", off), ("on", on)):
        with run_gateway(
            tmp_path / name,
            replies=[Reply(body=SCRIPT[0])],
            **_cfg("anthropic_messages", caching=caching),
        ) as gw:
            gw.request(path, body)
            [records[name]] = gw.calls(1)
    assert records["off"]["translation_dropped"] == {"tools.bash_20250124": 1}
    assert records["on"]["translation_dropped"] == {"cache_control": 1, "tools.bash_20250124": 1}


def test_gemini_local_routes(tmp_path):
    with run_gateway(
        tmp_path,
        **_cfg(
            "gemini_generate",
            model={"upstream_model": "google/gemini-3-pro-preview", "presented_names": ["gemini-2.5-pro"]},
        ),
    ) as gw:
        counted = gw.request(
            "/v1beta/models/gemini-2.5-pro:countTokens",
            {"contents": [{"role": "user", "parts": [{"text": "x" * 36}]}]},
        )
        models = gw.request("/v1beta/models", method="GET")
        other = gw.request("/v1/chat/completions", {"messages": []})
        assert gw.upstream.received == []
    assert counted.json() == {"totalTokens": 10, "estimate": True}
    assert models.json()["models"][0]["name"] == "models/gemini-2.5-pro"
    assert other.status == 404
    assert other.json()["error"]["status"] == "NOT_FOUND"


def test_untranslatable_requests_are_refused_natively(tmp_path):
    with run_gateway(tmp_path, **_cfg("openai_responses")) as gw:
        stateful = gw.request("/api/v1/responses", {"model": "codex", "input": "x", "previous_response_id": "resp_1"})
        conversation = gw.request("/api/v1/responses", {"model": "codex", "input": "x", "conversation": "conv_1"})
        image = gw.request(
            "/api/v1/responses",
            {
                "model": "codex",
                "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:x"}]}],
            },
        )
        assert gw.upstream.received == []
    assert stateful.status == conversation.status == image.status == 400
    assert image.json()["error"]["code"] == "unsupported_content"


SESSIONS = {
    "claude-code": WP07_GATEWAY / "claude-code.json",
    "codex": WP07_GATEWAY / "codex.json",
    "gemini-cli": FIXTURES / "translate" / "gemini_cli_session.json",
}


@pytest.mark.parametrize("harness", sorted(SESSIONS))
def test_recorded_harness_sessions_translate_end_to_end(tmp_path, harness):
    """The pinned binaries' real requests (WP-07 smoke sessions; the Gemini CLI session recorded by
    WP-06) through translate mode: nothing dropped, classes and the system prompt recorded."""
    session = json.loads(SESSIONS[harness].read_text())
    front = session["front_protocol"]
    replies = [Reply(events=[e.encode() for e in ANSWERS[f]["events"]]) for f in ("anthropic", "open_weights")]
    overrides = _cfg(front, request_class_source=session["request_class_source"])
    with run_gateway(tmp_path, replies=replies, **overrides) as gw:
        responses = [gw.request(c["path"], c["body"], headers=c.get("headers")) for c in session["calls"]]
        records = gw.calls(len(session["calls"]))
        state = gw.settled()
    assert [r.status for r in responses] == [200, 200]
    assert [r["translation_dropped"] for r in records] == [{}, {}]
    assert [r["request_class"] for r in records] == ["main", "main"]
    assert state["requests_by_class"]["main"] == 2
    assert records[0]["native_system_prompt"]
    assert records[1]["native_system_prompt"] is None


def test_local_overlay_turns_reasoning_details_into_reasoning_text():
    import importlib
    import sys

    from backend.generation.tests.wp06_gateway.support import LOCAL_DIR

    sys.path.insert(0, str(LOCAL_DIR))
    try:
        local = importlib.import_module("local")
    finally:
        sys.path.remove(str(LOCAL_DIR))
    cfg = config_dict(
        "http://proxy:8001/v1",
        front_protocol="anthropic_messages",
        protocol_mode="translate",
        upstream={"kind": "local_proxy"},
    )
    upstream = local.LocalProxyUpstream(common.Policy.from_config(cfg), api_key="")
    body = {
        "model": "qwen3:floor",
        "messages": [
            {"role": "assistant", "content": None, "reasoning_details": ANSWERS["anthropic"]["expect_details"]},
            {
                "role": "assistant",
                "content": "x",
                "reasoning": "kept",
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "E"}],
            },
        ],
        "provider": {"only": ["x"]},
    }
    out = upstream.prepare(body)
    assert out["model"] == "qwen3"
    assert "provider" not in out
    assert out["messages"][0] == {
        "role": "assistant",
        "content": None,
        "reasoning": ANSWERS["anthropic"]["expect_details"][0]["text"],
    }
    assert out["messages"][1] == {"role": "assistant", "content": "x", "reasoning": "kept"}


def test_recorded_gemini_cli_requests_translate_without_loss():
    from backend.generation.tests.wp06_gateway.support import gemini_generate

    session = json.loads(SESSIONS["gemini-cli"].read_text())
    _first, second = session["calls"]
    for call in session["calls"]:
        path, _, query = call["path"].partition("?")
        options = gemini_generate.request_options(path, query)
        assert options == {"stream": True, "sse": True, "model": "gemini-2.5-pro"}
        chat, dropped = gemini_generate.to_chat(call["body"], **options)
        assert dropped == call["expect_dropped"]
        assert chat["messages"][0]["role"] == "system"
        assert chat["reasoning"] == {"max_tokens": 8192}
        assert all(t["function"]["parameters"].get("type") == "object" for t in chat["tools"])
    chat, _ = gemini_generate.to_chat(second["body"], stream=True, model="google/gemini-2.5-pro")
    assistant, tool = chat["messages"][-2:]
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"] == "run_shell_command_1793724095801_0"
    # Gemini CLI's own placeholder signature goes to a Google upstream as a native Gemini detail (there it
    # means "skip validation"); for any other upstream it is dropped (test_wave3_followups.py).
    assert assistant["reasoning_details"] == [
        {
            "type": "reasoning.encrypted",
            "data": "skip_thought_signature_validator",
            "format": "google-gemini-v1",
            "id": "run_shell_command_1793724095801_0",
        },
    ]
