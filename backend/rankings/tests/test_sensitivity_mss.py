"""MSS v2 selection sensitivity: RBO, Jaccard, Dirichlet draws, robustness classes, seeded reproducibility."""

from __future__ import annotations

import random

import pytest

from backend.rankings.services.sensitivity import dirichlet
from backend.rankings.services.sensitivity import jaccard
from backend.rankings.services.sensitivity import rank_biased_overlap
from backend.rankings.services.sensitivity import robustness_class
from backend.rankings.services.study_selection import run_selection
from backend.rankings.tests.test_selection import _inputs
from backend.rankings.tests.test_selection import _params


def test_rbo_identical_disjoint_and_partial():
    a = list("abcdefg")
    assert rank_biased_overlap(a, a) == pytest.approx(1.0)
    assert rank_biased_overlap(list("abc"), list("xyz")) == 0.0
    # Webber et al. (2010) extrapolated RBO, computed by hand for a one-swap pair at depth 2:
    # X_1 = 0, X_2 = 2 -> RBO_ext = (2/2)p^2 + (1-p)/p * (0 + (2/2) p^2) with p = 0.9.
    p = 0.9
    expected = p**2 + (1 - p) / p * p**2
    assert rank_biased_overlap(["a", "b"], ["b", "a"], p=p) == pytest.approx(expected, abs=1e-6)
    # Agreement at the top weighs more than at the bottom.
    top_swap = rank_biased_overlap(list("bacdef"), list("abcdef"))
    bottom_swap = rank_biased_overlap(list("abcdfe"), list("abcdef"))
    assert bottom_swap > top_swap


def test_jaccard():
    assert jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert jaccard([], []) == 1.0


def test_dirichlet_draws_sum_to_one_and_concentrate_on_the_nominal_weights():
    rng = random.Random(1)  # noqa: S311 - seeded test fixture; not cryptographic
    w = [0.45, 0.30, 0.15, 0.10]
    draws = [dirichlet(rng, [50 * x for x in w]) for _ in range(4000)]
    assert all(abs(sum(d) - 1) < 1e-9 for d in draws)
    means = [sum(d[i] for d in draws) / len(draws) for i in range(4)]
    assert means == pytest.approx(w, abs=0.01)


def test_robustness_classes():
    assert robustness_class(0.70, picked=True) == "robust"
    assert robustness_class(0.69, picked=True) == "borderline"
    assert robustness_class(0.40, picked=True) == "borderline"
    assert robustness_class(0.39, picked=True) == "fragile"
    assert robustness_class(0.45, picked=False) == "contender"
    assert robustness_class(0.10, picked=False) == "rarely"


def _sensitivity(seed=20261109, draws=None):
    params = _params(seed=seed, draws=draws or {"S1": 60, "S2": 30, "S3": 20})
    return run_selection(_inputs(), params, sensitivity=True)


def test_selection_sensitivity_is_reproducible_with_a_fixed_seed():
    a = _sensitivity()
    b = _sensitivity()
    assert a["sensitivity"] == b["sensitivity"]
    other = _sensitivity(seed=7)
    assert other["sensitivity"]["S1"]["draws"] == 60


def test_selection_sensitivity_reports_every_run_and_classifies_picks():
    payload = _sensitivity()
    sens = payload["sensitivity"]
    assert {"S1", "S2", "S3", "S4", "S5", "S6"} <= set(sens)
    s1 = sens["S1"]
    assert s1["draws"] == 60
    for key in ("jaccard_mean", "jaccard_p5", "kendall_tau_b_mean", "rbo_mean", "rbo_p5"):
        assert s1[key] is not None
    freqs = {f["canonical_id"]: f for f in s1["frequencies"]}
    s3 = {f["canonical_id"]: f["frequency"] for f in sens["S3"]["frequencies"]}
    for pick in payload["selection"]["picks"]:
        cid = pick["canonical_id"]
        assert freqs[cid]["picked"] is True
        # Robustness is the weaker of S1 (weights) and S3 (specification, incl. rating noise).
        assert pick["selection_frequency_by_run"] == {"S1": freqs[cid]["frequency"], "S3": s3.get(cid, 0.0)}
        assert pick["selection_frequency"] == min(freqs[cid]["frequency"], s3.get(cid, 0.0))
        assert pick["robustness_basis"] == "min(S1, S3)"
        assert pick["robustness"] in ("robust", "borderline", "fragile")
        assert isinstance(pick["floor_sensitive"], bool)
    assert s1["rank_metrics_scope"].startswith("within each nominal price tier")
    assert s1["top_k_per_tier"] == 10
    assert s1["kendall_tau_top_k_mean"] is not None
    # S4: leave out each of the four within-tier components, and x0.5 / x1.5 each (12 runs).
    assert len(sens["S4"]["changes"]) == 12
    assert {c["change"] for c in sens["S4"]["changes"]} >= {"leave out popularity", "capability x1.5"}
    assert len(sens["S5"]["changes"]) == 4
    assert [c["change"] for c in sens["S6"]["changes"]] == [
        "floor at the tier's 40th percentile",
        "no quality floor",
        "floor tested on the WebDev rating's upper 95% bound",
    ]
    left_in_s6 = {cid for c in sens["S6"]["changes"] for cid in c["left"]}
    for pick in payload["selection"]["picks"]:
        assert pick["floor_sensitive"] is (pick["canonical_id"] in left_in_s6)


def test_leave_one_out_reruns_change_nothing_when_a_component_is_constant():
    payload = _sensitivity(draws={"S1": 0, "S2": 0, "S3": 0})
    by_change = {c["change"]: c for c in payload["sensitivity"]["S4"]["changes"]}
    # Every model was released on the same day: dropping recency cannot move the selection.
    assert by_change["leave out recency"]["entered"] == []
    assert by_change["leave out recency"]["left"] == []
    assert by_change["leave out recency"]["jaccard"] == 1.0


def test_rank_agreement_is_measured_within_tiers():
    from backend.rankings.services.sensitivity import summarize_draws

    order = ["f1", "f2", "f3", "b1", "b2", "b3"]
    tiers = dict.fromkeys(["f1", "f2", "f3"], "frontier") | dict.fromkeys(["b1", "b2", "b3"], "budget")
    scores = {"f1": 0.9, "f2": 0.8, "f3": 0.7, "b1": 0.3, "b2": 0.2, "b3": 0.1}
    # The budget tier reverses completely, the frontier tier is unchanged.
    flipped = {**scores, "b1": 0.1, "b3": 0.3}
    draw = {"picks": ["f1", "b3"], "order": ["f1", "f2", "f3", "b3", "b2", "b1"], "scores": flipped}
    out = summarize_draws(
        "S1",
        "",
        [draw],
        nominal_picks=["f1", "b1"],
        nominal_order=order,
        nominal_scores=scores,
        eligible_ids=order,
        nominal_tiers=tiers,
    )
    # tau_b: (+1 frontier, -1 budget) / 2 = 0 -- over the pooled order it would be strongly positive.
    assert out["kendall_tau_b_mean"] == pytest.approx(0.0)
    assert out["kendall_tau_top_k_mean"] == pytest.approx(0.0)
    pooled = summarize_draws(
        "S1",
        "",
        [draw],
        nominal_picks=["f1", "b1"],
        nominal_order=order,
        nominal_scores=scores,
        eligible_ids=order,
    )
    assert pooled["kendall_tau_b_mean"] > 0.5
    assert pooled["rank_metrics_scope"] == "over the whole order"
