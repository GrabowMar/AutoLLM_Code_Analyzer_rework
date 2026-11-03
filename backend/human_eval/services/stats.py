"""Pure-Python statistics for human-evaluation results (owner D1): agreement, Bradley-Terry, bootstraps, tests.

No Django, numpy or scipy. Every resampling function takes an explicit seed so
results regenerate identically; :func:`derive_seed` turns the task seed plus a
label (block, criterion, group, ...) into an independent stream, so one CI does
not depend on how many others were computed before it.

- :func:`krippendorff_alpha` — Krippendorff's alpha for any number of raters with
  missing values (coincidence-matrix form, Krippendorff 2011), at the nominal,
  ordinal, interval or ratio level. Units are lists of the values the unit got;
  units with fewer than two values are not pairable and are ignored.
- :func:`percent_agreement` — share of agreeing rater pairs among all pairable pairs.
- :func:`bradley_terry` — maximum-likelihood strengths, ties count half a win for
  each side, with a weak prior of ``prior`` wins and ``prior`` losses against a
  virtual opponent of fixed strength 1 (it anchors the scale and keeps undefeated /
  winless items finite; it is not part of the normalisation or the output), fitted by
  Newton-Raphson on log strengths (MM, Hunter 2004, without a prior or as a
  fallback). Strengths are normalised to geometric mean 1 over ``items``.
- :func:`bt_components` — connected components of the comparison graph: strengths
  are only comparable within one component, so callers fit each separately.
- :func:`bootstrap_bt` — percentile CIs on log strength from a caller-supplied
  resampler (cluster bootstraps: items with all their comparisons, or apps within
  groups).
- :func:`binomial_two_sided` — exact two-sided binomial test against p = 0.5.
- :func:`wilcoxon_p` — Wilcoxon signed-rank p (exact up to 25 non-zero deltas, normal approximation
  with tie and continuity correction above).
- :func:`cluster_bootstrap_mean_ci` / :func:`bootstrap_diff_ci` — percentile
  bootstraps over clusters (apps are the unit of analysis), None below three.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from collections import defaultdict
from operator import mul
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Hashable
    from collections.abc import Iterable
    from collections.abc import Mapping
    from collections.abc import Sequence

LEVELS = ("nominal", "ordinal", "interval", "ratio")
BOOTSTRAP_DRAWS = 2000
BT_BOOTSTRAP_DRAWS = 1000
MIN_CLUSTERS_FOR_CI = 3
CI_LOW = 0.025
CI_HIGH = 0.975
_BT_TOLERANCE = 1e-10
_BT_BOOTSTRAP_TOLERANCE = 1e-4  # quadratic convergence: the error after a step this small is ~1e-8
_NEWTON_TRUSTED_STEP = 0.5
_BT_MAX_ITER = 10_000

Comparison = tuple  # (a, b, score_a) or (a, b, score_a, weight); score_a: 1 win, 0.5 tie, 0 loss


# ---------------------------------------------------------------------------
# Seeds and descriptive helpers
# ---------------------------------------------------------------------------


def derive_seed(base: int, *parts: Any) -> int:
    """A 64-bit seed from ``base`` and labels: sha256 of ``"base:part1:part2..."`` (stable across runs)."""
    text = ":".join([str(base), *(str(part) for part in parts)])
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def mean(values: Iterable[float]) -> float | None:
    """Arithmetic mean, None when empty."""
    items = list(values)
    return sum(items) / len(items) if items else None


def sd(values: Iterable[float]) -> float | None:
    """Sample standard deviation (n - 1), None below two values."""
    items = list(values)
    if len(items) < 2:
        return None
    centre = sum(items) / len(items)
    return math.sqrt(sum((value - centre) ** 2 for value in items) / (len(items) - 1))


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (type 7) of an already sorted, non-empty sequence."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def median(values: Iterable[float]) -> float | None:
    """Median, None when empty."""
    items = sorted(values)
    return quantile(items, 0.5) if items else None


def percentile_ci(draws: Iterable[float]) -> tuple[float, float] | None:
    """The 2.5th and 97.5th percentiles of bootstrap draws, None when there are none."""
    items = sorted(draws)
    if not items:
        return None
    return quantile(items, CI_LOW), quantile(items, CI_HIGH)


def average_ranks(values: Sequence[float]) -> list[float]:
    """Average 1-based ranks, ties sharing their mean rank (as ``statistics.services.inference.ranks``)."""
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for position in range(i, j + 1):
            result[order[position]] = (i + j + 2) / 2
        i = j + 1
    return result


def within_group_percentile_ranks(values: Mapping[Hashable, float], groups: Mapping[Hashable, str]) -> dict:
    """Each unit's percentile rank ``(rank - 0.5) / n`` among the units of its group (average ranks for ties).

    Used to correlate human and automated scores within app specs, so spec difficulty
    cannot drive the correlation (Simpson's paradox when specs are pooled).
    """
    by_group: dict[str, list[Hashable]] = defaultdict(list)
    for key in values:
        by_group[groups[key]].append(key)
    result: dict[Hashable, float] = {}
    for keys in by_group.values():
        group_ranks = average_ranks([values[key] for key in keys])
        for key, rank in zip(keys, group_ranks, strict=True):
            result[key] = (rank - 0.5) / len(keys)
    return result


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


def _pairable(units: Iterable[Sequence[Hashable]]) -> list[list[Hashable]]:
    return [list(values) for values in units if len(values) >= 2]


def pairable_counts(units: Iterable[Sequence[Hashable]]) -> tuple[int, int]:
    """``(n_units, n_values)``: units with at least two values, and the values in them."""
    pairable = _pairable(units)
    return len(pairable), sum(len(values) for values in pairable)


def percent_agreement(units: Iterable[Sequence[Hashable]]) -> float | None:
    """Share of agreeing pairs among all unordered rater pairs within units; None without a pair."""
    agree = total = 0
    for values in _pairable(units):
        counts = Counter(values)
        m = len(values)
        total += m * (m - 1) // 2
        agree += sum(count * (count - 1) // 2 for count in counts.values())
    return agree / total if total else None


def _delta_squared(level: str, marginals: dict[Any, float], ordered: list[Any]) -> Callable[[Any, Any], float]:
    if level == "nominal":
        return lambda c, k: 0.0 if c == k else 1.0
    if level == "interval":
        return lambda c, k: float((c - k) ** 2)
    if level == "ratio":
        return lambda c, k: 0.0 if c == k else ((c - k) / (c + k)) ** 2
    # ordinal: (sum of the marginals from c to k - (n_c + n_k) / 2) squared (Krippendorff 2011, ordinal metric)
    position = {value: index for index, value in enumerate(ordered)}
    cumulative = [0.0]
    for value in ordered:
        cumulative.append(cumulative[-1] + marginals[value])

    def ordinal(c: Any, k: Any) -> float:
        low, high = sorted((position[c], position[k]))
        between = cumulative[high + 1] - cumulative[low]
        return (between - (marginals[c] + marginals[k]) / 2) ** 2

    return ordinal


def krippendorff_alpha(units: Iterable[Sequence[Hashable]], level: str = "nominal") -> float | None:
    """Krippendorff's alpha over ``units`` (each the list of values it received) at ``level``.

    ``alpha = 1 - D_o / D_e`` from the coincidence matrix: within-unit pairs weighted
    1 / (m_u - 1) give the observed disagreement, all pairs of pairable values the expected one.
    Ordinal / interval / ratio values must be numbers (ordinal: any orderable values).
    None when fewer than two values are pairable or every value is identical (D_e = 0).
    """
    if level not in LEVELS:
        msg = f"Unknown level {level!r}"
        raise ValueError(msg)
    pairable = _pairable(units)
    coincidences: Counter = Counter()
    for values in pairable:
        counts = Counter(values)
        weight = 1 / (len(values) - 1)
        for c, n_c in counts.items():
            for k, n_k in counts.items():
                pairs = n_c * (n_c - 1) if c == k else n_c * n_k
                if pairs:
                    coincidences[(c, k)] += pairs * weight
    marginals: dict[Any, float] = defaultdict(float)
    for (c, _k), count in coincidences.items():
        marginals[c] += count
    n = sum(marginals.values())
    if n < 2:
        return None
    ordered = sorted(marginals)
    delta = _delta_squared(level, marginals, ordered)
    observed = sum(count * delta(c, k) for (c, k), count in coincidences.items()) / n
    expected = sum(marginals[c] * marginals[k] * delta(c, k) for c in ordered for k in ordered) / (n * (n - 1))
    if expected <= 0:
        return None
    return 1 - observed / expected


# ---------------------------------------------------------------------------
# Exact binomial test
# ---------------------------------------------------------------------------


def binomial_two_sided(k: int, n: int) -> float | None:
    """Exact two-sided binomial p for ``k`` successes in ``n`` trials under p = 0.5; None for n = 0."""
    if n <= 0:
        return None
    if k < 0 or k > n:
        msg = "k must be between 0 and n"
        raise ValueError(msg)
    tail = min(k, n - k)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(tail + 1)) / 2**n)


# ---------------------------------------------------------------------------
# Bootstraps
# ---------------------------------------------------------------------------


def cluster_bootstrap_mean_ci(
    app_values: Mapping[Hashable, float] | Sequence[float],
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> tuple[float, float] | None:
    """Percentile 95% CI of the mean of per-cluster (per-app) values, resampling clusters with replacement.

    The group value is the mean of app values, so resampling the app values is the
    cluster bootstrap. None below :data:`MIN_CLUSTERS_FOR_CI` clusters.
    """
    values = list(app_values.values()) if hasattr(app_values, "values") else list(app_values)
    k = len(values)
    if k < MIN_CLUSTERS_FOR_CI:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    draw = rng.random
    samples = []
    for _ in range(draws):
        total = 0.0
        for _i in range(k):
            total += values[int(draw() * k)]
        samples.append(total / k)
    return percentile_ci(samples)


def bootstrap_diff_ci(
    values: Sequence[float],
    baseline: Sequence[float],
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> tuple[float, float] | None:
    """Percentile 95% CI of ``mean(values) - mean(baseline)``, resampling each group's apps independently.

    None when either side has fewer than :data:`MIN_CLUSTERS_FOR_CI` apps.
    """
    a, b = list(values), list(baseline)
    if len(a) < MIN_CLUSTERS_FOR_CI or len(b) < MIN_CLUSTERS_FOR_CI:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    draw = rng.random
    na, nb = len(a), len(b)
    samples = []
    for _ in range(draws):
        mean_a = sum(a[int(draw() * na)] for _i in range(na)) / na
        mean_b = sum(b[int(draw() * nb)] for _i in range(nb)) / nb
        samples.append(mean_a - mean_b)
    return percentile_ci(samples)


# ---------------------------------------------------------------------------
# Bradley-Terry
# ---------------------------------------------------------------------------


def _unpack(comparison: Comparison) -> tuple[Hashable, Hashable, float, float]:
    if len(comparison) == 3:
        a, b, score = comparison
        return a, b, float(score), 1.0
    a, b, score, weight = comparison
    return a, b, float(score), float(weight)


def bt_components(comparisons: Iterable[Comparison], items: Iterable[Hashable]) -> list[list[Hashable]]:
    """Connected components of the comparison graph over ``items`` (each sorted by ``str``; ordered by first item)."""
    parent: dict[Hashable, Hashable] = {item: item for item in items}

    def find(x: Hashable) -> Hashable:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for comparison in comparisons:
        a, b, _score, weight = _unpack(comparison)
        if weight <= 0 or a == b:
            continue
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a
    groups: dict[Hashable, list[Hashable]] = defaultdict(list)
    for item in parent:
        groups[find(item)].append(item)
    components = [sorted(members, key=str) for members in groups.values()]
    return sorted(components, key=lambda members: str(members[0]))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _softplus(x: float) -> float:
    """``log(1 + e^x)`` without overflow."""
    return x + math.log1p(math.exp(-x)) if x > 0 else math.log1p(math.exp(x))


def _cholesky_solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Solve ``matrix @ x = vector`` for a symmetric positive-definite matrix (None if it is not)."""
    k = len(vector)
    lower = [[0.0] * k for _ in range(k)]
    for i in range(k):
        row_i = lower[i]
        for j in range(i):
            row_j = lower[j]
            row_i[j] = (matrix[i][j] - sum(map(mul, row_i[:j], row_j[:j]))) / row_j[j]
        diagonal = matrix[i][i] - sum(map(mul, row_i[:i], row_i[:i]))
        if diagonal <= 0:
            return None
        row_i[i] = math.sqrt(diagonal)
    forward = [0.0] * k
    for i in range(k):
        forward[i] = (vector[i] - sum(map(mul, lower[i][:i], forward[:i]))) / lower[i][i]
    solution = [0.0] * k
    for i in range(k - 1, -1, -1):
        tail = sum(lower[m][i] * solution[m] for m in range(i + 1, k))
        solution[i] = (forward[i] - tail) / lower[i][i]
    return solution


def _bt_newton(
    wins: list[float],
    pairs: list[tuple[int, int, float]],
    virtual: float,
    theta: list[float],
    tolerance: float,
    max_iter: int = 100,
) -> list[float] | None:
    """Newton-Raphson on log strengths (the negative log-likelihood is strictly convex with a prior).

    Minimises ``-sum_i w_i t_i + sum_pairs n log(e^t_i + e^t_j) + v sum_i log(1 + e^t_i)`` (the
    virtual opponent sits at t = 0). Steps longer than :data:`_NEWTON_TRUSTED_STEP` are halved until the
    objective decreases; shorter ones are taken in full (quadratic-convergence region of this smooth,
    strictly convex objective). Returns None when it does not converge (callers fall back to MM).
    """
    k = len(wins)

    def objective(t: list[float]) -> float:
        value = -sum(map(mul, wins, t)) + virtual * sum(_softplus(x) for x in t)
        for i, j, n in pairs:
            high, low = (t[i], t[j]) if t[i] > t[j] else (t[j], t[i])
            value += n * (high + math.log1p(math.exp(low - high)))
        return value

    current: float | None = None
    for _ in range(max_iter):
        gradient = [-w for w in wins]
        hessian = [[0.0] * k for _ in range(k)]
        for i in range(k):
            share = _sigmoid(theta[i])
            gradient[i] += virtual * share
            hessian[i][i] += virtual * share * (1 - share)
        for i, j, n in pairs:
            share = _sigmoid(theta[i] - theta[j])
            curvature = n * share * (1 - share)
            gradient[i] += n * share
            gradient[j] += n * (1 - share)
            hessian[i][i] += curvature
            hessian[j][j] += curvature
            hessian[i][j] -= curvature
            hessian[j][i] -= curvature
        step = _cholesky_solve(hessian, gradient)
        if step is None:
            return None
        full_step = max(abs(d) for d in step)
        candidate = [t - d for t, d in zip(theta, step, strict=True)]
        if full_step > _NEWTON_TRUSTED_STEP:
            # far from the optimum: halve the step until the objective decreases
            current = objective(theta) if current is None else current
            scale = 1.0
            for _halving in range(40):
                value = objective(candidate)
                if value <= current + 1e-12 * (1 + abs(current)):
                    break
                scale /= 2
                candidate = [t - scale * d for t, d in zip(theta, step, strict=True)]
            else:
                return None
            current = value
        else:
            current = None  # full Newton steps this close to the optimum always descend
        theta = candidate
        if full_step < tolerance:  # judged on the full Newton step, never on a damped one
            return theta
    return None


def _bt_mm(
    wins: list[float],
    pairs: list[tuple[int, int, float]],
    virtual: float,
    strengths: list[float],
    tolerance: float,
    max_iter: int,
) -> list[float]:
    """Hunter's (2004) MM iteration on strengths; renormalised every step without a prior."""
    k = len(wins)
    for _ in range(max_iter):
        denominators = [virtual / (p + 1.0) for p in strengths] if virtual else [0.0] * k
        for i, j, n in pairs:
            share = n / (strengths[i] + strengths[j])
            denominators[i] += share
            denominators[j] += share
        updated = [wins[i] / denominators[i] if denominators[i] else strengths[i] for i in range(k)]
        if not virtual:
            scale = math.exp(sum(math.log(p) for p in updated) / k)
            updated = [p / scale for p in updated]
        change = max(abs(math.log(new / old)) for new, old in zip(updated, strengths, strict=True))
        strengths = updated
        if change < tolerance:
            break
    return strengths


def bradley_terry(
    comparisons: Iterable[Comparison],
    items: Sequence[Hashable],
    *,
    prior: float = 0.5,
    init: Mapping[Hashable, float] | None = None,
    tolerance: float = _BT_TOLERANCE,
    max_iter: int = _BT_MAX_ITER,
) -> dict[Hashable, float]:
    """Bradley-Terry strengths of ``items`` (geometric mean 1).

    ``comparisons`` are ``(a, b, score_a)`` or ``(a, b, score_a, weight)`` with score 1 (a won),
    0.5 (tie: half a win each) or 0 (b won); comparisons touching an item outside ``items`` are
    ignored. ``prior`` adds that many wins and as many losses against a virtual opponent of fixed
    strength 1 for every item; the log-likelihood is then strictly concave and is maximised by
    Newton-Raphson (MM as a fallback). With ``prior = 0`` this is the plain maximum-likelihood fit by
    MM, which exists only when every item has won at least once (else ``ValueError``). Fit one
    connected component at a time either way. ``init`` warm-starts the fit (any scale).
    """
    keys = list(items)
    if not keys:
        return {}
    index = {key: i for i, key in enumerate(keys)}
    k = len(keys)
    wins = [prior] * k
    pair_games: dict[tuple[int, int], float] = defaultdict(float)
    for comparison in comparisons:
        a, b, score, weight = _unpack(comparison)
        if a not in index or b not in index or a == b or weight <= 0:
            continue
        i, j = index[a], index[b]
        wins[i] += score * weight
        wins[j] += (1 - score) * weight
        pair_games[(i, j) if i < j else (j, i)] += weight
    if prior <= 0 and any(w <= 0 for w in wins):
        msg = "The maximum-likelihood Bradley-Terry fit does not exist: an item never won"
        raise ValueError(msg)
    pairs = [(i, j, n) for (i, j), n in pair_games.items()]
    virtual = 2 * prior
    start = [max(float(init.get(key, 1.0)), 1e-300) if init else 1.0 for key in keys]
    logs = None
    if virtual:
        logs = _bt_newton(wins, pairs, virtual, [math.log(p) for p in start], tolerance)
    if logs is None:
        logs = [math.log(p) for p in _bt_mm(wins, pairs, virtual, start, tolerance, max_iter)]
    centre = sum(logs) / k
    return {key: math.exp(value - centre) for key, value in zip(keys, logs, strict=True)}


def bootstrap_bt(
    resample: Callable[[random.Random], list[Comparison]],
    items: Sequence[Hashable],
    *,
    seed: int,
    draws: int = BT_BOOTSTRAP_DRAWS,
    prior: float = 0.5,
    init: Mapping[Hashable, float] | None = None,
) -> dict[Hashable, tuple[float, float] | None]:
    """Percentile 95% CIs on each item's normalised log strength over ``draws`` bootstrap refits.

    ``resample(rng)`` returns one bootstrap sample of comparisons (e.g. items resampled with
    all their comparisons). Every draw refits all ``items`` — an item absent from the sample
    keeps only the prior — and normalises over all of them, i.e. the whole estimator is
    bootstrapped. ``init`` (the point estimate) warm-starts each refit.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    logs: dict[Hashable, list[float]] = {key: [] for key in items}
    for _ in range(draws):
        fitted = bradley_terry(resample(rng), items, prior=prior, init=init, tolerance=_BT_BOOTSTRAP_TOLERANCE)
        for key, strength in fitted.items():
            logs[key].append(math.log(strength))
    return {key: percentile_ci(values) for key, values in logs.items()}


# ---------------------------------------------------------------------------
# Wilcoxon signed-rank test
# ---------------------------------------------------------------------------

_EXACT_WILCOXON_MAX_N = 25


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
