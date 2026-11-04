"""Validity notes (spec 6.13) and the record helpers of the runner (6.9)."""

from __future__ import annotations

from backend.generation.agentic import runner
from backend.generation.agentic.validity import add_notes
from backend.generation.agentic.validity import build_validity
from backend.generation.agentic.validity import note
from backend.generation.agentic.validity import policy_knob
from backend.generation.contracts.outcome import Termination
from backend.generation.contracts.resources import preset_content
from backend.generation.testing.fake_gateway import canned_call


def codes(validity) -> set[str]:
    return {n.code for n in validity.notes}


def manifest(served_name: str | None = None, *, slug: str = "deepseek/deepseek-v4.1-flash", **harness) -> dict:
    return {
        "model": {"slug": slug, "endpoint": {"id": 1, "kind": "ollama", "served_model_name": served_name}},
        "harness": {"name": "opencode", "unsupported_knobs": [], **harness},
    }


def test_truncation_notes_and_flag() -> None:
    assert codes(build_validity(termination=Termination.WALL_CLOCK)) == {"wall_clock"}
    assert codes(build_validity(termination=Termination.IDLE)) == {"idle_timeout"}
    assert codes(build_validity(termination=Termination.MAX_TURNS)) == {"max_turns"}
    budget = build_validity(termination=Termination.BUDGET, budget_exhausted="budget_exhausted:cost")
    assert codes(budget) == {"budget_exhausted:cost"}
    assert budget.truncated is True
    assert build_validity(termination=Termination.COMPLETED).truncated is False


def test_harness_and_llm_error_notes() -> None:
    assert codes(build_validity(termination=Termination.HARNESS_ERROR)) == {"harness_error"}
    assert codes(build_validity(termination=Termination.HARNESS_CRASH)) == {"harness_crash"}
    assert codes(build_validity(termination=Termination.LLM_ERROR)) == {"ended_on_llm_error"}


def test_call_truncations_count_successful_length_calls() -> None:
    calls = [canned_call(1, finish_reason="length"), canned_call(2, finish_reason="length"), canned_call(3)]
    assert "call_truncations:2" in codes(build_validity(termination=Termination.COMPLETED, calls=calls))


def test_served_model_differs_uses_the_frozen_served_name_not_the_slug() -> None:
    # The provider serves the slug under another name: no note without a frozen served name ...
    calls = [{**canned_call(1), "served_model": "qwen3-coder:30b-q4"}]
    assert "served_model_differs" not in codes(
        build_validity(termination="completed", calls=calls, manifest=manifest()),
    )
    # ... and none when it matches the frozen served name, even though it differs from the slug.
    same = build_validity(termination="completed", calls=calls, manifest=manifest("qwen3-coder:30b-q4"))
    assert "served_model_differs" not in codes(same)
    other = build_validity(termination="completed", calls=calls, manifest=manifest("qwen3-coder:30b-q8"))
    assert "served_model_differs" in codes(other)


def test_provider_mixed_and_response_model_differs() -> None:
    calls = [
        {**canned_call(1, provider="A"), "response_model": "m-1"},
        {**canned_call(2, provider="B"), "response_model": "m-2"},
    ]
    found = codes(build_validity(termination="completed", calls=calls))
    assert {"provider_mixed", "response_model_differs"} <= found


def test_harness_knob_notes() -> None:
    effective = {"content": preset_content("frugal").model_dump(mode="json")}
    found = codes(
        build_validity(
            termination="completed",
            manifest={
                **manifest(unsupported_knobs=["max_turns", "compaction"], vendor_affinity="anthropic"),
                "resources": {"effective": effective},
            },
        ),
    )
    assert {"unsupported_knob:max_turns", "unsupported_knob:compaction", "call_cap_without_compaction"} <= found
    assert "vendor_affinity" in found  # a deepseek model on an anthropic-affine harness


def test_add_notes_keeps_existing_codes() -> None:
    first = build_validity(termination=Termination.WALL_CLOCK).model_dump(mode="json")
    merged = add_notes(first, [note("eval_reused"), note("wall_clock", level="error")])
    assert [n["code"] for n in merged["notes"]] == ["wall_clock", "eval_reused"]
    assert merged["notes"][0]["level"] == "info"


def test_policy_knob_reads_every_recorded_shape() -> None:
    content = preset_content("frugal").model_dump(mode="json")
    expected = content["run"]["max_llm_requests"]
    assert policy_knob({"content": content}, "run.max_llm_requests") == expected
    assert policy_knob(content, "run.max_llm_requests") == expected
    assert policy_knob({"run.max_llm_requests": 7}, "run.max_llm_requests") == 7
    assert policy_knob({}, "run.max_llm_requests") is None


def test_budget_exhausted_when_main_requests_reach_the_cap() -> None:
    calls = [canned_call(i) for i in range(1, 4)] + [canned_call(4, request_class="auxiliary")]
    assert runner.budget_exhausted_kind(state={}, events=[], calls=calls, max_llm_requests=3) == "requests"
    assert runner.budget_exhausted_kind(state={}, events=[], calls=calls, max_llm_requests=4) is None
    failed = [*calls[:2], canned_call(3, status=502)]
    assert runner.budget_exhausted_kind(state={}, events=[], calls=failed, max_llm_requests=3) is None


def test_budget_exhausted_reported_by_the_gateway_wins() -> None:
    state = {"budget_exhausted": "budget_exhausted:cost"}
    assert runner.budget_exhausted_kind(state=state, events=[], calls=[], max_llm_requests=40) == "cost"
    events = [{"event": "budget_exhausted", "kind": "tokens"}]
    assert runner.budget_exhausted_kind(state=None, events=events, calls=[], max_llm_requests=40) == "tokens"


def test_attempt_usage_and_served() -> None:
    calls = [
        canned_call(1, prompt_tokens=100, completion_tokens=10, cached_tokens=40),
        canned_call(2, request_class="compaction", prompt_tokens=50, completion_tokens=5),
        canned_call(3, status=502),
    ]
    usage = runner.attempt_usage(calls)
    assert usage.requests_by_class == {"main": 1, "compaction": 1}
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens) == (150, 15, 40)
    assert usage.uncached_prompt_tokens == 110
    served = runner.attempt_served(calls)
    assert served.providers == {"FakeProvider": 2}
    telemetry = runner.call_telemetry(calls[0])
    assert telemetry["tokens"]["prompt"] == 100
    assert telemetry["request_class"] == "main"
    assert "error" in runner.call_telemetry(calls[2])
