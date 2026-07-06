"""Score computation primitives for rankings (MSS components)."""

from __future__ import annotations

import math
from typing import Any

from backend.rankings.services.constants import BENCHMARK_RANGES
from backend.rankings.services.constants import BENCHMARK_WEIGHTS
from backend.rankings.services.constants import COMPOSITE_EMPIRICAL_WEIGHT
from backend.rankings.services.constants import COMPOSITE_MSS_WEIGHT
from backend.rankings.services.constants import DOCS_SCORES
from backend.rankings.services.constants import EMPIRICAL_DENSITY_CAP
from backend.rankings.services.constants import EMPIRICAL_FUNCTIONAL_WEIGHT
from backend.rankings.services.constants import LICENSE_SCORES
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.constants import STABILITY_SCORES


def normalize_benchmark_score(benchmark: str, score: float) -> float:
    if score is None:
        return 0.0
    lo, hi = BENCHMARK_RANGES.get(benchmark, (0, 100))
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (score - lo) / (hi - lo)))


def compute_benchmark_score(entry: dict[str, Any]) -> float:
    total = 0.0
    weight_used = 0.0
    for name, weight in BENCHMARK_WEIGHTS.items():
        val = entry.get(name)
        if val is not None:
            total += normalize_benchmark_score(name, float(val)) * weight
            weight_used += weight
    if weight_used == 0:
        return 0.0
    return total / weight_used


def compute_cost_efficiency_score(entry: dict[str, Any]) -> float:
    benchmark_score = entry.get("benchmark_score", 0.0) or 0.0
    avg_in = entry.get("price_per_million_input")
    avg_out = entry.get("price_per_million_output")
    context = entry.get("context_length", 0) or 0
    is_free = bool(entry.get("is_free", False))

    if is_free:
        price_eff = 1.0
    elif avg_in is not None and avg_out is not None:
        avg_price = (float(avg_in) + float(avg_out)) / 2
        if avg_price <= 0 or benchmark_score <= 0:
            return 0.0
        max_price, min_price = 100.0, 0.10
        normalized_price = min(
            1.0,
            (avg_price - min_price) / (max_price - min_price),
        )
        price_eff = (1.0 - normalized_price) * 0.5 + benchmark_score * 0.5
    else:
        return 0.0

    if context > 0:
        ctx_bonus = min(0.3, (math.log(context / 4096) / math.log(256)) * 0.3)
        ctx_bonus = max(0.0, ctx_bonus)
    else:
        ctx_bonus = 0.0

    eff = price_eff * 0.7 + ctx_bonus
    return min(1.0, max(0.0, eff))


def compute_accessibility_score(entry: dict[str, Any]) -> float:
    license_type = (entry.get("license_type") or "").lower() or None
    stability = (entry.get("api_stability") or "").lower() or None
    docs = (entry.get("documentation_quality") or "").lower() or None
    license_s = LICENSE_SCORES.get(license_type, 0.7) if license_type else 0.7
    stab_s = STABILITY_SCORES.get(stability, 0.7) if stability else 0.7
    docs_s = DOCS_SCORES.get(docs, 0.7) if docs else 0.7
    return 0.40 * license_s + 0.40 * stab_s + 0.20 * docs_s


def compute_adoption_score(entry: dict[str, Any]) -> float:  # noqa: PLR0911
    """Adoption from OpenRouter programming-category rank or local usage."""

    rank = entry.get("openrouter_programming_rank")
    if rank:
        rank = int(rank)
        if rank <= 5:
            return 1.0 - (rank - 1) * 0.04
        if rank <= 10:
            return 0.8 - (rank - 6) * 0.04
        if rank <= 20:
            return 0.6 - (rank - 11) * 0.02
        if rank <= 50:
            return 0.4 - (rank - 21) * 0.00667
        return max(0.0, 0.2 - (rank - 51) * 0.004)

    # Fallback: derive adoption from local generation-job count (apps built).
    apps = int(entry.get("apps", 0) or 0)
    if apps <= 0:
        return 0.0
    return min(1.0, math.log10(apps + 1) / 2.0)


def compute_mss(entry: dict[str, Any]) -> float:
    adoption = entry.get("adoption_score", 0.0) or 0.0
    benchmarks = entry.get("benchmark_score", 0.0) or 0.0
    cost = entry.get("cost_efficiency_score", 0.0) or 0.0
    access = entry.get("accessibility_score", 0.0) or 0.0
    return round(
        0.35 * adoption + 0.30 * benchmarks + 0.20 * cost + 0.15 * access,
        4,
    )


def compute_empirical_quality(entry: dict[str, Any]) -> float | None:
    """Quality measured by this platform's own experiments, 0..1.

    Combines severity-weighted findings density (per KLOC of generated code,
    lower is better) with the functional smoke-test pass rate. Returns None
    when the model has no completed local generations — no data is not the
    same as bad data.
    """
    if not entry.get("apps_completed"):
        return None
    loc = entry.get("local_loc", 0) or 0
    findings = entry.get("findings") or {}
    if loc <= 0:
        return None

    weighted = sum(SEVERITY_WEIGHTS.get(sev, 0) * (count or 0) for sev, count in findings.items())
    density = weighted / loc * 1000
    density_score = max(0.0, 1.0 - min(density, EMPIRICAL_DENSITY_CAP) / EMPIRICAL_DENSITY_CAP)

    functional_rate = entry.get("functional_pass_rate")
    if functional_rate is None:
        # No smoke data — score on density alone rather than punishing.
        return round(density_score, 4)
    return round(
        (1 - EMPIRICAL_FUNCTIONAL_WEIGHT) * density_score + EMPIRICAL_FUNCTIONAL_WEIGHT * float(functional_rate),
        4,
    )


def compute_composite(mss: float, empirical: float | None) -> float:
    """MSS blended with measured quality; plain MSS when nothing was measured."""
    if empirical is None:
        return round(mss, 4)
    return round(COMPOSITE_MSS_WEIGHT * mss + COMPOSITE_EMPIRICAL_WEIGHT * empirical, 4)
