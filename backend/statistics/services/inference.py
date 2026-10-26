"""Small-sample inference used by the study report (pure functions, no Django).

Model-level analyses in a study have n = number of models (often ~10), so
every estimate here comes with an exact or resampling-based p-value or
interval rather than a normal approximation, and every family of tests is
corrected (Holm for familywise error, Benjamini-Hochberg for FDR). All
randomness takes an explicit seed so a report regenerates identically.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Above this n, permutation p-values are Monte Carlo instead of exact.
EXACT_PERMUTATION_MAX_N = 8
PERMUTATION_DRAWS = 10_000
BOOTSTRAP_DRAWS = 2_000


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion; None for n = 0."""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def zero_count_upper_bound(n: int, confidence: float = 0.95) -> float | None:
    """Exact (Clopper-Pearson) one-sided upper bound on a rate observed 0 times in *n* trials.

    ``1 - (1 - confidence) ** (1 / n)``, roughly the "rule of three" 3/n:
    zero critical findings in 20 apps still allows a per-app rate up to ~14%.
    """
    if n <= 0:
        return None
    return round(1 - (1 - confidence) ** (1 / n), 4)


def ranks(values: Sequence[float]) -> list[float]:
    """Average 1-based ranks, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j + 2) / 2
        i = j + 1
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho (Pearson on average ranks); None below n = 3 or for a constant variable."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rho = _pearson(ranks(xs), ranks(ys))
    return None if rho is None else round(rho, 4)


def spearman_permutation_p(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    seed: int = 0,
    draws: int = PERMUTATION_DRAWS,
) -> float | None:
    """Two-sided permutation p for Spearman's rho: exact up to n = 8, Monte Carlo above.

    Valid at any n without the t-approximation (which misbehaves at n = 10
    with ties). The Monte Carlo p counts the observed statistic, so it is
    never 0.
    """
    rho = spearman(xs, ys)
    if rho is None:
        return None
    rx, ry = ranks(xs), ranks(ys)
    observed = abs(rho) - 1e-12
    n = len(rx)
    if n <= EXACT_PERMUTATION_MAX_N:
        hits = total = 0
        for perm in itertools.permutations(ry):
            r = _pearson(rx, perm)
            total += 1
            hits += r is not None and abs(r) >= observed
        return round(hits / total, 4)
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    shuffled = list(ry)
    hits = 0
    for _ in range(draws):
        rng.shuffle(shuffled)
        r = _pearson(rx, shuffled)
        hits += r is not None and abs(r) >= observed
    return round((hits + 1) / (draws + 1), 4)


def spearman_bootstrap_ci(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    seed: int = 0,
    draws: int = BOOTSTRAP_DRAWS,
) -> tuple[float, float] | None:
    """Percentile 95% CI for rho, resampling the (x, y) units with replacement."""
    n = len(xs)
    if n < 4:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    values = []
    for _ in range(draws):
        idx = [rng.randrange(n) for _ in range(n)]
        rho = spearman([xs[i] for i in idx], [ys[i] for i in idx])
        if rho is not None:
            values.append(rho)
    if len(values) < draws // 2:
        return None
    values.sort()
    return round(_percentile(values, 0.025), 4), round(_percentile(values, 0.975), 4)


def leave_one_out(xs: Sequence[float], ys: Sequence[float], labels: Sequence[str]) -> dict:
    """rho with each unit dropped: the range, and the unit whose removal moves rho most."""
    full = spearman(xs, ys)
    if full is None or len(xs) < 4:
        return {"min": None, "max": None, "most_influential": None, "rho_without": None}
    drops = []
    for i in range(len(xs)):
        rho = spearman([x for j, x in enumerate(xs) if j != i], [y for j, y in enumerate(ys) if j != i])
        if rho is not None:
            drops.append((rho, labels[i]))
    if not drops:
        return {"min": None, "max": None, "most_influential": None, "rho_without": None}
    rho_without, label = max(drops, key=lambda d: abs(d[0] - full))
    return {
        "min": min(d[0] for d in drops),
        "max": max(d[0] for d in drops),
        "most_influential": label,
        "rho_without": rho_without,
        # Removing one unit flips the sign: the correlation rests on it.
        "sign_flips": any((d[0] > 0) != (full > 0) for d in drops if d[0] != 0),
    }


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def percentile(values: Sequence[float], q: float) -> float | None:
    return _percentile(sorted(values), q) if values else None


def holm(p_values: Sequence[float | None]) -> list[float | None]:
    """Holm step-down adjusted p-values (None entries stay None and are not counted)."""
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    m = len(indexed)
    adjusted: list[float | None] = [None] * len(p_values)
    running = 0.0
    for rank, (p, i) in enumerate(indexed):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[i] = round(running, 4)
    return adjusted


def benjamini_hochberg(p_values: Sequence[float | None]) -> list[float | None]:
    """Benjamini-Hochberg FDR-adjusted p-values (q-values)."""
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    m = len(indexed)
    adjusted: list[float | None] = [None] * len(p_values)
    running = 1.0
    for rank in range(m - 1, -1, -1):
        p, i = indexed[rank]
        running = min(running, p * m / (rank + 1))
        adjusted[i] = round(min(1.0, running), 4)
    return adjusted


def kendall_tau(order_a: Sequence[str], order_b: Sequence[str]) -> float | None:
    """Kendall's tau-a between two orderings of (mostly) the same items."""
    common = [x for x in order_a if x in set(order_b)]
    pos_b = {x: i for i, x in enumerate(order_b)}
    n = len(common)
    if n < 2:
        return None
    concordant = sum(1 for a, b in itertools.combinations(common, 2) if pos_b[a] < pos_b[b])
    pairs = n * (n - 1) / 2
    return round((2 * concordant - pairs) / pairs, 4)


def cohens_kappa(pairs: Sequence[tuple]) -> float | None:
    """Cohen's kappa for paired categorical labels."""
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    expected = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    if expected == 1:
        return 1.0 if observed == 1 else None
    return round((observed - expected) / (1 - expected), 4)


def describe(values: Sequence[float]) -> dict:
    """n, mean, sd, median, IQR and range of *values*."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "mean": None, "sd": None, "median": None, "q1": None, "q3": None, "min": None, "max": None}
    n = len(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else None
    return {
        "n": n,
        "mean": round(mean, 4),
        "sd": round(sd, 4) if sd is not None else None,
        "median": round(_percentile(vals, 0.5), 4),
        "q1": round(_percentile(vals, 0.25), 4),
        "q3": round(_percentile(vals, 0.75), 4),
        "min": vals[0],
        "max": vals[-1],
    }


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    d = describe(values)
    if d["n"] < 2 or not d["mean"]:
        return None
    return round(d["sd"] / d["mean"], 4)
