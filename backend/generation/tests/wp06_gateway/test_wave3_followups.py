"""Wave-3 follow-ups of the gateway (WP-06): Gemini model paths and placeholder signatures (WP-07 asks),
non-streamed ``finish_reason=error`` as a native error on every translated front, and the seed on
translated Anthropic/Responses fronts (lead decision 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.generation.budget import gateway_config
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import anthropic_messages
from backend.generation.tests.wp06_gateway.support import chat_completion
from backend.generation.tests.wp06_gateway.support import gemini_generate
from backend.generation.tests.wp06_gateway.support import openai_responses
from backend.generation.tests.wp06_gateway.support import run_gateway
from backend.generation.tests.wp06_gateway.support import translation

WP07_GEMINI = (
    Path(__file__).resolve().parents[1] / "wp07_harnesses" / "fixtures" / "gateway-translate" / "gemini-cli.json"
)
FRONTS = {
    "anthropic_messages": anthropic_messages,
    "openai_responses": openai_responses,
    "gemini_generate": gemini_generate,
}
CALLS = {
    "anthropic_messages": (
        "/v1/messages?beta=true",
        {"model": "claude", "max_tokens": 64, "messages": [{"role": "user", "content": "x"}]},
    ),
    "openai_responses": ("/api/v1/responses", {"model": "codex", "input": "x"}),
    "gemini_generate": (
        "/v1beta/models/deepseek/deepseek-v4.1-flash:generateContent",
        {"contents": [{"role": "user", "parts": [{"text": "x"}]}]},
    ),
}
NO_RETRY = {"upstream_retries": 0, "backoff_s": 0, "retry_on": ["5xx"], "llm_call_timeout_s": 30}


def _failed_turn(*, choice_error: bool = False, top_error: bool = False) -> dict:
    body = chat_completion("partial answ", finish="error")
    if choice_error:
        body["choices"][0]["error"] = {"message": "provider died mid-generation", "code": 502}
    if top_error:
        body["error"] = {"message": "provider died mid-generation", "code": 502}
    return body


# =====================================================================================
# Gemini: the model in the URL keeps its vendor prefix (WP-07 ask 2)
# =====================================================================================


@pytest.mark.parametrize(
    ("path", "model"),
    [
        ("/v1beta/models/deepseek/deepseek-v4.1-flash:streamGenerateContent", "deepseek/deepseek-v4.1-flash"),
        ("/v1beta/models/deepseek%2Fdeepseek-v4.1-flash:generateContent", "deepseek/deepseek-v4.1-flash"),
        ("/v1beta/models/google/gemini-2.5-pro:countTokens", "google/gemini-2.5-pro"),
        ("/v1beta/models/gemini-2.5-pro:generateContent", "gemini-2.5-pro"),
        ("/v1beta/models/qwen/qwen3-coder:free:generateContent", "qwen/qwen3-coder:free"),
        ("/v1beta/models", None),
    ],
)
def test_requested_model_keeps_the_vendor_prefix(path, model):
    assert gemini_generate.request_options(path, "alt=sse")["model"] == model


def test_the_call_record_names_a_vendor_prefixed_model(tmp_path):
    path, body = CALLS["gemini_generate"]
    with run_gateway(
        tmp_path,
        replies=[Reply(body=chat_completion())],
        front_protocol="gemini_generate",
        protocol_mode="translate",
    ) as gw:
        resp = gw.request(path, body)
        [record] = gw.calls(1)
    assert resp.status == 200
    assert record["requested_model"] == "deepseek/deepseek-v4.1-flash"


# =====================================================================================
# Gemini: the CLI's placeholder thoughtSignature is "no signature" for a non-Google upstream (WP-07 ask 1)
# =====================================================================================


def test_the_recorded_gemini_cli_placeholder_is_dropped_for_a_non_google_model():
    second = json.loads(WP07_GEMINI.read_text())["calls"][1]
    path, _, query = second["path"].partition("?")
    options = gemini_generate.request_options(path, query)
    assert options["model"] == "deepseek/deepseek-v4.1-flash"
    chat, dropped = gemini_generate.to_chat(second["body"], **options)
    assert dropped == {"thoughtSignature.synthetic": 1}
    assistant = next(m for m in chat["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
    assert "reasoning_details" not in assistant
    assert "reasoning" not in assistant
    assert all("skip_thought_signature_validator" not in json.dumps(m) for m in chat["messages"])


@pytest.mark.parametrize("model", ["google/gemini-2.5-pro", "gemini-2.5-flash"])
def test_the_placeholder_still_reaches_a_google_model(model):
    second = json.loads(WP07_GEMINI.read_text())["calls"][1]
    chat, dropped = gemini_generate.to_chat(second["body"], stream=True, model=model)
    assert dropped == {}
    assistant = next(m for m in chat["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant["reasoning_details"][0]["data"] == "skip_thought_signature_validator"
    assert assistant["reasoning_details"][0]["format"] == "google-gemini-v1"


def test_a_real_signature_is_never_dropped_for_any_model():
    body = {
        "contents": [
            {"role": "user", "parts": [{"text": "go"}]},
            {
                "role": "model",
                "parts": [{"functionCall": {"name": "ls", "args": {}}, "thoughtSignature": "c2lnbmF0dXJl"}],
            },
            {"role": "user", "parts": [{"functionResponse": {"name": "ls", "response": {"output": "a"}}}]},
        ],
    }
    chat, dropped = gemini_generate.to_chat(body, model="deepseek/deepseek-v4.1-flash")
    assert dropped == {}
    assert chat["messages"][1]["reasoning_details"][0]["data"] == "c2lnbmF0dXJl"


def test_a_part_holding_only_the_placeholder_counts_once():
    body = {
        "contents": [
            {"role": "user", "parts": [{"text": "go"}]},
            {"role": "model", "parts": [{"text": "ok"}, {"thoughtSignature": "skip_thought_signature_validator"}]},
            {"role": "user", "parts": [{"text": "next"}]},
        ],
    }
    _, dropped = gemini_generate.to_chat(body, model="moonshotai/kimi-k3")
    assert dropped == {"thoughtSignature.synthetic": 1}


def test_google_model_names():
    assert gemini_generate.is_google_model("google/gemini-3-pro")
    assert gemini_generate.is_google_model("gemini-2.5-pro")
    assert not gemini_generate.is_google_model("deepseek/deepseek-v4.1-flash")
    assert not gemini_generate.is_google_model("gemma3:27b")
    assert not gemini_generate.is_google_model(None)


# =====================================================================================
# Non-streamed finish_reason=error: a native error on every front
# =====================================================================================


@pytest.mark.parametrize("variant", ["finish_only", "choice_error", "top_error"])
def test_completion_failure_reads_every_failed_turn_shape(variant):
    body = _failed_turn(choice_error=variant == "choice_error", top_error=variant == "top_error")
    message, code = translation.completion_failure(body)
    if variant == "finish_only":
        assert message == translation.FINISHED_WITH_ERROR
        assert code is None
    else:
        assert message == "provider died mid-generation"
        assert code == "502"
    assert translation.completion_failure(chat_completion()) is None


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_from_chat_turns_a_failed_turn_into_the_native_error(front):
    native = FRONTS[front].from_chat(_failed_turn(choice_error=True), CALLS[front][1])
    if front == "anthropic_messages":
        assert native["type"] == "error"
        assert native["error"]["type"] == "api_error"
        assert "provider died" in native["error"]["message"]
    elif front == "openai_responses":
        assert native["object"] == "response"
        assert native["status"] == "failed"
        assert native["error"] == {"code": "502", "message": "provider died mid-generation"}
        assert native["output"][0]["type"] == "message"  # the partial output is kept
    else:
        assert set(native) == {"error"}
        assert native["error"]["status"] == "UNAVAILABLE"
        assert "provider died" in native["error"]["message"]


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_a_non_streamed_failed_turn_reaches_the_harness_as_a_native_error(tmp_path, front):
    path, body = CALLS[front]
    with run_gateway(
        tmp_path,
        replies=[Reply(body=_failed_turn())],
        front_protocol=front,
        protocol_mode="translate",
        retries=NO_RETRY,
    ) as gw:
        resp = gw.request(path, body)
        [record] = gw.calls(1)
    assert resp.status == 502
    payload = resp.json()
    if front == "openai_responses":
        assert payload["status"] == "failed"
        assert payload["error"] == {"code": "upstream_error", "message": translation.FINISHED_WITH_ERROR}
    else:
        assert translation.FINISHED_WITH_ERROR in payload["error"]["message"]
    # The record stays the canonical one (the upstream answered 200 with finish_reason=error).
    assert record["status"] == 200
    assert record["finish_reason"] == "error"


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_a_stream_that_finishes_with_error_names_it(front):
    chunk = {"id": "g", "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "error"}]}
    raw = b"".join(FRONTS[front].stream_from_chat(iter([b"data: " + json.dumps(chunk).encode() + b"\n\n"])))
    assert translation.FINISHED_WITH_ERROR.encode() in raw
    assert translation.STREAM_ENDED_EARLY.encode() not in raw


def test_a_choice_level_error_in_a_stream_is_the_error_event():
    chunk = {
        "id": "g",
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "error", "error": {"message": "overloaded", "code": 529}},
        ],
    }
    raw = b"".join(anthropic_messages.stream_from_chat(iter([b"data: " + json.dumps(chunk).encode() + b"\n\n"])))
    assert b"overloaded" in raw


# =====================================================================================
# Lead decision 1: the seed travels on translated Anthropic/Responses fronts
# =====================================================================================


@pytest.mark.parametrize(
    ("front", "mode", "carried"),
    [
        ("openai_chat", "native", True),
        ("gemini_generate", "translate", True),
        ("anthropic_messages", "translate", True),
        ("openai_responses", "translate", True),
        ("anthropic_messages", "passthrough", False),
        ("openai_responses", "passthrough", False),
        ("anthropic_messages", "native", False),
        (None, None, True),
    ],
)
def test_seed_carried(front, mode, carried):
    assert gateway_config.seed_carried(front, mode) is carried


@pytest.mark.parametrize("front", ["anthropic_messages", "openai_responses"])
def test_a_translated_anthropic_or_responses_call_sends_the_seed_upstream(tmp_path, front):
    path, body = CALLS[front]
    with run_gateway(
        tmp_path,
        replies=[Reply(body=chat_completion())],
        front_protocol=front,
        protocol_mode="translate",
        sampling={"forced": {}, "stripped": [], "seed": 4242, "forward_seed": True},
    ) as gw:
        resp = gw.request(path, body)
        [received] = gw.upstream.received
        [record] = gw.calls(1)
    assert resp.status == 200
    assert received.body["seed"] == 4242
    assert record["request"]["seed"] == 4242
