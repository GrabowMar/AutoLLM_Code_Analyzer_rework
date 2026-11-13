"""MSS v2 components, goalposts, missing-data reweighting and the frame scoring. Pure, no database."""

from __future__ import annotations

import math

import pytest

from backend.rankings.services.frame import build_frame
from backend.rankings.services.mss import NOMINAL
from backend.rankings.services.mss import Variant
from backend.rankings.services.mss import band_price_score
from backend.rankings.services.mss import capability_score
from backend.rankings.services.mss import compute_mss
from backend.rankings.services.mss import mid_rank_percentiles
from backend.rankings.services.mss import popularity_score
from backend.rankings.services.mss import price_score
from backend.rankings.services.mss import recency_score
from backend.rankings.services.mss import score_frame
from backend.rankings.services.pricing import run_cost
from backend.rankings.tests.mss_helpers import AS_OF
from backend.rankings.tests.mss_helpers import CONFIG
from backend.rankings.tests.mss_helpers import eligible_row
from backend.rankings.tests.mss_helpers import lmarena


def _score(rows, *, market=None, variant=NOMINAL):
    frame = build_frame(rows, AS_OF, config=CONFIG)
    return {
        r["canonical_id"]: r for r in score_frame(frame["candidates"], as_of=AS_OF, market=market, variant=variant)
    }


# ── goalposts ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("p_raw", "score"), [(-4.0, 0.0), (-1.0, 1.0), (-2.5, 0.5), (-6.0, 0.0), (0.0, 1.0)])
def test_popularity_goalposts_on_the_log_share(p_raw, score):
    assert popularity_score(p_raw) == pytest.approx(score)


@pytest.mark.parametrize(("cost", "score"), [(0.01, 1.0), (10.0, 0.0), (0.1, 2 / 3), (100.0, 0.0), (0.001, 1.0)])
def test_price_goalposts_are_log_usd_per_run_cheaper_is_better(cost, score):
    assert price_score(cost) == pytest.approx(score)


def test_price_score_is_none_without_a_cost():
    assert price_score(None) is None
    assert price_score(0.0) is None


@pytest.mark.parametrize(("rating", "score"), [(1450, 0.0), (1850, 1.0), (1650, 0.5), (1300, 0.0), (1900, 1.0)])
def test_capability_is_linear_in_the_webdev_rating(rating, score):
    assert capability_score(rating) == pytest.approx(score)


@pytest.mark.parametrize(("days", "score"), [(0, 1.0), (365, 0.0), (182.5, 0.5), (400, 0.0)])
def test_recency_is_linear_over_a_year(days, score):
    assert recency_score(days) == pytest.approx(score)


def test_band_price_score_renormalises_inside_the_tier_band():
    assert band_price_score(0.1, (0.1, 1.0)) == pytest.approx(1.0)
    assert band_price_score(1.0, (0.1, 1.0)) == pytest.approx(0.0)
    assert band_price_score(math.sqrt(0.1), (0.1, 1.0)) == pytest.approx(0.5)


# ── aggregation ────────────────────────────────────────────────────────────


def test_missing_component_is_renormalised_never_zero():
    out = compute_mss({"popularity": 1.0, "price": 0.5, "capability": None, "recency": 1.0})
    assert out["mss"] == pytest.approx((0.4 * 1 + 0.3 * 0.5 + 0.1 * 1) / 0.8)
    assert out["coverage"] == pytest.approx(0.8)
    assert out["missing"] == ["capability"]
    # Scoring the missing component as 0 would give a lower value: it is not done.
    assert out["mss"] > 0.4 * 1 + 0.3 * 0.5 + 0.1 * 1


def test_full_coverage_is_the_plain_weighted_sum():
    out = compute_mss({"popularity": 0.5, "price": 0.5, "capability": 0.5, "recency": 0.5})
    assert out == {"mss": 0.5, "coverage": 1.0, "missing": []}


def test_missing_popularity_or_price_is_insufficient_evidence():
    scored = _score(
        [
            eligible_row("a/full"),
            eligible_row("b/no-pop", share=None),
            eligible_row("c/no-price", price=None),
            eligible_row("d/no-cap", rating=None),
        ],
    )
    assert scored["a/full"]["eligible"]
    assert scored["a/full"]["coverage"] == 1.0
    for cid, missing in (("b/no-pop", "popularity"), ("c/no-price", "price")):
        assert scored[cid]["mss"] is None
        assert scored[cid]["exclusion"] == {
            "step": "E8",
            "code": "insufficient_evidence",
            "detail": f"missing {missing}",
        }
    assert scored["d/no-cap"]["eligible"]
    assert scored["d/no-cap"]["coverage"] == pytest.approx(0.8)
    assert "capability_missing" in scored["d/no-cap"]["flags"]


def test_frame_scores_use_the_goalposts_and_reference_basket():
    scored = _score([eligible_row("a/m", share=0.01, price=(1.0, 4.0), rating=1650.0, release="2026-09-01")])["a/m"]
    cost = run_cost({"input": 1.0, "output": 4.0, "cache_read": None, "cache_write": None})
    # no cache-read price: cached prompt tokens at the input price -> 0.9 x 1.0 + 0.036 x 4.0
    assert cost == pytest.approx(0.9 + 0.144)
    assert scored["raw"]["run_cost_usd"] == pytest.approx(cost)
    assert scored["scores"]["price"] == pytest.approx(price_score(cost), abs=1e-6)
    # no market -> no value share -> volume only, flagged in the definition
    assert scored["raw"]["popularity"]["definition"].startswith("volume")
    assert scored["scores"]["popularity"] == pytest.approx(popularity_score(-2.0), abs=1e-6)
    assert scored["scores"]["capability"] == pytest.approx(0.5)
    assert scored["scores"]["recency"] == pytest.approx(
        1 - (AS_OF - __import__("datetime").date(2026, 9, 1)).days / 365,
        abs=1e-6,
    )


def test_censored_days_are_imputed_at_the_censor_factor_of_the_day_limit():
    rows = [eligible_row("a/m", shares=[None] * 7)]  # off the list every day; limit share 0.001
    nominal = _score(rows)["a/m"]
    assert nominal["raw"]["popularity"]["volume_share"] == pytest.approx(0.0005)
    assert "censored" in nominal["flags"]
    full = _score(rows, variant=Variant(censor_factor=1.0))["a/m"]
    assert full["raw"]["popularity"]["volume_share"] == pytest.approx(0.001)


def _market(day_shares: dict[str, float], total: int = 10**12) -> dict:
    return {
        "days": {
            "2026-11-02": {
                "total": total,
                "limit": 10**9,
                "listed": {k: int(v * total) for k, v in day_shares.items()},
            },
        },
    }


def test_geometric_mean_identity_volume_plus_half_log_relative_price():
    rows = [
        eligible_row("a/cheap", shares=[0.3], price=(0.1, 0.4)),
        eligible_row("b/dear", shares=[0.05], price=(5.0, 25.0)),
    ]
    market = _market({"a/cheap": 0.3, "b/dear": 0.05})
    scored = _score(rows, market=market)
    tokens = 0.936e6
    u = {cid: scored[cid]["raw"]["run_cost_usd"] / tokens for cid in scored}
    spend = 0.3 * u["a/cheap"] + 0.05 * u["b/dear"]  # per unit of market tokens
    u_bar = spend / 1.0
    for cid, share in (("a/cheap", 0.3), ("b/dear", 0.05)):
        expected = math.log10(share) + 0.5 * math.log10(u[cid] / u_bar)
        assert scored[cid]["raw"]["p_raw"] == pytest.approx(expected, abs=1e-6)
    # Value share favours the expensive model, volume share the cheap one; the geometric mean sits between.
    assert scored["b/dear"]["raw"]["popularity"]["value_share"] > 0.05
    assert scored["a/cheap"]["raw"]["popularity"]["value_share"] < 0.3


def test_popularity_variants_volume_and_value():
    rows = [
        eligible_row("a/cheap", shares=[0.3], price=(0.1, 0.4)),
        eligible_row("b/dear", shares=[0.05], price=(5.0, 25.0)),
    ]
    market = _market({"a/cheap": 0.3, "b/dear": 0.05})
    volume = _score(rows, market=market, variant=Variant(popularity="volume"))
    value = _score(rows, market=market, variant=Variant(popularity="value"))
    assert volume["a/cheap"]["raw"]["p_raw"] == pytest.approx(math.log10(0.3), abs=1e-6)
    assert value["b/dear"]["raw"]["p_raw"] > value["a/cheap"]["raw"]["p_raw"]


def test_percentile_normalisation_variant():
    rows = [
        eligible_row(f"v{i}/m", share=0.001 * (i + 1), price=(0.5 * (i + 1), 2.0), rating=1500 + 50 * i)
        for i in range(3)
    ]
    scored = _score(rows, variant=Variant(normalisation="percentile"))
    assert [scored[f"v{i}/m"]["scores"]["popularity"] for i in range(3)] == [0.0, 0.5, 1.0]
    assert [scored[f"v{i}/m"]["scores"]["price"] for i in range(3)] == [1.0, 0.5, 0.0]


def test_mid_rank_percentiles_average_ties():
    assert mid_rank_percentiles({"a": 1.0, "b": 1.0, "c": 2.0}) == {"a": 0.25, "b": 0.25, "c": 1.0}
    assert mid_rank_percentiles({"a": 3.0}) == {"a": 0.5}


def test_missing_capability_p25_imputation_variant():
    rows = [eligible_row(f"v{i}/m", rating=1450 + 100 * i) for i in range(4)] + [eligible_row("x/nocap", rating=None)]
    scored = _score(rows, variant=Variant(missing_capability="p25"))
    assert scored["x/nocap"]["scores"]["capability"] == pytest.approx(0.1875)  # P25 of 0, .25, .5, .75
    assert "capability_imputed_p25" in scored["x/nocap"]["flags"]


def test_lmarena_proxy_only_without_any_openrouter_presence():
    rows = [eligible_row(f"v{i}/m", share=0.001 * 10**i, rating=1600) for i in range(3)]
    for i, r in enumerate(rows):
        r["metadata"]["lmarena"] = lmarena(1600, votes=100 * 10**i)
    rows.append(eligible_row("x/arena-only", share=None, rating=1600))
    rows[-1]["metadata"]["lmarena"] = lmarena(1600, votes=100 * 10**2)
    scored = _score(rows)
    proxy = scored["x/arena-only"]
    assert "lmarena_proxy" in proxy["flags"]
    assert proxy["raw"]["popularity_source"] == "lmarena_proxy"
    assert proxy["scores"]["popularity"] == pytest.approx(scored["v2/m"]["scores"]["popularity"], abs=1e-6)
    assert proxy["eligible"]


def test_a_model_excluded_before_e8_has_no_mss_but_keeps_its_component_scores():
    scored = _score(
        [
            eligible_row("a/new", release="2026-10-01"),
            eligible_row("a/old", release="2025-01-01"),  # E6 too old
        ],
    )
    old = scored["a/old"]
    assert old["exclusion"]["step"] == "E6"
    assert old["mss"] is None
    assert old["scores"]["popularity"] is not None
    assert old["scores"]["price"] is not None
    assert scored["a/new"]["mss"] is not None
