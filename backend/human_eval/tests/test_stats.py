"""services/stats.py against published and hand-computable values."""

from __future__ import annotations

import itertools
import math

import pytest

from backend.human_eval.services import stats

pytestmark = pytest.mark.django_db

_ = None

# Krippendorff, K. (2011). "Computing Krippendorff's Alpha-Reliability". Departmental Papers (ASC),
# Annenberg School for Communication, University of Pennsylvania, https://repository.upenn.edu/asc_papers/43
# — the reliability-data example with 4 observers (A-D), 12 units and missing values ("C. Nominal data, any
# number of observers, missing data", reused for the other metrics). Published results: alpha nominal 0.743,
# ordinal 0.815, interval 0.849, ratio 0.797. The values below were also checked against an independent
# pairwise (non-coincidence-matrix) computation.
KRIPPENDORFF_2011 = [
    [1, 2, 3, 3, 2, 1, 4, 1, 2, _, _, _],  # observer A
    [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, _, 3],  # observer B
    [_, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, _],  # observer C
    [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, _],  # observer D
]

# The 3-coder, 15-unit example of the Wikipedia article "Krippendorff's alpha" (also the README example of
# the `krippendorff` Python package): nominal 0.691, interval 0.811.
WIKIPEDIA_EXAMPLE = [
    [_, _, _, _, _, 3, 4, 1, 2, 1, 1, 3, 3, _, 3],
    [1, _, 2, 1, 3, 3, 4, 3, _, _, _, _, _, _, _],
    [_, _, 2, 1, 3, 4, 4, _, 2, 1, 1, 3, 3, _, 4],
]


def _units(matrix: list[list]) -> list[list]:
    return [[row[unit] for row in matrix if row[unit] is not None] for unit in range(len(matrix[0]))]


@pytest.mark.parametrize(
    ("level", "expected"),
    [("nominal", 0.743), ("ordinal", 0.815), ("interval", 0.849), ("ratio", 0.797)],
)
def test_krippendorff_alpha_matches_krippendorff_2011(level, expected):
    alpha = stats.krippendorff_alpha(_units(KRIPPENDORFF_2011), level)
    assert alpha == pytest.approx(expected, abs=5e-4)


def test_krippendorff_alpha_more_digits():
    units = _units(KRIPPENDORFF_2011)
    assert stats.krippendorff_alpha(units, "nominal") == pytest.approx(0.743421, abs=1e-6)
    assert stats.krippendorff_alpha(units, "ordinal") == pytest.approx(0.815388, abs=1e-6)
    assert stats.krippendorff_alpha(units, "interval") == pytest.approx(0.849107, abs=1e-6)


@pytest.mark.parametrize(("level", "expected"), [("nominal", 0.691), ("interval", 0.811)])
def test_krippendorff_alpha_matches_wikipedia_example(level, expected):
    assert stats.krippendorff_alpha(_units(WIKIPEDIA_EXAMPLE), level) == pytest.approx(expected, abs=5e-4)


def test_unpairable_units_are_ignored_and_counted_out():
    units = _units(KRIPPENDORFF_2011)
    # unit 12 has a single value (observer B): not pairable
    assert stats.pairable_counts(units) == (11, 40)
    with_extra = [*units, [5], []]
    assert stats.krippendorff_alpha(with_extra, "nominal") == stats.krippendorff_alpha(units, "nominal")


def test_krippendorff_alpha_edge_cases():
    assert stats.krippendorff_alpha([[1, 1], [2, 2], [3, 3]], "interval") == pytest.approx(1.0)
    assert stats.krippendorff_alpha([[1, 1], [1, 1]], "nominal") is None  # no variation: undefined
    assert stats.krippendorff_alpha([[1], [2]], "nominal") is None  # nothing pairable
    assert stats.krippendorff_alpha([], "ordinal") is None
    # systematic disagreement is worse than chance
    assert stats.krippendorff_alpha([[1, 2], [2, 1], [1, 2], [2, 1]], "nominal") < 0
    with pytest.raises(ValueError, match="Unknown level"):
        stats.krippendorff_alpha([[1, 2]], "fuzzy")


def test_ordinal_alpha_works_on_strings_and_ints_alike():
    units = [[1, 2, 2], [0, 0], [2, 2, 1], [1, 1]]
    as_text = [[("a", "b", "c")[v] for v in unit] for unit in units]
    assert stats.krippendorff_alpha(units, "ordinal") == pytest.approx(stats.krippendorff_alpha(as_text, "ordinal"))


def test_percent_agreement():
    # unit 1: 3 values, all equal → 3 agreeing pairs; unit 2: [1, 2] → 0 of 1
    assert stats.percent_agreement([[4, 4, 4], [1, 2], [7]]) == pytest.approx(3 / 4)
    assert stats.percent_agreement([[1], []]) is None


@pytest.mark.parametrize(
    ("k", "n", "expected"),
    [
        (0, 5, 0.0625),  # 2 * (1/32)
        (5, 5, 0.0625),
        (1, 10, 0.021484375),  # 2 * (1 + 10) / 1024
        (2, 10, 0.109375),  # 2 * (1 + 10 + 45) / 1024
        (5, 10, 1.0),
        (3, 3, 0.25),
        (8, 20, 0.5034446716308594),  # 2 * sum C(20, i), i <= 8, / 2**20
    ],
)
def test_binomial_two_sided_exact_values(k, n, expected):
    assert stats.binomial_two_sided(k, n) == pytest.approx(expected, rel=1e-12)


def test_binomial_two_sided_edges():
    assert stats.binomial_two_sided(0, 0) is None
    with pytest.raises(ValueError, match="between 0 and n"):
        stats.binomial_two_sided(4, 3)


def test_bradley_terry_two_items_mle_is_win_ratio():
    # A beat B 3 times out of 4: MLE ratio 3; geometric mean 1 → sqrt(3) and 1/sqrt(3)
    comparisons = [("a", "b", 1)] * 3 + [("a", "b", 0)]
    strengths = stats.bradley_terry(comparisons, ["a", "b"], prior=0)
    assert strengths["a"] == pytest.approx(math.sqrt(3), rel=1e-8)
    assert strengths["b"] == pytest.approx(1 / math.sqrt(3), rel=1e-8)


def test_bradley_terry_cycle_and_ties_are_equal():
    cycle = [("a", "b", 1), ("b", "c", 1), ("c", "a", 1)]
    assert stats.bradley_terry(cycle, ["a", "b", "c"], prior=0) == pytest.approx({"a": 1.0, "b": 1.0, "c": 1.0})
    ties = [("a", "b", 0.5), ("a", "b", 0.5)]
    assert stats.bradley_terry(ties, ["a", "b"]) == pytest.approx({"a": 1.0, "b": 1.0})


def test_bradley_terry_three_item_mle_hand_check():
    # a beats b twice, b beats c twice, a and c split 1-1. Score equations (no prior):
    #   wins_i = sum_j n_ij p_i / (p_i + p_j); check them at the fitted strengths.
    comparisons = [("a", "b", 1)] * 2 + [("b", "c", 1)] * 2 + [("a", "c", 1), ("a", "c", 0)]
    comparisons += [("a", "b", 0), ("b", "c", 0)]  # every item wins at least once (MLE exists)
    p = stats.bradley_terry(comparisons, ["a", "b", "c"], prior=0)
    wins = {"a": 3, "b": 3, "c": 2}
    games = {("a", "b"): 3, ("b", "c"): 3, ("a", "c"): 2}
    for item in "abc":
        expected = sum(
            n * p[item] / (p[item] + p[other])
            for (x, y), n in games.items()
            for other in ([y] if x == item else [x] if y == item else [])
        )
        assert wins[item] == pytest.approx(expected, rel=1e-8)
    assert math.prod(p.values()) == pytest.approx(1.0)


def test_bradley_terry_prior_keeps_undefeated_finite_and_satisfies_score_equations():
    # a beat b once and never lost: the MLE is infinite, the prior keeps it finite.
    p = stats.bradley_terry([("a", "b", 1)], ["a", "b"])
    assert math.isfinite(p["a"])
    assert p["a"] > p["b"]
    assert p["a"] * p["b"] == pytest.approx(1.0)

    # Unnormalised strengths r satisfy, with 0.5 win + 0.5 loss vs a virtual opponent of strength 1:
    #   wins_i + 0.5 = sum_j n_ij r_i / (r_i + r_j) + r_i / (r_i + 1)
    # Normalisation divided r by an unknown scale c: solve a's equation for c, then check b's.
    def residual(scale):
        ra, rb = p["a"] * scale, p["b"] * scale
        return 1.5 - (ra / (ra + rb) + ra / (ra + 1))

    low, high = 1e-6, 1e6
    for _ in range(200):
        mid = math.sqrt(low * high)
        if residual(mid) > 0:
            low = mid
        else:
            high = mid
    ra, rb = p["a"] * low, p["b"] * low
    assert rb / (ra + rb) + rb / (rb + 1) == pytest.approx(0.5, abs=1e-6)


def test_newton_fit_matches_the_mm_fallback(monkeypatch):
    items = list("abcdefg")
    scores = (1.0, 0.0, 0.5, 1.0, 1.0)
    comparisons = [
        (a, b, scores[(index + repeat) % len(scores)])
        for index, (a, b) in enumerate(itertools.combinations(items, 2))
        for repeat in range(3)
    ]
    comparisons += [("a", other, 1.0) for other in items[1:] for _ in range(25)]  # far from the cold start
    newton = stats.bradley_terry(comparisons, items)
    monkeypatch.setattr(stats, "_bt_newton", lambda *args, **kwargs: None)
    mm = stats.bradley_terry(comparisons, items, tolerance=1e-14, max_iter=500_000)
    assert newton == pytest.approx(mm, rel=1e-7)
    assert newton["a"] > 10 * max(value for key, value in newton.items() if key != "a")


def test_bradley_terry_mle_requires_a_win():
    with pytest.raises(ValueError, match="never won"):
        stats.bradley_terry([("a", "b", 1)], ["a", "b"], prior=0)


def test_bradley_terry_weights_equal_repeats_and_ignore_outsiders():
    repeated = [("a", "b", 1)] * 3 + [("b", "a", 1)]
    weighted = [("a", "b", 1, 3), ("b", "a", 1, 1), ("a", "z", 1, 5)]
    assert stats.bradley_terry(weighted, ["a", "b"]) == pytest.approx(stats.bradley_terry(repeated, ["a", "b"]))
    assert stats.bradley_terry([], []) == {}


def test_bt_components():
    comparisons = [("a", "b", 1), ("c", "d", 0), ("d", "e", 1, 0)]  # zero weight does not connect
    assert stats.bt_components(comparisons, ["a", "b", "c", "d", "e"]) == [["a", "b"], ["c", "d"], ["e"]]


def test_cluster_bootstrap_is_seeded_and_needs_three_clusters():
    values = [0.2, 0.5, 0.9, 0.4, 0.7]
    first = stats.cluster_bootstrap_mean_ci(values, seed=11)
    assert first == stats.cluster_bootstrap_mean_ci(values, seed=11)
    assert first != stats.cluster_bootstrap_mean_ci(values, seed=12)
    low, high = first
    assert min(values) <= low < sum(values) / len(values) < high <= max(values)
    assert stats.cluster_bootstrap_mean_ci([1.0, 2.0], seed=1) is None
    assert stats.cluster_bootstrap_mean_ci({"x": 1.0, "y": 1.0, "z": 1.0}, seed=1) == (1.0, 1.0)


def test_bootstrap_diff_ci():
    a, b = [3.0, 4.0, 5.0, 4.5], [1.0, 2.0, 1.5]
    ci = stats.bootstrap_diff_ci(a, b, seed=3)
    assert ci == stats.bootstrap_diff_ci(a, b, seed=3)
    assert ci[0] > 0
    assert stats.bootstrap_diff_ci(a, [1.0, 2.0], seed=3) is None


def test_bootstrap_bt_is_seeded():
    clusters = [[("a", "b", 1)], [("a", "b", 1), ("b", "c", 1)], [("c", "a", 0)], [("b", "c", 0.5)]]

    def resample(rng):
        drawn = []
        for _ in range(len(clusters)):
            drawn.extend(clusters[int(rng.random() * len(clusters))])
        return drawn

    first = stats.bootstrap_bt(resample, ["a", "b", "c"], seed=5, draws=200)
    assert first == stats.bootstrap_bt(resample, ["a", "b", "c"], seed=5, draws=200)
    point = stats.bradley_terry([row for cluster in clusters for row in cluster], ["a", "b", "c"])
    low, high = first["a"]
    assert low <= math.log(point["a"]) <= high


def test_descriptives_and_seeds():
    assert stats.mean([]) is None
    assert stats.median([3, 1, 2]) == 2
    assert stats.median([1, 2, 3, 4]) == 2.5
    assert stats.sd([1]) is None
    assert stats.sd([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, abs=1e-5)
    assert stats.derive_seed(7, "rating", "a") == stats.derive_seed(7, "rating", "a")
    assert stats.derive_seed(7, "rating", "a") != stats.derive_seed(7, "rating", "b")
    assert 0 <= stats.derive_seed(1, "x") < 2**64


def test_within_group_percentile_ranks():
    values = {"x": 1.0, "y": 3.0, "z": 2.0, "u": 5.0, "v": 5.0}
    groups = {"x": "g", "y": "g", "z": "g", "u": "h", "v": "h"}
    ranks = stats.within_group_percentile_ranks(values, groups)
    assert ranks == pytest.approx({"x": 1 / 6, "z": 0.5, "y": 5 / 6, "u": 0.5, "v": 0.5})
