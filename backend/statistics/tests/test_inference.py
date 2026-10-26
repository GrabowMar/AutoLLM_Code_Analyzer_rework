"""Small-sample inference primitives."""

from __future__ import annotations

import pytest

from backend.statistics.services import inference as inf


def test_spearman_handles_ties_and_constants():
    assert inf.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert inf.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert inf.spearman([1, 1, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9487, abs=1e-4)
    assert inf.spearman([1, 1, 1], [1, 2, 3]) is None
    assert inf.spearman([1, 2], [1, 2]) is None


def test_exact_permutation_p_for_small_n():
    # n = 5, perfect monotone: 2 of 120 permutations reach |rho| = 1.
    assert inf.spearman_permutation_p([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(2 / 120, abs=1e-4)


def test_monte_carlo_permutation_is_reproducible_and_never_zero():
    xs = list(range(12))
    p1 = inf.spearman_permutation_p(xs, xs, seed=3, draws=500)
    p2 = inf.spearman_permutation_p(xs, xs, seed=3, draws=500)
    assert p1 == p2
    assert 0 < p1 < 0.01


def test_bootstrap_ci_brackets_rho():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    ys = [2, 1, 4, 3, 6, 5, 8, 7, 10, 9]
    lo, hi = inf.spearman_bootstrap_ci(xs, ys, seed=1, draws=500)
    assert lo <= inf.spearman(xs, ys) <= hi


def test_leave_one_out_flags_a_correlation_resting_on_one_unit():
    # Flat cloud plus one outlier that creates the correlation.
    xs = [1, 2, 3, 4, 100]
    ys = [3, 1, 4, 2, 100]
    loo = inf.leave_one_out(xs, ys, ["a", "b", "c", "d", "outlier"])
    assert loo["most_influential"] == "outlier"
    assert loo["min"] < loo["max"]


def test_corrections():
    assert inf.holm([0.01, 0.04, None, 0.03]) == [0.03, 0.06, None, 0.06]
    assert inf.benjamini_hochberg([0.01, 0.04, 0.03]) == [0.03, 0.04, 0.04]


def test_zero_count_bound_and_wilson():
    assert inf.zero_count_upper_bound(20) == pytest.approx(0.1391, abs=1e-4)
    assert inf.zero_count_upper_bound(0) is None
    lo, hi = inf.wilson_interval(0, 10)
    assert lo == 0.0
    assert 0.25 < hi < 0.35


def test_kendall_and_describe():
    assert inf.kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert inf.kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0
    d = inf.describe([1, 2, 3, 4])
    assert (d["n"], d["median"], d["q1"], d["q3"]) == (4, 2.5, 1.75, 3.25)
    assert inf.coefficient_of_variation([10, 10, 10]) == 0.0
