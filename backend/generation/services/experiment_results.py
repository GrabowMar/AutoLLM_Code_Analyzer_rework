"""Experiment results: leaderboard, app-blocked paired comparisons, matrix (spec §F.1, decision 19).

The single implementation behind the Results tab, ``ResultsMatrix`` (via
``results.matrix``) and the auto-generated ``experiment_report``.

* **Unit of analysis: the app.** Repeats of one (condition, app) share the
  app and, in agent mode, the seed, so they are strongly correlated. Each
  (condition, app) collapses to the mean over its usable repeats; a
  condition's score is the mean of its app means.
* **Pairing on ``app_id``** ("app-blocked"): a comparison vs the baseline uses
  the apps where both conditions have a mean, so ``n_pairs <= apps``.
* **Uncertainty:** cluster bootstraps resample apps with their repeats nested
  (2 000 draws, RNG seeded by the experiment id), for the per-condition CI and
  for the paired delta CI.
* **Tests:** a two-sided exact sign test and a Wilcoxon signed-rank test on
  the app-level deltas (zero deltas dropped), Holm-adjusted across the
  conditions compared with the baseline (``p_holm`` adjusts the Wilcoxon p).
  P-values are null while the experiment is not terminal (``interim``).
* **Usable cells:** a cell counts when its current attempt is COMPLETED and
  graded (``metrics.functional.pass_rate`` not null). Exclusions (invalid,
  truncated, minority fingerprint) remove the (app, repeat) cell for every
  condition through the common-cells rule (default on), so they never select
  on one condition's outcome.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationJob

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

BOOTSTRAP_DRAWS = 2000
# Bootstrap CIs are pure functions of (data, seed): cached so reloading a large
# Results tab (60 conditions x 20 apps is seconds of resampling) is instant and
# a running experiment only recomputes the conditions whose cells changed.
_CI_CACHE_SECONDS = 6 * 3600
MIN_APPS_FOR_INFERENCE = 6
METRICS = ("pass_rate",)
_EXACT_WILCOXON_MAX_N = 25

NOTE_INTERIM = "Interim: conditions at unequal progress; p-values are hidden until the experiment ends"
NOTE_POST_HOC = "Baseline chosen post hoc; p-values exploratory"
NOTE_NO_PREREGISTERED = "No pre-registered baseline; the first condition is used (exploratory)"
NOTE_FEW_APPS = "Too few apps for inference (fewer than 6): treat p-values as descriptive"
NOTE_ONE_SIDED = "Common cells off: exclusions may select on the outcome"
NOTE_TIMINGS = "Timings are not comparable across endpoints (machine and load differ)"
_TERMINAL = (Experiment.Status.COMPLETED, Experiment.Status.CANCELLED, Experiment.Status.ARCHIVED)


@dataclass(frozen=True)
class ResultFilters:
    metric: str = "pass_rate"
    exclude_invalid: bool = False
    exclude_truncated: bool = True
    common_cells: bool = True
    fingerprint: str = "all"  # "all" | "majority"


@dataclass
class _Cell:
    condition_id: int
    app_id: int
    repeat: int
    job_id: str
    code: str
    value: float | None  # pass rate when graded
    passed: bool
    valid: bool | None
    validity_warn: bool
    truncated: bool
    termination: str | None
    fingerprint: str | None
    cost: float
    tokens: float
    seconds: float | None
    terminal: bool
    excluded: str = ""  # "", "invalid", "truncated", "fingerprint"


# ── statistics (pure) ─────────────────────────────────────────────────


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


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
    draw = rng.random  # int(random() * n) instead of randrange: ~5x faster on this hot loop
    samples = []
    for _ in range(draws):
        total = 0.0
        for _i in range(k):
            total += _resampled_mean(pools[int(draw() * k)], draw)
        samples.append(total / k)
    return _ci(samples)


def paired_bootstrap_ci(
    pairs: dict[Any, tuple[list[float], list[float]]],
    rng: random.Random,
    draws: int = BOOTSTRAP_DRAWS,
) -> list | None:
    """95% CI of the mean app-level delta: resample apps; within each, both conditions' repeats."""
    pools = [(tuple(treated), tuple(baseline)) for treated, baseline in pairs.values()]
    if not pools:
        return None
    k = len(pools)
    draw = rng.random
    samples = []
    for _ in range(draws):
        total = 0.0
        for _i in range(k):
            treated, baseline = pools[int(draw() * k)]
            total += _resampled_mean(treated, draw) - _resampled_mean(baseline, draw)
        samples.append(total / k)
    return _ci(samples)


def _bootstrap(fn: Callable[..., list | None], data: dict[Any, Any], seed: int) -> list | None:
    """``fn(data, Random(seed))``, memoised in the cache on its exact inputs (it is deterministic)."""
    if not data:
        return None
    key = (
        "experiment-results:ci:"
        + hashlib.sha256(
            repr((fn.__name__, seed, BOOTSTRAP_DRAWS, list(data.values()))).encode(),
        ).hexdigest()
    )
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 — a cache outage only costs the recomputation
        logger.debug("results CI cache read failed", exc_info=True)
        cached = None
    if cached is not None:
        return cached
    value = fn(data, random.Random(seed))  # noqa: S311 - reproducible bootstrap, not crypto
    try:
        cache.set(key, value, _CI_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.debug("results CI cache write failed", exc_info=True)
    return value


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


def wilcoxon_p(deltas: list[float]) -> float | None:
    """Two-sided Wilcoxon signed-rank p for *deltas* (zeros dropped; average ranks for ties).

    Exact (the null distribution of W+ by dynamic programming over doubled
    ranks, so tied half-ranks stay integers) up to 25 non-zero deltas; the
    normal approximation with tie and continuity correction above.
    """
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
        mirrored = round(2 * total) - observed
        lo, hi = min(observed, mirrored), max(observed, mirrored)
        extreme = sum(c for s, c in counts.items() if s <= lo or s >= hi)
        return min(1.0, extreme / 2**n)
    mean = total / 2
    tie_groups = Counter(ranks).values()
    variance = n * (n + 1) * (2 * n + 1) / 24 - sum(t**3 - t for t in tie_groups) / 48
    if variance <= 0:
        return None
    z = (abs(w_plus - mean) - 0.5) / math.sqrt(variance)
    return min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2)))


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


# ── data ──────────────────────────────────────────────────────────────


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cells(experiment: Experiment, now) -> list[_Cell]:
    from backend.generation.services.experiments import cell_code
    from backend.generation.services.experiments import current_jobs

    cells = []
    for row in current_jobs(experiment):
        validity = row.get("result_data__agent__validity")
        validity = validity if isinstance(validity, dict) else {}
        notes = validity.get("notes") or []
        status = row["status"]
        graded = status == GenerationJob.Status.COMPLETED
        value = _float(row.get("metrics__functional__pass_rate")) if graded else None
        code = cell_code(row, now)
        cells.append(
            _Cell(
                condition_id=row["condition_id"],
                app_id=row["app_requirement_id"],
                repeat=row["repeat_index"],
                job_id=str(row["id"]),
                code=code,
                value=value,
                passed=code == "c",
                valid=validity.get("valid") if "valid" in validity else None,
                validity_warn=any(isinstance(n, dict) and n.get("severity") == "warn" for n in notes),
                truncated=bool(
                    row.get("result_data__backend_truncated") or row.get("result_data__frontend_truncated"),
                ),
                termination=row.get("result_data__agent__termination"),
                fingerprint=row.get("resolved_bundle__config_fingerprint"),
                cost=_float(row.get("metrics__cost")) or 0.0,
                tokens=_float(row.get("metrics__total_tokens")) or 0.0,
                seconds=_float(row.get("duration_seconds")) if graded else None,
                terminal=status
                in (GenerationJob.Status.COMPLETED, GenerationJob.Status.FAILED, GenerationJob.Status.CANCELLED),
            ),
        )
    return cells


def _resolve_baseline(
    experiment: Experiment,
    conditions: list[ExperimentCondition],
    baseline: ExperimentCondition | int | None,
) -> tuple[ExperimentCondition | None, str, list[str]]:
    by_id = {c.id: c for c in conditions}
    requested = baseline.id if isinstance(baseline, ExperimentCondition) else baseline
    if requested is not None and int(requested) not in by_id:
        msg = f"Condition {requested} is not part of this experiment"
        raise ValueError(msg)
    preregistered = by_id.get(experiment.baseline_condition_id) if experiment.baseline_condition_id else None
    if requested is None or (preregistered is not None and int(requested) == preregistered.id):
        if preregistered is not None:
            return preregistered, "preregistered", []
        first = conditions[0] if conditions else None
        return first, "post_hoc", [NOTE_NO_PREREGISTERED] if first is not None else []
    return by_id[int(requested)], "post_hoc", [NOTE_POST_HOC]


# ── compute ───────────────────────────────────────────────────────────


def compute(
    experiment: Experiment,
    baseline: ExperimentCondition | int | None,
    filters: ResultFilters | None,
    user,
) -> dict[str, Any]:
    """The ``ExperimentResults`` payload (§F.1) for ``experiment``.

    ``baseline`` None → the pre-registered ``experiment.baseline_condition``
    (``baseline_source="preregistered"``); any other condition →
    ``"post_hoc"``. ``user`` scopes owner-only fields (results carry none
    today). O(cells): one ``values()`` query over current-attempt jobs.
    Raises ``ValueError`` for an unknown metric or baseline.
    """
    from backend.generation.services.experiments import condition_endpoint_slug
    from backend.generation.services.experiments import condition_label

    filters = filters or ResultFilters()
    if filters.metric not in METRICS:
        msg = f"Unknown metric {filters.metric!r} (supported: {', '.join(METRICS)})"
        raise ValueError(msg)
    if filters.fingerprint not in ("all", "majority"):
        msg = "fingerprint must be 'all' or 'majority'"
        raise ValueError(msg)

    now = timezone.now()
    conditions = list(experiment.conditions.select_related("model__endpoint").order_by("id"))
    apps = list(experiment.app_requirements.order_by("id"))
    base_condition, baseline_source, notes = _resolve_baseline(experiment, conditions, baseline)
    interim = experiment.status not in _TERMINAL
    condition_ids = {c.id for c in conditions}
    cells = [c for c in _cells(experiment, now) if c.condition_id in condition_ids]
    by_condition: dict[int, list[_Cell]] = defaultdict(list)
    for cell in cells:
        by_condition[cell.condition_id].append(cell)

    # Mixed fingerprints per condition; ``majority`` drops the minority cells.
    mixed: dict[int, dict[str, Any] | None] = {}
    for condition in conditions:
        prints = Counter(c.fingerprint for c in by_condition[condition.id] if c.fingerprint)
        if len(prints) > 1:
            majority, count = prints.most_common(1)[0]
            mixed[condition.id] = {"count": len(prints), "majority_share": round(count / sum(prints.values()), 4)}
            if filters.fingerprint == "majority":
                for cell in by_condition[condition.id]:
                    if cell.fingerprint and cell.fingerprint != majority:
                        cell.excluded = "fingerprint"
        else:
            mixed[condition.id] = None

    for cell in cells:
        if cell.excluded or cell.value is None:
            continue
        if filters.exclude_invalid and cell.valid is False:
            cell.excluded = "invalid"
        elif filters.exclude_truncated and cell.truncated:
            cell.excluded = "truncated"

    def usable(cell: _Cell) -> bool:
        return cell.value is not None and not cell.excluded

    usable_keys = {
        condition.id: {(c.app_id, c.repeat) for c in by_condition[condition.id] if usable(c)}
        for condition in conditions
    }
    key_sets = list(usable_keys.values())
    balanced = bool(key_sets) and all(ks == key_sets[0] for ks in key_sets)
    common = set.intersection(*key_sets) if key_sets else set()
    if not filters.common_cells:
        notes.append(NOTE_ONE_SIDED)

    def kept(cell: _Cell) -> bool:
        return usable(cell) and (not filters.common_cells or (cell.app_id, cell.repeat) in common)

    # Per (condition, app) values of the kept cells.
    app_values: dict[int, dict[int, list[float]]] = {c.id: defaultdict(list) for c in conditions}
    for cell in cells:
        if kept(cell):
            app_values[cell.condition_id][cell.app_id].append(cell.value)

    seed = experiment.id.int % (2**32)
    endpoints_used = {condition_endpoint_slug(c) for c in conditions}
    if len(endpoints_used) > 1:
        notes.append(NOTE_TIMINGS)

    leaderboard = []
    for condition in conditions:
        mine = by_condition[condition.id]
        values = app_values[condition.id]
        app_means = [sum(v) / len(v) for v in values.values() if v]
        kept_cells = [c for c in mine if kept(c)]
        missing = {
            "failed": sum(1 for c in mine if c.code in ("x", "k")),
            "ungraded": sum(1 for c in mine if c.code == "u"),
            "excluded_invalid": sum(1 for c in mine if c.excluded in ("invalid", "fingerprint")),
            "excluded_truncated": sum(1 for c in mine if c.excluded == "truncated"),
            "not_common": sum(1 for c in mine if usable(c) and not kept(c)),
        }
        seconds = sorted(c.seconds for c in kept_cells if c.seconds is not None)
        validity = {"ok": 0, "warn": 0, "invalid": 0}
        for c in mine:
            if c.code not in ("c", "f", "u"):
                continue
            if c.valid is False:
                validity["invalid"] += 1
            elif c.validity_warn:
                validity["warn"] += 1
            else:
                validity["ok"] += 1
        leaderboard.append(
            {
                "condition_id": condition.id,
                "label": condition_label(condition),
                "model": {
                    "id": condition.model_id,
                    "name": condition.model.model_name,
                    "endpoint_slug": condition_endpoint_slug(condition),
                },
                "harness": condition.harness,
                "n_apps": len(app_means),
                "repeats_per_app": round(len(kept_cells) / len(app_means), 2) if app_means else 0,
                "n_cells": len(apps) * experiment.repeats,
                "n_graded": len(kept_cells),
                "k_passed": sum(1 for c in kept_cells if c.passed),
                "n_missing": missing,
                "pass_rate_mean": round(_mean(app_means), 4) if app_means else None,
                "pass_rate_ci95": _bootstrap(
                    cluster_bootstrap_ci,
                    {a: v for a, v in values.items() if v},
                    seed ^ condition.id,
                ),
                "cost_usd": round(sum(c.cost for c in mine), 4),
                "tokens_total": int(sum(c.tokens for c in mine)),
                "median_seconds": round(_percentile(seconds, 0.5), 1) if seconds else None,
                "timing_endpoint_slug": condition_endpoint_slug(condition),
                "terminations": dict(Counter(c.termination for c in mine if c.termination)),
                "validity": validity,
                "mixed_fingerprints": mixed[condition.id],
            },
        )
    ranked = sorted(leaderboard, key=lambda r: (r["pass_rate_mean"] is None, -(r["pass_rate_mean"] or 0.0)))
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank

    paired_rows = []
    if base_condition is not None:
        base_values = app_values[base_condition.id]
        for condition in conditions:
            if condition.id == base_condition.id:
                continue
            values = app_values[condition.id]
            pairs = {a: (values[a], base_values[a]) for a in values if values[a] and base_values.get(a)}
            deltas = [sum(t) / len(t) - sum(b) / len(b) for t, b in pairs.values()]
            wins = sum(1 for d in deltas if d > 0)
            losses = sum(1 for d in deltas if d < 0)
            paired_rows.append(
                {
                    "condition_id": condition.id,
                    "n_pairs": len(pairs),
                    "mean_delta": round(_mean(deltas), 4) if deltas else None,
                    "ci95": _bootstrap(paired_bootstrap_ci, pairs, seed ^ (condition.id << 16) ^ base_condition.id),
                    "wins": wins,
                    "losses": losses,
                    "ties": len(deltas) - wins - losses,
                    "sign_test_p": sign_test_p(wins, losses),
                    "wilcoxon_p": wilcoxon_p(deltas),
                },
            )
        adjusted = holm([r["wilcoxon_p"] for r in paired_rows])
        for row, p in zip(paired_rows, adjusted, strict=True):
            row["p_holm"] = p
            for key in ("sign_test_p", "wilcoxon_p", "p_holm"):
                if interim:
                    row[key] = None
                elif row[key] is not None:
                    row[key] = round(row[key], 6)

    n_apps = len({c.app_id for c in cells if kept(c)})
    if interim:
        notes.insert(0, NOTE_INTERIM)
    elif paired_rows and n_apps < MIN_APPS_FOR_INFERENCE:
        notes.append(NOTE_FEW_APPS)

    matrix_cells = []
    for condition in conditions:
        per_app: dict[int, list[_Cell]] = defaultdict(list)
        for cell in by_condition[condition.id]:
            per_app[cell.app_id].append(cell)
        for app in apps:
            runs = sorted(per_app.get(app.id, []), key=lambda c: c.repeat)
            used = [c for c in runs if kept(c)]
            matrix_cells.append(
                {
                    "condition_id": condition.id,
                    "app_id": app.id,
                    "k": sum(1 for c in used if c.passed),
                    "n": len(used),
                    "mean": round(sum(c.value for c in used) / len(used), 4) if used else None,
                    "runs": [
                        {
                            "job_id": c.job_id,
                            "repeat_index": c.repeat,
                            "pass_rate": round(c.value, 4) if c.value is not None else None,
                            "termination": c.termination,
                            "valid": c.valid,
                            "code": c.code,
                        }
                        for c in runs
                    ],
                },
            )

    return {
        "metric": filters.metric,
        "exclude_invalid": filters.exclude_invalid,
        "exclude_truncated": filters.exclude_truncated,
        "common_cells": filters.common_cells,
        "fingerprint_filter": filters.fingerprint,
        "baseline_condition_id": base_condition.id if base_condition is not None else None,
        "baseline_source": baseline_source,
        "interim": interim,
        "balanced": balanced,
        "leaderboard": ranked,
        "matrix": {"apps": [{"id": a.id, "slug": a.slug, "name": a.name} for a in apps], "cells": matrix_cells},
        "paired": {
            "baseline_condition_id": base_condition.id if base_condition is not None else None,
            "pairing": "app_blocked",
            "n_apps": n_apps,
            "rows": paired_rows,
        },
        "notes": notes,
        "generated_at": now,
    }
