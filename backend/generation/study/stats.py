"""Pure statistics of the study results (ported from v2 ``experiment_results``; D18: pure Python only).

* exact two-sided sign test; Wilcoxon signed-rank (exact up to 25 non-zero deltas, normal approximation with
  tie and continuity correction above); Holm step-down (Benjamini-Hochberg comes from
  ``statistics.services.inference``);
* the unit-level **sign-flip permutation test** used when there are fewer than :data:`FEW_CLUSTERS` units
  (exact up to :data:`EXACT_PERMUTATION_MAX` units, else :data:`PERMUTATION_DRAWS` seeded draws);
* two-stage cluster bootstraps (units, then the repeats nested in each unit) for descriptive intervals and,
  with enough units, the paired delta CI. Few-cluster inference never uses cluster-robust SEs (Appendix A #17).

Every function is deterministic for its inputs and seed.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any
from typing import Final

__all__ = [
    "BOOTSTRAP_DRAWS",
    "EXACT_PERMUTATION_MAX",
    "FEW_CLUSTERS",
    "MIN_UNITS_FOR_CI",
    "PERMUTATION_DRAWS",
    "cluster_bootstrap_ci",
    "holm",
    "mean",
    "paired_bootstrap_ci",
    "percentile",
    "ranks",
    "sign_flip_p",
    "sign_test_p",
    "wilcoxon_p",
]

BOOTSTRAP_DRAWS: Final = 2000
PERMUTATION_DRAWS: Final = 10_000
EXACT_PERMUTATION_MAX: Final = 15
FEW_CLUSTERS: Final = 20  # spec 9.4: fewer units than this -> unit-level permutation tests
MIN_UNITS_FOR_CI: Final = 3
_EXACT_WILCOXON_MAX_N: Final = 25
_TOLERANCE: Final = 1e-12


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted list (q in 0..1)."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lo = math.floor(position)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (position - lo)


def _ci(draws: list[float]) -> list[float] | None:
    if not draws:
        return None
    ordered = sorted(draws)
    return [round(percentile(ordered, 0.025), 4), round(percentile(ordered, 0.975), 4)]


def _resampled_mean(values: tuple[float, ...], draw: Callable[[], float]) -> float:
    n = len(values)
    if n == 1:
        return values[0]
    return sum(values[int(draw() * n)] for _ in range(n)) / n


def cluster_bootstrap_ci(units: dict[Any, list[float]], seed: int, draws: int = BOOTSTRAP_DRAWS) -> list | None:
    """95% CI of the mean of unit means: resample units, then each unit's repeats (two-stage)."""
    pools = [tuple(values) for values in units.values() if values]
    if len(pools) < MIN_UNITS_FOR_CI:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible bootstrap, not crypto
    draw = rng.random
    k = len(pools)
    samples = [sum(_resampled_mean(pools[int(draw() * k)], draw) for _ in range(k)) / k for _ in range(draws)]
    return _ci(samples)


def paired_bootstrap_ci(
    pairs: dict[Any, tuple[list[float], list[float]]],
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> list | None:
    """95% CI of the mean unit-level delta: resample units; within each, both conditions' repeats."""
    pools = [(tuple(a), tuple(b)) for a, b in pairs.values() if a and b]
    if len(pools) < MIN_UNITS_FOR_CI:
        return None
    rng = random.Random(seed)  # noqa: S311
    draw = rng.random
    k = len(pools)
    samples = []
    for _ in range(draws):
        total = 0.0
        for _i in range(k):
            treated, baseline = pools[int(draw() * k)]
            total += _resampled_mean(treated, draw) - _resampled_mean(baseline, draw)
        samples.append(total / k)
    return _ci(samples)


def sign_test_p(wins: int, losses: int) -> float | None:
    """Two-sided exact binomial sign test (ties dropped); None without non-zero deltas."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based) of ``values``, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j + 2) / 2
        for k in range(i, j + 1):
            out[order[k]] = rank
        i = j + 1
    return out


def wilcoxon_p(deltas: list[float], alternative: str = "two-sided") -> float | None:  # noqa: PLR0911
    """Wilcoxon signed-rank p for ``deltas`` (zeros dropped; average ranks for ties)."""
    if alternative not in ("two-sided", "greater", "less"):
        msg = f"Unknown alternative {alternative!r}"
        raise ValueError(msg)
    nonzero = [d for d in deltas if d != 0]
    n = len(nonzero)
    if n == 0:
        return None
    rank_values = ranks([abs(d) for d in nonzero])
    w_plus = sum(r for r, d in zip(rank_values, nonzero, strict=True) if d > 0)
    total = sum(rank_values)
    if n <= _EXACT_WILCOXON_MAX_N:
        counts: Counter[int] = Counter({0: 1})
        for r in (round(2 * r) for r in rank_values):
            nxt: Counter[int] = Counter()
            for s, c in counts.items():
                nxt[s] += c
                nxt[s + r] += c
            counts = nxt
        observed = round(2 * w_plus)
        if alternative == "greater":
            return min(1.0, sum(c for s, c in counts.items() if s >= observed) / 2**n)
        if alternative == "less":
            return min(1.0, sum(c for s, c in counts.items() if s <= observed) / 2**n)
        mirrored = round(2 * total) - observed
        lo, hi = min(observed, mirrored), max(observed, mirrored)
        return min(1.0, sum(c for s, c in counts.items() if s <= lo or s >= hi) / 2**n)
    centre = total / 2
    tie_groups = Counter(rank_values).values()
    variance = n * (n + 1) * (2 * n + 1) / 24 - sum(t**3 - t for t in tie_groups) / 48
    if variance <= 0:
        return None
    sd = math.sqrt(variance)
    if alternative == "greater":
        return min(1.0, 0.5 * math.erfc(((w_plus - centre - 0.5) / sd) / math.sqrt(2)))
    if alternative == "less":
        return min(1.0, 0.5 * math.erfc(((centre - w_plus - 0.5) / sd) / math.sqrt(2)))
    z = (abs(w_plus - centre) - 0.5) / sd
    return min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2)))


def sign_flip_p(deltas: list[float], *, seed: int, alternative: str = "two-sided") -> float | None:
    """Unit-level sign-flip permutation p of the mean delta (exact for few units, seeded draws otherwise)."""
    if not deltas or all(d == 0 for d in deltas):
        return None
    n = len(deltas)
    observed = sum(deltas) / n

    def extreme(value: float) -> bool:
        if alternative == "greater":
            return value >= observed - _TOLERANCE
        if alternative == "less":
            return value <= observed + _TOLERANCE
        return abs(value) >= abs(observed) - _TOLERANCE

    if n <= EXACT_PERMUTATION_MAX:
        hits = 0
        for signs in itertools.product((1, -1), repeat=n):
            hits += extreme(sum(s * d for s, d in zip(signs, deltas, strict=True)) / n)
        return min(1.0, hits / 2**n)
    rng = random.Random(seed)  # noqa: S311
    hits = 1  # the observed assignment
    for _ in range(PERMUTATION_DRAWS):
        hits += extreme(sum(d if rng.random() < 0.5 else -d for d in deltas) / n)
    return min(1.0, hits / (PERMUTATION_DRAWS + 1))


def holm(p_values: list[float | None]) -> list[float | None]:
    """Holm step-down adjusted p-values (None entries stay None and are not counted)."""
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    m = len(indexed)
    adjusted: list[float | None] = [None] * len(p_values)
    running = 0.0
    for rank, (p, i) in enumerate(indexed):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[i] = running
    return adjusted
