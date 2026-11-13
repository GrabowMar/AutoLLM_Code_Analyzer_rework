"""Interval estimates and exact tests against published values (spec 7.9 step 6; WP-13 tests).

Sources:
- Wilson and Clopper-Pearson: Newcombe (1998a), "Two-sided confidence intervals for the single proportion",
  Stat Med 17:857-872, Table I (81/263, 15/148, 0/20, 1/29).
- Newcombe hybrid score (method 10) for independent proportions: Newcombe (1998b), "Interval estimation for
  the difference between independent proportions", Stat Med 17:873-890, Table II (56/70 vs 48/80,
  9/10 vs 3/10, 6/7 vs 2/7, 5/56 vs 0/29).
- Paired Newcombe (method 10) and exact McNemar: Fagerland, Lydersen & Laake (2013), "The McNemar test for
  binary matched-pairs data", BMC Med Res Methodol 13:91 / (2014) SMMR, airway hyper-responsiveness
  example (n11=1, n12=1, n21=7, n22=12): Newcombe square-and-add -0.502 to -0.036, exact McNemar p = 0.0703.
- Fisher exact: Fisher's lady tasting tea (3,1/1,3): p = 0.4857; (1,9/11,3): p = 0.002759.
"""

from __future__ import annotations

import pytest

from backend.generation.patterns import stats


def r4(values):
    return tuple(round(v, 4) for v in values)


@pytest.mark.parametrize(
    ("k", "n", "wilson", "cp"),
    [
        (81, 263, (0.2553, 0.3662), (0.2527, 0.3676)),
        (15, 148, (0.0624, 0.1605), (0.0578, 0.1617)),
        (0, 20, (0.0, 0.1611), (0.0, 0.1684)),
        (1, 29, (0.0061, 0.1718), (0.0009, 0.1776)),
    ],
)
def test_wilson_and_clopper_pearson_match_newcombe_1998a(k, n, wilson, cp):
    assert r4(stats.wilson(k, n)) == wilson
    assert r4(stats.clopper_pearson(k, n)) == cp


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((56, 70, 48, 80), (0.2, 0.0524, 0.3339)),
        ((9, 10, 3, 10), (0.6, 0.1705, 0.8090)),
        ((6, 7, 2, 7), (0.5714, 0.0582, 0.8062)),
        ((5, 56, 0, 29), (0.0893, -0.0381, 0.1926)),
    ],
)
def test_newcombe_independent_matches_newcombe_1998b(args, expected):
    assert r4(stats.newcombe_independent(*args)) == expected


def test_newcombe_paired_and_mcnemar_match_fagerland():
    diff, low, high = stats.newcombe_paired(1, 1, 7, 12)
    assert round(diff, 4) == -0.2857
    assert (round(low, 3), round(high, 3)) == (-0.502, -0.036)
    assert round(stats.mcnemar_exact(1, 7), 4) == 0.0703


def test_fisher_exact_matches_published_values():
    assert round(stats.fisher_exact(3, 1, 1, 3), 4) == 0.4857
    assert round(stats.fisher_exact(1, 9, 11, 3), 6) == 0.002759


def test_empty_samples_have_no_estimate_never_zero():
    assert stats.wilson(0, 0) is None
    assert stats.clopper_pearson(0, 0) is None
    assert stats.newcombe_independent(1, 2, 0, 0) is None
    assert stats.newcombe_paired(0, 0, 0, 0) is None
    assert stats.mcnemar_exact(0, 0) is None
    assert stats.fisher_exact(0, 0, 3, 3) is None
    assert stats.shannon_entropy([]) is None
    assert stats.chao1([]) is None


def test_repeated_look_critical_value_is_bonferroni():
    assert round(stats.z_for(0.05, 1), 4) == 1.96
    assert round(stats.z_for(0.05, 3), 4) == 2.394
    assert stats.wilson(5, 20, stats.z_for(0.05, 4))[0] < stats.wilson(5, 20)[0]


def test_mantel_haenszel_rd_and_exact_permutation_on_a_fixture():
    # Two targets: (8/10 vs 2/10) and (6/10 vs 4/10): weights 5 and 5, RD = (0.6 + 0.2) / 2 = 0.4.
    strata = [stats.Stratum("t1", 8, 10, 2, 10), stats.Stratum("t2", 6, 10, 4, 10)]
    mh = stats.mantel_haenszel_rd(strata)
    assert round(mh["rd"], 6) == 0.4
    # Sato variance by hand: P = sum((k1*n0^2 - k0*n1^2 + n1*n0*(n0-n1)/2)/N^2), Q = sum((k1*(n0-k0)+k0*(n1-k1))/(2N))
    p_sum = (8 * 100 - 2 * 100) / 400 + (6 * 100 - 4 * 100) / 400
    q_sum = (8 * 8 + 2 * 2) / 40 + (6 * 6 + 4 * 4) / 40
    se = ((0.4 * p_sum + q_sum) / 100) ** 0.5
    assert round(mh["se"], 6) == round(se, 6)
    perm = stats.stratified_permutation_p(strata)
    # Sign patterns of (0.3, 0.1): |0.4|, |0.2|, |-0.2|, |-0.4| -> two of four reach 0.4.
    assert perm == {"p": 0.5, "method": "exact", "arrangements": 4}


def test_permutation_monte_carlo_is_seeded():
    strata = [stats.Stratum(f"t{i}", 6, 10, 4, 10) for i in range(25)]
    a = stats.stratified_permutation_p(strata, exact_max_strata=20, draws=500, seed=3)
    b = stats.stratified_permutation_p(strata, exact_max_strata=20, draws=500, seed=3)
    assert a == b
    assert a["method"] == "monte_carlo"


def test_entropy_and_chao1():
    assert stats.shannon_entropy([2, 2]) == 1.0
    assert stats.chao1([1, 1, 1, 2]) == 4 + 3 * 2 / (2 * 2)
