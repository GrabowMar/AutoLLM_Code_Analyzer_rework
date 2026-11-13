"""``CostPrior`` maintenance and lookup (spec section 4.4 step 1; refreshed nightly and on run completion).

Keys are ``kind|harness|model|size|tier``. ``refresh_priors`` writes four levels for every observed run,
so the estimator's fallback chain always has a row to try:

1. ``kind|harness|model|size|tier``
2. ``kind|harness|model|*|tier``
3. ``kind|harness|model_family|size|*``
4. ``kind|harness|*|size|*``

A key is used only with ``n >= PRIOR_MIN_N`` runs. Two auxiliary row families share the table (their
statistics are documented per field):

* ``calibration|<model>|<provider_sort>|*|*``: ``cost_p50`` = median(billed cost / catalog cost) of the
  model under that routing policy (the heuristic's routing calibration);
* ``overhead|<kind>|<harness>|*|*``: ``price_at = {infra_retry_rate, mean_partial_cost,
  mean_partial_list_cost, failed_call_cost_share}`` (the estimator's failure overhead, step 6).

Chain rows carry the billed percentiles (``cost_p*``) and the list ones (``list_cost_p*``): the estimator
uses the list ones for the list estimate, so a prior of subscription runs (billed 0) still prices a list cap.

Observations are completed runs of the last ``PRIOR_WINDOW_DAYS`` that recorded a cost
(``metrics.cost_usd``; runs that recorded none are left out, never counted as free). Percentiles are
pure Python (D18).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from statistics import median
from typing import Any

from django.db import transaction
from django.utils import timezone

from backend.generation.budget import constants as k

__all__ = [
    "Observation",
    "calibration",
    "chain_keys",
    "lookup",
    "observations",
    "overhead",
    "percentile",
    "prior_key",
    "refresh_for_run",
    "refresh_priors",
]

logger = logging.getLogger(__name__)
W = k.WILDCARD


def prior_key(kind: str, harness: str, model: str, size: str, tier: str) -> str:
    return "|".join(str(part or W) for part in (kind, harness, model, size, tier))


def chain_keys(*, kind: str, harness: str, model: str, family: str, size: str, tier: str) -> list[str]:
    """The fallback chain of 4.4 step 1, most specific first (duplicates removed)."""
    return list(
        dict.fromkeys(
            [
                prior_key(kind, harness, model, size, tier),
                prior_key(kind, harness, model, W, tier),
                prior_key(kind, harness, family, size, W),
                prior_key(kind, harness, W, size, W),
            ],
        ),
    )


def percentile(values: Iterable[float], q: float) -> float | None:
    """Linear-interpolation percentile (``q`` in 0..1); ``None`` for no values."""
    data = sorted(float(v) for v in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    pos = q * (len(data) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


@dataclass(frozen=True)
class Observation:
    """One finished run, as the priors see it."""

    kind: str
    harness: str
    model: str
    family: str
    size: str
    tier: str
    provider_sort: str
    cost: float
    list_cost: float | None
    requests: float | None
    prompt_tokens: float | None
    cached_tokens: float | None
    completion_tokens: float | None
    wall_s: float | None
    input_per_mtok: float | None
    output_per_mtok: float | None
    infra_retries: int
    prior_attempts_cost: float
    prior_attempts_list_cost: float | None = None

    @property
    def keys(self) -> list[str]:
        """The distinct chain keys this run feeds (two levels coincide when the size bucket is unknown)."""
        return chain_keys(
            kind=self.kind,
            harness=self.harness,
            model=self.model,
            family=self.family,
            size=self.size,
            tier=self.tier,
        )


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _observation(run: Any) -> Observation | None:
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    cost = _num(metrics.get("cost_usd"))
    if cost is None:
        return None  # no recorded cost: not an observation (never a free run)
    manifest = getattr(getattr(run, "manifest", None), "manifest", None) or {}
    model_block = manifest.get("model") or {}
    pricing = model_block.get("pricing") or {}
    harness = (manifest.get("harness") or {}).get("name") or ""
    slug = model_block.get("slug") or (run.model.model_id if run.model_id else "")
    family = (getattr(run.model, "family", "") or "").lower() if run.model_id else ""
    family = family or slug.split("/", 1)[0].lower()
    derived = getattr(run.task_version, "derived", None) if run.task_version_id else None
    size = (derived.get("size_bucket") if isinstance(derived, dict) else None) or W
    tier = getattr(run.resource_policy, "tier", "") if run.resource_policy_id else ""
    effective = ((manifest.get("resources") or {}).get("effective") or {}).get("content") or {}
    provider_sort = ((effective.get("routing") or {}).get("provider_sort")) or "none"
    requests = metrics.get("requests_by_class") or {}
    execution = run.execution if isinstance(run.execution, dict) else {}
    attempts = execution.get("attempts") or []
    return Observation(
        kind=run.kind,
        harness=harness,
        model=slug,
        family=family,
        size=size,
        tier=tier or W,
        provider_sort=provider_sort,
        cost=cost,
        list_cost=_num(metrics.get("list_cost_usd")),
        requests=float(sum(v for v in requests.values() if isinstance(v, int))) if requests else None,
        prompt_tokens=_num(metrics.get("prompt_tokens")),
        cached_tokens=_num(metrics.get("cached_tokens")),
        completion_tokens=_num(metrics.get("completion_tokens")),
        wall_s=_num(metrics.get("wall_s")) or _num(run.duration_seconds),
        input_per_mtok=_num(pricing.get("input_per_mtok")),
        output_per_mtok=_num(pricing.get("output_per_mtok")),
        infra_retries=sum(1 for a in attempts if isinstance(a, dict) and a.get("reason")),
        prior_attempts_cost=_num(execution.get("prior_attempts_cost_usd")) or 0.0,
        prior_attempts_list_cost=_num(execution.get("prior_attempts_list_cost_usd")),
    )


def observations(*, window_days: int | None = None, runs: Any = None) -> list[Observation]:
    """Finished runs of the window as ``Observation``s (runs without a recorded cost are skipped)."""
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import RunStatus

    if runs is None:
        since = timezone.now() - timedelta(days=window_days or k.PRIOR_WINDOW_DAYS)
        runs = GenerationRun.objects.filter(
            status__in=[RunStatus.COMPLETED, RunStatus.FAILED],
            completed_at__gte=since,
        ).exclude(kind="reference")
    runs = runs.select_related("manifest", "model", "task_version", "resource_policy")
    out = []
    for run in runs.iterator(chunk_size=500):
        obs = _observation(run)
        if obs is not None:
            out.append(obs)
    return out


def _median_or_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return median(present) if present else None


def _stats(rows: list[Observation]) -> dict[str, Any]:
    costs = [r.cost for r in rows]
    prices_in = [r.input_per_mtok for r in rows if r.input_per_mtok is not None]
    prices_out = [r.output_per_mtok for r in rows if r.output_per_mtok is not None]
    price_at: dict[str, Any] = {}
    if prices_in and prices_out:
        price_at = {
            "input_per_mtok": sum(prices_in) / len(prices_in),
            "output_per_mtok": sum(prices_out) / len(prices_out),
        }
    return {
        "n": len(rows),
        "cost_p10": percentile(costs, 0.1),
        "cost_p50": percentile(costs, 0.5),
        "cost_p90": percentile(costs, 0.9),
        "list_cost_p10": percentile([r.list_cost for r in rows if r.list_cost is not None], 0.1),
        "list_cost_p50": _median_or_none([r.list_cost for r in rows]),
        "list_cost_p90": percentile([r.list_cost for r in rows if r.list_cost is not None], 0.9),
        "requests_p50": _median_or_none([r.requests for r in rows]),
        "prompt_tokens_p50": _median_or_none([r.prompt_tokens for r in rows]),
        "cached_tokens_p50": _median_or_none([r.cached_tokens for r in rows]),
        "completion_tokens_p50": _median_or_none([r.completion_tokens for r in rows]),
        "wall_s_p50": percentile([r.wall_s for r in rows if r.wall_s is not None], 0.5),
        "wall_s_p90": percentile([r.wall_s for r in rows if r.wall_s is not None], 0.9),
        "price_at": price_at,
    }


def _grouped(rows: Iterable[Observation]) -> dict[str, list[Observation]]:
    groups: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        for key in row.keys:
            groups[key].append(row)
        groups[f"calibration|{row.model}|{row.provider_sort}|*|*"].append(row)
        groups[f"overhead|{row.kind}|{row.harness or W}|*|*"].append(row)
    return groups


def _list_partial(row: Observation) -> float:
    """The list cost of a run's retried attempts (the billed one when no list amount was recorded)."""
    return row.prior_attempts_list_cost if row.prior_attempts_list_cost is not None else row.prior_attempts_cost


def _aux_stats(key: str, rows: list[Observation]) -> dict[str, Any]:
    if key.startswith("calibration|"):
        ratios = [r.cost / r.list_cost for r in rows if r.list_cost]
        return {
            "n": len(ratios),
            "cost_p50": median(ratios) if ratios else None,
            "price_at": {"routing_calibration": median(ratios) if ratios else None},
        }
    n = len(rows)
    retried = [r for r in rows if r.infra_retries]
    partial = [r.prior_attempts_cost / r.infra_retries for r in retried if r.infra_retries]
    partial_list = [_list_partial(r) / r.infra_retries for r in retried if r.infra_retries]
    total = sum(r.cost for r in rows)
    wasted = sum(r.prior_attempts_cost for r in rows)
    return {
        "n": n,
        "price_at": {
            "infra_retry_rate": (len(retried) / n) if n else 0.0,
            "mean_partial_cost": (sum(partial) / len(partial)) if partial else 0.0,
            "mean_partial_list_cost": (sum(partial_list) / len(partial_list)) if partial_list else 0.0,
            # Failed upstream calls inside an attempt are not observable per call yet (RunAttempt.usage
            # has no failed-call cost); the wasted cost of retried attempts is carried by the two keys above.
            "failed_call_cost_share": 0.0,
            "wasted_share": (wasted / total) if total else 0.0,
        },
    }


def refresh_priors(*, keys: list[str] | None = None, window_days: int | None = None, runs: Any = None) -> int:
    """Recompute the priors (all, or ``keys``) from the window's observations; returns the rows written."""
    from backend.generation.models import CostPrior

    groups = _grouped(observations(window_days=window_days, runs=runs))
    if keys is not None:
        wanted = set(keys)
        groups = {key: rows for key, rows in groups.items() if key in wanted}
    written = 0
    with transaction.atomic():
        for key, rows in groups.items():
            if key.startswith(("calibration|", "overhead|")):
                values = _aux_stats(key, rows)
            else:
                values = _stats(rows)
            CostPrior.objects.update_or_create(key=key[:200], defaults=values)
            written += 1
    return written


def refresh_for_run(run: Any) -> int:
    """Refresh only the keys one finished run contributes to (called on run completion; never raises)."""
    try:
        obs = _observation(run)
        if obs is None:
            return 0
        keys = [
            *obs.keys,
            f"calibration|{obs.model}|{obs.provider_sort}|*|*",
            f"overhead|{obs.kind}|{obs.harness or W}|*|*",
        ]
        return refresh_priors(keys=keys)
    except Exception:
        logger.exception("Refreshing the cost priors of run %s failed", getattr(run, "pk", None))
        return 0


def lookup(
    *,
    kind: str,
    harness: str,
    model: str,
    family: str,
    size: str,
    tier: str,
    priors: Mapping[str, Any] | None = None,
) -> tuple[str, Any] | None:
    """The first prior of the chain with ``n >= PRIOR_MIN_N``: ``(key, CostPrior)``, or ``None``.

    ``priors`` (``{key: CostPrior-like}``) replaces the database, for tests and bulk estimates.
    """
    keys = chain_keys(kind=kind, harness=harness, model=model, family=family, size=size, tier=tier)
    if priors is None:
        from backend.generation.models import CostPrior

        rows = {row.key: row for row in CostPrior.objects.filter(key__in=keys)}
    else:
        rows = {key: priors[key] for key in keys if key in priors}
    for key in keys:
        row = rows.get(key)
        if row is not None and (row.n or 0) >= k.PRIOR_MIN_N and row.cost_p50 is not None:
            return key, row
    return None


def calibration(model: str, provider_sort: str, *, priors: Mapping[str, Any] | None = None) -> float:
    """``median(cost / catalog cost)`` for (model, routing policy), 1.0 without enough history."""
    key = f"calibration|{model}|{provider_sort or 'none'}|*|*"
    row = priors.get(key) if priors is not None else _row(key)
    if row is None or (row.n or 0) < k.PRIOR_MIN_N or not row.cost_p50:
        return 1.0
    return float(row.cost_p50)


def overhead(kind: str, harness: str, *, priors: Mapping[str, Any] | None = None) -> dict[str, float]:
    """``{infra_retry_rate, mean_partial_cost, mean_partial_list_cost, failed_call_cost_share}`` (zeros
    without enough history; ``mean_partial_list_cost`` falls back to ``mean_partial_cost`` on rows written
    before it existed)."""
    key = f"overhead|{kind}|{harness or W}|*|*"
    row = priors.get(key) if priors is not None else _row(key)
    empty = {
        "infra_retry_rate": 0.0,
        "mean_partial_cost": 0.0,
        "mean_partial_list_cost": 0.0,
        "failed_call_cost_share": 0.0,
    }
    if row is None or (row.n or 0) < k.PRIOR_MIN_N:
        return empty
    data = row.price_at if isinstance(row.price_at, dict) else {}
    out = {name: float(data.get(name) or 0.0) for name in empty}
    if data.get("mean_partial_list_cost") is None:
        out["mean_partial_list_cost"] = out["mean_partial_cost"]
    return out


def _row(key: str) -> Any:
    from backend.generation.models import CostPrior

    return CostPrior.objects.filter(key=key).first()
