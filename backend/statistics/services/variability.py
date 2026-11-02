"""Run-to-run variability of repeats of one setup (descriptive statistics, no p-values).

The Results tab's statistics average repeats away (the unit is the app);
this module measures the repeats themselves. Three scopes share one set of
pure estimators:

* **Experiment** (:func:`experiment_variability`): the kept cells of the
  Results tab (current attempts, planned repeats, common cells, exclusions
  and fingerprint filter, mirroring ``experiment_results.compute``). Per
  (condition, app) cell with r repeats, for each metric: n, mean, sd (r-1),
  min, max, range, IQR (r >= 4) and CV (ratio-scale metrics only, never a
  pass rate). Per condition: the pooled within-cell SD
  ``sqrt(sum (r_a-1) s_a^2 / sum (r_a-1))``, the median cell CV and the
  share of cells whose repeats agree on functional pass. Variance
  components (condition, app, condition x app, repeat) by the method of
  moments on unweighted cell means (harmonic r), negatives truncated at 0
  and flagged; per condition the one-way ICC(1) over apps with the
  unbalanced r0, a two-stage cluster-bootstrap 95% CI, the design effect
  ``1 + (r0-1) ICC`` and the effective n. pass@k ``1 - C(n-c,k)/C(n,k)`` and
  pass^k ``C(c,k)/C(n,k)``, check-level agreement, code similarity within
  cells against a between-condition reference, finding overlap, bootstrap
  rank intervals, and a precision planner (terminal experiments only, an
  optional-stopping guard).
* **Job** (:func:`job_replicates`): every visible job sharing the job's
  ``config_fingerprint`` (legacy jobs: an approximate key), with each
  member's relation (replicate / replay / rerun), metrics and similarity.
* **Model** (:func:`model_variability`): within-group spread over all of a
  model's same-setup groups.

Pass rate: an experiment's cells count it as the Results tab does (the
stored ``metrics.functional.pass_rate``; a kept run whose value is not a
conformance-suite verdict is named in the notes); the job and model scopes
count the conformance suite only (``conformance.conformance_pass_rate``: a
generation-time smoke check or an ungraded outcome is None). A billed cost
of $0 under a subscription is marked ``billing``, never a ceiling.

Missing measurements are None, never 0. Code issues come from one
:func:`backend.statistics.services.study.issue_breakdowns` call over the
whole scope (latest result per tool, common tools only). Source LOC comes
from ``reports.services.loc``. Resampling uses seeds derived from the scope
id, so a payload regenerates identically.
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

from backend.generation.services.conformance import conformance_pass_rate
from backend.generation.services.conformance import conformance_passed
from backend.statistics.services import similarity as sim

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

PAYLOAD_VERSION = 4  # 4: conformance-only pass rate outside experiments; billed $0 cost is no ceiling
BOOTSTRAP_DRAWS = 1000
MIN_APPS_FOR_CI = 3  # as the Results tab (experiment_results.MIN_APPS_FOR_CI)
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
NOTE_INTERIM = "Interim: the experiment is still running; every estimate can still move."
NOTE_SINGLE_TRIAL = "One repeat per cell: run-to-run variation cannot be estimated (plan 2 or more repeats)."
NOTE_APPROX_SOURCE = (
    "Some runs have neither a workspace manifest nor a stack skeleton: their code similarity includes "
    "scaffold files (approximate)."
)
NOTE_REFERENCE = (
    "Reference pairs are different conditions on the same app and repeat index: the similarity any two "
    "solutions of the task share (boilerplate floor)."
)
NOTE_WALL = "Wall time is not comparable across endpoints (machine and load differ)."
NOTE_SUBSCRIPTION = (
    "{n} of {total} kept runs were subscription-billed ({sources}): their billed cost is $0, so Cost describes "
    "billing, not usage; List-price cost is the provider's list-price equivalent (not billed)."
)
NOTE_NOT_CONFORMANCE = (
    "{n} of {total} kept runs have no conformance-suite pass rate: their stored value is the generation-time smoke "
    "check (or an ungraded outcome). It is counted here as the Results tab counts it, so the two agree; "
    "re-evaluate those runs for a conformance score."
)
NOTE_CONFORMANCE_ONLY = (
    "Pass rate: conformance-suite verdicts only; a generation-time smoke check or an ungraded outcome shows —."
)


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
        "metrics.functional.pass_rate as the Results tab counts it (conformance suite; any other value is named "
        "in the notes)",
        ratio=False,
        better="higher",
        core=True,
    ),
    _spec("cost", "Cost", "USD", "metrics.cost (billed, this attempt)", ratio=True, better="lower", core=True),
    _spec(
        "list_cost",
        "List-price cost",
        "USD",
        "metrics.list_cost (provider-reported list-price equivalent of subscription-billed runs; not billed)",
        ratio=True,
        better="lower",
    ),
    _spec("total_tokens", "Tokens", "tokens", "metrics.total_tokens", ratio=True, better="lower", core=True),
    _spec("llm_calls", "LLM calls", "calls", "metrics.llm_calls", ratio=True, better=None),
    _spec("wall_seconds", "Wall time", "s", "agent.wall_seconds, else the job duration", ratio=True, better="lower"),
    _spec(
        "source_loc",
        "Source LOC",
        "lines",
        "reports.services.loc (non-test source)",
        ratio=True,
        better=None,
        core=True,
    ),
    _spec("source_files", "Source files", "files", "reports.services.loc (non-test source)", ratio=True, better=None),
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
        "issue_breakdowns unique_per_kloc (code issues per source kLOC)",
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
# Trajectory summary keys (metrics.behaviour) that are not ratio-scale or not numeric outcomes.
_BEHAVIOUR_SKIP = frozenset({"parser_version", "comparable"})
_BEHAVIOUR_NOT_RATIO = frozenset({"cache_hit_ratio", "reasoning_share", "first_build_action_index"})
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
            "reason": f"every run was subscription-billed ({', '.join(components['billing'])}): the billed $0 "
            "describes billing, not usage, so there is nothing to plan from",
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


def metric_specs(behaviour_keys: Iterable[str] = ()) -> list[MetricSpec]:
    specs = list(BASE_METRICS)
    for key in sorted(set(behaviour_keys)):
        specs.append(
            _spec(
                f"behaviour.{key}",
                key.replace("_", " ").capitalize().replace("Llm", "LLM"),
                "",
                "metrics.behaviour (agent trajectory summary; heuristic counts, compare within a harness)",
                ratio=key not in _BEHAVIOUR_NOT_RATIO,
                better=None,
            ),
        )
    return specs


_RUN_FIELDS = (
    "id",
    "status",
    "mode",
    "updated_at",
    "duration_seconds",
    "metrics__cost",
    "metrics__list_cost",
    "metrics__billing_source",
    "metrics__total_tokens",
    "metrics__llm_calls",
    "metrics__behaviour",
    "metrics__functional",
    "metrics__functional__deployed",
    "result_data__agent__wall_seconds",
    "result_data__agent__termination",
    "result_data__evaluation__checks",
    "result_data__evaluation__tester_sha256",
)


def _issue_values(job_ids: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """``{job_id: {code_issues, issues_per_kloc}}`` from one issue_breakdowns call over *job_ids*, and coverage."""
    from backend.analysis.models import ToolResult
    from backend.statistics.services.study import issue_breakdowns
    from backend.statistics.services.study import study_jobs

    if not job_ids or not ToolResult.objects.filter(run__generation_job_id__in=job_ids).exists():
        return {}, None
    report = issue_breakdowns(study_jobs(None, job_ids=job_ids, current_only=False))
    values = {
        str(row["job_id"]): {
            "code_issues": _num(row.get("code_unique")),
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


def _tool_metrics(job_ids: list[str]) -> dict[str, dict[str, float | None]]:
    """lizard avg/max CCN and jscpd duplication % from each job's latest completed result of those tools."""
    from backend.analysis.models import ToolResult

    out: dict[str, dict[str, float | None]] = defaultdict(dict)
    if not job_ids:
        return {}
    for job_id, slug, status, metrics in ToolResult.objects.filter(
        id__in=ToolResult.latest_ids_per_job(job_ids),
        tool_slug__in=("lizard", "jscpd"),
    ).values_list("run__generation_job_id", "tool_slug", "status", "metrics"):
        if status != ToolResult.Status.COMPLETED or not isinstance(metrics, dict):
            continue
        if slug == "lizard":
            out[str(job_id)]["avg_ccn"] = _num(metrics.get("avg_ccn"))
            out[str(job_id)]["max_ccn"] = _num(metrics.get("max_ccn"))
        else:
            out[str(job_id)]["duplication_pct"] = _num(metrics.get("duplication_percentage"))
    return dict(out)


def _run_rows(job_ids: list[str]) -> dict[str, dict[str, Any]]:
    from backend.generation.models import GenerationJob

    rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(job_ids), 500):
        for row in GenerationJob.objects.filter(id__in=job_ids[start : start + 500]).values(*_RUN_FIELDS):
            rows[str(row["id"])] = row
    return rows


def _seconds(row: dict[str, Any]) -> float | None:
    wall = _num(row.get("result_data__agent__wall_seconds"))
    return wall if wall is not None else _num(row.get("duration_seconds"))


def _pass_rate(row: dict[str, Any]) -> float | None:
    """A completed job's conformance-suite pass rate (``conformance_pass_rate``); None when not graded by it."""
    return conformance_pass_rate(row.get("metrics__functional")) if row.get("status") == "completed" else None


def _passed(row: dict[str, Any]) -> bool | None:
    """A completed job's conformance-suite pass (``conformance_passed``); None when not graded by it."""
    return conformance_passed(row.get("metrics__functional")) if row.get("status") == "completed" else None


def run_measurements(
    job_ids: list[str],
    store: sim.SourceStore,
    *,
    with_issues: bool = True,
) -> tuple[dict[str, dict[str, float | None]], dict[str, dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Per job ``{metric: value}`` (None = unmeasured), the raw rows, issue coverage and behaviour keys."""
    rows = _run_rows(job_ids)
    store.load(job_ids, features=False)
    issues, coverage = _issue_values(job_ids) if with_issues else ({}, None)
    tools = _tool_metrics(job_ids)
    behaviour_keys: set[str] = set()
    values: dict[str, dict[str, float | None]] = {}
    for job_id in job_ids:
        row = rows.get(job_id, {})
        loc = store.loc.get(job_id) or {}
        total_loc = _num(loc.get("total_loc"))
        v: dict[str, float | None] = {
            "pass_rate": _pass_rate(row),
            "cost": _num(row.get("metrics__cost")),
            "list_cost": _num(row.get("metrics__list_cost")),
            "total_tokens": _num(row.get("metrics__total_tokens")),
            "llm_calls": _num(row.get("metrics__llm_calls")),
            "wall_seconds": _seconds(row),
            # A job without any source has no LOC measurement (not a 0-line app).
            "source_loc": total_loc if total_loc else None,
            "source_files": _num(loc.get("source_files")) if total_loc else None,
            "code_issues": (issues.get(job_id) or {}).get("code_issues"),
            "issues_per_kloc": (issues.get(job_id) or {}).get("issues_per_kloc"),
            "avg_ccn": (tools.get(job_id) or {}).get("avg_ccn"),
            "max_ccn": (tools.get(job_id) or {}).get("max_ccn"),
            "duplication_pct": (tools.get(job_id) or {}).get("duplication_pct"),
        }
        behaviour = row.get("metrics__behaviour")
        if isinstance(behaviour, dict):
            for key, raw in behaviour.items():
                if key in _BEHAVIOUR_SKIP or isinstance(raw, bool):
                    continue
                num = _num(raw)
                if num is not None or raw is None:
                    behaviour_keys.add(key)
                    v[f"behaviour.{key}"] = num
        values[job_id] = v
    return values, rows, coverage, sorted(behaviour_keys)


# ── experiment scope ──────────────────────────────────────────────────


def kept_cells(experiment, filters=None) -> tuple[list[Any], dict[str, Any]]:
    """The Results tab's kept cells (current attempts that count), with the selection's context.

    Mirrors the selection in ``experiment_results.compute`` (which does not
    expose it per run): planned repeats only, majority fingerprint per
    (condition, app), invalid / truncated exclusions (the frozen plan's
    truncation rule when unset) and the common (app, repeat) cells over
    the conditions that have any usable cell.
    """
    from backend.generation.services import experiment_results as er
    from backend.generation.services.analysis_plan import plan_excludes_truncated

    filters = filters or er.ResultFilters()
    if filters.fingerprint not in ("all", "majority"):
        msg = "fingerprint must be 'all' or 'majority'"
        raise ValueError(msg)
    now = timezone.now()
    conditions = list(experiment.conditions.select_related("model__endpoint").order_by("id"))
    plan = er.frozen_plan(experiment)
    exclude_truncated = (
        filters.exclude_truncated
        if filters.exclude_truncated is not None
        else (plan_excludes_truncated(plan) if plan else True)
    )
    planned = er.planned_repeats(experiment)
    ids = {c.id for c in conditions}
    all_cells = [c for c in er._cells(experiment, now) if c.condition_id in ids]  # noqa: SLF001
    cells = [c for c in all_cells if c.repeat < planned] if filters.planned_only else all_cells
    if filters.fingerprint == "majority":
        per: dict[tuple[int, int], list[Any]] = defaultdict(list)
        for c in cells:
            if c.fingerprint:
                per[(c.condition_id, c.app_id)].append(c)
        for group in per.values():
            prints = Counter(c.fingerprint for c in group)
            if len(prints) > 1:
                majority = prints.most_common(1)[0][0]
                for c in group:
                    if c.fingerprint != majority:
                        c.excluded = "fingerprint"

    def usable(c) -> bool:
        if c.value is None or c.excluded:
            return False
        if filters.exclude_invalid and c.valid is False:
            return False
        return not (exclude_truncated and c.truncated)

    keys = {cid: {(c.app_id, c.repeat) for c in cells if c.condition_id == cid and usable(c)} for cid in ids}
    populated = [k for k in keys.values() if k]
    common = set.intersection(*populated) if populated else set()
    kept = [c for c in cells if usable(c) and (not filters.common_cells or (c.app_id, c.repeat) in common)]
    return kept, {
        "conditions": conditions,
        "planned_repeats": planned,
        "exclude_truncated": exclude_truncated,
        "filters": filters,
        "interim": experiment.status not in er._TERMINAL,  # noqa: SLF001
    }


def _payload_key(experiment, filters) -> str:
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import ToolResult
    from backend.generation.models import GenerationJob

    unfinished = (ToolResult.Status.PENDING, ToolResult.Status.RUNNING)
    run_finished = (AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL, AnalysisRun.Status.FAILED)
    jobs = GenerationJob.objects.filter(experiment=experiment).aggregate(
        n=Count("id"),
        at=Max("updated_at"),
        # Backfills (trajectory, call telemetry) add metrics keys without touching updated_at.
        metrics=MD5(StringAgg(MD5(Cast("metrics", TextField())), Value(""), order_by="id")),
    )
    # A tool result is created running and finished in place, and the run is
    # finalized after it: neither adds a row, so count the finished ones.
    results = ToolResult.objects.filter(run__generation_job__experiment=experiment).aggregate(
        n=Count("id"),
        at=Max("created_at"),
        finished=Count("id", filter=~Q(status__in=unfinished)),
    )
    runs = AnalysisRun.objects.filter(generation_job__experiment=experiment).aggregate(
        n=Count("id"),
        finished=Count("id", filter=Q(status__in=run_finished)),
    )
    parts = (
        PAYLOAD_VERSION,
        sim.ALGO_VERSION,
        str(experiment.id),
        experiment.status,
        str(experiment.updated_at),
        repr(filters),
        jobs["n"],
        str(jobs["at"]),
        jobs["metrics"],
        results["n"],
        str(results["at"]),
        results["finished"],
        runs["n"],
        runs["finished"],
    )
    return "variability:experiment:" + hashlib.sha256(repr(parts).encode()).hexdigest()


def experiment_variability(experiment, filters=None) -> dict[str, Any]:
    """The experiment's run-to-run variability payload (cached on its jobs' and results' state)."""
    from backend.generation.services import experiment_results as er

    filters = filters or er.ResultFilters()
    key = _payload_key(experiment, filters)
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001
        hit = None
    if hit is not None:
        return hit
    payload = _compute_experiment(experiment, filters)
    try:
        cache.set(key, payload, _PAYLOAD_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("variability payload cache write failed", exc_info=True)
    return payload


def _checks_of(row: dict[str, Any]) -> dict[str, tuple[bool, str]] | None:
    checks = row.get("result_data__evaluation__checks")
    if not isinstance(checks, list) or not checks:
        return None
    out = {}
    for c in checks:
        if isinstance(c, dict) and c.get("name") and isinstance(c.get("passed"), bool):
            out[str(c["name"])] = (c["passed"], str(c.get("category") or ""))
    return out or None


def _cell_checks(job_ids: list[str], rows: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Check-level agreement of a cell's repeats (one tester version; checks every repeat ran)."""
    graded = [(j, _checks_of(rows.get(j, {}))) for j in job_ids]
    graded = [(j, c) for j, c in graded if c]
    if len(graded) < 2:
        return None
    testers = {rows[j].get("result_data__evaluation__tester_sha256") for j, _c in graded}
    if len(testers) > 1:
        return {"comparable": False, "reason": "repeats were graded by different tester versions"}
    names = set.intersection(*[set(c) for _j, c in graded])
    disagree = sorted(n for n in names if len({c[n][0] for _j, c in graded}) > 1)
    return {
        "comparable": True,
        "n_checks": len(names),
        "disagree": disagree,
        "all_agree": not disagree,
        "categories": {n: graded[0][1][n][1] for n in disagree},
    }


def _summarize_pairs(pairs: list[dict[str, Any]], keys: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"n_pairs": len(pairs)}
    for k in keys:
        out[f"median_{k}"] = _r(median(p.get(k) for p in pairs), 4)
    out["approximate"] = any(p.get("approximate") for p in pairs)
    return out


_SIM_KEYS = ("shingle_jaccard", "line_jaccard", "path_jaccard", "ast_jaccard", "identical_files")


def _compute_experiment(experiment, filters) -> dict[str, Any]:
    from backend.generation.services.experiments import condition_endpoint_slug
    from backend.generation.services.experiments import condition_label

    kept, ctx = kept_cells(experiment, filters)
    conditions = ctx["conditions"]
    interim = ctx["interim"]
    labels = {c.id: condition_label(c) for c in conditions}
    apps = {a.id: a for a in experiment.app_requirements.order_by("id")}
    base_seed = experiment.id.int % (2**32)
    store = sim.SourceStore()

    by_cell: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for c in sorted(kept, key=lambda c: (c.condition_id, c.app_id, c.repeat)):
        by_cell[(c.condition_id, c.app_id)].append(c)
    job_ids = [c.job_id for cells in by_cell.values() for c in cells]
    values, rows, coverage, behaviour_keys = run_measurements(job_ids, store)
    # Pass rate (and k_passed below) as the Results tab counts the cell: its stored value,
    # so these numbers match the Results tables; a value that is not a conformance verdict is named.
    for c in kept:
        values[c.job_id]["pass_rate"] = c.value
    specs = metric_specs(behaviour_keys)
    notes: list[str] = [NOTE_DESCRIPTIVE]
    if interim:
        notes.insert(0, NOTE_INTERIM)
    not_conformance = [j for j in job_ids if conformance_pass_rate(rows.get(j, {}).get("metrics__functional")) is None]
    if not_conformance:
        notes.append(NOTE_NOT_CONFORMANCE.format(n=len(not_conformance), total=len(job_ids)))
    # Subscription-billed runs: their billed cost is $0 by billing, not a measured floor.
    billed = {j: str(source) for j in job_ids if (source := rows.get(j, {}).get("metrics__billing_source"))}

    def billing_marker(key: str, ids: Iterable[str]) -> list[str] | None:
        """The billing sources when every run with a *key* value was subscription-billed (cost only), else None."""
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
        "n_apps": len({k[1] for k in by_cell}),
        "n_cells": len(by_cell),
        "n_runs": len(job_ids),
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
    if len({condition_endpoint_slug(c) for c in conditions}) > 1:
        notes.append(NOTE_WALL)

    # ── per cell ──
    cell_rows = []
    for (cid, app_id), cells in by_cell.items():
        ids = [c.job_id for c in cells]
        stats = {}
        vals_out = {}
        for spec in specs:
            series = [values[j].get(spec.key) for j in ids]
            if all(v is None for v in series):
                continue
            vals_out[spec.key] = [_r(v) for v in series]
            stats[spec.key] = describe(series, ratio=spec.ratio)
        passed = [c.passed for c in cells]
        deployed = [rows.get(j, {}).get("metrics__functional__deployed") for j in ids]
        deployed_known = [d for d in deployed if isinstance(d, bool)]
        app = apps.get(app_id)
        cell_rows.append(
            {
                "condition_id": cid,
                "app_id": app_id,
                "app_slug": app.slug if app else str(app_id),
                "app_name": app.name if app else str(app_id),
                "n": len(cells),
                "job_ids": ids,
                "repeat_indexes": [c.repeat for c in cells],
                "values": vals_out,
                "stats": stats,
                "k_passed": sum(1 for p in passed if p),
                "pass_agree": (len(set(passed)) == 1) if len(cells) >= 2 else None,
                "deploy_agree": (len(set(deployed_known)) == 1)
                if len(deployed_known) >= 2 and len(deployed_known) == len(ids)
                else None,
                "checks": _cell_checks(ids, rows) if len(cells) >= 2 else None,
                "similarity": None,
                "finding_overlap": None,
            },
        )
    cell_index = {(r["condition_id"], r["app_id"]): r for r in cell_rows}

    # ── per condition dispersion ──
    condition_rows = []
    ceilings = []
    for cond in conditions:
        mine = [r for r in cell_rows if r["condition_id"] == cond.id]
        if not mine:
            continue
        dispersion = {}
        for spec in specs:
            groups = [[values[j].get(spec.key) for j in r["job_ids"]] for r in mine]
            flat = [v for g in groups for v in g if v is not None]
            if not flat:
                continue
            psd, df = pooled_sd(groups)
            cvs = [r["stats"][spec.key]["cv"] for r in mine if spec.key in r["stats"] and r["n"] >= 2]
            marker = billing_marker(spec.key, [j for r in mine for j in r["job_ids"]])
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
        repeated = [r for r in mine if r["n"] >= 2]
        agree = [r for r in repeated if r["pass_agree"]]
        deploy = [r for r in repeated if r["deploy_agree"] is not None]
        condition_rows.append(
            {
                "condition_id": cond.id,
                "label": labels[cond.id],
                "model": cond.model.model_name,
                "n_apps": len(mine),
                "n_cells": len(mine),
                "n_runs": sum(r["n"] for r in mine),
                "cells_with_repeats": len(repeated),
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
            NOTE_SUBSCRIPTION.format(n=sum(billing.values()), total=len(job_ids), sources=", ".join(sorted(billing))),
        )
    billing_by_metric = {spec.key: m for spec in specs if (m := billing_marker(spec.key, job_ids))}
    for spec in specs:
        flat = [values[j].get(spec.key) for j in job_ids]
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
            k: [values[j].get(spec.key) for j in r["job_ids"]] for k, r in cell_index.items() if spec.key in r["stats"]
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
    k_max = max((r["n"] for r in cell_rows), default=0)
    pass_rows = []
    for cond in conditions:
        mine = [r for r in cell_rows if r["condition_id"] == cond.id]
        if not mine:
            continue
        by_k = []
        for k in range(1, k_max + 1):
            eligible = [r for r in mine if r["n"] >= k]
            if not eligible:
                continue
            at = [pass_at_k(r["n"], r["k_passed"], k) for r in eligible]
            hat = [pass_hat_k(r["n"], r["k_passed"], k) for r in eligible]
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
    within_pairs: list[tuple[str, str, tuple[int, int]]] = []
    for key, r in cell_index.items():
        ids = r["job_ids"]
        within_pairs.extend((ids[i], ids[j], key) for i in range(len(ids)) for j in range(i + 1, len(ids)))
    reference_pairs: list[tuple[str, str, int, int]] = []
    slices: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    for c in kept:
        slices[(c.app_id, c.repeat)][c.condition_id] = c.job_id
    cond_order = [c.id for c in conditions]
    for slice_jobs in slices.values():
        present = [cid for cid in cond_order if cid in slice_jobs]
        if len(present) < 2:
            continue
        seen = set()
        for i, cid in enumerate(present):
            other = present[(i + 1) % len(present)]
            pair = tuple(sorted((cid, other)))
            if other == cid or pair in seen:
                continue
            seen.add(pair)
            reference_pairs.append((slice_jobs[pair[0]], slice_jobs[pair[1]], pair[0], pair[1]))
    sims = sim.pair_similarities([(a, b) for a, b, _k in within_pairs], store)
    ref_sims = sim.pair_similarities([(a, b) for a, b, _c1, _c2 in reference_pairs], store)
    within_by_cell: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for a, b, key in within_pairs:
        within_by_cell[key].append({"job_a": a, "job_b": b, **sims[(a, b)]})
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
    if any(p.get("approximate") for p in [*all_within, *all_ref]):
        notes.append(NOTE_APPROX_SOURCE)
    if all_ref:
        notes.append(NOTE_REFERENCE)

    # ── finding overlap ──
    fsets = sim.finding_sets(job_ids)
    overlap_by_cell: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for a, b, key in within_pairs:
        ov = sim.finding_overlap(fsets, a, b)
        if ov is not None:
            overlap_by_cell[key].append({"job_a": a, "job_b": b, **ov})
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
        data: dict[int, dict[int, list[float]]] = defaultdict(dict)
        for (cid, app_id), r in cell_index.items():
            vals = [v for v in r["values"].get(metric, []) if v is not None]
            if vals:
                data[cid][app_id] = vals
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
            "Withheld while the experiment is running (optional-stopping guard): plan the next experiment "
            "from the finished one."
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
            "basis": "normal approximation from this experiment's variance components (planning guidance only)",
        }

    if coverage is not None and coverage["apps_measured"] < len(job_ids):
        notes.append(
            f"Code issues: {coverage['apps_measured']} of {len(job_ids)} kept runs measured by the common "
            f"instrument set ({', '.join(coverage['tools_common']) or 'no tool'}); the rest show —.",
        )

    return {
        "experiment_id": str(experiment.id),
        "status": experiment.status,
        "interim": interim,
        "generated_at": timezone.now(),
        "filters": {
            "exclude_invalid": filters.exclude_invalid,
            "exclude_truncated": ctx["exclude_truncated"],
            "common_cells": filters.common_cells,
            "fingerprint": filters.fingerprint,
            "planned_only": filters.planned_only,
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
                "n_runs": sum(1 for j in job_ids if values[j].get(s.key) is not None),
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
        # Subscription billing of the kept runs; ``metrics`` lists those whose every value is a billed $0.
        "billing": {
            "sources": sorted(billing),
            "n_runs": sum(billing.values()),
            "n_kept": len(job_ids),
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


def experiment_variability_csv(payload: dict[str, Any]) -> str:
    """One row per (condition, app) cell: n, pass agreement, per-metric mean/sd/min/max/cv, similarity, overlap."""
    from backend.export.services.csv_safe import safe_row

    labels = {r["condition_id"]: r["label"] for r in payload["conditions"]}
    metrics = [m["key"] for m in payload["metrics"] if m["n_runs"]]
    header = [
        "condition_id",
        "condition_label",
        "app_slug",
        "n_repeats",
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
        "job_ids",
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
            r["app_slug"],
            r["n"],
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
            " ".join(r["job_ids"]),
            payload["interim"],
        ]
        writer.writerow(safe_row(row))
    return buffer.getvalue()


# ── same-setup groups (job and model scopes) ──────────────────────────


def approximate_key(job) -> str | None:
    """The legacy grouping key (mode, model, app, prompt hash, method, stack); None without a model or app."""
    if not job.model_id or not job.app_requirement_id:
        return None
    return "|".join(
        str(p)
        for p in (
            job.mode,
            job.model_id,
            job.app_requirement_id,
            job.prompt_hash or "-",
            job.bundle_key or "-",
            job.stack_slug or "-",
        )
    )


def _no_fingerprint() -> Q:
    return (
        Q(resolved_bundle__config_fingerprint__isnull=True)
        | Q(resolved_bundle__config_fingerprint=None)
        | Q(
            resolved_bundle__config_fingerprint="",
        )
    )


def _group_queryset(job, user):
    """The visible jobs sharing *job*'s setup, and the grouping descriptor."""
    from backend.generation.models import GenerationJob

    rb = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    fingerprint = rb.get("config_fingerprint")
    qs = GenerationJob.objects.filter(created_by=user)
    if isinstance(fingerprint, str) and fingerprint:
        return qs.filter(resolved_bundle__config_fingerprint=fingerprint), {
            "key": "config_fingerprint",
            "value": fingerprint,
            "approximate": False,
        }
    key = approximate_key(job)
    if key is None:
        return qs.filter(id=job.id), {"key": None, "value": None, "approximate": False}
    return (
        qs.filter(_no_fingerprint()).filter(
            mode=job.mode,
            model_id=job.model_id,
            app_requirement_id=job.app_requirement_id,
            prompt_hash=job.prompt_hash,
            bundle_key=job.bundle_key,
            stack_slug=job.stack_slug,
        ),
        {"key": "approximate", "value": key, "approximate": True},
    )


def _superseded(jobs: list[Any]) -> set[str]:
    """Experiment-cell jobs a newer attempt of the same cell replaced (retries)."""
    from backend.generation.models import GenerationJob

    cells = {
        (j.experiment_id, j.condition_id, j.app_requirement_id, j.repeat_index)
        for j in jobs
        if j.experiment_id and j.condition_id and j.repeat_index is not None
    }
    if not cells:
        return set()
    current = set(
        GenerationJob.objects.filter(
            experiment_id__in={c[0] for c in cells},
            condition_id__in={c[1] for c in cells},
            repeat_index__isnull=False,
        )
        .order_by("experiment_id", "condition_id", "app_requirement_id", "repeat_index", "-created_at")
        .distinct("experiment_id", "condition_id", "app_requirement_id", "repeat_index")
        .values_list("id", flat=True),
    )
    return {
        str(j.id)
        for j in jobs
        if j.experiment_id and j.condition_id and j.repeat_index is not None and j.id not in current
    }


def _relation(member, self_job, superseded: set[str]) -> str:
    if member.id == self_job.id:
        return "self"
    rb_m = member.resolved_bundle if isinstance(member.resolved_bundle, dict) else {}
    rb_s = self_job.resolved_bundle if isinstance(self_job.resolved_bundle, dict) else {}
    if rb_m.get("run_fingerprint") and rb_m.get("run_fingerprint") == rb_s.get("run_fingerprint"):
        return "replay"
    if member.repeat_index is None or str(member.id) in superseded:
        return "rerun"
    return "replicate"


def _seed_of(job) -> int | None:
    rb = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    seed = rb.get("seed")
    return seed if isinstance(seed, int) and not isinstance(seed, bool) else job.experiment_seed


def job_replicates(job, user, *, cap: int = MAX_REPLICATES) -> dict[str, Any]:
    """Contract C3: every visible job sharing *job*'s setup, with relation, metrics and similarity to *job*."""
    from backend.generation.services.experiments import condition_label

    qs, grouping = _group_queryset(job, user)
    total = qs.count()
    members = list(
        qs.exclude(id=job.id)
        .select_related("experiment", "condition__model")
        .defer("result_data")
        .order_by("-created_at")[: max(0, cap - 1)],
    )
    self_job = type(job).objects.select_related("experiment", "condition__model").defer("result_data").get(id=job.id)
    members = sorted([self_job, *members], key=lambda j: j.created_at)
    notes: list[str] = []
    if grouping["approximate"]:
        notes.append(
            "No configuration fingerprint (legacy job): grouped by mode, model, app, prompt hash, method and "
            "stack. Approximate: the runs may differ in settings this key does not cover.",
        )
    elif grouping["key"] is None:
        notes.append("This job has no configuration fingerprint and no model/app to group by: no same-setup group.")
    if total > cap:
        notes.append(f"The group has {total} jobs; the {cap} most recent (this one included) are shown.")
    superseded = _superseded(members)
    ids = [str(m.id) for m in members]
    completed = [str(m.id) for m in members if m.status == "completed"]
    store = sim.SourceStore()
    values, rows, coverage, _bk = run_measurements(ids, store, with_issues=bool(completed))
    analyzed = coverage is not None and coverage["apps_measured"] > 0
    self_id = str(job.id)
    sims = (
        sim.pair_similarities([(self_id, j) for j in completed if j != self_id], store) if self_id in completed else {}
    )
    out_members = []
    for m in members:
        mid = str(m.id)
        v = values.get(mid, {})
        s = sims.get((self_id, mid))
        out_members.append(
            {
                "job_id": mid,
                "is_self": mid == self_id,
                "relation": _relation(m, self_job, superseded),
                "seed": _seed_of(m),
                "repeat_index": m.repeat_index,
                "experiment_id": str(m.experiment_id) if m.experiment_id else None,
                "experiment_name": m.experiment.name if m.experiment_id else None,
                "condition_label": condition_label(m.condition) if m.condition_id else None,
                "batch_id": str(m.batch_id) if m.batch_id else None,
                "created_at": m.created_at,
                "status": m.status,
                "pass_rate": v.get("pass_rate"),
                "source_loc": v.get("source_loc"),
                "source_files": v.get("source_files"),
                "cost": v.get("cost"),
                "total_tokens": v.get("total_tokens"),
                "llm_calls": v.get("llm_calls"),
                "wall_seconds": v.get("wall_seconds"),
                "termination": rows.get(mid, {}).get("result_data__agent__termination")
                or ((m.metrics or {}).get("termination") if isinstance(m.metrics, dict) else None),
                "code_issues": v.get("code_issues"),
                "issues_per_kloc": v.get("issues_per_kloc"),
                "findings_measured": v.get("code_issues") is not None,
                "similarity_to_self": {
                    "path_jaccard": s.get("path_jaccard"),
                    "shingle_jaccard": s.get("shingle_jaccard"),
                    "line_jaccard": s.get("line_jaccard"),
                    "identical_files": s.get("identical_files"),
                    "approximate": s.get("approximate"),
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
        if m["relation"] == "replay" and m["status"] == "completed" and self_job.status == "completed":
            probe = sim.call_divergence(self_id, m["job_id"])
            determinism.append({"job_id": m["job_id"], **probe})
    if len(members) > 1:
        notes.append(
            "The summary pools every completed member (replicates, replays and reruns): replays share the seed and "
            "reruns ran outside a designed sample, so it is descriptive, not a design estimate.",
        )
    else:
        notes.append("No other visible run shares this setup yet.")
    ungraded = sum(1 for j in completed if values.get(j, {}).get("pass_rate") is None)
    if ungraded:
        notes.append(
            f"{NOTE_CONFORMANCE_ONLY} {ungraded} of {len(completed)} completed members have no conformance-suite "
            "pass rate.",
        )
    if completed and not analyzed:
        notes.append("No member has been analyzed: code issues are unmeasured (—).")
    elif analyzed and coverage["apps_measured"] < len(completed):
        notes.append(
            f"Code issues: one instrument set over the group's analyzed runs ({coverage['apps_measured']} of "
            f"{len(completed)}; tools: {', '.join(coverage['tools_common']) or 'none'}); the others show —.",
        )
    billed = sorted(
        {str(rows[j]["metrics__billing_source"]) for j in ids if rows.get(j, {}).get("metrics__billing_source")},
    )
    if billed:
        notes.append(
            f"Subscription-billed runs ({', '.join(billed)}) record a billed cost of $0: Cost describes billing, not "
            "usage (each run's page shows its list-price equivalent).",
        )
    if determinism:
        notes.append(
            "Replays send the same seed on every call; the determinism probe compares the stored call artifacts in "
            "order (content truncated at 8000 chars).",
        )
    return {
        "job_id": self_id,
        "grouping": grouping,
        "n": total,
        "members": out_members,
        "summary": summary,
        "notes": notes,
        "determinism": determinism,
        "issue_coverage": coverage,
    }


def _model_payload_key(model, user) -> str:
    from backend.generation.models import GenerationJob

    agg = GenerationJob.objects.filter(created_by=user, model=model).aggregate(n=Count("id"), at=Max("updated_at"))
    parts = (PAYLOAD_VERSION, sim.ALGO_VERSION, model.pk, getattr(user, "pk", None), agg["n"], str(agg["at"]))
    return "variability:model:" + hashlib.sha256(repr(parts).encode()).hexdigest()


def model_variability(model, user) -> dict[str, Any]:
    """Within-group spread over the model's same-setup groups of the user's completed jobs (cached)."""
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
    from backend.generation.models import GenerationJob

    jobs = list(
        GenerationJob.objects.filter(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
        .select_related("app_requirement", "experiment")
        .defer("result_data")
        .order_by("created_at"),
    )
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for j in jobs:
        rb = j.resolved_bundle if isinstance(j.resolved_bundle, dict) else {}
        fp = rb.get("config_fingerprint")
        if isinstance(fp, str) and fp:
            groups[("config_fingerprint", fp)].append(j)
        else:
            key = approximate_key(j)
            if key is not None:
                groups[("approximate", key)].append(j)
    multi = sorted(((k, v) for k, v in groups.items() if len(v) >= 2), key=lambda kv: (-len(kv[1]), kv[0]))
    notes = [
        "Spread within same-setup groups (one configuration fingerprint, else an approximate legacy key), never "
        "across setups; descriptive, no p-values.",
    ]
    if len(multi) > MAX_MODEL_GROUPS:
        notes.append(f"{len(multi)} groups; the {MAX_MODEL_GROUPS} largest are shown.")
        multi = multi[:MAX_MODEL_GROUPS]
    ids = [str(j.id) for _k, members in multi for j in members]
    store = sim.SourceStore()
    values, _rows, _cov, _bk = run_measurements(ids, store, with_issues=False)
    group_rows = []
    pooled: dict[str, list[list[float | None]]] = defaultdict(list)
    cvs: dict[str, list[float]] = defaultdict(list)
    all_sims: list[float] = []
    replays_total = 0
    replays_reproduced = 0
    for (kind, value), members in multi:
        mids = [str(m.id) for m in members]
        stats = {}
        for key in _MODEL_METRICS:
            spec = _SPECS[key]
            series = [values.get(j, {}).get(key) for j in mids]
            d = describe(series, ratio=spec.ratio)
            stats[key] = {k: d[k] for k in ("n", "mean", "sd", "cv")}
            pooled[key].append(series)
            if d["cv"] is not None:
                cvs[key].append(d["cv"])
        passed = [p for p in (_passed_job(m) for m in members) if p is not None]
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
            rfp = (m.resolved_bundle or {}).get("run_fingerprint") if isinstance(m.resolved_bundle, dict) else None
            if rfp:
                by_run[rfp].append(str(m.id))
        for same in by_run.values():
            for i in range(1, len(same)):
                probe = sim.call_divergence(same[0], same[i])
                if probe["comparable"]:
                    replays_total += 1
                    replays_reproduced += bool(probe["reproduced"])
                    replay_pairs.append(
                        {"job_a": same[0], "job_b": same[i], "first_divergent_call": probe["first_divergent_call"]},
                    )
        head = members[0]
        group_rows.append(
            {
                "key": kind,
                "value": value,
                "approximate": kind == "approximate",
                "n": len(members),
                "app_slug": head.app_requirement.slug if head.app_requirement_id else None,
                "method": head.bundle_key or head.mode,
                "mode": head.mode,
                "experiments": sorted({m.experiment.name for m in members if m.experiment_id}),
                "job_ids": mids,
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
    notes.append(NOTE_CONFORMANCE_ONLY)
    if any(g["approximate"] for g in group_rows):
        notes.append("Approximate groups (legacy jobs without a fingerprint) may mix settings the key does not cover.")
    if replays_total:
        notes.append(
            "Seeded replays: the same seed was sent on every call; reproduced = every stored call response matched.",
        )
    return {
        "model_id": model.pk,
        "model_name": model.model_name,
        "n_groups": len(group_rows),
        "n_runs": sum(g["n"] for g in group_rows),
        "n_completed_jobs": len(jobs),
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


def _passed_job(job) -> bool | None:
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    return _passed({"status": job.status, "metrics__functional": metrics.get("functional")})


__all__ = [
    "BASE_METRICS",
    "describe",
    "experiment_variability",
    "experiment_variability_csv",
    "icc_bootstrap_ci",
    "icc_oneway",
    "job_replicates",
    "kept_cells",
    "model_variability",
    "pass_at_k",
    "pass_hat_k",
    "pooled_sd",
    "precision_plan",
    "rank_intervals",
    "variance_components",
]
