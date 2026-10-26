"""The gateway call log -> usage / providers / failure evidence of one agentic review sample."""

from __future__ import annotations

import json

import pytest

from backend.analysis.engine.reviewer.agentic_usage import CallLog
from backend.analysis.engine.reviewer.agentic_usage import jsonl


def _call(status=200, *, content="ok", finish="stop", provider="InferenceNet", usage=None, error=None, retries=0):
    response = {
        "id": "gen-1",
        "model": "deepseek/deepseek-v4.1-flash",
        "provider": provider,
        "content": content,
        "finish_reason": finish,
        "usage": usage,
        "error": error,
    }
    return {"seq": 1, "status": status, "response": response, "duration_s": 1.5, "upstream_retries": retries}


def _usage(prompt=1000, completion=100, cost=0.001, reasoning=10, cached=500):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost": cost,
        "completion_tokens_details": {"reasoning_tokens": reasoning},
        "prompt_tokens_details": {"cached_tokens": cached},
    }


def _log(*calls, events=()):
    return CallLog.from_logs(
        {
            "calls.jsonl": "\n".join(json.dumps(c) for c in calls).encode(),
            "gateway_events.jsonl": "\n".join(json.dumps(e) for e in events).encode(),
        },
    )


def test_jsonl_skips_garbage_and_non_objects():
    data = b'{"a": 1}\nnot json\n[1, 2]\n\n{"b": 2}\n'
    assert jsonl(data) == [{"a": 1}, {"b": 2}]
    assert jsonl(None) == []


def test_usage_sums_every_call_including_failed_billed_ones():
    log = _log(
        _call(usage=_usage(cost=0.002), retries=1),
        _call(status=502, usage=_usage(prompt=10, completion=0, cost=0.0001, reasoning=0, cached=0)),
        _call(usage=_usage(cost=0.003), finish="length"),
    )
    usage = log.usage()
    assert usage["llm_calls"] == 3
    assert usage["llm_calls_failed"] == 1
    assert usage["prompt_tokens"] == 2010
    assert usage["completion_tokens"] == 200
    assert usage["total_tokens"] == 2210
    assert usage["reasoning_tokens"] == 20
    assert usage["cached_tokens"] == 1000
    assert usage["cost_usd"] == pytest.approx(0.0051)
    assert usage["cost_estimated"] is False
    assert usage["retries"] == 1
    assert usage["http_attempts"] == 4
    assert usage["truncated_calls"] == 1
    assert usage["served_models"] == {"deepseek/deepseek-v4.1-flash": 2}


def test_missing_usage_is_none_not_zero_and_missing_cost_is_estimated():
    no_usage = _log(_call(status=502, usage=None))
    usage = no_usage.usage()
    assert usage["prompt_tokens"] is None
    assert usage["completion_tokens"] is None
    assert usage["calls_without_usage"] == 1
    assert usage["cost_usd"] is None  # a call happened but its spend is unknown
    assert usage["cost_complete"] is False

    estimated = _log(_call(usage={"prompt_tokens": 1000, "completion_tokens": 100}))
    usage = estimated.usage(pricing={"input_per_token": 1e-6, "output_per_token": 2e-6})
    assert usage["cost_usd"] == pytest.approx(0.0012)
    assert usage["cost_estimated"] is True
    assert usage["total_tokens"] == 1100


def test_no_logged_call_means_nothing_was_spent():
    usage = _log().usage()
    assert usage["llm_calls"] == 0
    assert usage["cost_usd"] == 0.0
    assert usage["prompt_tokens"] is None


def test_partially_unknown_usage_is_flagged_as_a_lower_bound():
    usage = _log(_call(usage=_usage(cost=0.002)), _call(status=502, usage=None)).usage()
    assert usage["cost_usd"] == pytest.approx(0.002)
    assert usage["cost_complete"] is False
    assert usage["calls_without_usage"] == 1


def test_providers_and_finish_reasons_count_successful_calls_only():
    log = _log(
        _call(provider="A", finish="tool_calls", usage=_usage()),
        _call(provider="B", finish="stop", usage=_usage()),
        _call(status=429, provider="C", error={"message": "rate limited"}),
    )
    assert log.providers() == {"A": 1, "B": 1}
    assert log.finish_reasons() == {"tool_calls": 1, "stop": 1}
    assert log.last_answer() == ("ok", "stop")


def test_error_in_http_200_body_is_a_failed_call():
    log = _log(_call(status=200, error={"message": "provider died"}))
    assert log.ok_calls == []
    assert log.first_error() == "HTTP 200: provider died"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (402, "insufficient_credits"), (404, "model_unavailable"), (400, "bad_request")],
)
def test_failure_kind_classifies_like_single_shot(status, kind):
    log = _log(_call(status=status, error={"message": "nope"}))
    assert log.failure_kind() == kind
    assert log.first_error().startswith(f"HTTP {status}")


def test_failure_kind_defaults_to_api_error_and_gateway_errors_are_reported():
    log = CallLog(calls=[{"status": 502, "error": "ConnectionResetError()"}])
    assert log.failure_kind() == "api_error"
    assert "ConnectionResetError" in log.first_error()


def test_budget_exhaustion_comes_from_the_gateway_events():
    log = _log(
        _call(usage=_usage()),
        events=[{"event": "started"}, {"event": "budget_exhausted", "reason": "max_cost_usd"}],
    )
    assert log.budget_exhausted == "max_cost_usd"
    assert log.gateway_summary()["budget_exhausted"] == "max_cost_usd"
    assert _log(_call(usage=_usage())).budget_exhausted is None


def test_gateway_summary_reports_rejections_and_model_overrides():
    call = _call(usage=_usage())
    call["policy_diff"] = {"model": "openai/gpt-4o-mini"}
    log = _log(
        call,
        events=[
            {"event": "rejected_path", "path": "/v1/embeddings"},
            {"event": "rejected_connect", "target": "evil.example:443"},
            {"event": "upstream_retry", "attempt": 1},
        ],
    )
    summary = log.gateway_summary()
    assert summary["rejected_paths"] == ["/v1/embeddings"]
    assert summary["rejected_connects"] == ["evil.example:443"]
    assert summary["upstream_retry_events"] == 1
    assert summary["policy_model_overrides"] == 1
