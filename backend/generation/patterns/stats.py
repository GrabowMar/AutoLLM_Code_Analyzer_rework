"""Pure-Python interval estimates and exact tests for pattern prevalence (spec section 7.9 step 6).

No numpy/scipy (D18). Wilson and Benjamini-Hochberg come from :mod:`backend.statistics.services.inference`
(read-only reuse); this module adds what that module lacks: unrounded Wilson at any ``z`` (the conservative
repeated-look interval), Clopper-Pearson, Newcombe's hybrid score intervals for independent (method 10,
Newcombe 1998a) and paired (method 10, Newcombe 1998b) differences, Fisher's exact and the exact McNemar
test, the Mantel-Haenszel risk difference stratified by target (Greenland-Robins/Sato variance) and an exact
stratified permutation test at the target level.

Every function returns ``None`` where the estimate does not exist (``n = 0``), never ``0``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

Z95 = 1.959963984540054  # Phi^-1(0.975)
_TOL = 1e-12


# ---------------------------------------------------------------------------
# Normal quantile
# ---------------------------------------------------------------------------


def normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_quantile(p: float) -> float:
    """``Phi^-1(p)`` by bisection on ``erfc`` (exact to ~1e-12; only called a handful of times)."""
    if not 0.0 < p < 1.0:
        msg = f"p must be in (0, 1), got {p}"
        raise ValueError(msg)
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13:
            break
    return (lo + hi) / 2


def z_for(alpha: float = 0.05, looks: int = 1) -> float:
    """Two-sided critical value at ``alpha / looks`` (Bonferroni over repeated looks, section 4.6)."""
    looks = max(1, int(looks))
    if looks == 1 and abs(alpha - 0.05) < _TOL:
        return Z95
    return normal_quantile(1 - alpha / (2 * looks))


# ---------------------------------------------------------------------------
# Single proportion
# ---------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval (unrounded); ``None`` for ``n = 0``."""
    if n <= 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def wilson_half_width(k: int, n: int, z: float = Z95) -> float | None:
    ci = wilson(k, n, z)
    return None if ci is None else (ci[1] - ci[0]) / 2


def _binom_cdf(k: int, n: int, p: float) -> float:
    """``P(X <= k)`` for ``X ~ Bin(n, p)`` (log-space terms; exact enough for n up to a few thousand)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, total)


def _bisect(f, lo: float, hi: float) -> float:
    """Root of a monotone ``f`` on ``[lo, hi]``."""
    flo = f(lo)
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm < 0) == (flo < 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < 1e-14:
            break
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float] | None:
    """Exact (Clopper-Pearson) interval, by inverting the binomial tails; ``None`` for ``n = 0``."""
    if n <= 0:
        return None
    half = alpha / 2
    lower = 0.0 if k == 0 else _bisect(lambda p: (1 - _binom_cdf(k - 1, n, p)) - half, 0.0, 1.0)
    upper = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p) - half, 0.0, 1.0)
    return lower, upper


# ---------------------------------------------------------------------------
# Differences of proportions
# ---------------------------------------------------------------------------


def newcombe_independent(k1: int, n1: int, k2: int, n2: int, z: float = Z95) -> tuple[float, float, float] | None:
    """``(p1 - p2, low, high)`` by Newcombe's hybrid score (1998a, method 10); ``None`` if an arm is empty."""
    w1, w2 = wilson(k1, n1, z), wilson(k2, n2, z)
    if w1 is None or w2 is None:
        return None
    p1, p2 = k1 / n1, k2 / n2
    (l1, u1), (l2, u2) = w1, w2
    d = p1 - p2
    delta = math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    eps = math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, max(-1.0, d - delta), min(1.0, d + eps)


def newcombe_paired(a: int, b: int, c: int, d: int, z: float = Z95) -> tuple[float, float, float] | None:
    """``(p1 - p2, low, high)`` for paired binary data (Newcombe 1998b, method 10).

    ``a``: both present, ``b``: present in arm 1 only, ``c``: present in arm 2 only, ``d``: both absent;
    ``p1 = (a + b) / n``, ``p2 = (a + c) / n``. ``phi`` is the uncorrected correlation of the 2x2 table
    (0 when a margin is empty).
    """
    n = a + b + c + d
    if n <= 0:
        return None
    p1, p2 = (a + b) / n, (a + c) / n
    l1, u1 = wilson(a + b, n, z)  # type: ignore[misc]
    l2, u2 = wilson(a + c, n, z)  # type: ignore[misc]
    margins = (a + b) * (c + d) * (a + c) * (b + d)
    phi = 0.0 if margins == 0 else (a * d - b * c) / math.sqrt(margins)
    diff = p1 - p2
    delta2 = (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2
    eps2 = (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2
    delta, eps = math.sqrt(max(0.0, delta2)), math.sqrt(max(0.0, eps2))
    return diff, max(-1.0, diff - delta), min(1.0, diff + eps)


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(a: int, b: int, c: int, d: int) -> float | None:
    """Two-sided Fisher exact p for ``[[a, b], [c, d]]`` (sum of tables no more probable than the observed)."""
    row1, row2, col1 = a + b, c + d, a + c
    n = row1 + row2
    if n == 0 or row1 == 0 or row2 == 0:
        return None
    lo, hi = max(0, col1 - row2), min(col1, row1)

    def logp(x: int) -> float:
        return _log_comb(row1, x) + _log_comb(row2, col1 - x) - _log_comb(n, col1)

    observed = logp(a)
    total = sum(math.exp(logp(x)) for x in range(lo, hi + 1) if logp(x) <= observed + 1e-7)
    return min(1.0, total)


def mcnemar_exact(b: int, c: int) -> float | None:
    """Exact two-sided McNemar p from the discordant pairs (``2 * P(X <= min(b, c))``, ``X ~ Bin(b + c, 1/2)``)."""
    m = b + c
    if m == 0:
        return None
    return min(1.0, 2 * _binom_cdf(min(b, c), m, 0.5))


# ---------------------------------------------------------------------------
# Pooling across targets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stratum:
    """One target: ``k1/n1`` present in condition A, ``k0/n0`` in condition B."""

    key: str
    k1: int
    n1: int
    k0: int
    n0: int

    @property
    def usable(self) -> bool:
        return self.n1 > 0 and self.n0 > 0

    @property
    def weight(self) -> float:
        return self.n1 * self.n0 / (self.n1 + self.n0)

    @property
    def rd(self) -> float:
        return self.k1 / self.n1 - self.k0 / self.n0


def mantel_haenszel_rd(strata: Sequence[Stratum], z: float = Z95) -> dict[str, float] | None:
    """Mantel-Haenszel risk difference with the Greenland-Robins (Sato) variance; ``None`` without data.

    ``RD = sum(w_t * d_t) / sum(w_t)`` with ``w_t = n1 n0 / N``.
    """
    used = [s for s in strata if s.usable]
    if not used:
        return None
    w_total = sum(s.weight for s in used)
    rd = sum(s.weight * s.rd for s in used) / w_total
    # Sato (1989) / Greenland-Robins (1985) variance estimator.
    p_sum = 0.0
    q_sum = 0.0
    for s in used:
        big_n = s.n1 + s.n0
        p_sum += (s.k1 * s.n0 * s.n0 - s.k0 * s.n1 * s.n1 + s.n1 * s.n0 * (s.n0 - s.n1) / 2) / (big_n * big_n)
        q_sum += (s.k1 * (s.n0 - s.k0) + s.k0 * (s.n1 - s.k1)) / (2 * big_n)
    var = (rd * p_sum + q_sum) / (w_total * w_total)
    se = math.sqrt(max(0.0, var))
    return {"rd": rd, "se": se, "ci_low": max(-1.0, rd - z * se), "ci_high": min(1.0, rd + z * se)}


def stratified_permutation_p(
    strata: Sequence[Stratum],
    *,
    exact_max_strata: int = 20,
    draws: int = 10_000,
    seed: int = 0,
) -> dict[str, float | int | str] | None:
    """Two-sided permutation p of the MH risk difference at the target level.

    The unit of permutation is the target: swapping the two conditions' arms of one target flips the
    sign of its weighted difference (the MH weight is symmetric). With ``T <= exact_max_strata`` targets
    every one of the ``2^T`` arrangements is enumerated (exact); otherwise ``draws`` seeded Monte Carlo
    arrangements are used (p = (hits + 1) / (draws + 1)).
    """
    used = [s for s in strata if s.usable]
    if not used:
        return None
    w_total = sum(s.weight for s in used)
    contrib = [s.weight * s.rd / w_total for s in used]
    observed = abs(sum(contrib))
    t = len(contrib)
    if t <= exact_max_strata:
        # Every sign pattern of the other targets as one list of partial sums (2^(T-1) floats).
        partials = [0.0]
        for value in contrib[1:]:
            partials = [p + value for p in partials] + [p - value for p in partials]
        hits = 0
        for first in (contrib[0], -contrib[0]):
            hits += sum(1 for p in partials if abs(first + p) >= observed - 1e-12)
        total = 2 * len(partials)
        return {"p": hits / total, "method": "exact", "arrangements": total}
    rng = random.Random(seed)  # noqa: S311 - seeded resampling, not a secret
    hits = 0
    for _ in range(draws):
        stat = sum(v if rng.random() < 0.5 else -v for v in contrib)
        if abs(stat) >= observed - 1e-12:
            hits += 1
    return {"p": (hits + 1) / (draws + 1), "method": "monte_carlo", "arrangements": draws}


# ---------------------------------------------------------------------------
# Cluster statistics
# ---------------------------------------------------------------------------


def shannon_entropy(sizes: Sequence[int]) -> float | None:
    """Shannon entropy in bits of a cluster-size distribution; ``None`` when empty."""
    n = sum(sizes)
    if n <= 0:
        return None
    return -sum((s / n) * math.log2(s / n) for s in sizes if s > 0) + 0.0


def chao1(sizes: Sequence[int]) -> float | None:
    """Bias-corrected Chao1 richness ``S_obs + F1 (F1 - 1) / (2 (F2 + 1))``; ``None`` when empty."""
    observed = [s for s in sizes if s > 0]
    if not observed:
        return None
    f1 = sum(1 for s in observed if s == 1)
    f2 = sum(1 for s in observed if s == 2)
    return len(observed) + f1 * (f1 - 1) / (2 * (f2 + 1))
