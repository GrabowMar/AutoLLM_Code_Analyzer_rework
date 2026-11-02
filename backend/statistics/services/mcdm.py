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

Ties are ranks, not alphabet: models whose scores are equal (within
:data:`TIE_TOLERANCE`) share the best rank of their group (competition
ranking, 1-2-2-4), in the ranking table and in every simulated draw. So
P(rank 1) counts each model tied for first and the values can sum to more
than 1; ``models_tied`` flags a tie in the base ranking. Rank agreement
(TOPSIS vs WSM, equal weights, one-at-a-time) is Kendall's tau-b on those
ranks, and a rank reversal needs a strict order on both sides.

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

from backend.statistics.services.inference import kendall_tau_b
from backend.statistics.services.inference import percentile

if TYPE_CHECKING:
    from collections.abc import Callable

WEIGHT_DRAWS = 2000
DIRICHLET_CONCENTRATION = 20.0
OAT_FACTOR = 0.5
# Scores lie in [0, 1]; closer than this they are the same score.
TIE_TOLERANCE = 1e-12
TIES_NOTE = (
    "Tied models share the best rank of their group (1-2-2-4); P(rank 1) counts every model tied for first, "
    "so it can sum to more than 1."
)


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
    Criterion("issues_per_kloc", "Code issues / kLOC", "cost", 0.15),
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
    """Display order: best score first, names only to list tied models stably."""
    return sorted(scores, key=lambda a: (-scores[a], a))


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    """Competition ranks (1-2-2-4): models within :data:`TIE_TOLERANCE` of their group's best share its rank."""
    out: dict[str, int] = {}
    group_score: float | None = None
    group_rank = 0
    for i, a in enumerate(_order(scores), start=1):
        if group_score is None or not math.isclose(scores[a], group_score, rel_tol=0.0, abs_tol=TIE_TOLERANCE):
            group_score, group_rank = scores[a], i
        out[a] = group_rank
    return out


def _tied_groups(ranks: dict[str, int]) -> list[list[str]]:
    by_rank: dict[int, list[str]] = defaultdict(list)
    for a, r in ranks.items():
        by_rank[r].append(a)
    return [sorted(v) for _, v in sorted(by_rank.items()) if len(v) > 1]


def _tau(ranks_a: dict[str, int], ranks_b: dict[str, int]) -> float | None:
    """Kendall's tau-b between two rankings of the same models (ties neither agree nor disagree)."""
    common = sorted(set(ranks_a) & set(ranks_b))
    return kendall_tau_b([ranks_a[a] for a in common], [ranks_b[a] for a in common])


def _leaders(ranks: dict[str, int]) -> list[str]:
    return sorted(a for a, r in ranks.items() if r == 1)


def _rank_summary(rankings: list[dict[str, int]], alts: list[str]) -> list[dict[str, Any]]:
    positions: dict[str, list[int]] = defaultdict(list)
    for ranking in rankings:
        for a, r in ranking.items():
            positions[a].append(r)
    n = len(rankings)
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


def _swaps(base: dict[str, int], other: dict[str, int]) -> list[list[str]]:
    """Pairs from adjacent base rank groups whose order strictly inverts in *other*.

    A tie on either side is not a reversal: nothing was ordered there.
    """
    levels: dict[int, list[str]] = defaultdict(list)
    for a, r in base.items():
        levels[r].append(a)
    groups = [sorted(v) for _, v in sorted(levels.items())]
    return [
        [a, b]
        for upper, lower in itertools.pairwise(groups)
        for a in upper
        for b in lower
        if a in other and b in other and other[a] > other[b]
    ]


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
    base = _ranks(topsis)
    wsm_ranks = _ranks(wsm)
    alts = sorted(rows)

    ranking = [
        {
            "model": a,
            "topsis_rank": base[a],
            "topsis_score": round(topsis[a], 4),
            "wsm_rank": wsm_ranks[a],
            "wsm_score": round(wsm[a], 4),
            **{c.key: rows[a][j] for j, c in enumerate(criteria)},
        }
        for a in _order(topsis)
    ]
    tied = _tied_groups(base)

    equal = [1 / len(criteria)] * len(criteria)
    equal_scores = topsis_scores(rows, criteria, equal)
    equal_ranks = _ranks(equal_scores)

    oat = []
    for j, c in enumerate(criteria):
        for factor in (1 - OAT_FACTOR, 1 + OAT_FACTOR):
            w = list(weights)
            w[j] *= factor
            total = sum(w)
            w = [x / total for x in w]
            ranks = _ranks(topsis_scores(rows, criteria, w))
            leaders = _leaders(ranks)
            oat.append(
                {
                    "criterion": c.key,
                    "factor": factor,
                    "kendall_tau": _tau(base, ranks),
                    "rank_reversals": _swaps(base, ranks),
                    # None when several models tie for first: no single leader.
                    "leader": leaders[0] if len(leaders) == 1 else None,
                    "leaders": leaders,
                },
            )

    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    near = [
        _ranks(topsis_scores(rows, criteria, _dirichlet(rng, [w * DIRICHLET_CONCENTRATION for w in weights])))
        for _ in range(draws)
    ]
    flat = [_ranks(topsis_scores(rows, criteria, _dirichlet(rng, [1.0] * len(criteria)))) for _ in range(draws)]

    data_uncertainty = None
    if resample is not None and rows:
        boot_ranks = []
        for _ in range(max(200, draws // 4)):
            boot_rows, _ = _complete(resample(rng), criteria)
            if len(boot_rows) == len(rows):
                boot_ranks.append(_ranks(topsis_scores(boot_rows, criteria, weights)))
        data_uncertainty = {"draws": len(boot_ranks), "ranks": _rank_summary(boot_ranks, alts)} if boot_ranks else None

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
        "models_tied": bool(tied),
        "tied_groups": tied,
        "ties_note": TIES_NOTE,
        "topsis_vs_wsm_kendall_tau": _tau(base, wsm_ranks),
        "equal_weights": {
            "order": _order(equal_scores),
            "ranks": equal_ranks,
            "kendall_tau": _tau(base, equal_ranks),
            "rank_reversals": _swaps(base, equal_ranks),
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
