"""Stratified quota selection (spec §3 + Amendment A1): caps, quotas, look-ahead, budgets, routes, explanations."""

from __future__ import annotations

import itertools
import random

import pytest

from backend.rankings.services import selection as sel
from backend.rankings.services.frame import build_frame
from backend.rankings.services.study_selection import default_params
from backend.rankings.services.study_selection import resolve_budgets
from backend.rankings.services.study_selection import run_selection
from backend.rankings.tests.mss_helpers import AS_OF
from backend.rankings.tests.mss_helpers import CONFIG
from backend.rankings.tests.mss_helpers import anthropic_row
from backend.rankings.tests.mss_helpers import eligible_row
from backend.rankings.tests.mss_helpers import go_row

# ── the exact feasibility core ─────────────────────────────────────────────


def _item(cid, tier="budget", *, vendor=None, open_weights=True, route="openrouter", cost=1.0, origin="US"):
    return {
        "canonical_id": cid,
        "tier": tier,
        "vendor": vendor or cid.split("/")[0],
        "origin": origin,
        "open_weights": open_weights,
        "route": route,
        "route_cost": cost,
    }


def _problem(items, **cons):
    defaults = {
        "n": len(items),
        "tier_quotas": (0, 0, len(items)),
        "vendor_max": None,
        "vendors_min": 0,
        "open_min": 0,
        "closed_min": 0,
        "claude_min": 0,
    }
    defaults.update(cons)
    return sel.Problem(sel.compile_items(items), sel.Constraints(**defaults))


def _ids(problem, picks):
    return [problem.c.ids[k] for k in picks]


def test_vendor_cap_blocks_the_third_model_of_a_vendor():
    items = [_item("a/1"), _item("a/2"), _item("a/3"), _item("b/1")]
    problem = _problem(items, n=3, tier_quotas=(0, 0, 3), vendor_max=2)
    result = sel.run_greedy(problem, list(range(4)))
    assert _ids(problem, result["picks"]) == ["a/1", "a/2", "b/1"]
    assert result["trace"][2] == {"decision": "blocked", "blocked_by": "cap:Q-vendor(a)"}


def test_exact_tier_quotas_fill_each_stratum():
    items = [
        _item("a/f", "frontier"),
        _item("b/f", "frontier"),
        _item("c/m", "mid"),
        _item("d/b", "budget"),
        _item("e/b", "budget"),
    ]
    problem = _problem(items, n=3, tier_quotas=(1, 1, 1))
    result = sel.run_greedy(problem, list(range(5)))
    assert _ids(problem, result["picks"]) == ["a/f", "c/m", "d/b"]
    assert result["trace"][1]["blocked_by"] == "cap:tier_full(frontier)"


def test_lookahead_skips_a_higher_ranked_model_to_meet_a_quota():
    items = [_item("a/1", open_weights=False), _item("b/1", open_weights=False), _item("c/1", open_weights=True)]
    problem = _problem(items, n=2, tier_quotas=(0, 0, 2), open_min=1)
    result = sel.run_greedy(problem, list(range(3)))
    assert _ids(problem, result["picks"]) == ["a/1", "c/1"]
    assert result["trace"][1]["blocked_by"] == "lookahead"
    codes = sel.lookahead_codes(problem, result["trace"][1]["state"], 1, result["trace"][1]["rest"])
    assert codes == ["Q-open"]


def test_anthropic_budget_is_a_hard_cap():
    items = [
        _item("anthropic/opus", route="anthropic", cost=100.0, open_weights=False),
        _item("anthropic/sonnet", route="anthropic", cost=80.0, open_weights=False),
        _item("x/1"),
        _item("y/1"),
    ]
    problem = _problem(items, n=3, tier_quotas=(0, 0, 3), anthropic_budget=170.0, claude_min=1)
    result = sel.run_greedy(problem, list(range(4)))
    assert _ids(problem, result["picks"]) == ["anthropic/opus", "x/1", "y/1"]
    assert result["trace"][1]["blocked_by"] == "budget:B-anthropic"


def test_openrouter_budget_is_a_shared_knapsack():
    items = [
        _item("a/1", cost=10.0),
        _item("b/1", cost=10.0),
        _item("c/1", cost=1.0, route="go"),
        _item("d/1", cost=5.0),
    ]
    problem = _problem(items, n=3, tier_quotas=(0, 0, 3), openrouter_budget=16.0)
    result = sel.run_greedy(problem, list(range(4)))
    assert _ids(problem, result["picks"]) == ["a/1", "c/1", "d/1"]


def test_infeasible_diagnosis_names_the_quota():
    items = [_item("a/1", open_weights=False), _item("b/1", open_weights=False), _item("c/1", open_weights=True)]
    problem = _problem(items, n=2, tier_quotas=(0, 0, 2), open_min=2)
    assert not sel.run_greedy(problem, [0, 1, 2])["complete"]
    diagnosis = sel.diagnose_infeasible(problem, [0, 1, 2])
    assert {"constraint": "Q-open", "feasible_if_relaxed": True} in diagnosis["relaxations"]
    assert diagnosis["selectable_per_tier"] == {"frontier": 0, "mid": 0, "budget": 3}


def test_minimal_budget_is_reported_when_a_budget_makes_it_infeasible():
    items = [_item("a/1", cost=10.0), _item("b/1", cost=7.5)]
    problem = _problem(items, n=2, tier_quotas=(0, 0, 2), openrouter_budget=10.0)
    diagnosis = sel.diagnose_infeasible(problem, [0, 1])
    assert diagnosis["min_openrouter_budget_effective_usd"] == pytest.approx(17.5)


def _brute_force_lexicographic(problem, order):
    """The feasible set whose sorted order positions are lexicographically smallest (= the greedy's set)."""
    n = problem.cons.n
    best = None
    for combo in itertools.combinations(range(len(order)), n):
        state = problem.empty()
        ok = True
        for pos in combo:
            k = order[pos]
            if problem.cap_violation(state, k):
                ok = False
                break
            state = problem.add(state, k)
        if ok and not problem.unmet(state) and (best is None or combo < best):
            best = combo
    return None if best is None else [order[p] for p in best]


@pytest.mark.parametrize("seed", range(12))
def test_greedy_with_lookahead_is_the_lexicographically_greatest_feasible_set(seed):
    rng = random.Random(seed)  # noqa: S311 - seeded test fixture; not cryptographic
    tiers = ["frontier", "mid", "budget"]
    items = [
        _item(
            f"{rng.choice('abcd')}/{i}",
            rng.choice(tiers),
            open_weights=rng.random() < 0.5,
            route=rng.choice(["openrouter", "go", "anthropic"]),
            cost=rng.uniform(1, 20),
            origin=rng.choice(["US", "CN"]),
        )
        for i in range(10)
    ]
    problem = _problem(
        items,
        n=5,
        tier_quotas=(1, 2, 2),
        vendor_max=2,
        vendors_min=3,
        open_min=2,
        closed_min=1,
        openrouter_budget=30.0,
    )
    order = sorted(range(10), key=lambda k: (problem.c.tier[k], k))
    expected = _brute_force_lexicographic(problem, order)
    result = sel.run_greedy(problem, order)
    if expected is None:
        assert not result["complete"]
    else:
        assert result["complete"]
        assert sorted(result["picks"]) == sorted(expected)


def test_sum_optimal_matches_brute_force_on_a_small_instance():
    items = [_item(f"v{i}/m", ["frontier", "mid", "budget"][i % 3], open_weights=i % 2 == 0) for i in range(9)]
    problem = _problem(items, n=3, tier_quotas=(1, 1, 1), open_min=2)
    values = {k: 1.0 / (k + 1) + (0.5 if k == 7 else 0.0) for k in range(9)}
    best = max(
        (c for c in itertools.combinations(range(9), 3) if _feasible_combo(problem, c)),
        key=lambda c: sum(values[k] for k in c),
    )
    result = sel.sum_optimal(problem, list(range(9)), values)
    assert sorted(result["set"]) == sorted(problem.c.ids[k] for k in best)
    assert result["exhaustive"] is True


def _feasible_combo(problem, combo):
    state = problem.empty()
    for k in combo:
        if problem.cap_violation(state, k):
            return False
        state = problem.add(state, k)
    return not problem.unmet(state)


# ── tiers, routes, ordering ────────────────────────────────────────────────


def test_tertile_cut_points_and_scaling():
    costs = {f"m{i}": float(i) for i in range(1, 10)}
    tiers, info = sel.assign_tiers(costs)
    assert [tiers[f"m{i}"] for i in range(1, 10)] == ["budget"] * 3 + ["mid"] * 3 + ["frontier"] * 3
    assert info["budget_max_usd_per_run"] == 3.0
    assert info["frontier_min_usd_per_run_exclusive"] == 6.0
    moved, _ = sel.assign_tiers(costs, scale=(1.1, 1.1))
    assert moved["m3"] == "budget"
    assert moved["m6"] == "mid"
    explicit, info = sel.assign_tiers(costs, cuts=(2.0, 8.0))
    assert explicit["m3"] == "mid"
    assert explicit["m9"] == "frontier"
    assert info["rule"] == "explicit cut points"


def _route_item(vendor="z-ai", *, go=None, openrouter=True, anthropic=False, go_cost=5.0, limit=15.0):
    routes = {}
    if go is not None:
        routes["go"] = {"paid": go, "served_name": "x"}
    if openrouter:
        routes["openrouter"] = {"paid": True}
    if anthropic:
        routes["anthropic"] = {"paid": True}
    return {
        "vendor": vendor,
        "routes": routes,
        "go_limit_usd": limit,
        "raw": {"planning_cost_usd": 20.0, "go_planning_cost_usd": go_cost},
    }


def test_route_choice_is_part_of_feasibility():
    claude = sel.choose_route(_route_item("anthropic", go=True, anthropic=True), contingency=0.15, study_months=1)
    assert claude["route"] == "anthropic"  # even when Go serves it
    within = sel.choose_route(_route_item(go=True, go_cost=12.0), contingency=0.15, study_months=1)
    assert within["route"] == "go"
    assert within["cost"] == 12.0
    over = sel.choose_route(_route_item(go=True, go_cost=13.0), contingency=0.15, study_months=1)
    assert over["route"] == "openrouter"
    assert over["flags"] == ["go_limit_exceeded"]
    assert over["cost"] == 20.0
    two_months = sel.choose_route(_route_item(go=True, go_cost=13.0), contingency=0.15, study_months=2)
    assert two_months["route"] == "go"
    stuck = sel.choose_route(_route_item(go=True, go_cost=13.0, openrouter=False), contingency=0.15, study_months=1)
    assert stuck["route"] is None
    assert stuck["reason"] == "budget:B-go"


def test_go_usage_reports_weeks_and_runs_per_five_hours():
    usage = sel.go_usage(
        {"go_planning_cost_usd": 9.0, "go_run_cost_usd": 0.25},
        15.0,
        five_hour_share=0.2,
        weekly_share=0.5,
    )
    assert usage == {
        "monthly_limit_usd": 15.0,
        "planning_cost_usd": 9.0,
        "share_of_monthly_limit": 0.6,
        "weeks_needed": 2,
        "runs_per_5h": 12,
    }


def test_order_key_breaks_ties_deterministically():
    def item(cid, mss, pop, price, route):
        return {
            "canonical_id": cid,
            "tier": "mid",
            "mss_tier": mss,
            "scores": {"popularity": pop, "price": price},
            "route": route,
        }

    items = [
        item("b/x", 0.50001, 0.5, 0.5, "openrouter"),
        item("a/x", 0.50002, 0.5, 0.5, "openrouter"),  # equal at 4 dp -> next keys
        item("c/x", 0.5, 0.6, 0.1, "openrouter"),
        item("d/x", 0.5, 0.5, 0.5, "go"),
    ]
    assert [i["canonical_id"] for i in sorted(items, key=sel.order_key)] == ["c/x", "d/x", "a/x", "b/x"]


# ── end to end on a synthetic frame ────────────────────────────────────────

# (id, (input, output) price, share, webdev rating, open weights)
MODELS = [
    ("anthropic/opus", (5.0, 25.0), 0.05, 1800, False),
    ("openai/sol", (2.5, 15.0), 0.03, 1750, False),
    ("google/argon", (2.0, 12.0), 0.01, 1700, False),
    ("z-ai/glm", (1.0, 4.0), 0.02, 1600, True),
    ("tencent/hy", (0.8, 2.5), 0.04, 1630, True),
    ("meta/muse", (0.9, 3.0), 0.005, 1500, False),
    ("deepseek/flash", (0.15, 0.6), 0.2, 1620, True),
    ("xiaomi/mimo", (0.1, 0.3), 0.01, 1640, True),
    ("qwen/flash", (0.15, 0.45), 0.004, 1630, False),
    ("minimax/flash", (0.12, 0.5), 0.02, 1500, True),
]


def _inputs(*, extra_rows=(), openrouter=500.0, anthropic=400.0):
    rows = [eligible_row(cid, price=p, share=s, rating=q, open_weights=o) for cid, p, s, q, o in MODELS]
    rows += [anthropic_row("opus", "anthropic/opus"), go_row("flash", "deepseek/flash"), *extra_rows]
    frame = build_frame(rows, AS_OF, config=CONFIG)
    for entry in frame["candidates"]:
        entry["go_limit_usd"] = 100.0 if entry["routes"].get("go") else None
    params = _params(anthropic_budget=anthropic)
    budgets = resolve_budgets({**params, "openrouter_budget": openrouter}, live_credit=None, fallback_openrouter=None)
    return {
        "as_of": AS_OF.isoformat(),
        "frame": frame,
        "market": None,
        "market_snapshot_id": None,
        "budgets": budgets,
        "go_limits": {"five_hour_share": 0.2, "weekly_share": 0.5},
    }


def _params(**overrides):
    base = {
        "n": 6,
        "tier_quotas": {"frontier": 2, "mid": 2, "budget": 2},
        "vendors_min": 4,
        "open_min": 2,
        "closed_min": 2,
        "claude_min": 1,
        "draws": {"S1": 0, "S2": 0, "S3": 0},
    }
    base.update(overrides)
    return default_params(**base)


def test_budgets_take_the_contingency():
    budgets = resolve_budgets(_params(), live_credit={"available": 20.89, "source": "live"}, fallback_openrouter=5.0)
    assert budgets["anthropic_effective"] == pytest.approx(170.0)
    assert budgets["openrouter_effective"] == pytest.approx(0.85 * 20.89, abs=1e-4)
    fallback = resolve_budgets(_params(), live_credit=None, fallback_openrouter=5.0)
    assert fallback["openrouter_available_usd"] == 5.0
    assert "fallback" in fallback["openrouter_source"]
    none = resolve_budgets(_params(), live_credit=None, fallback_openrouter=None)
    assert none["openrouter_effective"] == 0.0


def test_stratified_selection_end_to_end():
    payload = run_selection(_inputs(), _params(), sensitivity=False)
    selection = payload["selection"]
    by_tier = selection["by_tier"]
    assert [len(by_tier[t]) for t in ("frontier", "mid", "budget")] == [2, 2, 2]
    picks = {p["canonical_id"]: p for p in selection["picks"]}
    assert picks["anthropic/opus"]["route"] == "anthropic"
    assert picks["anthropic/opus"]["pinned_version"]["model_id"] == "local-anthropic/opus"
    assert picks["deepseek/flash"]["route"] == "go"
    assert picks["deepseek/flash"]["go_usage"]["monthly_limit_usd"] == 100.0
    for p in picks.values():
        assert p["mss_tier"] is not None
        assert p["mss"] is not None
        assert p["admitted_by"] in ("rank",) or p["admitted_by"].startswith("quota:")
    statuses = {c["code"]: c for c in payload["constraint_status"]}
    assert statuses["Q-tier"]["satisfied"]
    assert statuses["Q-tier"]["binding"] is None
    assert all(c["satisfied"] for c in payload["constraint_status"])
    assert {"Q-quality", "B-go", "B-openrouter", "B-anthropic"} <= set(statuses)
    use = selection["budget_use"]
    assert use["anthropic"]["planned_usd"] == pytest.approx(picks["anthropic/opus"]["planning_cost_usd"], abs=0.01)
    assert payload["sum_optimal"]["exhaustive"] is True
    assert len(payload["alternates"]["replacements"]) == 6
    assert payload["tiers"]["counts"] == {"frontier": 3, "mid": 4, "budget": 3}


def test_the_quality_floor_keeps_the_bottom_half_of_a_tier_out():
    payload = run_selection(_inputs(), _params(), sensitivity=False)
    candidates = {c["canonical_id"]: c for c in payload["candidates"]}
    # meta/muse has the lowest capability of the mid tier: below its median.
    assert candidates["meta/muse"]["admissible"] is False
    assert candidates["meta/muse"]["floor_reason"].startswith("floor:Q-quality")
    assert payload["floor"]["levels"] == {"frontier": 50.0, "mid": 50.0, "budget": 50.0}


def test_selection_is_deterministic():
    a = run_selection(_inputs(), _params(), sensitivity=False)
    b = run_selection(_inputs(), _params(), sensitivity=False)
    assert a["selection"] == b["selection"]
    assert a["blocked"] == b["blocked"]


def test_a_tight_openrouter_budget_blocks_a_model_and_says_why():
    # Effective OpenRouter budget 0.85 x 188 = $159.8: openai/sol ($128 for 30 runs) would leave too little
    # for the mid and budget strata, so the look-ahead skips it for google/argon.
    payload = run_selection(_inputs(openrouter=188.0), _params(quality_floor=False), sensitivity=False)
    blocked = {b["canonical_id"]: b["blocked_by"] for b in payload["blocked"]}
    picks = [p["canonical_id"] for p in payload["selection"]["picks"]]
    assert "openai/sol" not in picks
    assert "google/argon" in picks
    assert blocked["openai/sol"] == "lookahead:B-openrouter"
    argon = next(p for p in payload["selection"]["picks"] if p["canonical_id"] == "google/argon")
    assert argon["admitted_by"] == "quota:B-openrouter"
    statuses = {c["code"]: c for c in payload["constraint_status"]}
    assert statuses["B-openrouter"]["binding"] is True
    assert payload["selection"]["budget_use"]["openrouter"]["planned_usd"] <= 159.8


def test_infeasible_selection_reports_the_constraints_and_minimal_budget():
    payload = run_selection(_inputs(openrouter=1.0), _params(), sensitivity=False)
    assert payload["selection"] is None
    infeasible = payload["infeasible"]
    relaxed = {r["constraint"]: r["feasible_if_relaxed"] for r in infeasible["relaxations"]}
    assert relaxed["B-openrouter"] is True
    assert infeasible["min_openrouter_budget_effective_usd"] > 0.85


def test_force_and_exclude_are_researcher_overrides():
    forced = run_selection(_inputs(), _params(force=["meta/muse"], quality_floor=False), sensitivity=False)
    assert "meta/muse" in forced["selection"]["by_tier"]["mid"]
    assert next(p for p in forced["selection"]["picks"] if p["canonical_id"] == "meta/muse")["admitted_by"] == "forced"
    excluded = run_selection(
        _inputs(),
        _params(exclude=["anthropic/opus"], claude_min=0, quality_floor=False),
        sensitivity=False,
    )
    assert "anthropic/opus" not in [p["canonical_id"] for p in excluded["selection"]["picks"]]
    assert any(s["step"] == "R" for s in excluded["flow"])


def test_missing_capability_is_admitted_only_with_top_quartile_popularity():
    extra = [
        eligible_row("x/popular-nocap", price=(0.11, 0.5), share=0.3, rating=None),
        eligible_row("y/quiet-nocap", price=(0.11, 0.5), share=0.0001, rating=None),
    ]
    payload = run_selection(_inputs(extra_rows=extra), _params(vendors_min=3), sensitivity=False)
    candidates = {c["canonical_id"]: c for c in payload["candidates"]}
    assert candidates["x/popular-nocap"]["admissible"] is True
    assert "capability_missing_admitted" in candidates["x/popular-nocap"]["flags"]
    assert candidates["y/quiet-nocap"]["admissible"] is False


# ── Review fixes (2026-11-09) ──────────────────────────────────────────────


def test_a_forced_pick_that_breaks_a_cap_is_reported_not_raised():
    # Three frontier models forced into a frontier quota of 2.
    params = _params(force=["anthropic/opus", "openai/sol", "google/argon"], quality_floor=False)
    payload = run_selection(_inputs(), params, sensitivity=False)
    assert payload["selection"] is None
    infeasible = payload["infeasible"]
    assert "tier_full(frontier)" in infeasible["forced_violation"]
    assert infeasible["note"].startswith("The forced picks cannot all be selected")


def test_a_forced_cap_violation_in_a_sensitivity_rerun_is_an_infeasible_draw():
    from backend.rankings.services.mss import score_frame
    from backend.rankings.services.study_selection import Stratifier
    from backend.rankings.services.study_selection import _attach_go_limits

    params = _params(force=["anthropic/opus", "openai/sol", "google/argon"], quality_floor=False)
    inputs = _inputs()
    scored = score_frame(inputs["frame"]["candidates"], as_of=AS_OF, market=None)
    _attach_go_limits(scored, inputs)
    strat = Stratifier(inputs, params, scored)
    result = strat.run(tier_scale=(1.1, 1.1))
    assert result["picks"] is None
    assert result["order"]


def test_budget_caps_are_never_overshot_by_cent_rounding():
    assert sel.cost_cents(2.351) == 236
    assert sel.cost_cents(2.35) == 235
    assert sel.cap_cents(59.1005) == 5910
    assert sel.cap_cents(59.1099) == 5910
    # Two picks of $29.553 each = $59.106 > a $59.1005 cap: rounding to the nearest cent
    # (2955 + 2955 <= 5910) used to admit both.
    items = [_item("a/1", cost=29.553), _item("b/1", cost=29.553)]
    problem = _problem(items, n=2, tier_quotas=(0, 0, 2), openrouter_budget=59.1005)
    assert not problem.feasible(problem.empty(), [0, 1])
    ok = _problem(items, n=2, tier_quotas=(0, 0, 2), openrouter_budget=59.12)  # 2 x 2956 cents
    assert ok.feasible(ok.empty(), [0, 1])


def test_sum_optimal_keeps_the_forced_picks():
    items = [_item("a/1", cost=1), _item("b/1", cost=1), _item("c/1", cost=1)]
    problem = _problem(items, n=2, tier_quotas=(0, 0, 2))
    values = {0: 0.9, 1: 0.8, 2: 0.1}
    free = sel.sum_optimal(problem, [0, 1, 2], values)
    assert set(free["set"]) == {"a/1", "b/1"}
    forced = sel.sum_optimal(problem, [0, 1, 2], values, forced=[2])
    assert set(forced["set"]) == {"a/1", "c/1"}
    assert forced["sum_mss_tier"] == pytest.approx(1.0)


def test_forced_selection_is_compared_with_the_forced_optimum():
    payload = run_selection(_inputs(), _params(force=["meta/muse"], quality_floor=False), sensitivity=False)
    assert "meta/muse" in payload["sum_optimal"]["set"]
    assert payload["sum_optimal"]["differs_from_greedy"] is False


def test_an_infeasible_selection_is_diagnosed_at_the_most_relaxed_floor():
    payload = run_selection(_inputs(openrouter=1.0), _params(), sensitivity=False)
    infeasible = payload["infeasible"]
    if infeasible["feasible_without_quality_floor"]:
        pytest.skip("the floor is the cause on this frame")
    assert infeasible["diagnosed_at_floor_percentiles"] == {"frontier": 33.0, "mid": 33.0, "budget": 33.0}


def test_break_even_cache_hit_is_linear_in_the_hit_share():
    from backend.rankings.services.study_selection import break_even_cache_hit

    # $240 at no cache hits, $81.60 at 80%: the $170 budget holds from 35.4% hits on.
    assert break_even_cache_hit(240.0, 81.6, 0.8, 170.0) == pytest.approx(0.8 * 70 / 158.4, abs=1e-4)
    assert break_even_cache_hit(100.0, 50.0, 0.8, 170.0) == 0.0
    assert break_even_cache_hit(240.0, 81.6, 0.8, None) is None


def test_claude_budget_reports_the_cache_hit_it_depends_on_and_can_be_planned_pessimistically():
    payload = run_selection(_inputs(), _params(), sensitivity=False)
    anth = payload["selection"]["budget_use"]["anthropic"]
    opus = next(p for p in payload["selection"]["picks"] if p["canonical_id"] == "anthropic/opus")
    assert anth["cache_hit_assumed"] == 0.8
    assert anth["planned_h0_usd"] == pytest.approx(opus["planning_cost_h0_usd"], abs=0.01)
    assert anth["break_even_cache_hit"] is not None
    pessimistic = run_selection(_inputs(), _params(anthropic_cache_hit=0.0), sensitivity=False)
    opus0 = next(p for p in pessimistic["selection"]["picks"] if p["canonical_id"] == "anthropic/opus")
    assert opus0["planning_cost_usd"] == pytest.approx(opus["planning_cost_h0_usd"], abs=0.01)
    assert pessimistic["selection"]["budget_use"]["anthropic"]["cache_hit_assumed"] == 0.0


def test_a_pick_admitted_without_capability_is_warned_about():
    extra = [eligible_row("x/popular-nocap", price=(0.11, 0.5), share=0.3, rating=None)]
    payload = run_selection(_inputs(extra_rows=extra), _params(vendors_min=3), sensitivity=False)
    picks = [p["canonical_id"] for p in payload["selection"]["picks"]]
    if "x/popular-nocap" not in picks:
        pytest.skip("not picked on this frame")
    assert any(w.startswith("capability_missing_admitted: x/popular-nocap") for w in payload["warnings"])
