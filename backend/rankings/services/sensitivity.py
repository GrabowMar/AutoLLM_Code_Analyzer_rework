"""Severity-weight sensitivity: how stable is the empirical ranking?

The baseline SEVERITY_WEIGHTS are asserted, not derived, so the empirical
ranking is recomputed under each alternative scheme in
SEVERITY_WEIGHT_SCHEMES and compared to the baseline order. A Kendall's tau
near 1.0 across schemes means the ranking does not hinge on the particular
weight choice.
"""

from __future__ import annotations

import itertools
from typing import Any

from backend.rankings.services.constants import SEVERITY_WEIGHT_SCHEMES
from backend.rankings.services.scoring import compute_empirical_quality


def kendall_tau(order_a: list[str], order_b: list[str]) -> float:
    """Rank correlation between two orderings of the same items, -1..1.

    Items missing from either ordering are ignored. Fewer than two common
    items gives a degenerate comparison; return 1.0 (nothing can disagree).
    """
    common = [x for x in order_a if x in set(order_b)]
    n = len(common)
    if n < 2:
        return 1.0
    pos_b = {item: i for i, item in enumerate(order_b)}
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if pos_b[common[i]] < pos_b[common[j]]:
                concordant += 1
            else:
                discordant += 1
    return round((concordant - discordant) / (n * (n - 1) / 2), 4)


def adjacent_swaps(baseline: list[str], other: list[str]) -> list[tuple[str, str]]:
    """Adjacent baseline pairs whose relative order inverts under `other`."""
    pos = {item: i for i, item in enumerate(other)}
    swaps = []
    for a, b in itertools.pairwise(baseline):
        if a in pos and b in pos and pos[a] > pos[b]:
            swaps.append((a, b))
    return swaps


def compute_weight_sensitivity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Re-rank models under each severity-weight scheme and compare.

    ``rows`` are aggregated ranking entries; only models with an empirical
    score (i.e. local measurements) participate. Rankings are by empirical
    quality alone — the measurement, deliberately not composite or MSS.
    """
    measured = [r for r in rows if r.get("empirical_quality_score") is not None]

    rankings: dict[str, list[dict[str, Any]]] = {}
    for scheme, weights in SEVERITY_WEIGHT_SCHEMES.items():
        scored = [
            {
                "model_id": r["model_id"],
                "empirical_quality": compute_empirical_quality(r, severity_weights=weights),
            }
            for r in measured
        ]
        scored.sort(key=lambda s: (-(s["empirical_quality"] or 0.0), s["model_id"]))
        rankings[scheme] = scored

    baseline_order = [s["model_id"] for s in rankings.get("baseline", [])]
    schemes = []
    for scheme, weights in SEVERITY_WEIGHT_SCHEMES.items():
        if scheme == "baseline":
            continue
        order = [s["model_id"] for s in rankings[scheme]]
        schemes.append(
            {
                "scheme": scheme,
                "weights": weights,
                "ranking": rankings[scheme],
                "kendall_tau": kendall_tau(baseline_order, order),
                "adjacent_swaps": adjacent_swaps(baseline_order, order),
            },
        )

    return {
        "models_evaluated": len(measured),
        "baseline_ranking": rankings.get("baseline", []),
        "schemes": schemes,
    }
