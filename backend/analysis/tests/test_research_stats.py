"""Reference tests for research/stats.py.

Every expected value was computed with scipy 1.15.3 / numpy 1.26.4 in the
dev container (read-only), e.g.::

    docker exec llmlab-django-1 /home/dev-user/.venv/bin/python -c \
        "import scipy.stats as s; print(s.mannwhitneyu([1.1, 2.3, 3.7, 4.2, 5.9], [2.0, 6.1, 7.4, 8.8]))"

The exact call is quoted next to each reference.
"""

from __future__ import annotations

import math

import pytest

from backend.analysis.research import stats

A = [12.1, 15.3, 9.8, 22.4, 18.0, 11.2, 14.7, 16.9, 13.3, 20.5]
B = [8.2, 10.4, 7.7, 12.9, 9.1, 11.8, 6.5, 10.0, 8.8, 13.6]
C = [14.0, 15.3, 16.1, 12.2, 19.4, 17.7, 13.9, 18.8]
MX, MY = [1.1, 2.3, 3.7, 4.2, 5.9], [2.0, 6.1, 7.4, 8.8]  # small, no ties -> exact MWU
TX, TY = [1, 2, 2, 3, 4, 4, 4, 5], [3, 3, 4, 5, 6, 6, 7]  # ties -> asymptotic MWU


def approx(value, rel=1e-9, abs_=1e-12):
    return pytest.approx(value, rel=rel, abs=abs_)


# --------------------------------------------------------------------------
# Descriptive statistics
# --------------------------------------------------------------------------


def test_quantile_type7_matches_numpy():
    # Reference value via np.quantile(A, [0, .1, .25, .5, .75, .9, 1])
    expected = [9.8, 11.059999999999999, 12.4, 15.0, 17.725, 20.689999999999998, 22.4]
    for q, value in zip([0, 0.1, 0.25, 0.5, 0.75, 0.9, 1], expected, strict=True):
        assert stats.quantile(A, q) == approx(value)


def test_mean_sd_iqr_match_numpy():
    # Reference value via np.mean(A), np.std(A, ddof=1), scipy.stats.iqr(A)
    assert stats.mean(A) == approx(15.420000000000002)
    assert stats.stdev(A) == approx(4.0663387845961765)
    assert stats.iqr(A) == approx(5.325000000000001)
    assert stats.median(A) == approx(15.0)


def test_undefined_statistics_are_none_not_zero():
    assert stats.mean([]) is None
    assert stats.median([None, None]) is None
    assert stats.stdev([3.0]) is None  # n < 2: undefined, never 0
    assert stats.variance([]) is None
    assert stats.iqr([]) is None


def test_missing_values_are_dropped_and_counted():
    d = stats.describe([1, None, 3, float("nan"), "x", True])
    assert d["n"] == 3  # 1, 3 and True (=1)
    assert d["n_missing"] == 3
    assert d["mean"] == approx(5 / 3)
    assert d["min"] == 1
    assert d["max"] == 3
    empty = stats.describe([None])
    assert empty == {
        "n": 0,
        "n_missing": 1,
        "mean": None,
        "sd": None,
        "median": None,
        "q1": None,
        "q3": None,
        "iqr": None,
        "min": None,
        "max": None,
    }


def test_rankdata_average_ties():
    # Reference value via scipy.stats.rankdata([3, 1, 4, 1, 5]) -> [3., 1.5, 4., 1.5, 5.]
    assert stats.rankdata([3, 1, 4, 1, 5]) == [3.0, 1.5, 4.0, 1.5, 5.0]


def test_quantile_rejects_out_of_range():
    with pytest.raises(ValueError, match="q must be"):
        stats.quantile([1, 2], 1.5)


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "df", "expected"),
    [
        # Reference value via scipy.stats.chi2.sf(x, df)
        (0.5, 1, 0.47950012218695337),
        (3.84, 1, 0.05004352124870519),
        (10, 3, 0.01856613546304325),
        (25, 4, 5.0309817823062075e-05),
        (100, 10, 5.4497019829205215e-17),
        (1e-3, 2, 0.9995001249791693),
        (60, 2, 9.357622968840163e-14),
        (7.5, 7.5, 0.43128944046089485),
        (0.2, 9, 0.9999994432502542),
    ],
)
def test_chi2_sf(x, df, expected):
    assert stats.chi2_sf(x, df) == pytest.approx(expected, rel=1e-8)


def test_chi2_sf_edges():
    assert stats.chi2_sf(0, 3) == 1.0
    with pytest.raises(ValueError, match="df"):
        stats.chi2_sf(1, 0)


@pytest.mark.parametrize(
    ("a", "x", "expected"),
    # Reference value via scipy.special.gammaincc(a, x)
    [
        (0.5, 0.1, 0.6547208460185768),
        (3, 2, 0.6766764161830634),
        (3, 8, 0.013753967744002971),
        (12.5, 30, 0.00010455486131526446),
    ],
)
def test_gammaincc(a, x, expected):
    assert stats.gammaincc(a, x) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize(
    ("a", "b", "x", "expected"),
    # Reference value via scipy.special.betainc(a, b, x)
    [
        (2, 3, 0.4, 0.5247999999999999),
        (0.5, 0.5, 0.9, 0.7951672353008665),
        (10, 2, 0.7, 0.11299009959999995),
        (1.5, 20, 0.05, 0.4434212016856899),
    ],
)
def test_betainc(a, b, x, expected):
    assert stats.betainc(a, b, x) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize(
    ("t", "df", "expected"),
    # Reference value via scipy.stats.t.sf(t, df)
    [
        (2.0, 5, 0.05096973941492914),
        (-1.3, 12, 0.8909914144582428),
        (0.5, 3.5, 0.3234252196612755),
        (10, 30, 2.2876257041148048e-11),
        (0.0, 4, 0.5),
    ],
)
def test_t_sf(t, df, expected):
    assert stats.t_sf(t, df) == pytest.approx(expected, rel=1e-8)


@pytest.mark.parametrize(
    ("x", "cdf", "sf"),
    # Reference value via scipy.stats.norm.cdf(x), scipy.stats.norm.sf(x)
    [
        (-3.0, 0.0013498980316300933, 0.9986501019683699),
        (-1.0, 0.15865525393145707, 0.8413447460685429),
        (0.0, 0.5, 0.5),
        (0.5, 0.6914624612740131, 0.3085375387259869),
        (1.96, 0.9750021048517795, 0.024997895148220435),
        (6.0, 0.9999999990134123, 9.865876450376946e-10),
    ],
)
def test_normal(x, cdf, sf):
    assert stats.normal_cdf(x) == approx(cdf)
    assert stats.normal_sf(x) == pytest.approx(sf, rel=1e-12)


@pytest.mark.parametrize(
    ("p", "expected"),
    # Reference value via scipy.stats.norm.ppf(p)
    [(0.025, -1.9599639845400545), (0.5, 0.0), (0.975, 1.959963984540054), (0.999, 3.090232306167813)],
)
def test_normal_ppf(p, expected):
    assert stats.normal_ppf(p) == approx(expected)


# --------------------------------------------------------------------------
# Mann-Whitney U
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alternative", "expected_p"),
    # Reference value via scipy.stats.mannwhitneyu(MX, MY, alternative=...) -> statistic 4.0 (method auto = exact)
    [("two-sided", 0.19047619047619047), ("less", 0.09523809523809523), ("greater", 0.9444444444444444)],
)
def test_mann_whitney_exact(alternative, expected_p):
    result = stats.mann_whitney_u(MX, MY, alternative=alternative)
    assert result["method"] == "exact"
    assert result["u"] == 4.0
    assert result["p"] == approx(expected_p)


def test_mann_whitney_exact_with_one_large_sample():
    # Reference value via scipy.stats.mannwhitneyu([3.1,4.4,5.0,2.2,9.9,7.3,6.6,1.5], [x/3+0.01 for x in range(1,21)])
    # giving statistic=105.0 and p=0.21769727856684382, exact because one sample has <= 8 values and no ties.
    y = [x / 3 + 0.01 for x in range(1, 21)]
    result = stats.mann_whitney_u([3.1, 4.4, 5.0, 2.2, 9.9, 7.3, 6.6, 1.5], y)
    assert result["method"] == "exact"
    assert result["u"] == 105.0
    assert result["p"] == approx(0.21769727856684382)


@pytest.mark.parametrize(
    ("alternative", "expected_p"),
    # Reference value via scipy.stats.mannwhitneyu(TX, TY, alternative=...) -> statistic 12.0
    # (ties, so the asymptotic method with continuity correction)
    [("two-sided", 0.06850971593297848), ("less", 0.03425485796648924), ("greater", 0.9737593470782838)],
)
def test_mann_whitney_ties_asymptotic(alternative, expected_p):
    result = stats.mann_whitney_u(TX, TY, alternative=alternative)
    assert result["method"] == "asymptotic"
    assert result["u"] == 12.0
    assert result["p"] == approx(expected_p)


def test_mann_whitney_large_samples():
    # Reference value via scipy.stats.mannwhitneyu(A, B) -> (89.0, 0.0036105143123296027)
    # Reference value via scipy.stats.mannwhitneyu(A, C) -> (34.5, 0.6566875815114986)
    ab = stats.mann_whitney_u(A, B)
    assert (ab["u"], ab["p"]) == (89.0, approx(0.0036105143123296027))
    ac = stats.mann_whitney_u(A, C)
    assert (ac["u"], ac["p"]) == (34.5, approx(0.6566875815114986))


def test_mann_whitney_degenerate_inputs():
    # Reference value via scipy.stats.mannwhitneyu([2, 2, 2], [2, 2]) -> (3.0, 1.0)
    const = stats.mann_whitney_u([2, 2, 2], [2, 2])
    assert (const["u"], const["p"]) == (3.0, 1.0)
    assert stats.mann_whitney_u([], [1, 2]) is None
    assert stats.mann_whitney_u([None], [1, 2]) is None


def test_mann_whitney_exact_distribution_totals():
    for m, n in [(1, 1), (3, 5), (8, 8), (4, 30)]:
        counts = stats._mwu_counts(m, n)
        assert len(counts) == m * n + 1
        assert sum(counts) == math.comb(m + n, m)
        assert list(counts) == list(reversed(counts))  # U is symmetric around mn/2


# --------------------------------------------------------------------------
# Wilcoxon signed-rank
# --------------------------------------------------------------------------

D_EXACT = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30, -0.4]
D_TIES = [1.5, -1.5, 2.0, 3.0, 3.0, -0.5, 4.0, 2.0, 5.0, 6.0, -2.0, 7.0]
D20 = [0, 0, 0, 1, 2, -3, 4, 5, -6, 7, 8, 9, 10, -11, 12, 13, 14, 15, -2, 3.5]
D60 = [((i * 37) % 23) - 9.5 + (0.25 if i % 7 == 0 else 0) for i in range(60)]


@pytest.mark.parametrize(
    ("alternative", "statistic", "expected_p"),
    # Reference value via scipy.stats.wilcoxon(D_EXACT, alternative=...) (no ties/zeros, n=10 -> exact)
    [("two-sided", 1.0, 0.00390625), ("less", 54.0, 0.9990234375), ("greater", 54.0, 0.001953125)],
)
def test_wilcoxon_exact(alternative, statistic, expected_p):
    result = stats.wilcoxon_signed_rank(D_EXACT, alternative=alternative)
    assert result["method"] == "exact"
    assert result["statistic"] == statistic
    assert result["p"] == approx(expected_p)


def test_wilcoxon_paired_xy_with_ties_uses_exact_permutation():
    # Reference value via scipy.stats.wilcoxon(px, py) -> (18.0, 0.6328125); ties + a zero, n=10 -> permutation test
    px = [125, 115, 130, 140, 140, 115, 140, 125, 140, 135]
    py = [110, 122, 125, 120, 140, 124, 123, 137, 135, 145]
    result = stats.wilcoxon_signed_rank(px, py)
    assert result["method"] == "permutation"
    assert result["n_zero"] == 1
    assert result["statistic"] == 18.0
    assert result["p"] == approx(0.6328125)


@pytest.mark.parametrize(
    ("alternative", "statistic", "expected_p"),
    # Reference value via scipy.stats.wilcoxon(D_TIES, alternative=...) (ties, n=12 -> permutation)
    [("two-sided", 8.5, 0.0146484375), ("less", 69.5, 0.99462890625), ("greater", 69.5, 0.00732421875)],
)
def test_wilcoxon_ties_permutation(alternative, statistic, expected_p):
    result = stats.wilcoxon_signed_rank(D_TIES, alternative=alternative)
    assert result["method"] == "permutation"
    assert result["statistic"] == statistic
    assert result["p"] == approx(expected_p)


def test_wilcoxon_zeros_small_sample():
    # Reference value via scipy.stats.wilcoxon([0.0, 1.0, -2.0, 3.0, 4.0, 0.0, 5.0, 6.5]) -> (2.0, 0.09375)
    result = stats.wilcoxon_signed_rank([0.0, 1.0, -2.0, 3.0, 4.0, 0.0, 5.0, 6.5])
    assert result["statistic"] == 2.0
    assert result["p"] == approx(0.09375)


@pytest.mark.parametrize(
    ("kwargs", "statistic", "expected_p"),
    [
        # Reference value via scipy.stats.wilcoxon(D20) (zeros, n=20 -> asymptotic)
        ({}, 27.5, 0.020346711127751316),
        # Reference value via scipy.stats.wilcoxon(D20, zero_method="pratt")
        ({"zero_method": "pratt"}, 39.5, 0.019324646125074608),
        # Reference value via scipy.stats.wilcoxon(D20, zero_method="zsplit")
        ({"zero_method": "zsplit"}, 42.5, 0.019579560404220396),
        # Reference value via scipy.stats.wilcoxon(D20, correction=True)
        ({"correction": True}, 27.5, 0.021663316798207967),
    ],
)
def test_wilcoxon_asymptotic_zero_methods(kwargs, statistic, expected_p):
    result = stats.wilcoxon_signed_rank(D20, **kwargs)
    assert result["method"] == "asymptotic"
    assert result["statistic"] == statistic
    assert result["p"] == approx(expected_p)


def test_wilcoxon_large_sample():
    # Reference value via scipy.stats.wilcoxon(D60) -> (699.5, 0.11245278512104012)
    # Reference value via scipy.stats.wilcoxon(D60, alternative="greater") -> (1130.5, 0.05622639256052006)
    two = stats.wilcoxon_signed_rank(D60)
    assert (two["statistic"], two["p"]) == (699.5, approx(0.11245278512104012))
    greater = stats.wilcoxon_signed_rank(D60, alternative="greater")
    assert (greater["statistic"], greater["p"]) == (1130.5, approx(0.05622639256052006))


def test_wilcoxon_undefined_and_pair_dropping():
    assert stats.wilcoxon_signed_rank([0, 0, 0]) is None
    assert stats.wilcoxon_signed_rank([]) is None
    # A missing side drops the whole pair.
    result = stats.wilcoxon_signed_rank([1, 2, None, 4], [0, None, 3, 1])
    assert result["n"] == 2
    with pytest.raises(ValueError, match="zero_method"):
        stats.wilcoxon_signed_rank([1, 2], zero_method="bogus")


def test_matched_pairs_rank_biserial():
    assert stats.matched_pairs_rank_biserial(54.0, 1.0) == approx(53 / 55)
    assert stats.matched_pairs_rank_biserial(0.0, 0.0) is None


# --------------------------------------------------------------------------
# k-sample tests
# --------------------------------------------------------------------------


def test_kruskal_wallis():
    # Reference value via scipy.stats.kruskal(A, B, C) -> (13.919911716397483, 0.0009491384702979117)
    result = stats.kruskal_wallis([A, B, C])
    assert result["statistic"] == approx(13.919911716397483)
    assert result["p"] == approx(0.0009491384702979117)
    assert result["df"] == 2
    assert result["epsilon_squared"] == approx(13.919911716397483 / 27)


def test_kruskal_wallis_with_ties():
    # Reference value via scipy.stats.kruskal([1,2,2,3,5], [2,3,3,4,6,6], [5,6,7,7,8])
    # giving statistic=8.912912912912924 and p=0.011603407703633302
    result = stats.kruskal_wallis([[1, 2, 2, 3, 5], [2, 3, 3, 4, 6, 6], [5, 6, 7, 7, 8]])
    assert result["statistic"] == approx(8.912912912912924)
    assert result["p"] == approx(0.011603407703633302)


def test_kruskal_wallis_undefined():
    assert stats.kruskal_wallis([A]) is None
    assert stats.kruskal_wallis([A, []]) is None
    assert stats.kruskal_wallis([[2, 2], [2, 2, 2]]) is None  # all values tied


def test_friedman_with_ties():
    # with blocks = [[1,2,3],[2,3,1],[1,3,2],[1,2,2],[3,1,2],[1,2,3],[2,3,3],[1,3,2]]
    # Reference value via scipy.stats.friedmanchisquare(*zip(*blocks)) -> (5.066666666666666, 0.07939393227707821)
    blocks = [[1, 2, 3], [2, 3, 1], [1, 3, 2], [1, 2, 2], [3, 1, 2], [1, 2, 3], [2, 3, 3], [1, 3, 2]]
    result = stats.friedman(blocks)
    assert result["statistic"] == approx(5.066666666666666)
    assert result["p"] == approx(0.07939393227707821)
    assert result["kendalls_w"] == approx(5.066666666666666 / (8 * 2))
    assert result["n_blocks"] == 8
    assert len(result["mean_ranks"]) == 3


def test_friedman_three_and_four_treatments():
    # Reference value via scipy.stats.friedmanchisquare(*zip(*[[A[i], B[i], C[i % 8] + 0.05] for i in range(10)]))
    # giving statistic=10.400000000000006 and p=0.005516564420760757
    rows = [[A[i], B[i], C[i % 8] + 0.05] for i in range(10)]
    result = stats.friedman(rows)
    assert result["statistic"] == approx(10.400000000000006)
    assert result["p"] == approx(0.005516564420760757)
    # Reference value via scipy.stats.friedmanchisquare on the 5 x 4 blocks below
    # giving statistic=14.039999999999992 and p=0.0028512084304522568
    blocks4 = [
        [4.1, 3.2, 5.5, 2.0],
        [3.9, 3.3, 5.0, 2.5],
        [5.0, 4.1, 4.9, 3.0],
        [4.4, 2.9, 6.1, 2.2],
        [3.8, 3.6, 5.2, 2.7],
    ]
    result4 = stats.friedman(blocks4)
    assert result4["statistic"] == approx(14.039999999999992)
    assert result4["p"] == approx(0.0028512084304522568)
    assert result4["kendalls_w"] == approx(14.04 / (5 * 3))


def test_friedman_undefined():
    assert stats.friedman([[1, 2, 3]]) is None  # one block
    assert stats.friedman([[1, 2], [1, None]]) is None  # incomplete block must be dropped by the caller
    assert stats.friedman([[1, 2], [3]]) is None  # ragged
    assert stats.friedman([[1, 1], [2, 2]]) is None  # every block tied


# --------------------------------------------------------------------------
# Effect sizes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "y", "delta", "a12"),
    [
        # Reference values via m = np.sign(np.subtract.outer(x, y)) as delta = m.mean() and
        # A12 as ((m > 0).sum() + 0.5 * (m == 0).sum()) / m.size
        (A, B, 0.78, 0.89),
        (TX, TY, -0.5714285714285714, 0.21428571428571427),
        (A, C, -0.1375, 0.43125),
    ],
)
def test_cliffs_delta_and_a12(x, y, delta, a12):
    assert stats.cliffs_delta(x, y) == approx(delta)
    assert stats.vargha_delaney_a12(x, y) == approx(a12)


def test_cliffs_delta_binary_is_risk_difference():
    x = [1, 1, 1, 0]  # 0.75
    y = [1, 0, 0, 0, 0]  # 0.2
    assert stats.cliffs_delta(x, y) == approx(0.75 - 0.2)
    assert stats.cliffs_delta([], [1]) is None


@pytest.mark.parametrize(
    ("delta", "label"),
    [
        (0.0, "negligible"),
        (-0.146, "negligible"),
        (0.147, "small"),
        (0.329, "small"),
        (-0.33, "medium"),
        (0.474, "large"),
        (None, None),
    ],
)
def test_cliffs_magnitude(delta, label):
    assert stats.cliffs_magnitude(delta) == label


def test_bootstrap_delta_ci():
    ci = stats.bootstrap_delta_ci(A, B, resamples=500, seed=3)
    assert ci is not None
    assert ci[0] <= 0.78 <= ci[1]
    assert ci == stats.bootstrap_delta_ci(A, B, resamples=500, seed=3)
    paired_ci = stats.bootstrap_delta_ci(A, B, is_paired=True, resamples=500, seed=3)
    assert paired_ci is not None
    assert -1.0 <= paired_ci[0] <= paired_ci[1] <= 1.0
    assert stats.bootstrap_delta_ci([1.0], B) is None


# --------------------------------------------------------------------------
# Intervals
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("k", "n", "lo", "hi"),
    # Reference value via scipy.stats.binomtest(k, n).proportion_ci(confidence_level=0.95, method="wilson")
    [
        (0, 10, 0.0, 0.27753279986288926),
        (3, 10, 0.10779126740630102, 0.6032218525388546),
        (10, 10, 0.7224672001371109, 1.0),
        (17, 40, 0.28509392559950375, 0.5780493145800892),
        (1, 1, 0.20654931437723745, 1.0),
    ],
)
def test_wilson_ci(k, n, lo, hi):
    got = stats.wilson_ci(k, n)
    assert got[0] == approx(lo)
    assert got[1] == approx(hi)


def test_wilson_ci_level_and_empty():
    # Reference value via scipy.stats.binomtest(17, 40).proportion_ci(confidence_level=0.90, method="wilson")
    lo, hi = stats.wilson_ci(17, 40, level=0.90)
    assert lo == approx(0.30523413698015683)
    assert hi == approx(0.5542688803166618)
    assert stats.wilson_ci(0, 0) is None
    with pytest.raises(ValueError, match="successes"):
        stats.wilson_ci(5, 3)


def test_bootstrap_ci_is_seeded_and_sane():
    ci = stats.bootstrap_ci(A, resamples=2000, seed=7)
    assert ci == stats.bootstrap_ci(A, resamples=2000, seed=7)
    assert ci != stats.bootstrap_ci(A, resamples=2000, seed=8)
    assert ci[0] < stats.mean(A) < ci[1]
    assert min(A) <= ci[0] <= ci[1] <= max(A)
    assert stats.bootstrap_ci([5.0], resamples=100) is None  # n < 2: no spread to resample
    assert stats.bootstrap_ci([], resamples=100) is None
    assert stats.bootstrap_ci([2.0, 2.0, 2.0], resamples=50) == (2.0, 2.0)
    median_ci = stats.bootstrap_ci(A, stats.median, resamples=500, seed=1)
    assert median_ci[0] <= stats.median(A) <= median_ci[1]


def test_cluster_bootstrap_equals_plain_bootstrap_for_singleton_clusters():
    plain = stats.bootstrap_ci(A, resamples=1000, seed=11)
    clustered = stats.cluster_bootstrap_ci([[v] for v in A], resamples=1000, seed=11)
    assert clustered == plain


def test_cluster_bootstrap_resamples_whole_clusters():
    clusters = [[1.0, 1.2, 0.8], [5.0, 5.5], [3.0], []]
    ci = stats.cluster_bootstrap_ci(clusters, resamples=1000, seed=5)
    assert ci == stats.cluster_bootstrap_ci(clusters, resamples=1000, seed=5)
    assert 0.8 <= ci[0] <= ci[1] <= 5.5
    assert stats.cluster_bootstrap_ci([[1.0, 2.0], []], resamples=100) is None  # one non-empty cluster


def test_derive_seed_is_stable():
    assert stats.derive_seed(0, "a", "b") == stats.derive_seed(0, "a", "b")
    assert stats.derive_seed(0, "a", "b") != stats.derive_seed(0, "b", "a")
    assert 0 <= stats.derive_seed(123, "x") < 2**63


# --------------------------------------------------------------------------
# Multiple comparisons
# --------------------------------------------------------------------------

P = [0.01, 0.04, 0.03, 0.005, 0.2]


def test_holm():
    # Reference value via statsmodels' algorithm in numpy, with order = argsort(p):
    # then adjusted[order] is minimum(1, maximum.accumulate(p[order] * arange(m, 0, -1)))
    assert stats.holm(P) == [approx(v) for v in [0.04, 0.09, 0.09, 0.025, 0.2]]


def test_benjamini_hochberg():
    # Reference value via scipy.stats.false_discovery_control([0.01, 0.04, 0.03, 0.005, 0.2], method="bh")
    assert stats.benjamini_hochberg(P) == [approx(v) for v in [0.025, 0.05, 0.05, 0.025, 0.2]]


def test_adjustment_keeps_missing_p_values_out_of_the_family():
    assert stats.holm([0.01, None, 0.04]) == [approx(0.02), None, approx(0.04)]
    assert stats.benjamini_hochberg([None, 0.01, 0.04]) == [None, approx(0.02), approx(0.04)]
    assert stats.adjust([0.5, None], "none") == [0.5, None]
    assert stats.holm([]) == []
    with pytest.raises(ValueError, match="unknown"):
        stats.adjust([0.1], "sidak")


# --------------------------------------------------------------------------
# Agreement and association
# --------------------------------------------------------------------------


def test_krippendorff_alpha_nominal_reference_example():
    # Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability", example C: published alpha = 0.743.
    # Independent numpy coincidence-matrix implementation in the dev container -> 0.743421052631579.
    coders = [
        [1, 2, 3, 3, 2, 1, 4, 1, 2, None, None, None],
        [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, None, 3],
        [None, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, None],
        [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, None],
    ]
    units = list(zip(*coders, strict=True))
    assert stats.krippendorff_alpha_nominal(units) == approx(0.743421052631579)


def test_krippendorff_alpha_edge_cases():
    assert stats.krippendorff_alpha_nominal([[1, 1], [0, 0], [1, 1]]) == approx(1.0)
    assert stats.krippendorff_alpha_nominal([[1, 1], [1, 1]]) is None  # one category: undefined
    assert stats.krippendorff_alpha_nominal([[1, None], [None]]) is None  # nothing pairable
    assert stats.krippendorff_alpha_nominal([]) is None
    # Systematic disagreement is below zero.
    assert stats.krippendorff_alpha_nominal([[1, 0], [0, 1], [1, 0], [0, 1]]) < 0


def test_jaccard():
    assert stats.jaccard({1, 2, 3}, {2, 3, 4}) == approx(0.5)
    assert stats.jaccard(set(), set()) is None
    assert stats.jaccard({1}, set()) == 0.0
    assert stats.mean_pairwise_jaccard([{1, 2}, {1, 2}, {1}]) == approx((1 + 0.5 + 0.5) / 3)
    assert stats.mean_pairwise_jaccard([set(), set()]) is None


def test_spearman():
    # Reference value via scipy.stats.spearmanr(A, [x * x - 3 * x for x in B]) -> (0.6, 0.06668799999999996)
    result = stats.spearman(A, [x * x - 3 * x for x in B])
    assert result["rho"] == approx(0.6)
    assert result["p"] == pytest.approx(0.06668799999999996, rel=1e-6)
    # Reference value via scipy.stats.spearmanr([1,2,2,3,4,5,5,6], [2,1,3,3,5,4,6,6])
    # giving statistic=0.8902439024390245 and p=0.003039295568488808
    ties = stats.spearman([1, 2, 2, 3, 4, 5, 5, 6], [2, 1, 3, 3, 5, 4, 6, 6])
    assert ties["rho"] == approx(0.8902439024390245)
    assert ties["p"] == pytest.approx(0.003039295568488808, rel=1e-6)
    assert stats.spearman([1, 2], [3, 4]) is None
    assert stats.spearman([1, 1, 1], [1, 2, 3]) is None
