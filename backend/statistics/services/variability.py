"""Run-to-run variability of repeats of one setup (descriptive statistics, no p-values).

A study's results average repeats away (the unit is the app); this module measures the repeats
themselves. Three scopes share one set of pure estimators:

* **Study** (:func:`study_variability`): the study's current runs (``runs.view.current_runs``: the first
  usable attempt per main-stage cell, ``beyond_plan`` runs excluded) that are usable, within the planned
  repeats, on the majority configuration fingerprint of their (condition, unit) when asked
  (``fingerprint="majority"``) and on the (unit, repeat) cells every condition has. The default is
  intention-to-treat: truncated runs are kept; ``sensitivity="exclude_truncated"`` drops them. Per
  (condition, unit) cell with r repeats, for each metric: n, mean, sd (r-1), min, max, range, IQR (r >= 4)
  and CV (ratio-scale metrics only, never a pass rate). Per condition: the pooled within-cell SD
  ``sqrt(sum (r_a-1) s_a^2 / sum (r_a-1))``, the median cell CV and the share of cells whose repeats agree
  on functional pass. Variance components (condition, unit, condition x unit, repeat) by the method of
  moments on unweighted cell means (harmonic r), negatives truncated at 0 and flagged; per condition the
  one-way ICC(1) over units with the unbalanced r0, a two-stage cluster-bootstrap 95% CI, the design effect
  ``1 + (r0-1) ICC`` and the effective n. pass@k ``1 - C(n-c,k)/C(n,k)`` and pass^k ``C(c,k)/C(n,k)``,
  check-level agreement, code similarity within cells against a between-condition reference, finding
  overlap, bootstrap rank intervals, and a precision planner (terminal studies only, an optional-stopping
  guard: every block of a running study is flagged ``interim``).
* **Run** (:func:`run_replicates`): every visible run sharing the run's ``config_fingerprint``, with each
  member's relation (self / retry / replay / rerun / replicate), metrics and similarity.
* **Model** (:func:`model_variability`): within-group spread over all of a model's same-setup groups.

Pass rate is the latest evaluation's agent pass rate as mirrored in ``GenerationRun.metrics["eval"]``
(infra and flaky checks excluded by the tester): a run whose evaluation is pending, errored or not measured
has None, never 0. Cost, tokens, LLM calls and wall time are the documented ``GenerationRun.metrics``
contract keys. A billed cost of $0 on an endpoint whose ``cost_basis`` is not ``billed`` (subscription or
local, from the run manifest) is marked ``billing``, never a floor.

Missing measurements are None, never 0. Code issues come from one
:func:`backend.statistics.services.study.issue_breakdowns` call over the whole scope (latest result per
tool, common tools only). Source LOC is the model scope's non-test source LOC. Resampling uses seeds
derived from the scope id, so a payload regenerates identically.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import random
import zlib
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from math import comb
from typing import TYPE_CHECKING
from typing import Any

from django.core.cache import cache
from django.db.models import Count
from django.db.models import Max
from django.db.models import Q
from django.db.models import StringAgg
from django.db.models import TextField
from django.db.models import Value
from django.db.models.functions import MD5
from django.db.models.functions import Cast
from django.utils import timezone

from backend.statistics.services import similarity as sim

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

PAYLOAD_VERSION = 5  # 5: v3 runs (current runs, metrics contract, EvalSuite pass rate, model-scope code)
BOOTSTRAP_DRAWS = 1000
MIN_APPS_FOR_CI = 3  # as the study results
_PAYLOAD_CACHE_SECONDS = 6 * 3600
_BOOT_CACHE_SECONDS = 6 * 3600
MAX_REPLICATES = 50
MAX_MODEL_GROUPS = 200
MAX_GROUP_PAIRS = 10
PLANNER_APPS = (5, 10, 20, 40)
PLANNER_REPEATS = (1, 2, 3, 5)
Z95 = 1.959964

NOTE_DESCRIPTIVE = (
    "Descriptive: dispersion, variance shares, ICCs, similarity and rank intervals describe this sample; "
    "no p-values are computed here."
)
NOTE_TWO_REPEATS = "Cells with 2 repeats: each within-cell SD has 1 degree of freedom and is very imprecise."
NOTE_INTERIM = "Interim: the study is still running; every estimate can still move."
NOTE_SINGLE_TRIAL = "One repeat per cell: run-to-run variation cannot be estimated (plan 2 or more repeats)."
NOTE_REFERENCE = (
    "Reference pairs are different conditions on the same unit and repeat index: the similarity any two "
    "solutions of the task share (boilerplate floor)."
)
NOTE_WALL = "Wall time is not comparable across endpoints (machine and load differ)."
NOTE_SUBSCRIPTION = (
    "{n} of {total} kept runs ran on endpoints that are not billed per call ({sources}): their billed cost is "
    "$0, so Cost describes billing, not usage; List-price cost is the catalog list-price equivalent (not billed)."
)
NOTE_GRADING = (
    "Pass rate: the latest evaluation's agent pass rate (infra and flaky checks excluded); a run whose "
    "evaluation is pending, errored or not measured shows —."
)
NOTE_ITT = "Intention-to-treat: truncated runs (budget, wall clock, idle, max turns) are included."
NOTE_SENSITIVITY_TRUNCATED = "Sensitivity analysis: truncated runs are excluded ({n} dropped)."


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    ratio: bool  # a true zero: CV is defined
    better: str | None  # "higher" | "lower" | None (no preferred direction)
    source: str
    core: bool = False  # ICC bootstrap CI and precision planner


def _spec(key: str, label: str, unit: str, source: str, *, ratio: bool, better: str | None, core: bool = False):
    return MetricSpec(key=key, label=label, unit=unit, ratio=ratio, better=better, source=source, core=core)


BASE_METRICS: tuple[MetricSpec, ...] = (
    _spec(
        "pass_rate",
        "Pass rate",
        "share",
        "metrics.eval.agent_pass_rate (latest measured evaluation; infra and flaky checks excluded)",
        ratio=False,
        better="higher",
        core=True,
    ),
    _spec("cost", "Cost", "USD", "metrics.cost_usd (billed)", ratio=True, better="lower", core=True),
    _spec(
        "list_cost",
        "List-price cost",
        "USD",
        "metrics.list_cost_usd (catalog list-price equivalent; not billed)",
        ratio=True,
        better="lower",
    ),
    _spec("total_tokens", "Tokens", "tokens", "metrics.total_tokens", ratio=True, better="lower", core=True),
    _spec("llm_calls", "LLM calls", "calls", "sum of metrics.requests_by_class", ratio=True, better=None),
    _spec("wall_seconds", "Wall time", "s", "metrics.wall_s, else the run duration", ratio=True, better="lower"),
    _spec(
        "source_loc",
        "Source LOC",
        "lines",
        "model-scope non-test source LOC (reports.services.loc rules)",
        ratio=True,
        better=None,
        core=True,
    ),
    _spec("source_files", "Source files", "files", "model-scope non-test source files", ratio=True, better=None),
    _spec(
        "code_issues",
        "Code issues",
        "issues",
        "issue_breakdowns code_unique (common tools, latest result per tool)",
        ratio=True,
        better="lower",
        core=True,
    ),
    _spec(
        "issues_per_kloc",
        "Issues / kLOC",
        "per kLOC",
        "issue_breakdowns unique_per_kloc (model-scope code issues per model source kLOC)",
        ratio=True,
        better="lower",
        core=True,
    ),
    _spec("avg_ccn", "Mean CCN", "", "lizard avg_ccn (latest result)", ratio=True, better="lower"),
    _spec("max_ccn", "Max CCN", "", "lizard max_ccn (latest result)", ratio=True, better="lower"),
    _spec(
        "duplication_pct",
        "Duplication",
        "%",
        "jscpd duplication_percentage (latest result)",
        ratio=False,
        better="lower",
    ),
)
# Replicates / model summaries.
REPLICATE_METRICS = (
    "pass_rate",
    "source_loc",
    "source_files",
    "cost",
    "total_tokens",
    "llm_calls",
    "wall_seconds",
    "code_issues",
    "issues_per_kloc",
)
_SPECS = {m.key: m for m in BASE_METRICS}


# ── pure estimators ───────────────────────────────────────────────────


def _r(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _num(value: Any) -> float | None:
    """A finite float, or None (booleans, strings, NaN and missing are not measurements)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def median(values: Iterable[float | None]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    return _percentile(vals, 0.5) if vals else None


def describe(values: Iterable[float | None], *, ratio: bool) -> dict[str, Any]:
    """n, mean, sd (n-1), min, max, range, IQR (n >= 4) and CV (ratio scale, mean > 0, n >= 2)."""
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None, "range": None, "iqr": None, "cv": None}
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n >= 2 else None
    iqr = _percentile(vals, 0.75) - _percentile(vals, 0.25) if n >= 4 else None
    cv = sd / mean if ratio and sd is not None and mean > 0 else None
    return {
        "n": n,
        "mean": _r(mean),
        "sd": _r(sd),
        "min": _r(vals[0]),
        "max": _r(vals[-1]),
        "range": _r(vals[-1] - vals[0]),
        "iqr": _r(iqr),
        "cv": _r(cv, 4),
    }


def pooled_sd(groups: Iterable[Sequence[float]]) -> tuple[float | None, int]:
    """Pooled within-group SD ``sqrt(sum (n_i-1) s_i^2 / sum (n_i-1))`` and its degrees of freedom."""
    ss = 0.0
    df = 0
    for g in groups:
        vals = [v for v in g if v is not None]
        if len(vals) < 2:
            continue
        m = sum(vals) / len(vals)
        ss += sum((v - m) ** 2 for v in vals)
        df += len(vals) - 1
    return (math.sqrt(ss / df), df) if df else (None, 0)


def variance_components(cells: dict[tuple[Any, Any], Sequence[float]]) -> dict[str, Any]:
    """Method-of-moments variance components of a crossed condition x app design with repeats.

    *cells* maps (condition, app) to the repeat values. Only apps with values
    under every condition are used (a complete crossing). Unbalanced repeats
    use the unweighted-means analysis: cell means with the harmonic mean
    repeat count ``r_h``; the residual is the pooled within-cell variance.
    With one condition (or one app) the design is one-way. Negative
    estimates are set to 0 and listed in ``truncated``. Shares are of the
    summed (truncated) components; None when every value is identical.
    """
    clean = {k: [v for v in vals if v is not None] for k, vals in cells.items()}
    clean = {k: v for k, v in clean.items() if v}
    conditions = sorted({c for c, _a in clean}, key=str)
    apps = sorted({a for _c, a in clean}, key=str)
    complete = [a for a in apps if all((c, a) in clean for c in conditions)]
    data = {(c, a): clean[(c, a)] for c in conditions for a in complete}
    n_c, n_a = len(conditions), len(complete)
    n_runs = sum(len(v) for v in data.values())
    base = {
        "n_conditions": n_c,
        "n_apps": n_a,
        "n_runs": n_runs,
        "n_cells": len(data),
        "apps_dropped": len(apps) - n_a,
    }
    df_e = sum(len(v) - 1 for v in data.values())
    if not data and clean:
        return {**base, "estimable": False, "reason": "no app has values under every condition", "components": []}
    if not data or df_e == 0:
        return {**base, "estimable": False, "reason": "no cell has 2 or more repeats", "components": []}
    if n_a < 2 and n_c < 2:
        return {**base, "estimable": False, "reason": "one cell only", "components": []}
    means = {k: sum(v) / len(v) for k, v in data.items()}
    r_h = len(data) / sum(1 / len(v) for v in data.values())
    ms_e = sum(sum((y - means[k]) ** 2 for y in v) for k, v in data.items()) / df_e
    grand = sum(means.values()) / len(means)
    m_c = {c: sum(means[(c, a)] for a in complete) / n_a for c in conditions}
    m_a = {a: sum(means[(c, a)] for c in conditions) / n_c for a in complete}
    raw: dict[str, float] = {}
    dfs: dict[str, int] = {"repeat": df_e}
    if n_c >= 2 and n_a >= 2:
        ms_c = r_h * n_a * sum((m_c[c] - grand) ** 2 for c in conditions) / (n_c - 1)
        ms_a = r_h * n_c * sum((m_a[a] - grand) ** 2 for a in complete) / (n_a - 1)
        df_ca = (n_c - 1) * (n_a - 1)
        ms_ca = r_h * sum((means[(c, a)] - m_c[c] - m_a[a] + grand) ** 2 for c in conditions for a in complete) / df_ca
        raw["condition"] = (ms_c - ms_ca) / (n_a * r_h)
        raw["app"] = (ms_a - ms_ca) / (n_c * r_h)
        raw["condition_x_app"] = (ms_ca - ms_e) / r_h
        dfs.update({"condition": n_c - 1, "app": n_a - 1, "condition_x_app": df_ca})
    elif n_c >= 2:  # one app: conditions only
        ms_c = r_h * sum((m_c[c] - grand) ** 2 for c in conditions) / (n_c - 1)
        raw["condition"] = (ms_c - ms_e) / r_h
        dfs["condition"] = n_c - 1
    else:  # one condition: apps only
        ms_a = r_h * sum((m_a[a] - grand) ** 2 for a in complete) / (n_a - 1)
        raw["app"] = (ms_a - ms_e) / r_h
        dfs["app"] = n_a - 1
    raw["repeat"] = ms_e
    truncated = sorted(k for k, v in raw.items() if v < 0)
    est = {k: max(0.0, v) for k, v in raw.items()}
    total = sum(est.values())
    order = ("condition", "app", "condition_x_app", "repeat")
    components = [
        {
            "source": k,
            "variance": _r(est[k], 8),
            "raw": _r(raw[k], 8),
            "share": _r(est[k] / total, 4) if total > 0 else None,
            "truncated": k in truncated,
            "df": dfs[k],
        }
        for k in order
        if k in est
    ]
    between_app = est.get("app", 0.0) + est.get("condition_x_app", 0.0)
    within = est["repeat"]
    return {
        **base,
        "estimable": True,
        "reason": None,
        "r_harmonic": _r(r_h, 4),
        "balanced": len({len(v) for v in data.values()}) == 1,
        "components": components,
        "total": _r(total, 8),
        "truncated": truncated,
        "ceiling": total == 0,
        # The correlation of two repeats of one (condition, app) given the condition (repeatability).
        "icc_within_condition": _r(between_app / (between_app + within), 4)
        if (n_a >= 2 and between_app + within > 0)
        else None,
        "between_app_variance": _r(between_app, 8) if n_a >= 2 else None,
        "interaction_variance": _r(est["condition_x_app"], 8) if "condition_x_app" in est else None,
    }


def icc_oneway(groups: Sequence[Sequence[float]]) -> dict[str, Any]:
    """One-way random-effects ICC(1) of repeats within groups (apps), unbalanced ``r0``.

    ``ICC = (MSB - MSW) / (MSB + (r0 - 1) MSW)`` with
    ``r0 = (N - sum n_i^2 / N) / (k - 1)``; a negative estimate is reported
    as 0 (``truncated``, ``icc_raw`` keeps it). Design effect
    ``DE = 1 + (r0 - 1) ICC`` and effective n ``N / DE``.
    """
    gs = [[v for v in g if v is not None] for g in groups]
    gs = [g for g in gs if g]
    k = len(gs)
    n_total = sum(len(g) for g in gs)
    out: dict[str, Any] = {"n_apps": k, "n_runs": n_total}
    if k < 2:
        return {**out, "icc": None, "reason": "fewer than 2 apps"}
    if n_total - k < 1:
        return {**out, "icc": None, "reason": "no app has 2 or more repeats"}
    grand = sum(sum(g) for g in gs) / n_total
    msb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in gs) / (k - 1)
    msw = sum(sum((y - sum(g) / len(g)) ** 2 for y in g) for g in gs) / (n_total - k)
    r0 = (n_total - sum(len(g) ** 2 for g in gs) / n_total) / (k - 1)
    denom = msb + (r0 - 1) * msw
    if denom <= 0:
        return {**out, "icc": None, "reason": "no variance (every value identical)", "r0": _r(r0, 4)}
    raw = (msb - msw) / denom
    icc = max(0.0, raw)
    de = 1 + (r0 - 1) * icc
    return {
        **out,
        "icc": _r(icc, 4),
        "icc_raw": _r(raw, 4),
        "truncated": raw < 0,
        "msb": _r(msb, 8),
        "msw": _r(msw, 8),
        "r0": _r(r0, 4),
        "design_effect": _r(de, 4),
        "effective_n": _r(n_total / de, 2),
        "reason": None,
    }


def _icc_value(groups: list[list[float]]) -> float | None:
    k = len(groups)
    n_total = sum(len(g) for g in groups)
    if k < 2 or n_total - k < 1:
        return None
    means = [sum(g) / len(g) for g in groups]
    grand = sum(sum(g) for g in groups) / n_total
    msb = sum(len(g) * (m - grand) ** 2 for g, m in zip(groups, means, strict=True)) / (k - 1)
    msw = sum(sum((y - m) ** 2 for y in g) for g, m in zip(groups, means, strict=True)) / (n_total - k)
    r0 = (n_total - sum(len(g) ** 2 for g in groups) / n_total) / (k - 1)
    denom = msb + (r0 - 1) * msw
    if denom <= 0:
        return None
    return max(0.0, (msb - msw) / denom)


def icc_bootstrap_ci(groups: Sequence[Sequence[float]], seed: int, draws: int = BOOTSTRAP_DRAWS) -> list[float] | None:
    """Two-stage cluster-bootstrap 95% CI of ICC(1): resample apps, then each app's repeats.

    None below :data:`MIN_APPS_FOR_CI` apps, or when fewer than half the
    draws give a defined ICC (a resample with no variance).
    """
    pools = [tuple(v for v in g if v is not None) for g in groups]
    pools = [p for p in pools if p]
    k = len(pools)
    if k < MIN_APPS_FOR_CI or sum(len(p) for p in pools) - k < 1:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    draw = rng.random
    values = []
    for _ in range(draws):
        sample = []
        for _i in range(k):
            pool = pools[int(draw() * k)]
            n = len(pool)
            sample.append([pool[int(draw() * n)] for _j in range(n)])
        value = _icc_value(sample)
        if value is not None:
            values.append(value)
    if len(values) < draws // 2:
        return None
    values.sort()
    return [round(_percentile(values, 0.025), 4), round(_percentile(values, 0.975), 4)]


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """Unbiased pass@k: ``1 - C(n-c, k) / C(n, k)`` (P(at least one of k repeats passes)); None for k > n."""
    if n <= 0 or k <= 0 or k > n or c < 0 or c > n:
        return None
    if n - c < k:
        return 1.0
    return 1 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float | None:
    """pass^k: ``C(c, k) / C(n, k)`` (P(all of k repeats pass), a reliability measure); None for k > n."""
    if n <= 0 or k <= 0 or k > n or c < 0 or c > n:
        return None
    return comb(c, k) / comb(n, k)


def _resampled_mean(values: tuple[float, ...], draw) -> float:
    n = len(values)
    if n == 1:
        return values[0]
    return sum(values[int(draw() * n)] for _ in range(n)) / n


def _competition_ranks(means: dict[Any, float], *, higher_is_better: bool) -> dict[Any, int]:
    """1-based competition ranks (ties share the best rank), on values rounded to 10 decimals."""
    sign = -1 if higher_is_better else 1
    vals = {k: round(sign * v, 10) for k, v in means.items()}
    return {k: 1 + sum(1 for other in vals.values() if other < v) for k, v in vals.items()}


def rank_intervals(
    by_condition: dict[Any, dict[Any, Sequence[float]]],
    *,
    higher_is_better: bool,
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    """Bootstrap rank intervals: resample the apps every condition has (shared), then each app's repeats.

    Per condition: the observed rank of its mean of app means, the median
    rank, a 95% rank interval, P(rank 1) and P(top 3). Below
    :data:`MIN_APPS_FOR_CI` common apps there is no interval.
    """
    conds = [c for c, apps in by_condition.items() if any(apps.values())]
    common = sorted(
        set.intersection(*[{a for a, v in by_condition[c].items() if v} for c in conds]) if conds else set(),
        key=str,
    )
    out: dict[str, Any] = {"n_conditions": len(conds), "n_apps": len(common), "draws": draws, "rows": []}
    if len(conds) < 2:
        return {**out, "reason": "fewer than 2 conditions"}
    if len(common) < MIN_APPS_FOR_CI:
        return {**out, "reason": f"fewer than {MIN_APPS_FOR_CI} apps shared by every condition"}
    pools = {c: [tuple(by_condition[c][a]) for a in common] for c in conds}
    observed = {c: sum(sum(p) / len(p) for p in pools[c]) / len(common) for c in conds}
    obs_rank = _competition_ranks(observed, higher_is_better=higher_is_better)
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    draw = rng.random
    k = len(common)
    positions: dict[Any, list[int]] = defaultdict(list)
    for _ in range(draws):
        idx = [int(draw() * k) for _i in range(k)]
        means = {c: sum(_resampled_mean(pools[c][i], draw) for i in idx) / k for c in conds}
        for c, rank in _competition_ranks(means, higher_is_better=higher_is_better).items():
            positions[c].append(rank)
    rows = []
    last = draws - 1
    for c in conds:
        ranks = sorted(positions[c])
        counts = Counter(ranks)
        rows.append(
            {
                "condition_id": c,
                "mean": _r(observed[c]),
                "rank": obs_rank[c],
                "median_rank": _percentile(ranks, 0.5),
                # Ranks are integers: order statistics (outward), never an interpolated 2.025.
                "rank_ci95": [ranks[math.floor(0.025 * last)], ranks[math.ceil(0.975 * last)]],
                "p_rank_1": round(counts.get(1, 0) / draws, 4),
                "p_top_3": round(sum(v for r, v in counts.items() if r <= 3) / draws, 4),
            },
        )
    rows.sort(key=lambda r: (r["rank"], r["median_rank"]))
    return {**out, "reason": None, "rows": rows}


def precision_plan(
    components: dict[str, Any],
    *,
    apps_options: Sequence[int] = PLANNER_APPS,
    repeats_options: Sequence[int] = PLANNER_REPEATS,
) -> dict[str, Any]:
    """CI half-widths for a new design (A apps x r repeats) from observed variance components.

    Condition mean: ``z sqrt(s_b^2/A + s_w^2/(A r))`` with ``s_b^2`` the
    between-app variance within a condition (app + condition x app) and
    ``s_w^2`` the repeat variance. Paired difference of two conditions on the
    same apps: ``z sqrt((2 s_ca^2 + 2 s_w^2 / r) / A)``, the app effect
    cancels. Normal approximation: read as planning guidance only.
    """
    if not components.get("estimable"):
        return {"available": False, "reason": components.get("reason") or "not estimable", "rows": []}
    if components.get("billing"):
        return {
            "available": False,
            "reason": f"every run ran on an endpoint not billed per call ({', '.join(components['billing'])}): "
            "the billed $0 describes billing, not usage, so there is nothing to plan from",
            "rows": [],
        }
    if components.get("ceiling"):
        return {
            "available": False,
            "reason": "no variance observed (ceiling): precision cannot be planned",
            "rows": [],
        }
    s_b = components.get("between_app_variance")
    s_ca = components.get("interaction_variance")
    s_w = next((c["variance"] for c in components["components"] if c["source"] == "repeat"), None)
    if s_b is None or s_w is None:
        return {"available": False, "reason": "needs 2 or more apps", "rows": []}
    rows = []
    for a in apps_options:
        for r in repeats_options:
            mean_hw = Z95 * math.sqrt(s_b / a + s_w / (a * r))
            delta_hw = Z95 * math.sqrt((2 * s_ca + 2 * s_w / r) / a) if s_ca is not None else None
            rows.append(
                {
                    "apps": a,
                    "repeats": r,
                    "runs_per_condition": a * r,
                    "mean_half_width": _r(mean_hw),
                    "delta_half_width": _r(delta_hw),
                },
            )
    return {
        "available": True,
        "reason": None,
        "between_app_variance": s_b,
        "interaction_variance": s_ca,
        "repeat_variance": s_w,
        "rows": rows,
    }


def min_apps_for_exact_test(alpha: float = 0.05) -> int:
    """Fewest apps for which an exact two-sided sign test can reach ``p <= alpha`` (``2^(1-A)``)."""
    a = 1
    while 2 ** (1 - a) > alpha:
        a += 1
    return a


def _seed(base: int, *parts: Any) -> int:
    value = base
    for p in parts:
        value ^= zlib.crc32(str(p).encode())
    return value % (2**32)


def _cached(prefix: str, payload: Any, fn, seconds: int = _BOOT_CACHE_SECONDS):
    """``fn()`` memoised in the cache on a hash of *payload* (fn must be a pure function of it)."""
    key = f"variability:{prefix}:v{PAYLOAD_VERSION}:" + hashlib.sha256(repr(payload).encode()).hexdigest()
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001 — a cache outage only costs the recomputation
        hit = None
    if hit is not None:
        return hit["v"]
    value = fn()
    try:
        cache.set(key, {"v": value}, seconds)
    except Exception:  # noqa: BLE001
        logger.debug("variability cache write failed", exc_info=True)
    return value


# ── per-run measurements (Django) ─────────────────────────────────────


def metric_specs() -> list[MetricSpec]:
    return list(BASE_METRICS)


_RUN_FIELDS = (
    "id",
    "status",
    "outcome",
    "usable",
    "kind",
    "updated_at",
    "duration_seconds",
    "endpoint_id",
    "metrics__cost_usd",
    "metrics__list_cost_usd",
    "metrics__total_tokens",
    "metrics__requests_by_class",
    "metrics__wall_s",
    "metrics__eval",
    "validity__termination",
    "validity__truncated",
)
# Usable outcomes whose app did / did not build and start (deploy agreement).
_DEPLOYED = {"tested": True, "build_fail": False, "start_fail": False, "output_invalid": False}


def _issue_values(run_ids: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """``{run_id: {code_issues, issues_per_kloc}}`` from one issue_breakdowns call over *run_ids*, and coverage."""
    from backend.analysis.models import ToolResult
    from backend.statistics.services.study import issue_breakdowns
    from backend.statistics.services.study import study_runs

    if not run_ids or not ToolResult.objects.filter(run__generation_job_id__in=run_ids).exists():
        return {}, None
    report = issue_breakdowns(study_runs(None, run_ids=run_ids, current_only=False))
    values = {
        str(row["run_id"]): {
            "code_issues": _num(row.get("code_unique_model")),
            "issues_per_kloc": _num(row.get("unique_per_kloc")),
        }
        for row in report["per_app"]
    }
    coverage = report.get("coverage") or {}
    return values, {
        "apps_measured": len(coverage.get("apps_measured") or []),
        "tools_common": list(coverage.get("tools_common") or []),
        "tools_partial": sorted(coverage.get("tools_partial") or {}),
    }


def _tool_metrics(run_ids: list[str]) -> dict[str, dict[str, float | None]]:
    """lizard avg/max CCN and jscpd duplication % from each run's latest measured result of those tools."""
    from backend.analysis.models import ToolResult
    from backend.statistics.services.study import tool_measured

    out: dict[str, dict[str, float | None]] = defaultdict(dict)
    if not run_ids:
        return {}
    for run_id, slug, status, summary, metrics in ToolResult.objects.filter(
        id__in=ToolResult.latest_ids_per_job(run_ids),
        tool_slug__in=("lizard", "jscpd"),
    ).values_list("run__generation_job_id", "tool_slug", "status", "summary", "metrics"):
        if not tool_measured(status, summary) or not isinstance(metrics, dict):
            continue
        if slug == "lizard":
            out[str(run_id)]["avg_ccn"] = _num(metrics.get("avg_ccn"))
            out[str(run_id)]["max_ccn"] = _num(metrics.get("max_ccn"))
        else:
            out[str(run_id)]["duplication_pct"] = _num(metrics.get("duplication_percentage"))
    return dict(out)


def _run_rows(run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The metrics contract keys of every run (``.values()``), plus the endpoint's cost basis from its manifest."""
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import run_views

    rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(run_ids), 500):
        chunk = GenerationRun.objects.filter(id__in=run_ids[start : start + 500])
        for row in chunk.values(*_RUN_FIELDS):
            rows[str(row["id"])] = row
        for view in run_views(chunk):
            endpoint = (view.manifest().get("model") or {}).get("endpoint") or {}
            if view.id in rows:
                rows[view.id]["cost_basis"] = endpoint.get("cost_basis") if isinstance(endpoint, dict) else None
    return rows


def _eval(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("metrics__eval")
    return summary if isinstance(summary, dict) else {}


def _seconds(row: dict[str, Any]) -> float | None:
    wall = _num(row.get("metrics__wall_s"))
    return wall if wall is not None else _num(row.get("duration_seconds"))


def _pass_rate(row: dict[str, Any]) -> float | None:
    """The latest measured evaluation's agent pass rate (``metrics.eval``); None when it was not measured."""
    summary = _eval(row)
    if summary.get("status") != "measured" or not summary.get("measured"):
        return None
    return _num(summary.get("agent_pass_rate"))


def _passed(row: dict[str, Any]) -> bool | None:
    """Every agent check passed in the latest measured evaluation; None when it was not measured."""
    rate = _pass_rate(row)
    return None if rate is None else rate >= 1.0


def _llm_calls(row: dict[str, Any]) -> float | None:
    by_class = row.get("metrics__requests_by_class")
    if not isinstance(by_class, dict) or not by_class:
        return None
    values = [_num(v) for v in by_class.values()]
    return float(sum(v for v in values if v is not None)) if any(v is not None for v in values) else None


def _billing(row: dict[str, Any]) -> str | None:
    """The endpoint's cost basis when calls are not billed (``list_price``, ``free``); None when billed or unknown."""
    basis = row.get("cost_basis")
    return str(basis) if basis and basis != "billed" else None


def run_measurements(
    run_ids: list[str],
    store: sim.SourceStore,
    *,
    with_issues: bool = True,
) -> tuple[dict[str, dict[str, float | None]], dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Per run ``{metric: value}`` (None = unmeasured), the raw rows and the issue coverage."""
    rows = _run_rows(run_ids)
    store.load(run_ids, features=False)
    issues, coverage = _issue_values(run_ids) if with_issues else ({}, None)
    tools = _tool_metrics(run_ids)
    values: dict[str, dict[str, float | None]] = {}
    for run_id in run_ids:
        row = rows.get(run_id, {})
        loc = store.loc.get(run_id) or {}
        values[run_id] = {
            "pass_rate": _pass_rate(row),
            "cost": _num(row.get("metrics__cost_usd")),
            "list_cost": _num(row.get("metrics__list_cost_usd")),
            "total_tokens": _num(row.get("metrics__total_tokens")),
            "llm_calls": _llm_calls(row),
            "wall_seconds": _seconds(row),
            # None when the run's tree was never collected (not a 0-line app); a model that wrote nothing is 0.
            "source_loc": _num(loc.get("total_loc")),
            "source_files": _num(loc.get("source_files")),
            "code_issues": (issues.get(run_id) or {}).get("code_issues"),
            "issues_per_kloc": (issues.get(run_id) or {}).get("issues_per_kloc"),
            "avg_ccn": (tools.get(run_id) or {}).get("avg_ccn"),
            "max_ccn": (tools.get(run_id) or {}).get("max_ccn"),
            "duplication_pct": (tools.get(run_id) or {}).get("duplication_pct"),
        }
    return values, rows, coverage


# ── study scope ───────────────────────────────────────────────────────

_TERMINAL = frozenset({"completed", "cancelled", "stopped"})
SENSITIVITIES = ("exclude_truncated",)


@dataclass
class KeptRun:
    """One current run of a study cell that the variability tables keep."""

    run_id: str
    condition_id: int
    unit_key: str
    repeat: int
    fingerprint: str
    truncated: bool
    excluded: str = ""


def kept_cells(study, *, fingerprint: str = "majority", sensitivity: str | None = None):
    """The kept current runs of *study* and the selection's context.

    Selection: the main stage's current run per cell (first usable attempt, ``beyond_plan`` excluded), usable
    runs only (infra errors and exclusions are attrition, reported per condition by the study results),
    planned repeats only, the majority ``config_fingerprint`` per (condition, unit) with
    ``fingerprint="majority"``, and the (unit, repeat) cells common to every condition that kept any.
    Intention-to-treat by default; ``sensitivity="exclude_truncated"`` drops truncated runs.
    """
    from backend.generation.runs.view import current_runs

    if fingerprint not in ("all", "majority"):
        msg = "fingerprint must be 'all' or 'majority'"
        raise ValueError(msg)
    if sensitivity and sensitivity not in SENSITIVITIES:
        msg = f"sensitivity must be one of: {', '.join(SENSITIVITIES)}"
        raise ValueError(msg)
    planned = int(study.repeats or 1)
    runs = list(
        current_runs(study, "main")
        .select_related("cell__unit")
        .only(
            "id",
            "usable",
            "condition_id",
            "repeat_index",
            "config_fingerprint",
            "validity",
            "cell__repeat_index",
            "cell__unit__unit_key",
        ),
    )
    not_usable = sum(1 for r in runs if r.usable is not True)
    candidates: list[KeptRun] = []
    for r in runs:
        repeat = r.repeat_index if r.repeat_index is not None else r.cell.repeat_index
        if r.usable is not True or repeat >= planned or r.condition_id is None:
            continue
        validity = r.validity if isinstance(r.validity, dict) else {}
        candidates.append(
            KeptRun(
                run_id=str(r.pk),
                condition_id=r.condition_id,
                unit_key=r.cell.unit.unit_key,
                repeat=repeat,
                fingerprint=r.config_fingerprint or "",
                truncated=bool(validity.get("truncated")),
            ),
        )
    if fingerprint == "majority":
        per: dict[tuple[int, str], list[KeptRun]] = defaultdict(list)
        for c in candidates:
            if c.fingerprint:
                per[(c.condition_id, c.unit_key)].append(c)
        for group in per.values():
            prints = Counter(c.fingerprint for c in group)
            if len(prints) > 1:
                # Most common, ties broken by the fingerprint itself (a payload regenerates identically).
                majority = min(prints, key=lambda fp: (-prints[fp], fp))
                for c in group:
                    if c.fingerprint != majority:
                        c.excluded = "fingerprint"
    truncated_dropped = 0
    if sensitivity == "exclude_truncated":
        for c in candidates:
            if c.truncated and not c.excluded:
                c.excluded = "truncated"
                truncated_dropped += 1
    usable = [c for c in candidates if not c.excluded]
    ids = sorted({c.condition_id for c in usable})
    keys = {cid: {(c.unit_key, c.repeat) for c in usable if c.condition_id == cid} for cid in ids}
    populated = [k for k in keys.values() if k]
    common = set.intersection(*populated) if populated else set()
    kept = [c for c in usable if (c.unit_key, c.repeat) in common]
    conditions = list(study.conditions.filter(id__in=ids).select_related("model").order_by("index", "id"))
    return kept, {
        "conditions": conditions,
        "planned_repeats": planned,
        "current_runs": len(runs),
        "not_usable": not_usable,
        "excluded_fingerprint": sum(1 for c in candidates if c.excluded == "fingerprint"),
        "excluded_truncated": truncated_dropped,
        "outside_common_cells": len(usable) - len(kept),
        "fingerprint": fingerprint,
        "sensitivity": sensitivity or None,
        "interim": study.status not in _TERMINAL,
    }


def _payload_key(study, fingerprint: str, sensitivity: str | None) -> str:
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import ToolResult
    from backend.generation.models import GenerationRun

    unfinished = (ToolResult.Status.PENDING, ToolResult.Status.RUNNING)
    run_finished = (AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL, AnalysisRun.Status.FAILED)
    runs = GenerationRun.objects.filter(study=study).aggregate(
        n=Count("id"),
        at=Max("updated_at"),
        # Evaluation mirrors and backfills may add metrics keys without touching updated_at.
        metrics=MD5(StringAgg(MD5(Cast("metrics", TextField())), Value(""), order_by="id")),
    )
    # A tool result is created running and finished in place, and the analysis is
    # finalized after it: neither adds a row, so count the finished ones.
    results = ToolResult.objects.filter(run__generation_job__study=study).aggregate(
        n=Count("id"),
        at=Max("created_at"),
        finished=Count("id", filter=~Q(status__in=unfinished)),
    )
    analyses = AnalysisRun.objects.filter(generation_job__study=study).aggregate(
        n=Count("id"),
        finished=Count("id", filter=Q(status__in=run_finished)),
    )
    parts = (
        PAYLOAD_VERSION,
        sim.ALGO_VERSION,
        str(study.id),
        study.status,
        str(study.updated_at),
        fingerprint,
        sensitivity or "",
        runs["n"],
        str(runs["at"]),
        runs["metrics"],
        results["n"],
        str(results["at"]),
        results["finished"],
        analyses["n"],
        analyses["finished"],
    )
    return "variability:study:" + hashlib.sha256(repr(parts).encode()).hexdigest()


def study_variability(study, *, fingerprint: str = "majority", sensitivity: str | None = None) -> dict[str, Any]:
    """The study's run-to-run variability payload (cached on its runs' and results' state).

    Raises ``ValueError`` for an unknown *fingerprint* or *sensitivity* (the API answers 400).
    """
    if fingerprint not in ("all", "majority"):
        msg = "fingerprint must be 'all' or 'majority'"
        raise ValueError(msg)
    if sensitivity and sensitivity not in SENSITIVITIES:
        msg = f"sensitivity must be one of: {', '.join(SENSITIVITIES)}"
        raise ValueError(msg)
    key = _payload_key(study, fingerprint, sensitivity)
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001
        hit = None
    if hit is not None:
        return hit
    payload = _compute_study(study, fingerprint=fingerprint, sensitivity=sensitivity)
    try:
        cache.set(key, payload, _PAYLOAD_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("variability payload cache write failed", exc_info=True)
    return payload


def _checks_by_run(run_ids: list[str], rows: dict[str, dict[str, Any]]) -> dict[str, tuple[tuple, dict]]:
    """Per run: (tester identity, ``{check_id: (passed, category)}``) of its latest measured evaluation.

    Infra checks and the evaluation's flaky checks are left out (Appendix A #8); errored and skipped checks
    say nothing about the app and are left out too.
    """
    from backend.generation.models import RunEvaluation

    wanted = {}
    for run_id in run_ids:
        summary = _eval(rows.get(run_id, {}))
        if summary.get("status") == "measured" and summary.get("evaluation_id"):
            wanted[int(summary["evaluation_id"])] = (run_id, str(summary.get("eval_suite") or ""))
    out: dict[str, tuple[tuple, dict]] = {}
    for evaluation_id, result in RunEvaluation.objects.filter(id__in=list(wanted)).values_list("id", "result"):
        run_id, suite = wanted[evaluation_id]
        data = result if isinstance(result, dict) else {}
        flaky = set(data.get("flaky_checks") or [])
        checks = {}
        for c in data.get("checks") or []:
            if not isinstance(c, dict) or not c.get("id") or c.get("category") == "infra" or c["id"] in flaky:
                continue
            if c.get("status") in ("pass", "fail"):
                checks[str(c["id"])] = (c["status"] == "pass", str(c.get("category") or ""))
        if checks:
            out[run_id] = ((suite, data.get("tester_version"), data.get("compiler_version")), checks)
    return out


def _cell_checks(run_ids: list[str], checks: dict[str, tuple[tuple, dict]]) -> dict[str, Any] | None:
    """Check-level agreement of a cell's repeats (one suite and tester version; checks every repeat ran)."""
    graded = [(r, checks[r]) for r in run_ids if r in checks]
    if len(graded) < 2:
        return None
    if len({tester for _r, (tester, _c) in graded}) > 1:
        return {"comparable": False, "reason": "repeats were graded by different suite or tester versions"}
    sets = [c for _r, (_t, c) in graded]
    names = set.intersection(*[set(c) for c in sets])
    disagree = sorted(n for n in names if len({c[n][0] for c in sets}) > 1)
    return {
        "comparable": True,
        "n_checks": len(names),
        "disagree": disagree,
        "all_agree": not disagree,
        "categories": {n: sets[0][n][1] for n in disagree},
    }


def _summarize_pairs(pairs: list[dict[str, Any]], keys: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"n_pairs": len(pairs)}
    for k in keys:
        out[f"median_{k}"] = _r(median(p.get(k) for p in pairs), 4)
    return out


_SIM_KEYS = ("shingle_jaccard", "line_jaccard", "path_jaccard", "ast_jaccard", "identical_files")


def _unit_labels(run_ids: list[str]) -> dict[str, tuple[str, str]]:
    """Per run: (task slug, task title) of its unit, through ``run_views``."""
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import run_views

    out = {}
    for view in run_views(GenerationRun.objects.filter(id__in=run_ids)):
        task = view.task
        out[view.id] = (task.ref, task.title) if task is not None else (view.unit_key or "", view.unit_key or "")
    return out


def _compute_study(study, *, fingerprint: str, sensitivity: str | None) -> dict[str, Any]:
    from backend.statistics.services.runs import condition_label

    kept, ctx = kept_cells(study, fingerprint=fingerprint, sensitivity=sensitivity)
    conditions = ctx["conditions"]
    interim = ctx["interim"]
    labels = {c.id: condition_label(c) for c in conditions}
    base_seed = study.id.int % (2**32)
    store = sim.SourceStore()

    by_cell: dict[tuple[int, str], list[KeptRun]] = defaultdict(list)
    for c in sorted(kept, key=lambda c: (c.condition_id, c.unit_key, c.repeat)):
        by_cell[(c.condition_id, c.unit_key)].append(c)
    run_ids = [c.run_id for cells in by_cell.values() for c in cells]
    values, rows, coverage = run_measurements(run_ids, store)
    checks = _checks_by_run(run_ids, rows)
    units = _unit_labels(run_ids)
    specs = metric_specs()
    notes: list[str] = [NOTE_DESCRIPTIVE, NOTE_GRADING]
    if interim:
        notes.insert(0, NOTE_INTERIM)
    if ctx["sensitivity"] == "exclude_truncated":
        notes.append(NOTE_SENSITIVITY_TRUNCATED.format(n=ctx["excluded_truncated"]))
    else:
        notes.append(NOTE_ITT)
    if ctx["not_usable"]:
        notes.append(
            f"{ctx['not_usable']} of {ctx['current_runs']} current runs are not usable (infra error, excluded or "
            "no outcome yet): they are attrition, not data, and are left out here.",
        )
    if ctx["excluded_fingerprint"]:
        notes.append(
            f"{ctx['excluded_fingerprint']} runs left out: not on the majority configuration fingerprint of their "
            "(condition, unit).",
        )
    # Endpoints not billed per call: their billed cost is $0 by billing, not a measured floor.
    billed = {j: source for j in run_ids if (source := _billing(rows.get(j, {})))}

    def billing_marker(key: str, ids: Iterable[str]) -> list[str] | None:
        """The cost bases when every run with a *key* value ran on an endpoint not billed per call, else None."""
        if key != "cost":
            return None
        measured = [j for j in ids if values[j].get(key) is not None]
        if not measured or any(j not in billed for j in measured):
            return None
        return sorted({billed[j] for j in measured})

    repeats = [len(v) for v in by_cell.values()]
    with_repeats = [k for k, v in by_cell.items() if len(v) >= 2]
    design = {
        "n_conditions": len({k[0] for k in by_cell}),
        "n_units": len({k[1] for k in by_cell}),
        "n_cells": len(by_cell),
        "n_runs": len(run_ids),
        "cells_with_repeats": len(with_repeats),
        "repeats_min": min(repeats) if repeats else None,
        "repeats_max": max(repeats) if repeats else None,
        "repeats_median": median(repeats) if repeats else None,
        "planned_repeats": ctx["planned_repeats"],
    }
    if ctx["planned_repeats"] < 2 or not with_repeats:
        notes.append(NOTE_SINGLE_TRIAL)
    if any(r == 2 for r in repeats):
        notes.append(NOTE_TWO_REPEATS)
    if len({rows.get(j, {}).get("endpoint_id") for j in run_ids} - {None}) > 1:
        notes.append(NOTE_WALL)

    # ── per cell ──
    cell_rows = []
    for (cid, unit_key), cells in by_cell.items():
        ids = [c.run_id for c in cells]
        stats = {}
        vals_out = {}
        for spec in specs:
            series = [values[j].get(spec.key) for j in ids]
            if all(v is None for v in series):
                continue
            vals_out[spec.key] = [_r(v) for v in series]
            stats[spec.key] = describe(series, ratio=spec.ratio)
        # Pass is known only for measured evaluations: unmeasured repeats are left out, never counted as fails.
        passed = [p for p in (_passed(rows.get(j, {})) for j in ids) if p is not None]
        deployed = [_DEPLOYED.get(rows.get(j, {}).get("outcome") or "") for j in ids]
        deployed_known = [d for d in deployed if isinstance(d, bool)]
        task, title = units.get(ids[0], (unit_key, unit_key))
        cell_rows.append(
            {
                "condition_id": cid,
                "unit_key": unit_key,
                "task": task,
                "task_title": title,
                "n": len(cells),
                "run_ids": ids,
                "repeat_indexes": [c.repeat for c in cells],
                "values": vals_out,
                "stats": stats,
                "n_pass_known": len(passed),
                "k_passed": sum(1 for p in passed if p),
                "pass_agree": (len(set(passed)) == 1) if len(passed) >= 2 else None,
                "deploy_agree": (len(set(deployed_known)) == 1)
                if len(deployed_known) >= 2 and len(deployed_known) == len(ids)
                else None,
                "checks": _cell_checks(ids, checks) if len(cells) >= 2 else None,
                "similarity": None,
                "finding_overlap": None,
            },
        )
    cell_index = {(r["condition_id"], r["unit_key"]): r for r in cell_rows}

    # ── per condition dispersion ──
    condition_rows = []
    ceilings = []
    for cond in conditions:
        mine = [r for r in cell_rows if r["condition_id"] == cond.id]
        if not mine:
            continue
        dispersion = {}
        for spec in specs:
            groups = [[values[j].get(spec.key) for j in r["run_ids"]] for r in mine]
            flat = [v for g in groups for v in g if v is not None]
            if not flat:
                continue
            psd, df = pooled_sd(groups)
            cvs = [r["stats"][spec.key]["cv"] for r in mine if spec.key in r["stats"] and r["n"] >= 2]
            marker = billing_marker(spec.key, [j for r in mine for j in r["run_ids"]])
            dispersion[spec.key] = {
                "n_runs": len(flat),
                "n_cells": sum(1 for g in groups if sum(1 for v in g if v is not None) >= 2),
                "mean": _r(sum(flat) / len(flat)),
                "pooled_sd": _r(psd),
                "df": df,
                "median_cv": _r(median(cvs), 4) if spec.ratio else None,
                # A billed $0 under a subscription is billing, not a floor: ``billing`` says why.
                "ceiling": len(flat) >= 2 and len(set(flat)) == 1 and marker is None,
                "billing": marker,
            }
        repeated = [r for r in mine if r["pass_agree"] is not None]
        agree = [r for r in repeated if r["pass_agree"]]
        deploy = [r for r in repeated if r["deploy_agree"] is not None]
        condition_rows.append(
            {
                "condition_id": cond.id,
                "label": labels[cond.id],
                "model": cond.model.model_name if cond.model_id else "",
                "n_units": len(mine),
                "n_cells": len(mine),
                "n_runs": sum(r["n"] for r in mine),
                "cells_with_repeats": sum(1 for r in mine if r["n"] >= 2),
                "pass_agreement": {
                    "cells": len(repeated),
                    "agree": len(agree),
                    "share": _r(len(agree) / len(repeated), 4) if repeated else None,
                },
                "deploy_agreement": {
                    "cells": len(deploy),
                    "agree": sum(1 for r in deploy if r["deploy_agree"]),
                    "share": _r(sum(1 for r in deploy if r["deploy_agree"]) / len(deploy), 4) if deploy else None,
                },
                "dispersion": dispersion,
            },
        )
    billing = Counter(billed.values())
    if billing:
        notes.append(
            NOTE_SUBSCRIPTION.format(n=sum(billing.values()), total=len(run_ids), sources=", ".join(sorted(billing))),
        )
    billing_by_metric = {spec.key: m for spec in specs if (m := billing_marker(spec.key, run_ids))}
    for spec in specs:
        flat = [values[j].get(spec.key) for j in run_ids]
        flat = [v for v in flat if v is not None]
        if len(flat) >= 2 and len(set(flat)) == 1:
            if spec.key in billing_by_metric:
                continue  # $0 billed by subscription is not a floor effect (the note above says so)
            ceilings.append(spec.key)
            notes.append(
                f"{spec.label}: every kept run scored {flat[0]:g} — no run-to-run variance to decompose "
                "(ceiling/floor).",
            )

    # ── variance components and ICC ──
    components = {}
    icc = {}
    for spec in specs:
        data = {
            k: [values[j].get(spec.key) for j in r["run_ids"]] for k, r in cell_index.items() if spec.key in r["stats"]
        }
        if not data:
            continue
        comp = variance_components(data)
        comp["interim"] = interim
        if spec.key in billing_by_metric:
            comp["ceiling"] = False
            comp["billing"] = billing_by_metric[spec.key]
        components[spec.key] = comp
        per_cond = []
        for cond in conditions:
            groups = [v for (c, _a), v in data.items() if c == cond.id]
            if not groups:
                continue
            row = icc_oneway(groups)
            row["condition_id"] = cond.id
            if spec.core and row.get("icc") is not None:
                clean = [tuple(v for v in g if v is not None) for g in groups]
                seed = _seed(base_seed, cond.id, spec.key)
                row["ci95"] = _cached(
                    "icc",
                    (seed, BOOTSTRAP_DRAWS, clean),
                    lambda clean=clean, seed=seed: icc_bootstrap_ci(clean, seed),
                )
            else:
                row["ci95"] = None
            row["interim"] = interim
            per_cond.append(row)
        icc[spec.key] = per_cond

    # ── pass@k, checks ──
    k_max = max((r["n_pass_known"] for r in cell_rows), default=0)
    pass_rows = []
    for cond in conditions:
        mine = [r for r in cell_rows if r["condition_id"] == cond.id]
        if not mine:
            continue
        by_k = []
        for k in range(1, k_max + 1):
            eligible = [r for r in mine if r["n_pass_known"] >= k]
            if not eligible:
                continue
            at = [pass_at_k(r["n_pass_known"], r["k_passed"], k) for r in eligible]
            hat = [pass_hat_k(r["n_pass_known"], r["k_passed"], k) for r in eligible]
            entry = {
                "k": k,
                "n_cells": len(eligible),
                "pass_at_k": _r(sum(at) / len(at), 4),
                "pass_hat_k": _r(sum(hat) / len(hat), 4),
                "pass_at_k_ci95": None,
            }
            if len(eligible) >= MIN_APPS_FOR_CI:
                seed = _seed(base_seed, cond.id, "pass_at_k", k)
                entry["pass_at_k_ci95"] = _cached(
                    "passk",
                    (seed, BOOTSTRAP_DRAWS, at),
                    lambda at=at, seed=seed: _mean_ci(at, seed),
                )
            by_k.append(entry)
        pass_rows.append({"condition_id": cond.id, "n_cells": len(mine), "by_k": by_k})
    checks_rows = []
    flaky: dict[str, dict[str, Any]] = {}
    for cond in conditions:
        compared = [r for r in cell_rows if r["condition_id"] == cond.id and r["checks"] and r["checks"]["comparable"]]
        if not compared:
            continue
        checks_rows.append(
            {
                "condition_id": cond.id,
                "cells": len(compared),
                "cells_all_agree": sum(1 for r in compared if r["checks"]["all_agree"]),
                "share_all_agree": _r(sum(1 for r in compared if r["checks"]["all_agree"]) / len(compared), 4),
                "check_cells": sum(r["checks"]["n_checks"] for r in compared),
                "check_cells_agree": sum(r["checks"]["n_checks"] - len(r["checks"]["disagree"]) for r in compared),
            },
        )
        for r in compared:
            for name in r["checks"]["disagree"]:
                entry = flaky.setdefault(
                    name,
                    {"name": name, "category": r["checks"]["categories"].get(name, ""), "cells": 0, "conditions": []},
                )
                entry["cells"] += 1
                if cond.id not in entry["conditions"]:
                    entry["conditions"].append(cond.id)
    compared_cells = sum(row["cells"] for row in checks_rows)

    # ── code similarity ──
    within_pairs: list[tuple[str, str, tuple[int, str]]] = []
    for key, r in cell_index.items():
        ids = r["run_ids"]
        within_pairs.extend((ids[i], ids[j], key) for i in range(len(ids)) for j in range(i + 1, len(ids)))
    reference_pairs: list[tuple[str, str, int, int]] = []
    slices: dict[tuple[str, int], dict[int, str]] = defaultdict(dict)
    for c in kept:
        slices[(c.unit_key, c.repeat)][c.condition_id] = c.run_id
    cond_order = [c.id for c in conditions]
    for slice_runs in slices.values():
        present = [cid for cid in cond_order if cid in slice_runs]
        if len(present) < 2:
            continue
        seen = set()
        for i, cid in enumerate(present):
            other = present[(i + 1) % len(present)]
            pair = tuple(sorted((cid, other)))
            if other == cid or pair in seen:
                continue
            seen.add(pair)
            reference_pairs.append((slice_runs[pair[0]], slice_runs[pair[1]], pair[0], pair[1]))
    sims = sim.pair_similarities([(a, b) for a, b, _k in within_pairs], store)
    ref_sims = sim.pair_similarities([(a, b) for a, b, _c1, _c2 in reference_pairs], store)
    within_by_cell: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for a, b, key in within_pairs:
        within_by_cell[key].append({"run_a": a, "run_b": b, **sims[(a, b)]})
    for key, pairs in within_by_cell.items():
        cell_index[key]["similarity"] = {**_summarize_pairs(pairs, _SIM_KEYS), "pairs": pairs}
    ref_by_cond: dict[int, list[dict]] = defaultdict(list)
    for a, b, c1, c2 in reference_pairs:
        ref_by_cond[c1].append(ref_sims[(a, b)])
        ref_by_cond[c2].append(ref_sims[(a, b)])
    sim_rows = []
    for cond in conditions:
        within = [p for (cid, _a), pairs in within_by_cell.items() if cid == cond.id for p in pairs]
        ref = ref_by_cond.get(cond.id, [])
        if not within and not ref:
            continue
        w = _summarize_pairs(within, _SIM_KEYS)
        rf = _summarize_pairs(ref, _SIM_KEYS)
        sim_rows.append(
            {
                "condition_id": cond.id,
                "within": w,
                "reference": rf,
                "excess_shingle": _r(w["median_shingle_jaccard"] - rf["median_shingle_jaccard"], 4)
                if w["median_shingle_jaccard"] is not None and rf["median_shingle_jaccard"] is not None
                else None,
            },
        )
    all_within = [p for pairs in within_by_cell.values() for p in pairs]
    all_ref = list(ref_sims.values())
    if all_ref:
        notes.append(NOTE_REFERENCE)

    # ── finding overlap ──
    fsets = sim.finding_sets(run_ids)
    overlap_by_cell: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for a, b, key in within_pairs:
        ov = sim.finding_overlap(fsets, a, b)
        if ov is not None:
            overlap_by_cell[key].append({"run_a": a, "run_b": b, **ov})
    for key, r in cell_index.items():
        if r["n"] < 2:
            continue
        pairs = overlap_by_cell.get(key, [])
        r["finding_overlap"] = (
            {
                "n_pairs": len(pairs),
                "median_code_jaccard": _r(median(p["code"]["jaccard"] for p in pairs), 4),
                "median_dependency_jaccard": _r(median(p["dependency"]["jaccard"] for p in pairs), 4),
            }
            if pairs
            else None
        )
    overlap_rows = []
    for cond in conditions:
        pairs = [p for (cid, _a), ps in overlap_by_cell.items() if cid == cond.id for p in ps]
        n_within = sum(1 for _a, _b, key in within_pairs if key[0] == cond.id)
        if not n_within:
            continue
        code_j = [p["code"]["jaccard"] for p in pairs if p["code"]["jaccard"] is not None]
        dep_j = [p["dependency"]["jaccard"] for p in pairs if p["dependency"]["jaccard"] is not None]
        overlap_rows.append(
            {
                "condition_id": cond.id,
                "n_pairs": n_within,
                "n_pairs_measured": len(pairs),
                "code": {
                    "n": len(code_j),
                    "median_jaccard": _r(median(code_j), 4),
                    "both_empty": sum(1 for p in pairs if p["code"]["jaccard"] is None),
                },
                "dependency": {
                    "n": len(dep_j),
                    "median_jaccard": _r(median(dep_j), 4),
                    "both_empty": sum(1 for p in pairs if p["dependency"]["jaccard"] is None),
                },
            },
        )
    tool_acc: dict[tuple[str, str], list[float | None]] = defaultdict(list)
    for pairs in overlap_by_cell.values():
        for p in pairs:
            for t in p["per_tool"]:
                for kind in ("code", "dependency"):
                    if t[kind]["a"] or t[kind]["b"]:
                        tool_acc[(t["tool"], kind)].append(t[kind]["jaccard"])
    by_tool = [
        {"tool": tool, "kind": kind, "n_pairs": len(js), "median_jaccard": _r(median(js), 4)}
        for (tool, kind), js in sorted(tool_acc.items())
    ]

    # ── rank stability ──
    ranks = {}
    rank_metrics = [("pass_rate", True)]
    continuous = next(
        (
            m
            for m in ("issues_per_kloc", "cost")
            if all(spec_has(cell_rows, m, cid) for cid in {k[0] for k in by_cell})
        ),
        None,
    )
    if continuous:
        rank_metrics.append((continuous, False))
    for metric, higher in rank_metrics:
        data: dict[int, dict[str, list[float]]] = defaultdict(dict)
        for (cid, unit_key), r in cell_index.items():
            vals = [v for v in r["values"].get(metric, []) if v is not None]
            if vals:
                data[cid][unit_key] = vals
        seed = _seed(base_seed, "rank", metric)
        frozen = {c: dict(sorted(a.items())) for c, a in sorted(data.items())}
        block = _cached(
            "rank",
            (seed, BOOTSTRAP_DRAWS, metric, higher, frozen),
            lambda frozen=frozen, higher=higher, seed=seed: rank_intervals(
                frozen,
                higher_is_better=higher,
                seed=seed,
            ),
        )
        ranks[metric] = {**block, "metric": metric, "direction": "higher" if higher else "lower", "interim": interim}

    # ── precision planner ──
    planner = None
    planner_reason = None
    if interim:
        planner_reason = (
            "Withheld while the study is running (optional-stopping guard): plan the next study from the finished one."
        )
    else:
        planner = {
            "metrics": {
                spec.key: precision_plan(components[spec.key])
                for spec in specs
                if spec.core and spec.key in components
            },
            "min_apps_exact_test": min_apps_for_exact_test(0.05),
            "z": Z95,
            "basis": "normal approximation from this study's variance components (planning guidance only)",
        }

    if coverage is not None and coverage["apps_measured"] < len(run_ids):
        notes.append(
            f"Code issues: {coverage['apps_measured']} of {len(run_ids)} kept runs measured by the common "
            f"instrument set ({', '.join(coverage['tools_common']) or 'no tool'}); the rest show —.",
        )

    return {
        "study_id": str(study.id),
        "status": study.status,
        "kind": study.kind,
        "interim": interim,
        "generated_at": timezone.now(),
        "filters": {
            "stage": "main",
            "fingerprint": ctx["fingerprint"],
            "sensitivity": ctx["sensitivity"],
            "itt": ctx["sensitivity"] is None,
            "planned_only": True,
            "common_cells": True,
        },
        "selection": {
            "current_runs": ctx["current_runs"],
            "not_usable": ctx["not_usable"],
            "excluded_fingerprint": ctx["excluded_fingerprint"],
            "excluded_truncated": ctx["excluded_truncated"],
            "outside_common_cells": ctx["outside_common_cells"],
            "kept": len(run_ids),
        },
        "design": design,
        "metrics": [
            {
                "key": s.key,
                "label": s.label,
                "unit": s.unit,
                "ratio_scale": s.ratio,
                "better": s.better,
                "source": s.source,
                "core": s.core,
                "n_runs": sum(1 for j in run_ids if values[j].get(s.key) is not None),
            }
            for s in specs
        ],
        "conditions": condition_rows,
        "cells": cell_rows,
        "components": components,
        "icc": icc,
        "pass_at_k": {"k_max": k_max, "conditions": pass_rows, "interim": interim},
        "checks": {
            "cells": compared_cells,
            "conditions": checks_rows,
            "flaky": sorted(flaky.values(), key=lambda f: (-f["cells"], f["name"])),
            "interim": interim,
        },
        "similarity": {
            "algo_version": sim.ALGO_VERSION,
            "shingle_k": sim.SHINGLE_K,
            "conditions": sim_rows,
            "within": _summarize_pairs(all_within, _SIM_KEYS),
            "reference": _summarize_pairs(all_ref, _SIM_KEYS),
            "interim": interim,
        },
        "finding_overlap": {
            "measured": bool(fsets.measured),
            "conditions": overlap_rows,
            "by_tool": by_tool,
            "interim": interim,
        },
        "rank_stability": ranks,
        "planner": planner,
        "planner_reason": planner_reason,
        "issue_coverage": coverage,
        "ceilings": ceilings,
        # Kept runs on endpoints not billed per call; ``metrics`` lists those whose every value is a billed $0.
        "billing": {
            "sources": sorted(billing),
            "n_runs": sum(billing.values()),
            "n_kept": len(run_ids),
            "metrics": sorted(billing_by_metric),
        }
        if billing
        else None,
        "notes": notes,
    }


def spec_has(cell_rows: list[dict[str, Any]], metric: str, condition_id: int) -> bool:
    """Whether every cell of *condition_id* has at least one value of *metric*."""
    mine = [r for r in cell_rows if r["condition_id"] == condition_id]
    return bool(mine) and all(any(v is not None for v in r["values"].get(metric, [])) for r in mine)


def _mean_ci(values: list[float], seed: int, draws: int = BOOTSTRAP_DRAWS) -> list[float] | None:
    """Percentile 95% CI of a mean over units (apps), resampling the units."""
    n = len(values)
    if n < MIN_APPS_FOR_CI:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not crypto
    draw = rng.random
    means = sorted(sum(values[int(draw() * n)] for _ in range(n)) / n for _d in range(draws))
    return [round(_percentile(means, 0.025), 4), round(_percentile(means, 0.975), 4)]


CSV_STAT_KEYS = ("mean", "sd", "min", "max", "cv")


def study_variability_csv(payload: dict[str, Any]) -> str:
    """One row per (condition, unit) cell: n, pass agreement, per-metric mean/sd/min/max/cv, similarity, overlap."""
    from backend.export.services.csv_safe import safe_row

    labels = {r["condition_id"]: r["label"] for r in payload["conditions"]}
    metrics = [m["key"] for m in payload["metrics"] if m["n_runs"]]
    header = [
        "condition_id",
        "condition_label",
        "unit_key",
        "task",
        "n_repeats",
        "n_pass_known",
        "k_passed",
        "pass_agree",
        "deploy_agree",
        "checks_all_agree",
        *(f"{m}_{s}" for m in metrics for s in CSV_STAT_KEYS),
        "within_shingle_jaccard",
        "within_line_jaccard",
        "within_path_jaccard",
        "code_finding_jaccard",
        "dependency_finding_jaccard",
        "run_ids",
        "interim",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)

    def cell(value: Any) -> Any:
        return "" if value is None else value

    for r in payload["cells"]:
        stats = r["stats"]
        sim_row = r.get("similarity") or {}
        ov = r.get("finding_overlap") or {}
        checks = r.get("checks") or {}
        row = [
            r["condition_id"],
            labels.get(r["condition_id"], ""),
            r["unit_key"],
            r["task"],
            r["n"],
            r["n_pass_known"],
            r["k_passed"],
            cell(r["pass_agree"]),
            cell(r["deploy_agree"]),
            cell(checks.get("all_agree") if checks.get("comparable") else None),
            *(cell((stats.get(m) or {}).get(s)) for m in metrics for s in CSV_STAT_KEYS),
            cell(sim_row.get("median_shingle_jaccard")),
            cell(sim_row.get("median_line_jaccard")),
            cell(sim_row.get("median_path_jaccard")),
            cell(ov.get("median_code_jaccard")),
            cell(ov.get("median_dependency_jaccard")),
            " ".join(r["run_ids"]),
            payload["interim"],
        ]
        writer.writerow(safe_row(row))
    return buffer.getvalue()


# ── same-setup groups (run and model scopes) ──────────────────────────


def _current_in_cells(members: list[Any]) -> set[str]:
    """Ids of the members that are their cell's current run (``runs.view.current_runs``)."""
    from backend.generation.runs.view import current_runs

    current: set[str] = set()
    for study_id, stage in {(m.study_id, m.cell.stage) for m in members if m.cell_id is not None and m.study_id}:
        current.update(str(pk) for pk in current_runs(study_id, stage).values_list("pk", flat=True))
    return current


def _relation(member, self_run, current: set[str]) -> str:
    """How *member* relates to *self_run*: self, retry (another attempt of its cell), replay, rerun or replicate.

    A replay shares the ``run_fingerprint`` (same manifest and seed); a rerun is outside any designed cell, a
    beyond-plan run, or an attempt its cell's current run replaced.
    """
    if member.pk == self_run.pk:
        return "self"
    if self_run.cell_id is not None and member.cell_id == self_run.cell_id:
        return "retry"
    if member.run_fingerprint and member.run_fingerprint == self_run.run_fingerprint:
        return "replay"
    if member.cell_id is None or member.beyond_plan or str(member.pk) not in current:
        return "rerun"
    return "replicate"


def _group_queryset(run, user):
    """The visible runs sharing *run*'s ``config_fingerprint``, and the grouping descriptor."""
    from backend.generation.models import GenerationRun

    qs = GenerationRun.objects.filter(created_by=user)
    if run.config_fingerprint:
        return qs.filter(config_fingerprint=run.config_fingerprint), {
            "key": "config_fingerprint",
            "value": run.config_fingerprint,
        }
    return qs.filter(id=run.id), {"key": None, "value": None}


def run_replicates(run, user, *, cap: int = MAX_REPLICATES) -> dict[str, Any]:
    """Every visible run sharing *run*'s configuration fingerprint, with relation, metrics and similarity to it."""
    from backend.statistics.services.runs import condition_label

    qs, grouping = _group_queryset(run, user)
    total = qs.count()
    related = ("study", "condition__model", "cell")
    members = list(qs.exclude(id=run.id).select_related(*related).order_by("-created_at")[: max(0, cap - 1)])
    self_run = type(run).objects.select_related(*related).get(id=run.id)
    members = sorted([self_run, *members], key=lambda m: (m.created_at, str(m.pk)))
    notes: list[str] = [NOTE_GRADING]
    if grouping["key"] is None:
        notes.append("This run has no configuration fingerprint: no same-setup group.")
    if total > cap:
        notes.append(f"The group has {total} runs; the {cap} most recent (this one included) are shown.")
    current = _current_in_cells(members)
    ids = [str(m.pk) for m in members]
    completed = [str(m.pk) for m in members if m.status == "completed"]
    store = sim.SourceStore()
    values, rows, coverage = run_measurements(ids, store, with_issues=bool(completed))
    analyzed = coverage is not None and coverage["apps_measured"] > 0
    self_id = str(run.pk)
    sims = (
        sim.pair_similarities([(self_id, j) for j in completed if j != self_id], store) if self_id in completed else {}
    )
    out_members = []
    for m in members:
        mid = str(m.pk)
        v = values.get(mid, {})
        s = sims.get((self_id, mid))
        validity = m.validity if isinstance(m.validity, dict) else {}
        out_members.append(
            {
                "run_id": mid,
                "is_self": mid == self_id,
                "relation": _relation(m, self_run, current),
                "seed": m.seed,
                "repeat_index": m.repeat_index,
                "cell_attempt": m.cell_attempt,
                "beyond_plan": m.beyond_plan,
                "study_id": str(m.study_id) if m.study_id else None,
                "study_name": m.study.name if m.study_id else None,
                "condition_label": condition_label(m.condition) if m.condition_id else None,
                "created_at": m.created_at,
                "status": m.status,
                "outcome": m.outcome or None,
                "usable": m.usable,
                "pass_rate": v.get("pass_rate"),
                "source_loc": v.get("source_loc"),
                "source_files": v.get("source_files"),
                "cost": v.get("cost"),
                "total_tokens": v.get("total_tokens"),
                "llm_calls": v.get("llm_calls"),
                "wall_seconds": v.get("wall_seconds"),
                "termination": validity.get("termination"),
                "truncated": bool(validity.get("truncated")),
                "code_issues": v.get("code_issues"),
                "issues_per_kloc": v.get("issues_per_kloc"),
                "findings_measured": v.get("code_issues") is not None,
                "similarity_to_self": {
                    "path_jaccard": s.get("path_jaccard"),
                    "shingle_jaccard": s.get("shingle_jaccard"),
                    "line_jaccard": s.get("line_jaccard"),
                    "identical_files": s.get("identical_files"),
                }
                if s
                else None,
            },
        )
    summary = {}
    for key in REPLICATE_METRICS:
        spec = _SPECS[key]
        d = describe([values.get(j, {}).get(key) for j in completed], ratio=spec.ratio)
        summary[key] = {k: d[k] for k in ("n", "mean", "sd", "min", "max", "cv")}
    determinism = []
    for m in out_members:
        if m["relation"] in ("replay", "retry") and m["status"] == "completed" and self_run.status == "completed":
            probe = sim.call_divergence(self_id, m["run_id"])
            determinism.append({"run_id": m["run_id"], "relation": m["relation"], **probe})
    if len(members) > 1:
        notes.append(
            "The summary pools every completed member (replicates, retries, replays and reruns): replays and "
            "retries share the seed and reruns ran outside a designed sample, so it is descriptive, not a design "
            "estimate.",
        )
    else:
        notes.append("No other visible run shares this setup yet.")
    ungraded = sum(1 for j in completed if values.get(j, {}).get("pass_rate") is None)
    if ungraded:
        notes.append(f"{ungraded} of {len(completed)} completed members have no measured evaluation (—).")
    if completed and not analyzed:
        notes.append("No member has been analyzed: code issues are unmeasured (—).")
    elif analyzed and coverage["apps_measured"] < len(completed):
        notes.append(
            f"Code issues: one instrument set over the group's analyzed runs ({coverage['apps_measured']} of "
            f"{len(completed)}; tools: {', '.join(coverage['tools_common']) or 'none'}); the others show —.",
        )
    billed = sorted({source for j in ids if (source := _billing(rows.get(j, {})))})
    if billed:
        notes.append(
            f"Runs on endpoints not billed per call ({', '.join(billed)}) record a billed cost of $0: Cost describes "
            "billing, not usage (each run's page shows its list-price equivalent).",
        )
    if determinism:
        notes.append(
            "Replays and retries send the same seed; the determinism probe compares the stored call artifacts of "
            "each run's last attempt in order (content truncated at 8000 chars).",
        )
    return {
        "run_id": self_id,
        "grouping": grouping,
        "n": total,
        "members": out_members,
        "summary": summary,
        "notes": notes,
        "determinism": determinism,
        "issue_coverage": coverage,
    }


def _model_payload_key(model, user) -> str:
    from backend.generation.models import GenerationRun

    agg = GenerationRun.objects.filter(created_by=user, model=model).aggregate(n=Count("id"), at=Max("updated_at"))
    parts = (PAYLOAD_VERSION, sim.ALGO_VERSION, model.pk, getattr(user, "pk", None), agg["n"], str(agg["at"]))
    return "variability:model:" + hashlib.sha256(repr(parts).encode()).hexdigest()


def model_variability(model, user) -> dict[str, Any]:
    """Within-group spread over the model's same-setup groups of the user's completed runs (cached)."""
    key = _model_payload_key(model, user)
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001
        hit = None
    if hit is not None:
        return hit
    payload = _compute_model(model, user)
    try:
        cache.set(key, payload, _PAYLOAD_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("variability model cache write failed", exc_info=True)
    return payload


_MODEL_METRICS = ("pass_rate", "cost", "total_tokens", "llm_calls", "wall_seconds", "source_loc")


def _compute_model(model, user) -> dict[str, Any]:
    from backend.generation.models import GenerationRun
    from backend.generation.runs.view import run_views

    runs = list(
        GenerationRun.objects.filter(created_by=user, model=model, status="completed")
        .select_related("study")
        .order_by("created_at", "pk"),
    )
    groups: dict[str, list[Any]] = defaultdict(list)
    unfingerprinted = 0
    for r in runs:
        if r.config_fingerprint:
            groups[r.config_fingerprint].append(r)
        else:
            unfingerprinted += 1
    multi = sorted(((k, v) for k, v in groups.items() if len(v) >= 2), key=lambda kv: (-len(kv[1]), kv[0]))
    notes = [
        "Spread within same-setup groups (one configuration fingerprint), never across setups; descriptive, "
        "no p-values.",
    ]
    if unfingerprinted:
        notes.append(f"{unfingerprinted} completed runs have no configuration fingerprint and are in no group.")
    if len(multi) > MAX_MODEL_GROUPS:
        notes.append(f"{len(multi)} groups; the {MAX_MODEL_GROUPS} largest are shown.")
        multi = multi[:MAX_MODEL_GROUPS]
    ids = [str(r.pk) for _k, members in multi for r in members]
    store = sim.SourceStore()
    values, rows, _cov = run_measurements(ids, store, with_issues=False)
    heads = {view.id: view for view in run_views([members[0] for _k, members in multi])}
    group_rows = []
    pooled: dict[str, list[list[float | None]]] = defaultdict(list)
    cvs: dict[str, list[float]] = defaultdict(list)
    all_sims: list[float] = []
    replays_total = 0
    replays_reproduced = 0
    for value, members in multi:
        mids = [str(m.pk) for m in members]
        stats = {}
        for key in _MODEL_METRICS:
            spec = _SPECS[key]
            series = [values.get(j, {}).get(key) for j in mids]
            d = describe(series, ratio=spec.ratio)
            stats[key] = {k: d[k] for k in ("n", "mean", "sd", "cv")}
            pooled[key].append(series)
            if d["cv"] is not None:
                cvs[key].append(d["cv"])
        passed = [p for p in (_passed(rows.get(j, {})) for j in mids) if p is not None]
        first = mids[:5]
        pair_list = [(first[i], first[k]) for i in range(len(first)) for k in range(i + 1, len(first))][
            :MAX_GROUP_PAIRS
        ]
        sims = sim.pair_similarities(pair_list, store)
        shingle = [s["shingle_jaccard"] for s in sims.values() if s.get("shingle_jaccard") is not None]
        med = median(shingle)
        if med is not None:
            all_sims.append(med)
        replay_pairs = []
        by_run: dict[str, list[str]] = defaultdict(list)
        for m in members:
            if m.run_fingerprint:
                by_run[m.run_fingerprint].append(str(m.pk))
        for same in by_run.values():
            for i in range(1, len(same)):
                probe = sim.call_divergence(same[0], same[i])
                if probe["comparable"]:
                    replays_total += 1
                    replays_reproduced += bool(probe["reproduced"])
                    replay_pairs.append(
                        {"run_a": same[0], "run_b": same[i], "first_divergent_call": probe["first_divergent_call"]},
                    )
        head = heads.get(mids[0])
        group_rows.append(
            {
                "key": "config_fingerprint",
                "value": value,
                "n": len(members),
                "task": head.task.ref if head is not None and head.task is not None else None,
                "unit_key": head.unit_key if head is not None else None,
                "kind": members[0].kind,
                "stack": head.stack.ref if head is not None and head.stack is not None else None,
                "harness": list(head.harness) if head is not None and head.harness else None,
                "studies": sorted({m.study.name for m in members if m.study_id}),
                "run_ids": mids,
                "pass": {
                    "n": len(passed),
                    "passed": sum(1 for p in passed if p),
                    "agree": (len(set(passed)) == 1) if len(passed) >= 2 else None,
                },
                "stats": stats,
                "similarity": {"n_pairs": len(sims), "median_shingle_jaccard": _r(med, 4)},
                "replays": replay_pairs,
            },
        )
    overall_metrics = {}
    for key in _MODEL_METRICS:
        psd, df = pooled_sd(pooled[key])
        overall_metrics[key] = {
            "pooled_sd": _r(psd),
            "df": df,
            "median_cv": _r(median(cvs[key]), 4) if _SPECS[key].ratio else None,
            "groups_with_cv": len(cvs[key]),
        }
    agree_known = [g["pass"]["agree"] for g in group_rows if g["pass"]["agree"] is not None]
    notes.append(NOTE_GRADING)
    if replays_total:
        notes.append(
            "Seeded replays: the same seed was sent on every call; reproduced = every stored call response matched.",
        )
    return {
        "model_id": model.pk,
        "model_name": model.model_name,
        "n_groups": len(group_rows),
        "n_runs": sum(g["n"] for g in group_rows),
        "n_completed_runs": len(runs),
        "overall": {
            "metrics": overall_metrics,
            "pass_agreement": {
                "groups": len(agree_known),
                "agree": sum(1 for a in agree_known if a),
                "share": _r(sum(1 for a in agree_known if a) / len(agree_known), 4) if agree_known else None,
            },
            "median_shingle_jaccard": _r(median(all_sims), 4),
            "replays": {"pairs": replays_total, "reproduced": replays_reproduced},
        },
        "groups": group_rows,
        "notes": notes,
    }


__all__ = [
    "BASE_METRICS",
    "describe",
    "icc_bootstrap_ci",
    "icc_oneway",
    "kept_cells",
    "model_variability",
    "pass_at_k",
    "pass_hat_k",
    "pooled_sd",
    "precision_plan",
    "rank_intervals",
    "run_replicates",
    "study_variability",
    "study_variability_csv",
    "variance_components",
]
