"""Strict OpenRouter client: payload, failure detection, retries, usage capture."""

from __future__ import annotations

import pytest
import requests

from backend.analysis.engine.reviewer import client
from backend.analysis.tests.reviewer_support import CONNECTION_ERROR
from backend.analysis.tests.reviewer_support import MODEL
from backend.analysis.tests.reviewer_support import FakeResponse
from backend.analysis.tests.reviewer_support import FakeSession
from backend.analysis.tests.reviewer_support import chat_body
from backend.analysis.tests.reviewer_support import ok

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def chat(session, sleeps=None, **kw):
    sleeps = [] if sleeps is None else sleeps
    return client.chat(
        "sk-test-key",
        model=MODEL,
        messages=MESSAGES,
        session=session,
        sleep=sleeps.append,
        **kw,
    )


def test_success_captures_content_usage_and_provenance(settings):
    settings.OPENROUTER_HTTP_REFERER = "https://lab.example"
    session = FakeSession(ok('{"findings": []}', gen_id="gen-42", provider="Parasail", cost=0.0021))
    call = chat(
        session,
        params={"temperature": 0.2, "max_tokens": 1000, "seed": None},
        response_format={"type": "json_object"},
        provider={"require_parameters": True},
        timeout_s=12,
    )
    assert call.ok is True
    assert call.content == '{"findings": []}'
    assert call.finish_reason == "stop"
    assert call.provider == "Parasail"
    assert call.model == MODEL
    assert call.generation_id == "gen-42"
    assert call.status_code == 200
    assert call.retries == 0
    assert call.error == ""
    assert call.usage == {
        "prompt_tokens": 3000,
        "completion_tokens": 800,
        "total_tokens": 3800,
        "reasoning_tokens": 200,
        "cached_tokens": 0,
        "cost": 0.0021,
    }
    assert call.raw["id"] == "gen-42"
    sent = session.requests[0]
    assert sent["url"] == client.OPENROUTER_COMPLETIONS_URL
    assert sent["timeout"] == 12
    assert sent["headers"]["Authorization"] == "Bearer sk-test-key"
    assert sent["headers"]["HTTP-Referer"] == "https://lab.example"
    body = sent["json"]
    assert body["model"] == MODEL
    assert body["messages"] == MESSAGES
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 1000
    assert "seed" not in body  # None params are dropped, nothing implicit added
    assert body["response_format"] == {"type": "json_object"}
    assert body["provider"] == {"require_parameters": True}
    assert body["usage"] == {"include": True}
    assert body["stream"] is False
    # The recorded request never carries the key.
    assert "sk-test-key" not in repr(call.request)


def test_no_implicit_sampling_params():
    session = FakeSession(ok("{}"))
    chat(session)
    body = session.requests[0]["json"]
    assert set(body) == {"model", "messages", "usage", "stream"}


def test_retries_rate_limit_honouring_retry_after():
    sleeps = []
    session = FakeSession(
        FakeResponse(429, {"error": {"code": 429, "message": "slow down"}}, headers={"Retry-After": "7"}),
        ok("{}"),
    )
    call = chat(session, sleeps)
    assert call.ok is True
    assert call.retries == 1
    assert sleeps == [7.0]
    assert [a["error_kind"] for a in call.attempts] == ["rate_limited", ""]


def test_retry_after_http_date_is_parsed():
    assert client._retry_after({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}) == 0.0
    assert client._retry_after({"Retry-After": "3.5"}) == 3.5
    assert client._retry_after({"Retry-After": "soon"}) is None
    assert client._retry_after({}) is None


def test_excessive_retry_after_gives_up():
    session = FakeSession(FakeResponse(503, {"error": {"message": "down"}}, headers={"Retry-After": "3600"}))
    call = chat(session)
    assert call.ok is False
    assert call.error_kind == "server"
    assert "Retry-After" in call.error
    assert len(session.requests) == 1


def test_server_errors_and_network_errors_are_retried_with_backoff(monkeypatch):
    monkeypatch.setattr(client.random, "uniform", lambda a, b: 0.0)
    sleeps = []
    session = FakeSession(
        FakeResponse(502, {"error": {"message": "bad gateway"}}),
        CONNECTION_ERROR,
        requests.exceptions.ReadTimeout("read timed out"),
        ok("{}"),
    )
    call = chat(session, sleeps, max_retries=3)
    assert call.ok is True
    assert call.retries == 3
    assert sleeps == [2.0, 4.0, 8.0]
    assert [a["error_kind"] for a in call.attempts] == ["server", "network", "network", ""]


def test_gives_up_after_max_retries():
    session = FakeSession(*[FakeResponse(500, "oops")] * 3)
    call = chat(session, max_retries=2)
    assert call.ok is False
    assert call.error_kind == "server"
    assert call.retries == 2
    assert call.error.startswith("HTTP 500")
    assert len(session.requests) == 3


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "bad_request"), (401, "auth"), (403, "auth"), (402, "credits"), (404, "not_found"), (422, "bad_request")],
)
def test_client_errors_are_not_retried(status, kind):
    session = FakeSession(FakeResponse(status, {"error": {"code": status, "message": "nope"}}))
    call = chat(session)
    assert call.ok is False
    assert call.error_kind == kind
    assert call.error == f"HTTP {status}: nope"
    assert len(session.requests) == 1


def test_error_in_http_200_body_is_a_failure_and_retried_when_transient():
    body = {"error": {"code": 502, "message": "upstream died", "metadata": {"provider_name": "Foo"}}}
    session = FakeSession(FakeResponse(200, body), ok("{}"))
    call = chat(session)
    assert call.ok is True
    assert call.attempts[0]["error_kind"] == "provider_error"
    assert "upstream died (provider Foo)" in call.attempts[0]["error"]


def test_error_in_http_200_body_with_client_code_is_final():
    session = FakeSession(FakeResponse(200, {"error": {"code": 400, "message": "context too long"}}))
    call = chat(session)
    assert call.ok is False
    assert call.error_kind == "bad_request"
    assert "context too long" in call.error
    assert len(session.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"id": "x", "choices": []},
        {"id": "x"},
        chat_body(None),
        chat_body(""),
        chat_body("   \n"),
    ],
)
def test_empty_answers_are_failures(body):
    session = FakeSession(*[FakeResponse(200, body)] * 2)
    call = chat(session, max_retries=1)
    assert call.ok is False
    assert call.error_kind == "empty"
    assert call.content == ""
    assert len(session.requests) == 2


def test_empty_answer_at_length_is_not_retried():
    session = FakeSession(FakeResponse(200, chat_body(None, finish_reason="length")))
    call = chat(session)
    assert call.ok is False
    assert call.finish_reason == "length"
    assert call.error_kind == "empty"
    assert len(session.requests) == 1


def test_truncated_answer_with_content_is_ok_but_flagged():
    session = FakeSession(ok('{"findings": [', finish_reason="length"))
    call = chat(session)
    assert call.ok is True
    assert call.finish_reason == "length"


def test_refusal_and_content_filter_are_final():
    refusal = chat_body(None)
    refusal["choices"][0]["message"]["refusal"] = "I can't help with that"
    call = chat(FakeSession(FakeResponse(200, refusal)))
    assert (call.ok, call.error_kind) == (False, "refusal")
    call = chat(FakeSession(ok("partial", finish_reason="content_filter")))
    assert (call.ok, call.error_kind) == (False, "content_filter")


def test_choice_level_error_is_a_provider_error():
    body = chat_body("x", finish_reason="error")
    body["choices"][0]["error"] = {"code": 500, "message": "mid-stream failure"}
    call = chat(FakeSession(FakeResponse(200, body), FakeResponse(200, body)), max_retries=1)
    assert call.ok is False
    assert call.error_kind == "provider_error"
    assert "mid-stream failure" in call.error


def test_call_fields_describe_the_final_attempt():
    first = chat_body("", finish_reason="stop", provider="FirstProvider", gen_id="gen-a")
    call = chat(FakeSession(FakeResponse(200, first), CONNECTION_ERROR), max_retries=1)
    assert call.ok is False
    assert call.error_kind == "network"
    assert (call.provider, call.generation_id, call.finish_reason, call.raw) == ("", "", "", {})
    assert call.attempts[0]["provider"] == "FirstProvider"
    assert call.status_code is None


def test_non_json_200_body_is_retried():
    session = FakeSession(FakeResponse(200, "<html>gateway</html>"), ok("{}"))
    call = chat(session)
    assert call.ok is True
    assert call.attempts[0]["error_kind"] == "invalid_body"


def test_content_parts_are_joined():
    body = chat_body(None)
    body["choices"][0]["message"]["content"] = [{"type": "text", "text": '{"a":'}, {"type": "text", "text": "1}"}]
    call = chat(FakeSession(FakeResponse(200, body)))
    assert call.ok is True
    assert call.content == '{"a":1}'


def test_usage_sums_every_billed_attempt():
    first = chat_body("", cost=0.001, prompt_tokens=100, completion_tokens=10)
    second = chat_body("{}", cost=0.002, prompt_tokens=200, completion_tokens=20)
    call = chat(FakeSession(FakeResponse(200, first), FakeResponse(200, second)))
    assert call.ok is True
    assert call.usage["cost"] == pytest.approx(0.003)
    assert call.usage["prompt_tokens"] == 300
    assert call.usage["completion_tokens"] == 30


def test_missing_usage_stays_missing():
    body = chat_body("{}")
    del body["usage"]
    call = chat(FakeSession(FakeResponse(200, body)))
    assert call.ok is True
    assert call.usage == {}
    body = chat_body("{}", cost=None)
    call = chat(FakeSession(FakeResponse(200, body)))
    assert "cost" not in call.usage
