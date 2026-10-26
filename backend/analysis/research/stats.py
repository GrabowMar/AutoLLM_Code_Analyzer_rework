"""Pure-Python statistics for research reports (owner: reports stage).

No numpy/scipy at runtime; every function is tested against reference
values. Undefined results are ``None`` (never 0).

Conventions
-----------
* Inputs are plain sequences. ``None``, NaN, infinities and non-numeric
  entries are *missing data* and are dropped by :func:`finite` before any
  computation; :func:`describe` reports how many were dropped
  (``n_missing``). Booleans count as 1/0 so binary outcomes work unchanged.
* Paired functions drop a pair when either side is missing.
* Tests mirror ``scipy.stats`` (1.15) defaults so reference values can be
  reproduced: Mann-Whitney U with continuity correction and the exact null
  distribution for small tie-free samples; Wilcoxon signed-rank with
  ``zero_method="wilcox"`` (zero differences dropped), no continuity
  correction, exact distribution for n <= 50 without ties/zeros and an
  exact sign-flip permutation distribution for n <= 13 with ties/zeros;
  Kruskal-Wallis and Friedman with tie correction.
* Randomness (bootstrap) only comes from ``random.Random(seed)``; the same
  seed and input always give the same interval.
"""

from __future__ import annotations

import bisect
import functools
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from collections.abc import Callable
from collections.abc import Hashable
from collections.abc import Iterable
from collections.abc import Sequence
from statistics import NormalDist
from typing import Any

STATS_VERSION = "stats-v1"

# Romano et al. (2006) thresholds for |Cliff's delta|; cite with care (a
# conference presentation), but they are the de-facto standard in SE.
CLIFF_THRESHOLDS: tuple[tuple[float, str], ...] = ((0.147, "negligible"), (0.33, "small"), (0.474, "medium"))

_STANDARD_NORMAL = NormalDist()
_TINY = 1e-300
_EPS = 1e-15
_MAX_ITER = 10_000
_ALTERNATIVES = ("two-sided", "less", "greater")


# --------------------------------------------------------------------------
# Cleaning and descriptive statistics
# --------------------------------------------------------------------------


def to_float(value: Any) -> float | None:
    """``value`` as a finite float, or ``None`` when it is missing / not numeric."""
    if value is None or isinstance(value, str):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def finite(values: Iterable[Any]) -> list[float]:
    """The finite numeric entries of *values* (missing data dropped)."""
    out: list[float] = []
    for v in values:
        f = to_float(v)
        if f is not None:
            out.append(f)
    return out


def paired(x: Iterable[Any], y: Iterable[Any]) -> tuple[list[float], list[float]]:
    """Complete pairs of *x* and *y* (a pair is dropped when either side is missing)."""
    xs: list[float] = []
    ys: list[float] = []
    for a, b in zip(x, y, strict=True):
        fa, fb = to_float(a), to_float(b)
        if fa is not None and fb is not None:
            xs.append(fa)
            ys.append(fb)
    return xs, ys


def mean(values: Iterable[Any]) -> float | None:
    xs = finite(values)
    return math.fsum(xs) / len(xs) if xs else None


def quantile(values: Iterable[Any], q: float) -> float | None:
    """Hyndman & Fan type 7 quantile (numpy/R default: linear interpolation)."""
    if not 0.0 <= q <= 1.0:
        msg = "q must be within [0, 1]"
        raise ValueError(msg)
    xs = sorted(finite(values))
    return _quantile_sorted(xs, q) if xs else None


def _quantile_sorted(xs: list[float], q: float) -> float:
    """Type 7 quantile of an already sorted, clean, non-empty list."""
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    if lo >= len(xs) - 1:
        return xs[-1]
    return xs[lo] + (h - lo) * (xs[lo + 1] - xs[lo])


def median(values: Iterable[Any]) -> float | None:
    return quantile(values, 0.5)


def iqr(values: Iterable[Any]) -> float | None:
    xs = finite(values)
    q1, q3 = quantile(xs, 0.25), quantile(xs, 0.75)
    return None if q1 is None or q3 is None else q3 - q1


def variance(values: Iterable[Any]) -> float | None:
    """Sample variance (n - 1); ``None`` for n < 2 (undefined, not 0).

    Computed with exact rational arithmetic (``statistics.variance``), so
    identical values give exactly 0 instead of rounding noise.
    """
    xs = finite(values)
    if len(xs) < 2:
        return None
    return statistics.variance(xs)


def stdev(values: Iterable[Any]) -> float | None:
    """Sample standard deviation (n - 1); ``None`` for n < 2."""
    var = variance(values)
    return None if var is None else math.sqrt(var)


def describe(values: Iterable[Any]) -> dict[str, Any]:
    """``{n, n_missing, mean, sd, median, q1, q3, iqr, min, max}`` (None where undefined)."""
    raw = list(values)
    xs = finite(raw)
    q1, q3 = quantile(xs, 0.25), quantile(xs, 0.75)
    return {
        "n": len(xs),
        "n_missing": len(raw) - len(xs),
        "mean": mean(xs),
        "sd": stdev(xs),
        "median": quantile(xs, 0.5),
        "q1": q1,
        "q3": q3,
        "iqr": None if q1 is None or q3 is None else q3 - q1,
        "min": min(xs) if xs else None,
        "max": max(xs) if xs else None,
    }


def rankdata(values: Sequence[float]) -> list[float]:
    """1-based ranks with ties given their average rank (scipy ``method="average"``)."""
    n = len(values)
    order = sorted(range(n), key=values.__getitem__)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def tie_sizes(values: Iterable[Hashable]) -> list[int]:
    """Sizes of the tie groups (only groups with more than one member)."""
    return [c for c in Counter(values).values() if c > 1]


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------


def normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_sf(x: float) -> float:
    """Upper tail of the standard normal (erfc keeps precision far in the tail)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def normal_ppf(p: float) -> float:
    return _STANDARD_NORMAL.inv_cdf(p)


def _gamma_series(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) by its series (x < a + 1)."""
    ap = a
    total = delta = 1.0 / a
    for _ in range(_MAX_ITER):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_continued_fraction(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) by Lentz's continued fraction (x >= a + 1)."""
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_ITER):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammaincc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma function Q(a, x)."""
    if a <= 0:
        msg = "a must be positive"
        raise ValueError(msg)
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        return max(0.0, 1.0 - _gamma_series(a, x))
    return _gamma_continued_fraction(a, x)


def chi2_sf(x: float, df: float) -> float:
    """Survival function of the chi-squared distribution."""
    if df <= 0:
        msg = "df must be positive"
        raise ValueError(msg)
    return gammaincc(df / 2.0, x / 2.0)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    h = d
    for m in range(1, _MAX_ITER):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = _TINY if abs(d) < _TINY else d
        c = 1.0 + aa / c
        c = _TINY if abs(c) < _TINY else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = _TINY if abs(d) < _TINY else d
        c = 1.0 + aa / c
        c = _TINY if abs(c) < _TINY else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """Survival function of Student's t distribution."""
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t >= 0 else 1.0 - tail


# --------------------------------------------------------------------------
# Intervals
# --------------------------------------------------------------------------


def derive_seed(base: int, *parts: Any) -> int:
    """A stable 63-bit seed for one sub-analysis, independent of iteration order."""
    payload = json.dumps([base, *parts], sort_keys=True, default=str).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") >> 1


def _percentile_interval(estimates: list[float], level: float) -> tuple[float, float] | None:
    ordered = sorted(e for e in estimates if math.isfinite(e))
    if not ordered:
        return None
    tail = (1.0 - level) / 2.0
    return _quantile_sorted(ordered, tail), _quantile_sorted(ordered, 1.0 - tail)


def _check_level(level: float) -> None:
    if not 0.0 < level < 1.0:
        msg = "level must be within (0, 1)"
        raise ValueError(msg)


def bootstrap_ci(
    values: Iterable[Any],
    statistic: Callable[[list[float]], float | None] | None = None,
    *,
    resamples: int = 10_000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval of *statistic* (default: the mean).

    ``None`` for fewer than 2 observations: one value has no sampling
    spread to resample, and a degenerate ``[x, x]`` would read as certainty.
    """
    _check_level(level)
    xs = finite(values)
    n = len(xs)
    if n < 2 or resamples < 1:
        return None
    if statistic is None and min(xs) == max(xs):
        return xs[0], xs[0]  # every resample has the same mean: skip the loop, avoid rounding noise
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not cryptography
    estimates: list[float] = []
    for _ in range(resamples):
        sample = rng.choices(xs, k=n)
        value = sum(sample) / n if statistic is None else statistic(sample)
        if value is not None:
            estimates.append(value)
    return _percentile_interval(estimates, level)


def cluster_bootstrap_ci(
    clusters: Iterable[Iterable[Any]],
    statistic: Callable[[list[float]], float | None] | None = None,
    *,
    resamples: int = 10_000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Percentile bootstrap that resamples whole clusters (e.g. apps) with replacement.

    Observations of one cluster are not independent (replicates of one app
    spec), so the cluster is the resampling unit; the statistic is computed
    over the pooled observations of the drawn clusters. With one
    observation per cluster this equals :func:`bootstrap_ci` for the same
    seed. ``None`` for fewer than 2 non-empty clusters.
    """
    _check_level(level)
    groups = [g for g in (finite(c) for c in clusters) if g]
    m = len(groups)
    if m < 2 or resamples < 1:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not cryptography
    estimates: list[float] = []
    if statistic is None:
        if min(min(g) for g in groups) == max(max(g) for g in groups):
            return groups[0][0], groups[0][0]
        # The pooled mean only needs each drawn cluster's (sum, size); drawing from this list
        # consumes the RNG exactly like drawing the clusters themselves.
        totals = [(sum(g), len(g)) for g in groups]
        for _ in range(resamples):
            drawn = rng.choices(totals, k=m)
            estimates.append(sum(s for s, _ in drawn) / sum(c for _, c in drawn))
        return _percentile_interval(estimates, level)
    for _ in range(resamples):
        drawn = rng.choices(groups, k=m)
        value = statistic([v for g in drawn for v in g])
        if value is not None:
            estimates.append(value)
    return _percentile_interval(estimates, level)


def wilson_ci(successes: int, n: int, *, level: float = 0.95) -> tuple[float, float] | None:
    """Wilson score interval for a proportion (no continuity correction); ``None`` for n == 0."""
    _check_level(level)
    if n <= 0:
        return None
    if not 0 <= successes <= n:
        msg = "successes must be within [0, n]"
        raise ValueError(msg)
    z = normal_ppf(1.0 - (1.0 - level) / 2.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


# --------------------------------------------------------------------------
# Two-sample tests and effect sizes
# --------------------------------------------------------------------------


def _check_alternative(alternative: str) -> None:
    if alternative not in _ALTERNATIVES:
        msg = f"alternative must be one of {_ALTERNATIVES}"
        raise ValueError(msg)


@functools.lru_cache(maxsize=128)
def _mwu_counts(m: int, n: int) -> tuple[int, ...]:
    """Number of orderings giving U = 0..m*n for sample sizes m, n.

    These are the coefficients of the Gaussian binomial coefficient
    ``[m+n choose m]_q = prod_i (1 - q^(n+i)) / (1 - q^i)``; integer
    arithmetic keeps the exact distribution exact.
    """
    m, n = min(m, n), max(m, n)
    poly = [1]
    for i in range(1, m + 1):
        k = n + i
        nxt = [*poly, *([0] * k)]
        for j, c in enumerate(poly):
            nxt[j + k] -= c
        for j in range(i, len(nxt)):
            nxt[j] += nxt[j - i]
        poly = nxt
    return tuple(poly[: m * n + 1])


def _mwu_exact_sf(u: int, m: int, n: int) -> float:
    counts = _mwu_counts(m, n)
    if u <= 0:
        return 1.0
    if u >= len(counts):
        return 0.0
    return sum(counts[u:]) / math.comb(m + n, m)


def mann_whitney_u(
    x: Iterable[Any],
    y: Iterable[Any],
    *,
    alternative: str = "two-sided",
    method: str = "auto",
    continuity: bool = True,
) -> dict[str, Any] | None:
    """Mann-Whitney U test (scipy semantics); ``None`` when a sample is empty.

    ``u`` is U1 (for *x*). ``method="auto"`` uses the exact null
    distribution when a sample has at most 8 observations and there are no
    ties, else the tie-corrected normal approximation (with continuity
    correction). Two identical constant samples give p = 1.
    """
    _check_alternative(alternative)
    xs, ys = finite(x), finite(y)
    n1, n2 = len(xs), len(ys)
    if n1 == 0 or n2 == 0:
        return None
    pooled = xs + ys
    ranks = rankdata(pooled)
    u1 = math.fsum(ranks[:n1]) - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    ties = tie_sizes(pooled)
    if alternative == "greater":
        u, factor = u1, 1
    elif alternative == "less":
        u, factor = u2, 1
    else:
        u, factor = max(u1, u2), 2
    if method == "auto":
        method = "asymptotic" if (n1 > 8 and n2 > 8) or ties else "exact"
    z: float | None = None
    if method == "exact":
        if ties:
            msg = "the exact Mann-Whitney distribution assumes no ties"
            raise ValueError(msg)
        p = _mwu_exact_sf(round(u), n1, n2)
    elif method == "asymptotic":
        n = n1 + n2
        tie_term = sum(t**3 - t for t in ties)
        var = n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))
        numerator = u - n1 * n2 / 2.0 - (0.5 if continuity else 0.0)
        if var <= 0:
            p = 1.0  # every observation tied: no evidence of a difference
        else:
            z = numerator / math.sqrt(var)
            p = normal_sf(z)
    else:
        msg = "method must be auto, exact or asymptotic"
        raise ValueError(msg)
    return {
        "u": u1,
        "u2": u2,
        "z": z,
        "p": min(1.0, max(0.0, p * factor)),
        "method": method,
        "n1": n1,
        "n2": n2,
        "alternative": alternative,
    }


def _subset_sum_counts(weights: Sequence[int]) -> list[int]:
    """counts[s] = number of sign assignments whose positive weights sum to s."""
    total = sum(weights)
    counts = [0] * (total + 1)
    counts[0] = 1
    for w in weights:
        for s in range(total, w - 1, -1):
            counts[s] += counts[s - w]
    return counts


def _exact_signed_rank_p(weights: list[int], observed: int, alternative: str) -> float:
    counts = _subset_sum_counts(weights)
    total = sum(counts)
    observed = max(0, min(observed, len(counts) - 1))
    cdf = sum(counts[: observed + 1]) / total
    sf = sum(counts[observed:]) / total
    if alternative == "less":
        return cdf
    if alternative == "greater":
        return sf
    return min(1.0, 2.0 * min(cdf, sf))


def wilcoxon_signed_rank(
    x: Iterable[Any],
    y: Iterable[Any] | None = None,
    *,
    zero_method: str = "wilcox",
    alternative: str = "two-sided",
    method: str = "auto",
    correction: bool = False,
) -> dict[str, Any] | None:
    """Wilcoxon signed-rank test on ``d = x - y`` (or on *x* when *y* is None).

    Zero differences (``zero_method``): ``"wilcox"`` drops them before
    ranking (default, as scipy); ``"pratt"`` ranks them, then drops them
    from the statistic and adjusts the normal approximation (Cureton);
    ``"zsplit"`` ranks them and splits their ranks between R+ and R-.
    Ties get average ranks with the usual variance correction.

    ``method="auto"``: more than 50 pairs -> normal approximation; else no
    ties and no zeros -> exact distribution; else up to 13 pairs -> exact
    sign-flip permutation distribution (scipy's permutation test is exact
    there); else the normal approximation. ``statistic`` is min(R+, R-) for
    two-sided tests, R+ otherwise. ``None`` when no non-zero difference is left.
    """
    _check_alternative(alternative)
    if zero_method not in ("wilcox", "pratt", "zsplit"):
        msg = "zero_method must be wilcox, pratt or zsplit"
        raise ValueError(msg)
    if y is None:
        d = finite(x)
    else:
        xs, ys = paired(x, y)
        d = [a - b for a, b in zip(xs, ys, strict=True)]
    n_total = len(d)
    n_zero = sum(1 for v in d if v == 0)
    if n_total in (0, n_zero):
        return None

    if zero_method == "wilcox":
        ranked = [v for v in d if v != 0]
        ranks = rankdata([abs(v) for v in ranked])
        count = len(ranked)
        ties = tie_sizes(abs(v) for v in ranked)
        zero_ranks = 0.0
    else:
        ranked = d
        ranks = rankdata([abs(v) for v in ranked])
        count = n_total
        groups = Counter(abs(v) for v in ranked)
        if zero_method == "pratt":
            # Zeros are not part of the tie correction under Pratt's method.
            groups.pop(0.0, None)
        ties = [c for c in groups.values() if c > 1]
        zero_ranks = math.fsum(r for r, v in zip(ranks, ranked, strict=True) if v == 0)

    r_plus = math.fsum(r for r, v in zip(ranks, ranked, strict=True) if v > 0)
    r_minus = math.fsum(r for r, v in zip(ranks, ranked, strict=True) if v < 0)
    if zero_method == "zsplit":
        r_plus += zero_ranks / 2.0
        r_minus += zero_ranks / 2.0

    mn = count * (count + 1.0) * 0.25
    se2 = count * (count + 1.0) * (2.0 * count + 1.0)
    if zero_method == "pratt":
        mn -= n_zero * (n_zero + 1.0) * 0.25
        se2 -= n_zero * (n_zero + 1.0) * (2.0 * n_zero + 1.0)
    se2 -= sum(t**3 - t for t in ties) / 2.0
    se = math.sqrt(se2 / 24.0) if se2 > 0 else 0.0

    has_ties = bool(tie_sizes(abs(v) for v in d if v != 0)) or (zero_method != "wilcox" and n_zero > 1)
    if method == "auto":
        if n_total > 50:
            method = "asymptotic"
        elif not has_ties and n_zero == 0:
            method = "exact"
        elif n_total <= 13:
            method = "permutation"
        else:
            method = "asymptotic"

    z: float | None = None
    if method in ("exact", "permutation"):
        # Doubled ranks are integers even with ties (average ranks are k or
        # k + 0.5), so the sign-flip distribution is computed exactly.
        nonzero = [(r, v) for r, v in zip(ranks, ranked, strict=True) if v != 0]
        weights = [round(2 * r) for r, _ in nonzero]
        observed = round(2 * math.fsum(r for r, v in nonzero if v > 0))
        p = _exact_signed_rank_p(weights, observed, alternative)
    elif method == "asymptotic":
        if se == 0:
            return None
        z = (r_plus - mn) / se
        if correction:
            sign = 1.0 if alternative == "greater" else -1.0 if alternative == "less" else math.copysign(1.0, z)
            z -= sign * 0.5 / se
        if alternative == "greater":
            p = normal_sf(z)
        elif alternative == "less":
            p = normal_cdf(z)
        else:
            p = min(1.0, 2.0 * normal_sf(abs(z)))
    else:
        msg = "method must be auto, exact, permutation or asymptotic"
        raise ValueError(msg)

    return {
        "statistic": min(r_plus, r_minus) if alternative == "two-sided" else r_plus,
        "r_plus": r_plus,
        "r_minus": r_minus,
        "z": z,
        "p": min(1.0, max(0.0, p)),
        "method": method,
        "n": n_total,
        "n_zero": n_zero,
        "zero_method": zero_method,
        "alternative": alternative,
    }


def matched_pairs_rank_biserial(r_plus: float | None, r_minus: float | None) -> float | None:
    """Matched-pairs rank-biserial correlation ``(R+ - R-) / (R+ + R-)`` (Kerby 2014)."""
    if r_plus is None or r_minus is None or (r_plus + r_minus) <= 0:
        return None
    return (r_plus - r_minus) / (r_plus + r_minus)


def _dominance(x: Iterable[Any], y: Iterable[Any]) -> tuple[int, int, int, int] | None:
    """(#x>y, #x<y, n1, n2) over all cross pairs, in O(n log n)."""
    xs = finite(x)
    ys = finite(y)
    if not xs or not ys:
        return None
    return _dominance_clean(xs, ys)


def _dominance_clean(xs: list[float], ys: list[float]) -> tuple[int, int, int, int]:
    ordered = sorted(ys)
    greater = less = 0
    n2 = len(ordered)
    for v in xs:
        greater += bisect.bisect_left(ordered, v)
        less += n2 - bisect.bisect_right(ordered, v)
    return greater, less, len(xs), n2


def _delta_clean(xs: list[float], ys: list[float]) -> float:
    greater, less, n1, n2 = _dominance_clean(xs, ys)
    return (greater - less) / (n1 * n2)


def cliffs_delta(x: Iterable[Any], y: Iterable[Any]) -> float | None:
    """Cliff's delta ``P(X > Y) - P(X < Y)`` in [-1, 1]; ``None`` when a sample is empty.

    For 0/1 outcomes this equals the risk difference p_x - p_y.
    """
    dom = _dominance(x, y)
    if dom is None:
        return None
    greater, less, n1, n2 = dom
    return (greater - less) / (n1 * n2)


def cliffs_magnitude(delta: float | None) -> str | None:
    """negligible / small / medium / large (Romano et al. 2006 thresholds on |delta|)."""
    if delta is None:
        return None
    size = abs(delta)
    for threshold, label in CLIFF_THRESHOLDS:
        if size < threshold:
            return label
    return "large"


def vargha_delaney_a12(x: Iterable[Any], y: Iterable[Any]) -> float | None:
    """Vargha-Delaney A12 = ``P(X > Y) + 0.5 * P(X = Y)``; ``None`` when a sample is empty."""
    dom = _dominance(x, y)
    if dom is None:
        return None
    greater, less, n1, n2 = dom
    ties = n1 * n2 - greater - less
    return (greater + 0.5 * ties) / (n1 * n2)


def bootstrap_delta_ci(
    x: Iterable[Any],
    y: Iterable[Any],
    *,
    is_paired: bool = False,
    resamples: int = 2_000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Percentile bootstrap interval of Cliff's delta.

    Unpaired: each sample is resampled independently. Paired (blocked
    designs): the complete pairs are resampled together, keeping the block
    structure. ``None`` when a side has fewer than 2 observations.
    """
    _check_level(level)
    if is_paired:
        xs, ys = paired(x, y)
        if len(xs) < 2 or resamples < 1:
            return None
        pairs = list(zip(xs, ys, strict=True))
    else:
        xs, ys = finite(x), finite(y)
        if len(xs) < 2 or len(ys) < 2 or resamples < 1:
            return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not cryptography
    estimates: list[float] = []
    for _ in range(resamples):
        # Inputs are already clean, so the resamples skip the missing-data filter.
        if is_paired:
            drawn = rng.choices(pairs, k=len(pairs))
            estimates.append(_delta_clean([a for a, _ in drawn], [b for _, b in drawn]))
        else:
            estimates.append(_delta_clean(rng.choices(xs, k=len(xs)), rng.choices(ys, k=len(ys))))
    return _percentile_interval(estimates, level)


# --------------------------------------------------------------------------
# k-sample tests
# --------------------------------------------------------------------------


def kruskal_wallis(groups: Sequence[Iterable[Any]]) -> dict[str, Any] | None:
    """Kruskal-Wallis H test (tie-corrected) with epsilon-squared.

    ``epsilon_squared = H / (n - 1)`` (Tomczak & Tomczak 2014). ``None``
    when fewer than 2 groups, a group is empty, or every value is tied.
    """
    samples = [finite(g) for g in groups]
    k = len(samples)
    if k < 2 or any(not s for s in samples):
        return None
    pooled = [v for s in samples for v in s]
    n = len(pooled)
    ranks = rankdata(pooled)
    ssbn = 0.0
    start = 0
    for s in samples:
        rank_sum = math.fsum(ranks[start : start + len(s)])
        ssbn += rank_sum * rank_sum / len(s)
        start += len(s)
    h = 12.0 / (n * (n + 1.0)) * ssbn - 3.0 * (n + 1.0)
    correction = 1.0 - sum(t**3 - t for t in tie_sizes(pooled)) / float(n**3 - n)
    if correction <= 0:
        return None
    h = max(h / correction, 0.0)
    df = k - 1
    return {
        "statistic": h,
        "df": df,
        "p": chi2_sf(h, df) if h > 0 else 1.0,
        "epsilon_squared": h / (n - 1.0),
        "n": n,
        "k": k,
    }


def friedman(blocks: Sequence[Sequence[Any]]) -> dict[str, Any] | None:
    """Friedman test over complete blocks (rows = blocks, columns = treatments) + Kendall's W.

    Tie-corrected like ``scipy.stats.friedmanchisquare``; ``kendalls_w =
    chi2 / (n (k - 1))``. Callers pass complete blocks only (a block with a
    missing value is dropped *before* calling). ``None`` with fewer than 2
    blocks or treatments, ragged/missing input, or when every block is all ties.
    """
    rows = [[to_float(v) for v in row] for row in blocks]
    n = len(rows)
    if n < 2:
        return None
    k = len(rows[0])
    if k < 2 or any(len(r) != k or any(v is None for v in r) for r in rows):
        return None
    rank_sums = [0.0] * k
    tie_term = 0
    for row in rows:
        values = [float(v) for v in row if v is not None]
        for j, r in enumerate(rankdata(values)):
            rank_sums[j] += r
        tie_term += sum(t * (t * t - 1) for t in tie_sizes(values))
    correction = 1.0 - tie_term / float(k * (k * k - 1) * n)
    if correction <= 0:
        return None
    ssbn = math.fsum(r * r for r in rank_sums)
    chi2 = max((12.0 / (k * n * (k + 1.0)) * ssbn - 3.0 * n * (k + 1.0)) / correction, 0.0)
    df = k - 1
    return {
        "statistic": chi2,
        "df": df,
        "p": chi2_sf(chi2, df) if chi2 > 0 else 1.0,
        "kendalls_w": chi2 / (n * (k - 1.0)),
        "n_blocks": n,
        "k": k,
        "mean_ranks": [r / n for r in rank_sums],
    }


# --------------------------------------------------------------------------
# Multiple comparisons
# --------------------------------------------------------------------------


def _valid_p(pvalues: Sequence[float | None]) -> dict[int, float]:
    return {i: f for i, p in enumerate(pvalues) if (f := to_float(p)) is not None}


def holm(pvalues: Sequence[float | None]) -> list[float | None]:
    """Holm-Bonferroni adjusted p-values (family-wise error control).

    ``None`` entries stay ``None`` and do not count toward the family size.
    """
    valid = _valid_p(pvalues)
    m = len(valid)
    out: list[float | None] = [None] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(sorted(valid, key=valid.__getitem__)):
        running = max(running, min(1.0, (m - rank) * valid[i]))
        out[i] = running
    return out


def benjamini_hochberg(pvalues: Sequence[float | None]) -> list[float | None]:
    """Benjamini-Hochberg FDR adjusted p-values (exploratory families); ``None`` stays ``None``."""
    valid = _valid_p(pvalues)
    m = len(valid)
    ordered = sorted(valid, key=valid.__getitem__)
    out: list[float | None] = [None] * len(pvalues)
    running = 1.0
    for rank in range(m - 1, -1, -1):
        i = ordered[rank]
        running = min(running, valid[i] * m / (rank + 1))
        out[i] = min(1.0, running)
    return out


def adjust(pvalues: Sequence[float | None], method: str) -> list[float | None]:
    """Dispatch to :func:`holm` / :func:`benjamini_hochberg` (``"none"`` returns the input)."""
    if method == "holm":
        return holm(pvalues)
    if method in ("bh", "fdr_bh", "benjamini_hochberg"):
        return benjamini_hochberg(pvalues)
    if method == "none":
        return [to_float(p) for p in pvalues]
    msg = f"unknown p-value adjustment {method!r}"
    raise ValueError(msg)


# --------------------------------------------------------------------------
# Agreement and association
# --------------------------------------------------------------------------


def krippendorff_alpha_nominal(units: Iterable[Iterable[Hashable | None]]) -> float | None:
    """Krippendorff's alpha for nominal data.

    *units* holds, per unit, the value each coder assigned (``None`` =
    missing). Units with fewer than 2 values are not pairable and are
    ignored. ``None`` when nothing is pairable or only one category occurs
    (expected disagreement 0: alpha is undefined, not perfect).
    """
    coincidence: Counter[tuple[Hashable, Hashable]] = Counter()
    for unit in units:
        values = [v for v in unit if v is not None]
        m = len(values)
        if m < 2:
            continue
        counts = Counter(values)
        for c, nc in counts.items():
            for k, nk in counts.items():
                pairs = nc * (nk - 1) if c == k else nc * nk
                if pairs:
                    coincidence[(c, k)] += pairs / (m - 1)
    marginals: Counter[Hashable] = Counter()
    for (c, _k), weight in coincidence.items():
        marginals[c] += weight
    n = sum(marginals.values())
    if n <= 1:
        return None
    observed = math.fsum(w for (c, k), w in coincidence.items() if c != k)
    expected = math.fsum(marginals[c] * marginals[k] for c in marginals for k in marginals if c != k)
    if expected <= 0:
        return None
    return 1.0 - (n - 1.0) * observed / expected


def jaccard(a: Iterable[Hashable], b: Iterable[Hashable]) -> float | None:
    """Size of the intersection over size of the union; ``None`` when both sets are empty."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return None
    return len(sa & sb) / len(union)


def mean_pairwise_jaccard(sets: Sequence[Iterable[Hashable]]) -> float | None:
    """Mean Jaccard over all pairs of *sets*, skipping undefined (both-empty) pairs."""
    frozen = [set(s) for s in sets]
    values = [
        value
        for i in range(len(frozen))
        for j in range(i + 1, len(frozen))
        if (value := jaccard(frozen[i], frozen[j])) is not None
    ]
    return mean(values)


def spearman(x: Iterable[Any], y: Iterable[Any]) -> dict[str, Any] | None:
    """Spearman's rho with the t-approximation p-value (scipy ``spearmanr``).

    ``None`` with fewer than 3 complete pairs or a constant variable.
    """
    xs, ys = paired(x, y)
    n = len(xs)
    if n < 3:
        return None
    rx, ry = rankdata(xs), rankdata(ys)
    mx, my = math.fsum(rx) / n, math.fsum(ry) / n
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = math.fsum((a - mx) ** 2 for a in rx)
    syy = math.fsum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    rho = max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))
    if abs(rho) >= 1.0:
        p = 0.0
    else:
        t = rho * math.sqrt((n - 2) / (1.0 - rho * rho))
        p = min(1.0, 2.0 * t_sf(abs(t), n - 2))
    return {"rho": rho, "p": p, "n": n}
