"""Non-stream calls streamed upstream under per-call limits (E2E-17).

A provider that does not bound reasoning by ``max_tokens`` used to hang a non-stream agent call until the
timeout chain ended the run as infra. The pump now streams such calls, re-assembles the answer, and cuts it
at the output cap or the per-call wall clock with ``finish_reason=length``: a model truncation.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import timedelta
from unittest import mock

import pytest

from backend.inference import pump
from backend.inference import security
from backend.inference import upstream
from backend.inference.tests.fake_openai_stream import PROMPT_TOKENS
from backend.inference.tests.fake_openai_stream import REASONING_PIECE
from backend.inference.tests.fake_openai_stream import VERBOSE_PIECE
from backend.inference.tests.fake_openai_stream import FakeOpenAIStream

CHAT = "/v1/chat/completions"


@pytest.fixture
def fake():
    server = FakeOpenAIStream().start()
    yield server
    server.stop()


@pytest.fixture
def target(fake):
    tgt = security.Target(
        scheme="http",
        host="llm.example.org",
        port=fake.port,
        base_path="",
        addresses=("127.0.0.1",),
    )
    with mock.patch("backend.inference.upstream.validate_direct_base_url", return_value=tgt):
        yield tgt


def _body(model: str, **extra) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "build it"}],
        "max_tokens": 200,
        "stream": False,
        **extra,
    }


def _call(model: str, limits: upstream.StreamLimits | None, timeout: float = 10.0, **extra):
    return upstream.call(
        "http://llm.example.org",
        "POST",
        CHAT,
        _body(model, **extra),
        timeout=timeout,
        stream_limits=limits,
    )


LIMITS = upstream.StreamLimits(max_output_tokens=200, wall_clock_s=10.0, prompt_tokens_estimate=40)


# =====================================================================================
# upstream.call
# =====================================================================================


def test_a_finished_stream_is_one_completion_with_the_streams_own_usage(fake, target):
    resp = _call("normal", LIMITS)
    sent = fake.received[-1]
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert resp.status == 200
    payload = resp.payload
    assert payload["object"] == "chat.completion"
    assert payload["id"] == "chatcmpl-fake"
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"] == {"role": "assistant", "content": "Hello, world", "reasoning_content": "hmm hmm hmm "}
    # Exact usage from the stream, never an estimate.
    assert payload["usage"]["prompt_tokens"] == PROMPT_TOKENS
    assert payload["usage"]["completion_tokens"] == 9
    assert "llmlab_estimated" not in payload["usage"]
    assert "llmlab_cut" not in payload


def test_a_never_ending_reasoning_stream_is_cut_at_the_output_cap(fake, target):
    started = time.monotonic()
    resp = _call("runaway", LIMITS)
    assert time.monotonic() - started < 5
    payload = resp.payload
    choice = payload["choices"][0]
    assert resp.status == 200
    assert choice["finish_reason"] == "length"
    assert choice["native_finish_reason"] == "llmlab_cut:max_output_tokens"
    assert choice["message"]["content"] == ""
    assert choice["message"]["reasoning_content"].startswith(REASONING_PIECE)
    usage = payload["usage"]
    assert usage["llmlab_estimated"] is True
    assert usage["prompt_tokens"] == 40
    # Cut once the output text reaches 8 chars per capped token (a runaway backstop): within one chunk of it.
    cut_chars = 200 * upstream.CAP_CUT_CHARS_PER_TOKEN
    assert cut_chars <= payload["llmlab_cut"]["output_chars"] < cut_chars + len(REASONING_PIECE)
    assert usage["completion_tokens"] == math.ceil(payload["llmlab_cut"]["output_chars"] / 3.6)
    assert usage["completion_tokens_details"]["reasoning_tokens"] == usage["completion_tokens"]
    assert payload["llmlab_cut"]["reason"] == "max_output_tokens"
    # The upstream stream was closed, so the provider stops generating.
    assert fake.wait_disconnects(1)


def test_a_slow_runaway_is_cut_at_the_wall_clock(fake, target):
    limits = upstream.StreamLimits(max_output_tokens=1_000_000, wall_clock_s=0.6, prompt_tokens_estimate=40)
    started = time.monotonic()
    resp = _call("slow-runaway", limits)
    elapsed = time.monotonic() - started
    assert 0.55 <= elapsed < 2.5
    choice = resp.payload["choices"][0]
    assert choice["finish_reason"] == "length"
    assert choice["native_finish_reason"] == "llmlab_cut:wall_clock"
    assert resp.payload["llmlab_cut"]["reason"] == "wall_clock"
    assert 0 < resp.payload["usage"]["completion_tokens"] < 1000


def test_a_silent_stream_is_still_an_upstream_timeout(fake, target):
    limits = upstream.StreamLimits(max_output_tokens=200, wall_clock_s=0.5)
    with pytest.raises(upstream.UpstreamError) as err:
        _call("silent", limits)
    assert err.value.kind == "timeout"


def test_a_server_that_ignores_stream_answers_json_as_before(fake, target):
    resp = _call("json", LIMITS)
    assert resp.status == 200
    assert resp.payload["choices"][0]["message"]["content"] == "plain"
    assert resp.payload["usage"]["prompt_tokens"] == PROMPT_TOKENS


def test_tool_calls_are_reassembled_and_a_cut_partial_call_is_dropped(fake, target):
    whole = _call("tools", LIMITS).payload
    message = whole["choices"][0]["message"]
    assert whole["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] is None
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "write", "arguments": '{"path": "a.txt", "text": "hi"}'},
        },
    ]
    cut = _call("tools-runaway", LIMITS).payload
    message = cut["choices"][0]["message"]
    assert cut["choices"][0]["finish_reason"] == "length"
    assert message["content"] == "Writing it."
    assert "tool_calls" not in message
    assert cut["llmlab_cut"]["dropped_tool_calls"] == 1


def test_broken_and_errored_streams_are_failures_not_answers(fake, target):
    with pytest.raises(upstream.UpstreamError) as err:
        _call("broken", LIMITS)
    assert err.value.kind == "connect"
    resp = _call("error", LIMITS)
    assert resp.status == 502
    assert resp.payload["error"]["message"] == "provider overloaded"
    resp = _call("http-500", LIMITS)
    assert resp.status == 500


def test_a_provider_that_bounds_its_answer_is_never_cut(fake, target):
    # 560 chars for 100 real tokens (5.6 chars/token: long identifiers). The old 3.6 estimate with 10 %
    # headroom cut it at 396 chars and booked a truncation and an estimate instead of the provider's own.
    limits = upstream.StreamLimits(max_output_tokens=100, wall_clock_s=10.0, prompt_tokens_estimate=40)
    payload = _call("verbose", limits).payload
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "length"
    assert "native_finish_reason" not in choice
    assert choice["message"]["content"] == VERBOSE_PIECE * 14
    assert "llmlab_cut" not in payload
    # The usage chunk that follows the finish is kept: exact, not estimated.
    assert payload["usage"]["completion_tokens"] == 100
    assert payload["usage"]["prompt_tokens"] == PROMPT_TOKENS
    assert "llmlab_estimated" not in payload["usage"]


def test_a_stream_without_a_usage_chunk_reports_an_estimated_usage(fake, target):
    payload = _call("no-usage", LIMITS).payload
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "hello"
    assert "llmlab_cut" not in payload
    # Never 0 tokens (0 spend, caps bypassed): the prompt estimate plus ceil(chars / 3.6), marked.
    assert payload["usage"] == {
        "prompt_tokens": 40,
        "completion_tokens": 2,
        "total_tokens": 42,
        "completion_tokens_details": {"reasoning_tokens": 0},
        "llmlab_estimated": True,
    }


def test_a_finished_answer_is_not_cut_while_waiting_for_its_usage(fake, target):
    limits = upstream.StreamLimits(max_output_tokens=200, wall_clock_s=0.5, prompt_tokens_estimate=40)
    payload = _call("finish-then-stall", limits).payload
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert "native_finish_reason" not in choice
    assert choice["message"]["content"] == "done here"
    assert "llmlab_cut" not in payload
    assert payload["usage"]["llmlab_estimated"] is True
    assert payload["usage"]["completion_tokens"] == math.ceil(len("done here") / 3.6)


def test_without_limits_or_for_stream_bodies_nothing_changes(fake, target):
    resp = _call("normal", None)
    assert fake.received[-1]["stream"] is False
    assert "stream_options" not in fake.received[-1]
    assert resp.payload["choices"][0]["message"]["content"] == "plain"


# =====================================================================================
# pump: which calls stream, and under which limits
# =====================================================================================


def _row(**fields):
    defaults = {"source": "agent_proxy", "method": "POST", "path": CHAT, "body": _body("m"), "job_id": None}
    defaults.update(fields)
    return mock.Mock(**defaults)


def _content(timeout_s=300, max_output=8192, reasoning=None):
    return {
        "time": {"wall_clock_s": 1200, "llm_call_timeout_s": timeout_s},
        "call": {"max_output_tokens": max_output, "max_input_tokens": 72000},
        "reasoning": reasoning or {"mode": "provider_default"},
    }


def test_stream_limits_follow_the_policy_and_the_body():
    endpoint = mock.Mock(request_timeout_s=900)
    with mock.patch.object(pump, "_policy_content", return_value=_content()):
        limits = pump.stream_limits(_row(job_id="r"), endpoint)
        # The body's policy-pinned max_tokens; the wall clock 30 s before the gateway's 300 s timeout.
        assert limits.max_output_tokens == 200
        assert limits.wall_clock_s == 270
        assert limits.call_wall_left_s == 270
        assert limits.prompt_tokens_estimate > 0
        assert pump.stream_limits(_row(source="slot_call", job_id="r"), endpoint) is not None
        for skipped in (
            _row(source="analysis"),
            _row(path="/api/chat"),
            _row(body=_body("m", stream=True)),
            _row(method="GET"),
        ):
            assert pump.stream_limits(skipped, endpoint) is None
    budget = _content(timeout_s=1800, reasoning={"mode": "max_tokens", "max_tokens": 1000})
    with mock.patch.object(pump, "_policy_content", return_value=budget):
        body = _body("m")
        body.pop("max_tokens")
        limits = pump.stream_limits(_row(job_id="r", body=body), endpoint)
        # No max_tokens in the body: call.max_output_tokens plus the reasoning budget; never past the endpoint.
        assert limits.max_output_tokens == 9192
        assert limits.wall_clock_s == 900
        assert limits.call_wall_left_s == 1770
    with mock.patch.object(pump, "_policy_content", return_value={}):
        limits = pump.stream_limits(_row(), endpoint)
        assert limits.wall_clock_s == 900
        assert limits.call_wall_left_s is None


def test_the_wall_clock_runs_from_the_rows_creation():
    """Queue time and earlier attempts come off the policy wall (E2E-20): the cut answer still beats the gateway."""
    from django.utils import timezone

    endpoint = mock.Mock(request_timeout_s=900)
    with mock.patch.object(pump, "_policy_content", return_value=_content()):
        queued = _row(job_id="r", created_at=timezone.now() - timedelta(seconds=100))
        limits = pump.stream_limits(queued, endpoint)
        assert 169 <= limits.wall_clock_s <= 170
        assert limits.call_wall_left_s == pytest.approx(limits.wall_clock_s)
        spent = _row(job_id="r", created_at=timezone.now() - timedelta(seconds=400))
        limits = pump.stream_limits(spent, endpoint)
        assert limits.wall_clock_s == 0
        assert limits.call_wall_left_s < 0


@pytest.mark.django_db(transaction=True)
def test_the_pump_records_a_runaway_agent_call_as_a_truncation(fake, target, owner):
    from backend.inference import requests as q
    from backend.inference.access import access_from_snapshot
    from backend.inference.chat import raise_for_request
    from backend.inference.chat import translate
    from backend.inference.models import LLMRequest
    from backend.inference.tests.conftest import make_agent_run
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model

    endpoint = make_endpoint(
        owner,
        kind="openai_compatible",
        api_flavor="openai",
        connection="direct",
        base_url="http://llm.example.org",
        request_timeout_s=900,
    )
    model = make_local_model(endpoint, name="runaway")
    run = make_agent_run(model, owner)
    manifest = run.manifest.manifest
    manifest["resources"]["effective"] = {"content": _content(timeout_s=300, max_output=64)}
    type(run.manifest)._base_manager.filter(pk=run.manifest.pk).update(manifest=manifest)
    section = {"id": endpoint.pk, "kind": endpoint.kind, "served_model_name": "runaway", "digest": model.digest}
    access = access_from_snapshot(section, endpoint)
    rid = q.enqueue(
        access,
        CHAT,
        {"model": "runaway", "messages": [{"role": "user", "content": "go"}], "max_tokens": 64, "stream": False},
        job=run,
        source=LLMRequest.Source.AGENT_PROXY,
        priority=1,
        digest="",
    )
    done = threading.Event()
    result = {}

    def work():
        try:
            result["handled"] = pump.run_one(endpoint.pk, "exec-test")
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    assert done.wait(20)
    assert result["handled"] is True
    row = LLMRequest.objects.select_related("endpoint").get(pk=rid)
    assert row.status == LLMRequest.Status.DONE
    assert row.response_status == 200
    assert row.completion_tokens >= 64
    raise_for_request(row)  # no error: a model answer
    answer = translate(row, access)
    assert answer["choices"][0]["finish_reason"] == "length"
    assert answer["usage"]["llmlab_estimated"] is True
    assert answer["llmlab_cut"]["reason"] == "max_output_tokens"


def _enqueue_agent_call(owner, model_name: str, *, timeout_s: int, age_s: float):
    from django.utils import timezone

    from backend.inference import requests as q
    from backend.inference.access import access_from_snapshot
    from backend.inference.models import LLMRequest
    from backend.inference.tests.conftest import make_agent_run
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model

    endpoint = make_endpoint(
        owner,
        kind="openai_compatible",
        api_flavor="openai",
        connection="direct",
        base_url="http://llm.example.org",
        request_timeout_s=900,
    )
    model = make_local_model(endpoint, name=model_name)
    run = make_agent_run(model, owner)
    manifest = run.manifest.manifest
    manifest["resources"]["effective"] = {"content": _content(timeout_s=timeout_s, max_output=64)}
    type(run.manifest)._base_manager.filter(pk=run.manifest.pk).update(manifest=manifest)
    section = {"id": endpoint.pk, "kind": endpoint.kind, "served_model_name": model_name, "digest": model.digest}
    rid = q.enqueue(
        access_from_snapshot(section, endpoint),
        CHAT,
        {"model": model_name, "messages": [{"role": "user", "content": "go"}], "max_tokens": 64, "stream": False},
        job=run,
        source=LLMRequest.Source.AGENT_PROXY,
        priority=1,
        digest="",
    )
    LLMRequest.objects.filter(pk=rid).update(created_at=timezone.now() - timedelta(seconds=age_s))
    return endpoint, rid


@pytest.mark.django_db(transaction=True)
def test_a_call_whose_wall_clock_was_spent_queued_fails_fast_without_an_upstream_call(fake, target, owner):
    from backend.inference.models import LLMRequest

    endpoint, rid = _enqueue_agent_call(owner, "normal", timeout_s=300, age_s=400)
    started = time.monotonic()
    assert pump.run_one(endpoint.pk, "exec-test") is True
    assert time.monotonic() - started < 5
    row = LLMRequest.objects.get(pk=rid)
    assert row.status == LLMRequest.Status.FAILED
    assert row.error_kind == "timeout"
    assert row.attempt == 0
    assert fake.received == []


@pytest.mark.django_db(transaction=True)
def test_a_timeout_after_the_wall_clock_is_not_requeued(fake, target, owner):
    from backend.inference.models import LLMRequest

    # Policy 10 s less a 1 s margin, 7 s already spent queued: ~2 s left, and a silent upstream uses them up.
    endpoint, rid = _enqueue_agent_call(owner, "silent", timeout_s=10, age_s=7)
    started = time.monotonic()
    assert pump.run_one(endpoint.pk, "exec-test") is True
    assert time.monotonic() - started < 6
    row = LLMRequest.objects.get(pk=rid)
    assert row.status == LLMRequest.Status.FAILED
    assert row.error_kind == "timeout"
    assert row.attempt == 0
    assert "not retried" in row.error
    assert len(fake.received) == 1
