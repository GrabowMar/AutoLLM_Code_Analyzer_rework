"""Plan metrics and the paired, app-blocked tests of the study report (pure Python, no ORM).

``PLAN_METRICS`` are the outcomes an analysis plan may declare (per-app values, an app's repeats
averaged, then the mean over a group's apps). The tests are exact where feasible: the sign test, the
Wilcoxon signed-rank test (exact up to 25 non-zero deltas) and a two-stage cluster bootstrap over apps.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable
from typing import Any

BOOTSTRAP_DRAWS = 2000
# Below this many apps a bootstrap interval is degenerate (one app at 100% would read as [1, 1]).
MIN_APPS_FOR_CI = 3
_EXACT_WILCOXON_MAX_N = 25

# Outcomes a plan may name: per-app values (an app's repeats averaged), then
# the mean over a condition's (or model's) apps. Plans store only the keys, so
# these descriptions can be sharpened without touching a locked plan.
PLAN_METRICS: dict[str, str] = {
    "deploy_rate": "Share of evaluated apps that build and answer their health check",
    "pass_rate": "Mean functional pass rate (0..1, infra and flaky checks excluded) over evaluated apps",
    "functional_passed": "Share of evaluated apps passing every functional check",
    "unique_issues_per_app": "Deduplicated static issues of every kind per analyzed app",
    "issues_per_kloc": (
        "Deduplicated code issues in the model's own files per 1,000 of their non-test source lines (dependency "
        "advisories, runtime (DAST) issues, starter files and test code excluded)"
    ),
    "security_per_app": (
        "Deduplicated security/secret issues in the model's own non-test files per analyzed app (dependency "
        "advisories, runtime (DAST) issues and starter files excluded)"
    ),
    "cost_per_app": "Generation cost per app (USD)",
}


def declared_metrics(plan: dict[str, Any]) -> set[str]:
    """Metrics a plan makes confirmatory (primary, secondary, research questions)."""
    if not plan or not plan.get("primary_metric"):
        return set()
    return {
        plan["primary_metric"],
        *plan.get("secondary_metrics", []),
        *(q["metric"] for q in plan.get("research_questions", [])),
    }


def plan_multiplicity(plan: dict[str, Any]) -> str:
    """The plan's correction family: ``all`` (one family over every declared test, the default) or ``per_metric``."""
    return plan.get("multiplicity") or "all"


def _percentile(sorted_values: list[float], q: float) -> float:
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
    return [round(_percentile(ordered, 0.025), 4), round(_percentile(ordered, 0.975), 4)]


def _resampled_mean(values: tuple[float, ...], draw: Callable[[], float]) -> float:
    """The mean of ``len(values)`` draws with replacement from *values* (the nested repeat level)."""
    n = len(values)
    if n == 1:
        return values[0]
    total = 0.0
    for _ in range(n):
        total += values[int(draw() * n)]
    return total / n


def cluster_bootstrap_ci(
    apps: dict[Any, list[float]],
    rng: random.Random,
    draws: int = BOOTSTRAP_DRAWS,
) -> list | None:
    """95% CI of the mean of app means: resample apps, then each app's repeats (two-stage cluster bootstrap)."""
    pools = [tuple(values) for values in apps.values() if values]
    if not pools:
        return None
    k = len(pools)
    draw: Callable[[], float] = rng.random  # int(random() * n) instead of randrange: ~5x faster on this hot loop
    samples = []
    for _ in range(draws):
        total = 0.0
        for _i in range(k):
            total += _resampled_mean(pools[int(draw() * k)], draw)
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


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based) of *values*, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j + 2) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def wilcoxon_p(deltas: list[float], alternative: str = "two-sided") -> float | None:  # noqa: PLR0911
    """Wilcoxon signed-rank p for *deltas* (zeros dropped; average ranks for ties).

    ``alternative`` is ``two-sided`` (default), ``greater`` (deltas tend to be
    positive) or ``less``. Exact (the null distribution of W+ by dynamic
    programming over doubled ranks, so tied half-ranks stay integers) up to 25
    non-zero deltas; the normal approximation with tie and continuity
    correction above.
    """
    if alternative not in ("two-sided", "greater", "less"):
        msg = f"Unknown alternative {alternative!r}"
        raise ValueError(msg)
    nonzero = [d for d in deltas if d != 0]
    n = len(nonzero)
    if n == 0:
        return None
    ranks = _ranks([abs(d) for d in nonzero])
    w_plus = sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
    total = sum(ranks)
    if n <= _EXACT_WILCOXON_MAX_N:
        doubled = [round(2 * r) for r in ranks]
        counts = Counter({0: 1})
        for r in doubled:
            nxt: Counter = Counter()
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
        extreme = sum(c for s, c in counts.items() if s <= lo or s >= hi)
        return min(1.0, extreme / 2**n)
    mean = total / 2
    tie_groups = Counter(ranks).values()
    variance = n * (n + 1) * (2 * n + 1) / 24 - sum(t**3 - t for t in tie_groups) / 48
    if variance <= 0:
        return None
    sd = math.sqrt(variance)
    if alternative == "greater":
        return min(1.0, 0.5 * math.erfc(((w_plus - mean - 0.5) / sd) / math.sqrt(2)))
    if alternative == "less":
        return min(1.0, 0.5 * math.erfc(((mean - w_plus - 0.5) / sd) / math.sqrt(2)))
    z = (abs(w_plus - mean) - 0.5) / sd
    return min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2)))
