"""Sensitivity analyses: the empirical ranking's severity weights and the MSS v2 selection.

Severity weights: the baseline SEVERITY_WEIGHTS are asserted, not derived, so
the empirical ranking is recomputed under each alternative scheme in
SEVERITY_WEIGHT_SCHEMES and compared to the baseline order. A Kendall's tau
near 1.0 across schemes means the ranking does not hinge on the particular
weight choice.

MSS v2 selection (docs/design/MSS_V2.md §4, Amendment A1): the stratified
selection is rerun under Dirichlet perturbations of the within-tier weights
(S1, pre-registered), uniform simplex weights (S2, SMAA), specification draws
(S3), one-at-a-time weight changes (S4), tier cut points moved ±10% (S5) and
floor variants (S6). Seeded ``random.Random``; no numpy.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable
from collections.abc import Sequence
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


# ── MSS v2 selection sensitivity ────────────────────────────────────────────


def dirichlet(rng: random.Random, alphas: Sequence[float]) -> list[float]:
    """One Dirichlet draw via gamma variates (no numpy)."""
    draws = [rng.gammavariate(a, 1.0) for a in alphas]
    total = sum(draws)
    return [d / total for d in draws]


def jaccard(a: Sequence[str] | set[str], b: Sequence[str] | set[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def rank_biased_overlap(a: Sequence[str], b: Sequence[str], p: float = 0.9) -> float:
    """Extrapolated rank-biased overlap of two rankings (Webber, Moffat & Zobel 2010, eq. 32).

    Both rankings are truncated to the shorter depth k:
    ``RBO_ext = X_k/k · p^k + (1-p)/p · Σ_{d=1..k} X_d/d · p^d``.
    Identical rankings give 1, disjoint ones 0.
    """
    k = min(len(a), len(b))
    if k == 0:
        return 1.0 if len(a) == len(b) else 0.0
    seen_a: set[str] = set()
    seen_b: set[str] = set()
    overlap = 0
    total = 0.0
    for d in range(1, k + 1):
        x, y = a[d - 1], b[d - 1]
        if x == y:
            overlap += 1
        else:
            overlap += (x in seen_b) + (y in seen_a)
        seen_a.add(x)
        seen_b.add(y)
        total += overlap / d * p**d
    return round(overlap / k * p**k + (1 - p) / p * total, 6)


def _p5(values: list[float]) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    return round(xs[max(0, math.ceil(0.05 * len(xs)) - 1)], 6)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def robustness_class(frequency: float, *, picked: bool) -> str:
    from backend.rankings.services.constants import BORDERLINE_FREQUENCY
    from backend.rankings.services.constants import ROBUST_FREQUENCY

    if picked:
        if frequency >= ROBUST_FREQUENCY:
            return "robust"
        if frequency >= BORDERLINE_FREQUENCY:
            return "borderline"
        return "fragile"
    return "contender" if frequency >= BORDERLINE_FREQUENCY else "rarely"


TOP_K_PER_TIER = 10


def _tier_groups(order: Sequence[str], tiers: dict[str, str] | None) -> list[list[str]]:
    """The nominal order split by nominal tier (one group without tiers), each in nominal order."""
    if not tiers:
        return [list(order)]
    groups: dict[str, list[str]] = {}
    for cid in order:
        groups.setdefault(tiers.get(cid, ""), []).append(cid)
    return list(groups.values())


def summarize_draws(
    label: str,
    description: str,
    results: list[dict[str, Any]],
    *,
    nominal_picks: Sequence[str],
    nominal_order: Sequence[str],
    nominal_scores: dict[str, float],
    eligible_ids: Sequence[str],
    nominal_tiers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Selection frequency, Jaccard, and the rank agreement of one run.

    Kendall tau-b (MSS_tier values), tau over the top ``TOP_K_PER_TIER`` and
    RBO are computed *within* each nominal tier and averaged over the tiers:
    MSS_tier values are on different scales across tiers, and over the
    tier-major order RBO would be dominated by the frontier tier.
    """
    from backend.statistics.services.inference import kendall_tau_b

    n = len(results)
    freq: dict[str, int] = dict.fromkeys(eligible_ids, 0)
    jac, taus, top_taus, rbos = [], [], [], []
    infeasible = 0
    groups = [g for g in _tier_groups(nominal_order, nominal_tiers) if len(g) >= 2]
    for res in results:
        picks = res.get("picks")
        if picks is None:
            infeasible += 1
            continue
        for cid in picks:
            freq[cid] = freq.get(cid, 0) + 1
        jac.append(jaccard(picks, nominal_picks))
        scores = res.get("scores") or {}
        order = res.get("order") or []
        g_tau, g_top, g_rbo = [], [], []
        for ids in groups:
            tau = kendall_tau_b([nominal_scores.get(i, 0.0) for i in ids], [scores.get(i, 0.0) for i in ids])
            if tau is not None:
                g_tau.append(tau)
            members = set(ids)
            run_order = [c for c in order if c in members]
            g_top.append(kendall_tau(ids[:TOP_K_PER_TIER], run_order))
            g_rbo.append(rank_biased_overlap(ids, run_order))
        if g_tau:
            taus.append(sum(g_tau) / len(g_tau))
        if g_top:
            top_taus.append(sum(g_top) / len(g_top))
            rbos.append(sum(g_rbo) / len(g_rbo))
    nominal = set(nominal_picks)
    frequencies = [
        {
            "canonical_id": cid,
            "frequency": round(count / n, 4) if n else 0.0,
            "picked": cid in nominal,
            "class": robustness_class(count / n if n else 0.0, picked=cid in nominal),
        }
        for cid, count in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        if count or cid in nominal
    ]
    return {
        "run": label,
        "description": description,
        "draws": n,
        "infeasible_draws": infeasible,
        "frequencies": frequencies,
        "jaccard_mean": _mean(jac),
        "jaccard_p5": _p5(jac),
        "rank_metrics_scope": (
            "within each nominal price tier, averaged over the tiers" if nominal_tiers else "over the whole order"
        ),
        "kendall_tau_b_mean": _mean(taus),
        "kendall_tau_b_p5": _p5(taus),
        "top_k_per_tier": TOP_K_PER_TIER,
        "kendall_tau_top_k_mean": _mean(top_taus),
        "rbo_mean": _mean(rbos),
        "rbo_p5": _p5(rbos),
    }


def mss_selection_sensitivity(
    runner: Any,
    *,
    nominal: dict[str, Any],
    draws: dict[str, int],
    seed: int,
    tier_weights: dict[str, float],
    concentration: float,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """S1-S6 for the stratified selection; ``runner`` reruns the full procedure under a change.

    ``runner.run(weights=..., variant=..., tier_scale=..., floor=...)`` returns
    ``{"picks": [ids] | None, "order": [ids], "scores": {id: MSS_tier}}``.
    """
    from backend.rankings.services.constants import COMPONENTS

    rng = random.Random(seed)  # noqa: S311 - seeded, reproducible sensitivity draws; not cryptographic
    # A fixed component order (not the dict's): a stored, JSON-normalised parameter set
    # (sorted keys) must replay to the same draws.
    keys = [k for k in COMPONENTS if k in tier_weights] + sorted(k for k in tier_weights if k not in COMPONENTS)
    summary_args = {
        "nominal_picks": nominal["picks"],
        "nominal_order": nominal["order"],
        "nominal_scores": nominal["scores"],
        "eligible_ids": nominal["order"],
        "nominal_tiers": nominal.get("tiers"),
    }
    runs: dict[str, Any] = {}

    def weight_draws(count: int, alphas: list[float]) -> list[dict[str, Any]]:
        out = []
        for _ in range(count):
            w = dict(zip(keys, dirichlet(rng, alphas), strict=True))
            out.append(runner.run(weights=w))
        return out

    if draws.get("S1"):
        runs["S1"] = summarize_draws(
            "S1",
            f"Within-tier weights ~ Dirichlet(alpha = {concentration:g} x w)",
            weight_draws(draws["S1"], [concentration * tier_weights[k] for k in keys]),
            **summary_args,
        )
        if progress:
            progress("S1")
    if draws.get("S2"):
        runs["S2"] = summarize_draws(
            "S2",
            "Within-tier weights uniform on the simplex (SMAA, no preference)",
            weight_draws(draws["S2"], [1.0] * len(keys)),
            **summary_args,
        )
        if progress:
            progress("S2")
    if draws.get("S3"):
        results = [runner.run(variant=runner.spec_draw(rng)) for _ in range(draws["S3"])]
        runs["S3"] = summarize_draws(
            "S3",
            "Specification: normalisation, popularity definition, cache hit share, censoring, "
            "missing capability, WebDev rating noise",
            results,
            **summary_args,
        )
        if progress:
            progress("S3")

    # S4: one at a time.
    oat = []
    for key in keys:
        w = {k: (0.0 if k == key else v) for k, v in tier_weights.items()}
        oat.append({"change": f"leave out {key}", **_diff(runner.run(weights=w), nominal)})
        for factor in (0.5, 1.5):
            w = {k: (v * factor if k == key else v) for k, v in tier_weights.items()}
            oat.append({"change": f"{key} x{factor}", **_diff(runner.run(weights=w), nominal)})
    runs["S4"] = {"run": "S4", "description": "One-at-a-time weight changes (renormalised)", "changes": oat}

    # S5: tier cut points moved ±10%; S6: floor variants.
    cuts = []
    for scale in ((0.9, 0.9), (1.1, 1.1), (0.9, 1.1), (1.1, 0.9)):
        cuts.append({"change": f"cut points x{scale[0]}/x{scale[1]}", **_diff(runner.run(tier_scale=scale), nominal)})
    runs["S5"] = {"run": "S5", "description": "Tier cut points moved by ±10%", "changes": cuts}
    floors = [
        {"change": "floor at the tier's 40th percentile", **_diff(runner.run(floor=[40.0, 33.0]), nominal)},
        {"change": "no quality floor", **_diff(runner.run(floor=[]), nominal)},
        {
            "change": "floor tested on the WebDev rating's upper 95% bound",
            **_diff(runner.run(floor_basis="upper"), nominal),
        },
    ]
    runs["S6"] = {"run": "S6", "description": "Quality-floor variants", "changes": floors}
    return runs


def _diff(result: dict[str, Any], nominal: dict[str, Any]) -> dict[str, Any]:
    picks = result.get("picks")
    if picks is None:
        return {"feasible": False, "entered": [], "left": [], "jaccard": None}
    base = set(nominal["picks"])
    return {
        "feasible": True,
        "entered": sorted(set(picks) - base),
        "left": sorted(base - set(picks)),
        "jaccard": round(jaccard(picks, base), 4),
    }
