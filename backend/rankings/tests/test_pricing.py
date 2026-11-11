"""MSS v2 price input: run cost, discount undo, first-party -> own endpoint -> median order (recorded fixtures)."""

from __future__ import annotations

import pytest

from backend.llm_models.services.enrichment import _normalize_endpoint
from backend.rankings.services.frame import build_frame
from backend.rankings.services.pricing import endpoint_price
from backend.rankings.services.pricing import planning_cost
from backend.rankings.services.pricing import price_from_cost
from backend.rankings.services.pricing import resolve_list_price
from backend.rankings.services.pricing import run_cost
from backend.rankings.services.pricing import undo_discount
from backend.rankings.tests.mss_helpers import AS_OF
from backend.rankings.tests.mss_helpers import CONFIG
from backend.rankings.tests.mss_helpers import go_row
from backend.rankings.tests.mss_helpers import load_fixture
from backend.rankings.tests.mss_helpers import row

BASKET = {"prompt_mtok": 0.9, "output_mtok": 0.036, "cache_hit": 0.8}


def _entry(*rows):
    frame = build_frame(list(rows), AS_OF, include_previews=True, config=CONFIG)
    return frame["candidates"][0]


def _endpoints(name):
    return [_normalize_endpoint(e) for e in load_fixture(name)["data"]["endpoints"]]


# ── run cost ───────────────────────────────────────────────────────────────


def test_run_cost_formula_with_cache_read():
    price = {"input": 1.4, "output": 4.4, "cache_read": 0.26, "cache_write": None}
    expected = 0.9 * (0.2 * 1.4 + 0.8 * 0.26) + 0.036 * 4.4
    assert run_cost(price, BASKET) == pytest.approx(expected)


def test_uncached_tokens_are_billed_at_the_cache_write_price_when_it_exceeds_input():
    anthropic = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}
    assert run_cost(anthropic, BASKET) == pytest.approx(0.9 * (0.2 * 6.25 + 0.8 * 0.5) + 0.036 * 25)


def test_a_cache_write_below_input_is_a_storage_fee_not_a_write_price():
    gemini = {"input": 2.0, "output": 12.0, "cache_read": 0.2, "cache_write": 0.1875}
    assert run_cost(gemini, BASKET) == pytest.approx(0.9 * (0.2 * 2.0 + 0.8 * 0.2) + 0.036 * 12)


def test_without_a_cache_read_price_cached_tokens_cost_input():
    assert run_cost({"input": 1.0, "output": 2.0}, BASKET) == pytest.approx(0.9 + 0.072)


def test_planning_cost_is_thirty_runs_of_the_verbose_profile_with_retries():
    price = {"input": 1.0, "output": 2.0, "cache_read": 0.1}
    per_run = 1.1 * (0.2 * 1.0 + 0.8 * 0.1) + 0.045 * 2.0
    assert planning_cost(price) == pytest.approx(30 * 1.25 * per_run)
    assert planning_cost(None) is None


def test_price_from_models_dev_cost_keeps_tiers_and_drops_a_zero_cache_write():
    price = price_from_cost(
        {"input": 1.4, "output": 4.4, "cache_read": 0.26, "cache_write": 0, "context_over_200k": {"input": 2.8}},
    )
    assert price["cache_write"] is None
    assert price["tiers"] == {"context_over_200k": {"input": 2.8}}
    assert price_from_cost({"output": 1}) is None


# ── endpoints ──────────────────────────────────────────────────────────────


def test_discount_undo():
    assert undo_discount(0.417, 0.5) == pytest.approx(0.834)
    assert undo_discount(1.0, 0.0) == 1.0
    assert undo_discount(None, 0.5) is None


def test_normalized_endpoint_carries_the_cache_write_price():
    eps = _endpoints("endpoints_glm-5.3.json")
    bedrock = next(e for e in eps if e["provider_name"] == "Amazon Bedrock")
    assert bedrock["cache_write_per_million"] == pytest.approx(2.1)
    wafer = next(e for e in eps if e["provider_name"] == "Wafer")
    assert wafer["cache_write_per_million"] is None


def test_vendor_own_endpoint_wins_over_the_cheapest_quantised_one():
    source, price, undone = endpoint_price(_endpoints("endpoints_glm-5.3.json"), "Z.AI")
    assert source == "openrouter-endpoint:Z.AI"
    assert (price["input"], price["output"]) == (pytest.approx(1.4), pytest.approx(4.4))
    assert undone is False


def test_median_over_fp8_endpoints_with_discounts_undone_when_the_vendor_has_none():
    source, price, undone = endpoint_price(_endpoints("endpoints_glm-5.3.json"), "Nobody")
    # fp8+: Z.AI 1.4, Novita 0.63/(1-0.55)=1.4, Parasail 1.4 and 2.1 -> median 1.4
    assert source.startswith("openrouter-endpoints-median(fp8+)")
    assert price["input"] == pytest.approx(1.4)
    assert undone is True


def test_hy4_own_endpoint_discount_is_undone():
    source, price, undone = endpoint_price(_endpoints("endpoints_hy4.json"), "Tencent")
    assert source == "openrouter-endpoint:Tencent"
    assert price["input"] == pytest.approx(0.834)
    assert price["output"] == pytest.approx(2.501)
    assert undone is True


# ── resolution order ───────────────────────────────────────────────────────


def _resolve(entry, *, endpoints=None, raw=None, **kwargs):
    return resolve_list_price(
        entry,
        raw=raw if raw is not None else load_fixture("models_dev_subset.json"),
        vendors=CONFIG.vendors,
        endpoints=endpoints,
        fetched_at="2026-11-09T10:00:00+00:00",
        **kwargs,
    )


def test_first_party_models_dev_record_comes_first_and_models_pricing_is_never_used():
    cheapest_meta = {"openrouter_pricing": {"prompt": "0.0000000699", "completion": "0.0000014"}}
    entry = _entry(row("z-ai/glm-5.3", metadata=cheapest_meta, input_per_m=0.0699, output_per_m=1.4))
    called = []
    record = _resolve(entry, endpoints=lambda: called.append(1) or [], cheapest={"input": 0.0699, "output": 1.4})
    assert record["source"] == "models.dev:zai/glm-5.3"
    assert (record["input"], record["output"], record["cache_read"]) == (1.4, 4.4, 0.26)
    assert record["first_party_record"]["release_date"] == "2026-08-14"
    assert record["first_party_record"]["context"] == 1_000_000
    assert record["openrouter_cheapest"] == {"input": 0.0699, "output": 1.4}  # stored, not used
    assert called == []  # no endpoint request once models.dev prices it


def test_go_record_is_stored_alongside_and_used_when_the_vendor_has_no_record():
    entry = _entry(row("deepseek/deepseek-v4.1-flash"), go_row("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"))
    record = _resolve(entry, go_limit=100.0, go_limit_source={"limit_source": "https://opencode.ai/docs/go"})
    assert record["source"] == "models.dev:opencode-go/deepseek-v4.1-flash"
    assert record["go"]["monthly_limit_usd"] == 100.0
    assert record["go"]["input"] == pytest.approx(0.15)
    assert record["go"]["limit_source"] == "https://opencode.ai/docs/go"


def test_endpoint_fallback_then_none():
    entry = _entry(row("tencent/hy4"))
    record = _resolve(entry, endpoints=lambda: _endpoints("endpoints_hy4.json"))
    assert record["source"] == "openrouter-endpoint:Tencent"
    assert record["discount_undone"] is True
    assert _resolve(_entry(row("tencent/hy4")), endpoints=None, raw={}) is None


def test_tiers_are_stored():
    raw = {
        "zai": {
            "models": {
                "glm-5.3": {"cost": {"input": 1.4, "output": 4.4, "context_over_200k": {"input": 2.8, "output": 8.8}}},
            },
        },
    }
    record = _resolve(_entry(row("z-ai/glm-5.3")), raw=raw)
    assert record["tiers"] == {"context_over_200k": {"input": 2.8, "output": 8.8}}
