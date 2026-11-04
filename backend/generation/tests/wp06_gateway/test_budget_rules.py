"""``protocols/budget_rules.py``: pre-call reservation, caps per request class, cost bases, state.json keys."""

from __future__ import annotations

import random
import threading

import pytest

from backend.generation.testing.fake_gateway import STATE_KEYS as FAKE_STATE_KEYS
from backend.generation.tests.wp06_gateway.support import budget_rules as br

PRICING = br.Pricing(input_per_mtok=1.0, output_per_mtok=2.0)


def limits(**kw):
    base = {"max_cost_usd": 1.0, "max_total_tokens": 1_000_000, "max_llm_requests": 10, "max_aux_requests": 5}
    base.update(kw)
    base.setdefault("pricing", PRICING)
    return br.BudgetLimits(**base)


def usage(prompt=100, completion=10, cost=None, cached=None, cache_write=None, reasoning=None):
    return br.normalized_usage(prompt, completion, reasoning, cached, cache_write, cost)


def test_state_keys_match_the_spec_and_the_fake():
    assert br.STATE_KEYS == FAKE_STATE_KEYS
    assert set(br.Ledger(limits()).snapshot()) == set(br.STATE_KEYS)


def test_estimate_is_chars_over_3_6_rounded_up():
    assert br.estimate_tokens("") == 0
    assert br.estimate_tokens("a" * 36) == 10
    assert br.estimate_tokens("a" * 37) == 11


def test_prompt_text_covers_every_front_shape():
    chat = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}], "tools": [{"x": 1}]}
    assert "hello" in br.prompt_text(chat)
    assert '"x": 1' in br.prompt_text(chat)
    anthropic = {"system": [{"type": "text", "text": "SYS"}], "messages": [{"role": "user", "content": "U"}]}
    assert br.prompt_text(anthropic).split("\n") == ["SYS", "U"]
    responses = {"instructions": "INS", "input": [{"type": "function_call_output", "output": "OUT"}]}
    assert br.prompt_text(responses).split("\n") == ["INS", "OUT"]


BIG = "x" * 36_000  # 10,000 estimated tokens


def _at_least_big(body):
    return br.estimate_tokens(br.prompt_text(body)) >= 10_000


def test_prompt_estimate_counts_chat_tool_call_history():
    body = {
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "write", "arguments": BIG}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
    }
    assert _at_least_big(body)


def test_prompt_estimate_counts_anthropic_tool_use_inputs():
    for tool_input in (
        {"command": BIG},
        {"file_path": "a.py", "old_string": BIG[:18_000], "new_string": BIG[18_000:]},
    ):
        body = {
            "system": "S",
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Edit", "input": tool_input}],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "done"}]},
            ],
        }
        assert _at_least_big(body)


def test_prompt_estimate_counts_responses_function_calls():
    body = {
        "instructions": "I",
        "input": [
            {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": BIG},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ],
    }
    assert _at_least_big(body)


def test_prompt_estimate_counts_gemini_function_calls():
    body = {
        "systemInstruction": {"parts": [{"text": "S"}]},
        "contents": [
            {"role": "model", "parts": [{"functionCall": {"name": "write_file", "args": {"content": BIG}}}]},
            {"role": "user", "parts": [{"functionResponse": {"name": "write_file", "response": {"ok": True}}}]},
        ],
    }
    assert _at_least_big(body)


def test_tool_heavy_context_trips_the_max_input_tokens_precheck():
    body = {"messages": [{"role": "assistant", "tool_calls": [{"function": {"name": "w", "arguments": BIG}}]}]}
    ledger = br.Ledger(limits(call_max_input_tokens=5_000))
    with pytest.raises(br.Refusal) as refusal:
        ledger.reserve("main", br.estimate_tokens(br.prompt_text(body)), 10)
    assert refusal.value.kind == br.CONTEXT_BUDGET


def test_request_class_from_header_or_default():
    source = "x-claude-code-request-class"
    assert br.request_class(source, {"X-Claude-Code-Request-Class": "Compaction"}) == ("compaction", "Compaction")
    assert br.request_class(source, {"x-claude-code-request-class": "workflow"}) == ("auxiliary", "workflow")
    assert br.request_class(source, {}) == ("main", None)
    assert br.request_class("default:main", {"x-claude-code-request-class": "subagent"}) == ("main", None)


def test_upstream_max_tokens_adds_the_reasoning_budget_and_clamps():
    cfg = {"call": {"max_output_tokens": 8192}, "reasoning": {"mode": "max_tokens", "max_tokens": 4000}, "model": {}}
    assert br.upstream_max_tokens(cfg) == 12192
    cfg["model"] = {"max_output": 10000}
    assert br.upstream_max_tokens(cfg) == 10000
    cfg["reasoning"] = {"mode": "effort", "effort": "high", "max_tokens": 4000}
    assert br.upstream_max_tokens(cfg) == 8192


def test_reservation_refuses_instead_of_clamping():
    # Worst case of one call: 1000 prompt x $1/M + 100k max_tokens x $2/M = $0.201.
    ledger = br.Ledger(limits(max_cost_usd=0.5))
    first = ledger.reserve("main", 1000, 100_000)
    second = ledger.reserve("main", 1000, 100_000)
    assert (first.max_tokens, second.max_tokens) == (100_000, 100_000)
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 1000, 100_000)  # 3 x 0.201 > 0.5 even though nothing has been spent yet
    assert exc.value.kind == "cost"
    assert exc.value.message == br.BUDGET_MESSAGE
    assert ledger.snapshot()["budget_exhausted"] == "budget_exhausted:cost"


def test_exhaustion_is_sticky():
    ledger = br.Ledger(limits(max_cost_usd=0.25))
    with pytest.raises(br.Refusal):
        ledger.reserve("main", 1000, 200_000)  # 0.401 > 0.25
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 1, 1)  # would fit, but the run is over
    assert exc.value.kind == "cost"


def test_main_request_cap_fires_after_exactly_max_successful_calls():
    ledger = br.Ledger(limits(max_llm_requests=3))
    for _ in range(3):
        ledger.settle(ledger.reserve("main", 10, 10), usage(), ok=True)
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 10, 10)
    assert exc.value.kind == "requests"
    assert ledger.snapshot()["requests_by_class"]["main"] == 3


def test_in_flight_calls_count_toward_the_request_cap():
    ledger = br.Ledger(limits(max_llm_requests=2))
    ledger.reserve("main", 10, 10)
    ledger.reserve("main", 10, 10)
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 10, 10)
    assert exc.value.kind == "requests"


def test_aux_classes_share_their_own_cap_and_leave_main_alone():
    ledger = br.Ledger(limits(max_llm_requests=5, max_aux_requests=2))
    ledger.settle(ledger.reserve("subagent", 10, 10), usage(), ok=True)
    ledger.settle(ledger.reserve("compaction", 10, 10), usage(), ok=True)
    for _ in range(5):
        ledger.settle(ledger.reserve("main", 10, 10), usage(), ok=True)
    assert ledger.snapshot()["requests_by_class"] == {"main": 5, "subagent": 1, "compaction": 1, "auxiliary": 0}
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("auxiliary", 10, 10)
    assert exc.value.kind == "aux_requests"


def test_failed_calls_count_only_when_configured():
    plain = br.Ledger(limits(max_llm_requests=1))
    plain.settle(plain.reserve("main", 10, 10), None, ok=False)
    plain.reserve("main", 10, 10)  # the failure did not use the single request
    strict = br.Ledger(limits(max_llm_requests=1, count_failed_calls=True))
    strict.settle(strict.reserve("main", 10, 10), None, ok=False)
    with pytest.raises(br.Refusal):
        strict.reserve("main", 10, 10)


def test_attempt_ceiling_stops_a_harness_looping_on_failures():
    ledger = br.Ledger(limits(max_llm_requests=1, max_aux_requests=0, max_attempts=4))
    for _ in range(4):
        ledger.settle(ledger.reserve("main", 10, 10), None, ok=False)
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 10, 10)
    assert exc.value.kind == "attempts"
    assert limits(max_llm_requests=10, max_aux_requests=5).attempt_ceiling == 50


def test_token_caps_reserve_prompt_and_max_tokens():
    ledger = br.Ledger(limits(max_total_tokens=1000))
    ledger.reserve("main", 300, 300)
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 300, 300)  # 600 held + 600 > 1000
    assert exc.value.kind == "tokens"


def test_uncached_input_and_output_caps():
    ledger = br.Ledger(limits(max_uncached_input_tokens=500))
    ledger.settle(ledger.reserve("main", 400, 10), usage(prompt=400, cached=300, completion=10), ok=True)
    ledger.reserve("main", 350, 10)  # 100 uncached so far + 350 <= 500
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 100, 10)  # 100 + 350 held + 100 > 500
    assert exc.value.kind == "uncached_input"
    out = br.Ledger(limits(max_output_tokens=1000))
    out.reserve("main", 10, 600)
    with pytest.raises(br.Refusal) as exc:
        out.reserve("main", 10, 600)
    assert exc.value.kind == "output_tokens"


def test_max_input_tokens_refuses_one_call_without_exhausting_the_run():
    ledger = br.Ledger(limits(call_max_input_tokens=1000))
    with pytest.raises(br.Refusal) as exc:
        ledger.reserve("main", 1001, 10)
    assert exc.value.kind == "context_budget"
    assert not exc.value.exhausted
    assert ledger.exhausted is None
    ledger.reserve("main", 999, 10)


def test_cost_bases():
    billed = br.Ledger(limits())
    s = billed.settle(billed.reserve("main", 100, 10), usage(prompt=100, completion=10, cost=0.5), ok=True)
    assert (s.cost, s.cost_source) == (0.5, "usage.cost")
    assert s.list_cost == pytest.approx((100 * 1 + 10 * 2) / 1e6)
    s = billed.settle(billed.reserve("main", 100, 10), usage(prompt=100, completion=10, cost=None), ok=True)
    assert (s.cost_source, s.cost) == ("price_table", pytest.approx(120 / 1e6))
    listed = br.Ledger(limits(cost_basis="list_price"))
    s = listed.settle(listed.reserve("main", 100, 10), usage(prompt=100, completion=10, cost=0.0), ok=True)
    assert (s.cost_source, s.cost) == ("list_price", pytest.approx(120 / 1e6))
    free = br.Ledger(limits(cost_basis="free", max_cost_usd=0.0, max_total_tokens=10**8))
    for _ in range(3):
        s = free.settle(free.reserve("main", 10**6, 10**5), usage(prompt=10**5, completion=10**4), ok=True)
    assert s.cost == 0.0
    assert free.snapshot()["list_cost"] > 0


def test_cached_and_cache_write_pricing():
    pricing = br.Pricing(
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cached_input_per_mtok=0.3,
        cache_write_per_mtok=3.75,
    )
    cost = pricing.price(prompt_tokens=10_000, completion_tokens=1000, cached_tokens=8000, cache_write_tokens=1000)
    assert cost == pytest.approx((1000 * 3 + 8000 * 0.3 + 1000 * 3.75 + 1000 * 15) / 1e6)


def test_reservation_prices_prompt_tokens_at_the_cache_write_rate_when_dearer():
    pricing = br.Pricing(input_per_mtok=3.0, output_per_mtok=15.0, cached_input_per_mtok=0.3, cache_write_per_mtok=6.0)
    reserved = pricing.worst_case(100_000, 1000)
    assert reserved == pytest.approx((100_000 * 6.0 + 1000 * 15.0) / 1e6)
    assert pricing.price(100_000, 1000, cache_write_tokens=100_000) <= reserved
    assert pricing.price(100_000, 1000, cached_tokens=100_000) <= reserved
    assert pricing.price(100_000, 1000) <= reserved
    cheap_writes = br.Pricing(input_per_mtok=3.0, output_per_mtok=15.0, cache_write_per_mtok=1.0)
    assert cheap_writes.worst_case(100_000, 0) == pytest.approx(0.3)


def test_failed_call_the_upstream_accepted_is_settled_at_its_reservation():
    ledger = br.Ledger(limits())
    reservation = ledger.reserve("main", 100, 1000)
    s = ledger.settle(reservation, None, ok=False, upstream_billed=True)
    assert (s.usage_estimated, s.cost_source) == (True, "reservation")
    assert s.cost == pytest.approx(reservation.usd)
    assert s.cost > 0
    stats = ledger.stats()
    assert stats["failed_by_class"]["main"] == 1
    assert stats["requests_by_class"]["main"] == 0
    assert (stats["prompt_tokens"], stats["completion_tokens"]) == (100, 1000)
    # A call the upstream refused (no usage, not billed) costs nothing.
    refused = ledger.settle(ledger.reserve("main", 100, 1000), None, ok=False)
    assert (refused.cost, refused.cost_source) == (0.0, "none")


def test_success_without_usage_is_settled_at_the_reservation_never_zero():
    ledger = br.Ledger(limits())
    reservation = ledger.reserve("main", 100, 1000)
    s = ledger.settle(reservation, None, ok=True)
    assert s.usage_estimated
    assert s.cost == pytest.approx(reservation.usd)
    assert s.cost > 0
    state = ledger.snapshot()
    assert (state["prompt_tokens"], state["completion_tokens"]) == (100, 1000)


def test_truncations_are_counted():
    ledger = br.Ledger(limits())
    ledger.settle(ledger.reserve("main", 10, 10), usage(), ok=True, finish_reason="length")
    ledger.settle(ledger.reserve("main", 10, 10), usage(), ok=True, truncated=True)
    ledger.settle(ledger.reserve("main", 10, 10), usage(), ok=True, finish_reason="stop")
    assert ledger.snapshot()["call_truncations"] == 2


def test_settle_is_idempotent_per_reservation():
    ledger = br.Ledger(limits())
    reservation = ledger.reserve("main", 10, 10)
    ledger.settle(reservation, usage(cost=0.1), ok=True)
    ledger.settle(reservation, usage(cost=0.1), ok=True)
    assert ledger.snapshot()["cost"] == pytest.approx(0.1)
    assert ledger.snapshot()["requests_by_class"]["main"] == 1


def test_calibration_learns_when_the_estimate_is_low():
    ledger = br.Ledger(limits())
    ledger.settle(ledger.reserve("main", 100, 10), usage(prompt=200, completion=1), ok=True)
    reservation = ledger.reserve("main", 100, 10)
    assert (reservation.est_prompt_tokens, reservation.prompt_tokens) == (100, 200)
    ledger.settle(reservation, usage(prompt=10_000, completion=1), ok=True)
    assert ledger.reserve("main", 100, 10).prompt_tokens == int(100 * br.MAX_CALIBRATION)


def test_on_change_sees_every_start_and_end():
    seen = []
    ledger = br.Ledger(limits(), on_change=lambda state: seen.append(state["inflight"]))
    reservation = ledger.reserve("main", 10, 10)
    ledger.settle(reservation, usage(), ok=True)
    assert seen == [1, 0]


def test_concurrent_in_flight_calls_never_overshoot_the_run_cap():
    """Spec 13.4 (WP-06): with every call's actual usage within its reservation, accrued cost never
    passes ``run.max_cost_usd``, however many calls are in flight at once."""
    cap = 0.05
    # Cache writes cost more than plain input (Anthropic 1.25x / 2x): reservations must cover them.
    pricing = br.Pricing(input_per_mtok=1.0, output_per_mtok=2.0, cached_input_per_mtok=0.1, cache_write_per_mtok=2.0)
    ledger = br.Ledger(
        limits(
            max_cost_usd=cap,
            max_llm_requests=10_000,
            max_aux_requests=10_000,
            max_attempts=10**6,
            pricing=pricing,
        ),
    )
    rng = random.Random(7)  # noqa: S311 - a reproducible test schedule
    plans = [(rng.randint(100, 5000), rng.randint(100, 4000)) for _ in range(400)]
    peaks: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def worker(chunk):
        barrier.wait()
        for est, max_tokens in chunk:
            try:
                reservation = ledger.reserve(rng.choice(["main", "subagent"]), est, max_tokens)
            except br.Refusal:
                return
            # The upstream bills at most the reserved prompt and max_tokens.
            prompt = rng.randint(1, reservation.prompt_tokens)
            completion = rng.randint(0, max_tokens)
            cache_write = rng.choice([0, prompt, rng.randint(0, prompt)])
            cached = rng.randint(0, prompt - cache_write)
            cost = pricing.price(prompt, completion, cached, cache_write)
            ledger.settle(
                reservation,
                usage(prompt=prompt, completion=completion, cost=cost, cached=cached, cache_write=cache_write),
                ok=True,
            )
            with lock:
                peaks.append(ledger.snapshot()["cost"])

    threads = [threading.Thread(target=worker, args=(plans[i::16],)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = ledger.snapshot()
    assert final["budget_exhausted"] == "budget_exhausted:cost"
    assert final["inflight"] == 0
    assert final["cache_write_tokens"] > 0
    assert max(peaks) <= cap + 1e-12
    assert final["cost"] <= cap + 1e-12
