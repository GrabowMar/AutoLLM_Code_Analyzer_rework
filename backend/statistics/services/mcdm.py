"""Multi-criteria model rankings (TOPSIS and weighted sum) with their uncertainty made explicit.

A ranking is only as good as its weights, and weights are a researcher's
choice. So every ranking comes with:

* **Two aggregation methods** (TOPSIS, WSM on min-max normalised criteria)
  and Kendall's tau between them,
* **Weight sensitivity**: equal weights; one-at-a-time ±50% changes to each
  weight (renormalised), listing the rank reversals each causes; and Monte
  Carlo weights drawn from a Dirichlet centred on the chosen weights
  (concentration 20) and from a flat Dirichlet (any weights at all),
  summarised as each model's median rank, 95% rank interval, P(rank 1) and
  P(top 3),
* **Data uncertainty** (optional): criteria recomputed from apps resampled
  with replacement within each model; the same rank summary.

Missing criterion values drop the model from the ranking (reported), never
imputed. Criteria must not share a denominator in opposite directions (for
example "LOC per app" as a benefit next to "issues per kLOC" as a cost
rewards verbosity twice); :data:`DEFAULT_CRITERIA` avoid that.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from backend.statistics.services.inference import kendall_tau
from backend.statistics.services.inference import percentile

if TYPE_CHECKING:
    from collections.abc import Callable

WEIGHT_DRAWS = 2000
DIRICHLET_CONCENTRATION = 20.0
OAT_FACTOR = 0.5


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    direction: str  # "benefit" | "cost"
    weight: float


# Researcher-chosen defaults (deployability and correctness first); the
# sensitivity analysis shows how much the order depends on them.
DEFAULT_CRITERIA: tuple[Criterion, ...] = (
    Criterion("deploy_rate", "Deploy rate", "benefit", 0.30),
    Criterion("pass_rate", "Functional pass rate", "benefit", 0.30),
    Criterion("issues_per_kloc", "Unique issues / kLOC", "cost", 0.15),
    Criterion("security_per_app", "Security issues / app", "cost", 0.15),
    Criterion("cost_per_app", "Cost / app (USD)", "cost", 0.10),
)


def criteria_from_config(config: list[dict[str, Any]] | None) -> tuple[Criterion, ...]:
    """Criteria from ``[{key, direction, weight, label?}]`` (weights renormalised); defaults when empty."""
    if not config:
        return DEFAULT_CRITERIA
    out = []
    for c in config:
        direction = c.get("direction", "benefit")
        if direction not in ("benefit", "cost"):
            msg = f"Criterion {c.get('key')}: direction must be benefit or cost"
            raise ValueError(msg)
        weight = float(c.get("weight", 0))
        if weight < 0:
            msg = f"Criterion {c.get('key')}: weight must be >= 0"
            raise ValueError(msg)
        out.append(Criterion(c["key"], c.get("label", c["key"]), direction, weight))
    total = sum(c.weight for c in out)
    if total <= 0:
        msg = "Criteria weights must sum to more than 0"
        raise ValueError(msg)
    return tuple(Criterion(c.key, c.label, c.direction, c.weight / total) for c in out)


def _complete(matrix: dict[str, dict[str, float | None]], criteria) -> tuple[dict[str, list[float]], list[str]]:
    rows, dropped = {}, []
    for alt, values in matrix.items():
        vec = [values.get(c.key) for c in criteria]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vec):
            dropped.append(alt)
        else:
            rows[alt] = [float(v) for v in vec]
    return rows, dropped


def topsis_scores(rows: dict[str, list[float]], criteria, weights: list[float]) -> dict[str, float]:
    """Closeness to the ideal solution (vector normalisation)."""
    if not rows:
        return {}
    alts = list(rows)
    norms = [math.sqrt(sum(rows[a][j] ** 2 for a in alts)) or 1.0 for j in range(len(criteria))]
    weighted = {a: [weights[j] * rows[a][j] / norms[j] for j in range(len(criteria))] for a in alts}
    ideal, anti = [], []
    for j, c in enumerate(criteria):
        col = [weighted[a][j] for a in alts]
        best, worst = (max(col), min(col)) if c.direction == "benefit" else (min(col), max(col))
        ideal.append(best)
        anti.append(worst)
    scores = {}
    for a in alts:
        d_plus = math.sqrt(sum((weighted[a][j] - ideal[j]) ** 2 for j in range(len(criteria))))
        d_minus = math.sqrt(sum((weighted[a][j] - anti[j]) ** 2 for j in range(len(criteria))))
        scores[a] = d_minus / (d_plus + d_minus) if (d_plus + d_minus) else 0.5
    return scores


def wsm_scores(rows: dict[str, list[float]], criteria, weights: list[float]) -> dict[str, float]:
    """Weighted sum of min-max normalised criteria (cost criteria inverted)."""
    if not rows:
        return {}
    alts = list(rows)
    scores = dict.fromkeys(alts, 0.0)
    for j, c in enumerate(criteria):
        col = [rows[a][j] for a in alts]
        lo, hi = min(col), max(col)
        for a in alts:
            norm = 0.5 if hi == lo else (rows[a][j] - lo) / (hi - lo)
            scores[a] += weights[j] * (norm if c.direction == "benefit" else 1 - norm)
    return scores


def _order(scores: dict[str, float]) -> list[str]:
    return sorted(scores, key=lambda a: (-scores[a], a))


def _rank_summary(orders: list[list[str]], alts: list[str]) -> list[dict[str, Any]]:
    positions: dict[str, list[int]] = defaultdict(list)
    for order in orders:
        for i, a in enumerate(order, start=1):
            positions[a].append(i)
    n = len(orders)
    out = []
    for a in alts:
        ranks = positions.get(a) or []
        if not ranks:
            continue
        counts = Counter(ranks)
        out.append(
            {
                "model": a,
                "median_rank": percentile(ranks, 0.5),
                "rank_ci95": [percentile(ranks, 0.025), percentile(ranks, 0.975)],
                "p_rank_1": round(counts.get(1, 0) / n, 4),
                "p_top_3": round(sum(v for k, v in counts.items() if k <= 3) / n, 4),
            },
        )
    return sorted(out, key=lambda r: (r["median_rank"], r["model"]))


def _dirichlet(rng: random.Random, alphas: list[float]) -> list[float]:
    draws = [rng.gammavariate(max(a, 1e-3), 1.0) for a in alphas]
    total = sum(draws)
    return [d / total for d in draws]


def _swaps(base: list[str], other: list[str]) -> list[list[str]]:
    pos = {a: i for i, a in enumerate(other)}
    return [[a, b] for a, b in itertools.pairwise(base) if pos.get(a, 0) > pos.get(b, 0)]


def rank_models(
    matrix: dict[str, dict[str, float | None]],
    criteria: tuple[Criterion, ...] = DEFAULT_CRITERIA,
    *,
    seed: int = 0,
    resample: Callable[[random.Random], dict[str, dict[str, float | None]]] | None = None,
    draws: int = WEIGHT_DRAWS,
) -> dict[str, Any]:
    """TOPSIS + WSM rankings of *matrix* (``{model: {criterion: value}}``) with sensitivity analyses.

    *resample*, when given, returns a re-estimated matrix from one bootstrap
    draw of the underlying apps; it drives the data-uncertainty summary.
    """
    rows, dropped = _complete(matrix, criteria)
    weights = [c.weight for c in criteria]
    topsis = topsis_scores(rows, criteria, weights)
    wsm = wsm_scores(rows, criteria, weights)
    base = _order(topsis)
    wsm_order = _order(wsm)
    alts = sorted(rows)

    ranking = [
        {
            "model": a,
            "topsis_rank": base.index(a) + 1,
            "topsis_score": round(topsis[a], 4),
            "wsm_rank": wsm_order.index(a) + 1,
            "wsm_score": round(wsm[a], 4),
            **{c.key: rows[a][j] for j, c in enumerate(criteria)},
        }
        for a in base
    ]

    equal = [1 / len(criteria)] * len(criteria)
    equal_order = _order(topsis_scores(rows, criteria, equal))

    oat = []
    for j, c in enumerate(criteria):
        for factor in (1 - OAT_FACTOR, 1 + OAT_FACTOR):
            w = list(weights)
            w[j] *= factor
            total = sum(w)
            w = [x / total for x in w]
            order = _order(topsis_scores(rows, criteria, w))
            oat.append(
                {
                    "criterion": c.key,
                    "factor": factor,
                    "kendall_tau": kendall_tau(base, order),
                    "rank_reversals": _swaps(base, order),
                    "leader": order[0] if order else None,
                },
            )

    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    near = [
        _order(topsis_scores(rows, criteria, _dirichlet(rng, [w * DIRICHLET_CONCENTRATION for w in weights])))
        for _ in range(draws)
    ]
    flat = [_order(topsis_scores(rows, criteria, _dirichlet(rng, [1.0] * len(criteria)))) for _ in range(draws)]

    data_uncertainty = None
    if resample is not None and rows:
        orders = []
        for _ in range(max(200, draws // 4)):
            boot_rows, _ = _complete(resample(rng), criteria)
            if len(boot_rows) == len(rows):
                orders.append(_order(topsis_scores(boot_rows, criteria, weights)))
        data_uncertainty = {"draws": len(orders), "ranks": _rank_summary(orders, alts)} if orders else None

    return {
        "criteria": [
            {"key": c.key, "label": c.label, "direction": c.direction, "weight": round(c.weight, 4)} for c in criteria
        ],
        "weights_note": (
            "Weights are researcher-chosen; the sensitivity sections show how far the order depends on them."
        ),
        "models_ranked": len(rows),
        "dropped_missing_criteria": dropped,
        "ranking": ranking,
        "topsis_vs_wsm_kendall_tau": kendall_tau(base, wsm_order),
        "equal_weights": {
            "order": equal_order,
            "kendall_tau": kendall_tau(base, equal_order),
            "rank_reversals": _swaps(base, equal_order),
        },
        "one_at_a_time": oat,
        "monte_carlo_near_weights": {
            "draws": draws,
            "concentration": DIRICHLET_CONCENTRATION,
            "ranks": _rank_summary(near, alts),
        },
        "monte_carlo_any_weights": {"draws": draws, "ranks": _rank_summary(flat, alts)},
        "data_uncertainty": data_uncertainty,
    }
