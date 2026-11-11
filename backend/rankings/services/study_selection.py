"""MSS v2 study selection: inputs from the database, then a pure, replayable pipeline.

``build_inputs`` is the only part that reads the database (frame rows, the
latest popularity market snapshot) and, optionally, the network (the live
OpenRouter credit). ``run_selection(inputs, params)`` is pure: it scores the
frame, assigns price tiers, routes and the quality floor, runs the stratified
lexicographic greedy with an exact look-ahead, explains every decision and,
when asked, runs the sensitivity analysis. ``replay`` in :mod:`snapshot`
feeds a stored payload's inputs back into ``run_selection``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import logging
import math
import random
from typing import Any

from backend.rankings.services import selection as sel
from backend.rankings.services.constants import DIRICHLET_CONCENTRATION
from backend.rankings.services.constants import MSS_TIER_WEIGHTS
from backend.rankings.services.constants import MSS_VERSION
from backend.rankings.services.constants import MSS_WEIGHTS
from backend.rankings.services.constants import PLANNING_BASKET
from backend.rankings.services.constants import REFERENCE_BASKET
from backend.rankings.services.constants import RETRY_FACTOR
from backend.rankings.services.constants import ROUTE_RANK
from backend.rankings.services.constants import RUNS_PER_MODEL
from backend.rankings.services.constants import SELECTION_DEFAULTS
from backend.rankings.services.constants import SENSITIVITY_DRAWS
from backend.rankings.services.constants import SENSITIVITY_SEED
from backend.rankings.services.constants import TIERS
from backend.rankings.services.mss import NOMINAL
from backend.rankings.services.mss import Variant
from backend.rankings.services.mss import band_price_score
from backend.rankings.services.mss import capability_score
from backend.rankings.services.mss import e8_flow_step
from backend.rankings.services.mss import mid_rank_percentiles
from backend.rankings.services.mss import percentile
from backend.rankings.services.mss import price_of
from backend.rankings.services.mss import score_frame
from backend.rankings.services.pricing import planning_cost

logger = logging.getLogger(__name__)

MARKET_SOURCE = "openrouter_popularity_market"


def default_params(**overrides: Any) -> dict[str, Any]:
    params = {
        **json.loads(json.dumps(SELECTION_DEFAULTS)),
        "version": MSS_VERSION,
        "weights": dict(MSS_WEIGHTS),
        "tier_weights": dict(MSS_TIER_WEIGHTS),
        "reference_basket": dict(REFERENCE_BASKET),
        "planning_basket": dict(PLANNING_BASKET),
        "retry_factor": RETRY_FACTOR,
        "runs_per_model": RUNS_PER_MODEL,
        "include_previews": False,
        "exclude": [],
        "force": [],
        "seed": SENSITIVITY_SEED,
        "draws": dict(SENSITIVITY_DRAWS),
        "dirichlet_concentration": DIRICHLET_CONCENTRATION,
    }
    params.update({key: value for key, value in overrides.items() if value is not None})
    return params


# ── Inputs (database) ──────────────────────────────────────────────────────


def latest_market() -> tuple[dict[str, Any] | None, int | None]:
    """The newest derived popularity market (``(None, None)`` before the first import or migration)."""
    from django.db import DatabaseError
    from django.db import transaction

    from backend.rankings.models import SourceSnapshot

    try:
        # A savepoint, so a missing table does not abort the request's transaction (ATOMIC_REQUESTS).
        with transaction.atomic():
            snap = SourceSnapshot.objects.filter(source=MARKET_SOURCE).order_by("-fetched_at", "-pk").first()
    except DatabaseError as exc:  # table missing until `migrate rankings` has run
        logger.warning("No popularity market available: %s", exc)
        return None, None
    return (snap.payload, snap.pk) if snap else (None, None)


def build_inputs(
    as_of: dt.date,
    *,
    include_previews: bool = False,
    budgets: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything ``run_selection`` needs, read from the database (read-only)."""
    from backend.rankings.services.frame import build_frame
    from backend.rankings.services.frame import load_frame_config
    from backend.rankings.services.frame import load_rows

    config = load_frame_config()
    frame = build_frame(
        rows if rows is not None else load_rows(),
        as_of,
        include_previews=include_previews,
        config=config,
    )
    for entry in frame["candidates"]:
        go = entry["routes"].get("go") or {}
        entry["go_limit_usd"] = config.go_limit(go.get("served_name")) if go else None
    market, market_id = latest_market()
    go_limits = config.go_limits or {}
    return {
        "as_of": as_of.isoformat(),
        "frame": frame,
        "market": market,
        "market_snapshot_id": market_id,
        "budgets": budgets or {},
        "go_limits": {k: v for k, v in go_limits.items() if k != "monthly_usd"},
    }


CREDIT_CACHE_KEY = "rankings:openrouter_credit:v1:{user}"
CREDIT_CACHE_SECONDS = 600


def openrouter_credit(user_id: int = 1, *, allow_network: bool = True) -> dict[str, Any] | None:
    """The live OpenRouter credit of ``user_id``'s stored key, cached for 10 minutes.

    With ``allow_network=False`` only the cached value is returned. ``None``
    when there is no key or the request failed (the key is never logged).
    """
    from django.core.cache import cache

    from backend.rankings.services.openrouter_api import OpenRouterError
    from backend.rankings.services.openrouter_api import fetch_credits
    from backend.rankings.services.openrouter_api import openrouter_key

    key = CREDIT_CACHE_KEY.format(user=user_id)
    cached = cache.get(key)
    if cached is not None or not allow_network:
        return cached
    secret = openrouter_key(user_id)
    if not secret:
        return None
    try:
        credit = fetch_credits(key=secret)
    except OpenRouterError as exc:
        logger.warning("OpenRouter credit unavailable: %s", exc)
        return None
    cache.set(key, credit, CREDIT_CACHE_SECONDS)
    return credit


def resolve_budgets(
    params: dict[str, Any],
    *,
    live_credit: dict[str, Any] | None,
    fallback_openrouter: float | None,
) -> dict[str, Any]:
    """Hard budgets with the contingency (A1 §4); records where the OpenRouter amount came from."""
    contingency = float(params.get("contingency", SELECTION_DEFAULTS["contingency"]))
    anthropic = params.get("anthropic_budget")
    if params.get("openrouter_budget") is not None:
        available, source = float(params["openrouter_budget"]), "explicit --openrouter-budget / parameter"
    elif live_credit and live_credit.get("available") is not None:
        available, source = float(live_credit["available"]), live_credit.get("source", "live credit")
    elif fallback_openrouter is not None:
        available, source = float(fallback_openrouter), "fallback --openrouter-budget (live credit unavailable)"
    else:
        available, source = 0.0, "no OpenRouter credit known: OpenRouter-routed picks are not selectable"
    return {
        "contingency": contingency,
        "anthropic_usd": anthropic,
        "anthropic_effective": None if anthropic is None else round((1 - contingency) * float(anthropic), 4),
        "openrouter_available_usd": available,
        "openrouter_source": source,
        "openrouter_credit": live_credit,
        "openrouter_effective": round((1 - contingency) * available, 4),
    }


# ── Pure pipeline ──────────────────────────────────────────────────────────


def _global_key(r: dict[str, Any]) -> tuple:
    routes = r.get("routes") or {}
    best_route = min((ROUTE_RANK[k] for k, v in routes.items() if v.get("paid") and k in ROUTE_RANK), default=3)
    return (
        -round(r["mss"] or 0.0, 4),
        -(r["scores"]["popularity"] or 0.0),
        -(r["scores"]["price"] or 0.0),
        best_route,
        r["canonical_id"],
    )


class Stratifier:
    """Reruns the stratified procedure under weight, specification, tier-cut and floor changes."""

    def __init__(self, inputs: dict[str, Any], params: dict[str, Any], scored: list[dict[str, Any]]):
        self.inputs = inputs
        self.params = params
        self.as_of = dt.date.fromisoformat(inputs["as_of"])
        self.scored = scored
        self.budgets = inputs.get("budgets") or {}
        self.cons = sel.Constraints.from_params(params, self.budgets)
        self.excluded = set(params.get("exclude") or [])
        self._cache: dict[tuple, dict[str, Any]] = {}
        self._floor_cache: dict[tuple, Any] = {}

    # -- preparation --------------------------------------------------------

    def prepare(
        self,
        scored: list[dict[str, Any]] | None = None,
        *,
        weights: dict[str, float] | None = None,
        tier_scale: tuple[float, float] = (1.0, 1.0),
        go_relaxed: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        scored = scored if scored is not None else self.scored
        eligible = [r for r in scored if r["eligible"] and r["canonical_id"] not in self.excluded]
        cuts = self.params.get("tier_cuts")
        tiers, tier_info = sel.assign_tiers(
            {r["canonical_id"]: r["raw"]["run_cost_usd"] for r in eligible},
            cuts=tuple(cuts) if cuts else None,
            scale=tier_scale,
        )
        bands = sel.tier_bands(tier_info)
        tier_info["bands_usd"] = {t: [round(b[0], 6), round(b[1], 6)] for t, b in bands.items()}
        percentile_mode = (self.params.get("_variant") or {}).get("normalisation") == "percentile"
        items = []
        for r in eligible:
            tier = tiers[r["canonical_id"]]
            item = {
                "canonical_id": r["canonical_id"],
                "vendor": r["vendor"],
                "origin": r["origin"],
                "open_weights": r["open_weights"],
                "routes": r["routes"],
                "go_limit_usd": r.get("go_limit_usd"),
                "scores": r["scores"],
                "raw": r["raw"],
                "flags": list(r["flags"]),
                "mss": r["mss"],
                "coverage": r["coverage"],
                "tier": tier,
                "tier_price_score": band_price_score(r["raw"]["run_cost_usd"], bands[tier]),
            }
            route = sel.choose_route(
                item,
                contingency=self.budgets.get("contingency", SELECTION_DEFAULTS["contingency"]),
                study_months=float(self.params.get("study_months", 1)),
                go_limits_relaxed=go_relaxed,
            )
            if route["route"] == "anthropic" and self.params.get("anthropic_cache_hit") is not None:
                # B-anthropic planned at an explicit cache-hit share (no Anthropic run measured yet).
                h = float(self.params["anthropic_cache_hit"])
                route["cost"] = planning_cost(price_of(r), basket={**PLANNING_BASKET, "cache_hit": h})
            item.update(
                route=route["route"],
                route_cost=route["cost"],
                route_reason=route["reason"],
                flags=sorted(set(item["flags"]) | set(route["flags"])),
            )
            items.append(item)
        if percentile_mode:
            for tier in TIERS:
                members = {i["canonical_id"]: i["raw"]["run_cost_usd"] for i in items if i["tier"] == tier}
                pct = mid_rank_percentiles(members, higher_is_better=False)
                for i in items:
                    if i["tier"] == tier:
                        i["tier_price_score"] = pct.get(i["canonical_id"])
        w = weights or self.params.get("tier_weights") or MSS_TIER_WEIGHTS
        for i in items:
            agg = sel.mss_tier(i, w)
            i["mss_tier"] = agg["mss"]
            i["mss_tier_coverage"] = agg["coverage"]
        items.sort(key=sel.order_key)
        return items, tier_info

    def popularity_cut(self, items: list[dict[str, Any]]) -> float | None:
        pct = float(self.params.get("missing_capability_popularity_pct", 75.0))
        return percentile([i["scores"]["popularity"] for i in items if i["scores"]["popularity"] is not None], pct)

    def _admissible(
        self,
        items,
        levels,
        pop_cut,
        *,
        basis: str = "point",
    ) -> dict[str, tuple[bool, str | None, float | None, bool]]:
        """Floor admission per model. ``basis="upper"`` (S6 variant only) admits a model whose
        WebDev rating's upper 95% bound reaches the threshold; the threshold itself is always
        the tier percentile of the point estimates."""
        thresholds = sel.floor_thresholds(items, levels)
        out = {}
        for i in items:
            thr = thresholds[i["tier"]]
            q = i["scores"]["capability"]
            if thr is None:
                out[i["canonical_id"]] = (True, None, None, False)
            elif q is not None:
                q_test = q
                if basis == "upper":
                    upper = (i["raw"].get("webdev") or {}).get("rating_upper")
                    if isinstance(upper, (int, float)):
                        q_test = max(q, capability_score(float(upper)) or q)
                ok = q_test >= thr - 1e-12
                out[i["canonical_id"]] = (
                    ok,
                    None if ok else f"floor:Q-quality(capability {q:.3f} < {thr:.3f})",
                    thr,
                    False,
                )
            else:
                pop = i["scores"]["popularity"]
                ok = pop is not None and pop_cut is not None and pop >= pop_cut - 1e-12
                reason = None if ok else "floor:Q-quality(capability missing, popularity below the top quartile)"
                out[i["canonical_id"]] = (ok, reason, thr, ok)
        return out

    def _selectable(self, items, admissible, cons) -> tuple[sel.Problem, list[int], list[dict[str, Any]]]:
        pool = [i for i in items if i["route"] and admissible[i["canonical_id"]][0]]
        comp = sel.compile_items(pool)
        problem = sel.Problem(comp, cons)
        return problem, list(range(len(pool))), pool

    def floor_percentiles(self, floor: list[float] | None = None) -> list[float]:
        pcts = floor if floor is not None else list(self.params.get("floor_percentiles") or [])
        return [] if not self.params.get("quality_floor", True) else list(pcts)

    def resolve_floor(
        self,
        items,
        floor: list[float] | None,
        cons,
        *,
        basis: str = "point",
    ) -> tuple[dict, list[dict[str, Any]], bool]:
        """Per-tier floor levels after the stepwise relaxation (A1 §3)."""
        pcts = self.floor_percentiles(floor)
        pop_cut = self.popularity_cut(items)
        if not pcts:
            levels = dict.fromkeys(TIERS)
            return levels, [], self._feasible(items, levels, pop_cut, cons, basis=basis)
        idx = dict.fromkeys(TIERS, 0)
        relaxations: list[dict[str, Any]] = []
        base = dict.fromkeys(TIERS, pcts[0])
        if not self._feasible(items, base, pop_cut, cons, basis=basis) and not self._feasible(
            items,
            dict.fromkeys(TIERS),
            pop_cut,
            cons,
            basis=basis,
        ):
            # Infeasible even without any floor: the floor is not the cause, so it is not relaxed.
            return base, [], False
        while True:
            levels = {t: pcts[idx[t]] for t in TIERS}
            if self._feasible(items, levels, pop_cut, cons, basis=basis):
                return levels, relaxations, True
            culprits = []
            for t in TIERS:
                if idx[t] >= len(pcts) - 1:
                    continue
                trial = {**levels, t: None}
                if self._feasible(items, trial, pop_cut, cons, basis=basis):
                    culprits.append(t)
            if not culprits:
                # Relaxing one tier's floor alone does not help: relax every tier that still can.
                culprits = [t for t in TIERS if idx[t] < len(pcts) - 1]
            if not culprits:
                return levels, relaxations, False
            for t in culprits:
                relaxations.append(
                    {
                        "tier": t,
                        "from_percentile": pcts[idx[t]],
                        "to_percentile": pcts[idx[t] + 1],
                        "reason": "quotas unattainable",
                    },
                )
                idx[t] += 1

    def _feasible(self, items, levels, pop_cut, cons, *, basis: str = "point") -> bool:
        admissible = self._admissible(items, levels, pop_cut, basis=basis)
        problem, order, _ = self._selectable(items, admissible, cons)
        return problem.feasible(problem.empty(), order)

    # -- one run ------------------------------------------------------------

    def run(
        self,
        *,
        weights: dict[str, float] | None = None,
        variant: Variant | None = None,
        tier_scale: tuple[float, float] = (1.0, 1.0),
        floor: list[float] | None = None,
        floor_basis: str = "point",
    ) -> dict[str, Any]:
        """Picks of the stratified procedure under one change (cached by its full input signature).

        A change under which a forced pick breaks a cap (e.g. S5 moves it into a
        full tier) is an infeasible draw (``picks`` None), never an error.
        """
        scored = self.scored
        if variant is not None:
            scored = score_frame(
                self.inputs["frame"]["candidates"],
                as_of=self.as_of,
                market=self.inputs.get("market"),
                variant=variant,
                weights=self.params.get("weights") or MSS_WEIGHTS,
            )
            _attach_go_limits(scored, self.inputs)
            params_variant = variant.describe()
        else:
            params_variant = None
        saved = self.params.get("_variant")
        self.params["_variant"] = params_variant
        try:
            items, _ = self.prepare(scored, weights=weights, tier_scale=tier_scale)
        finally:
            self.params["_variant"] = saved
        floor_key = (
            tuple(
                (
                    i["canonical_id"],
                    i["tier"],
                    i["route"],
                    i["route_cost"],
                    i["scores"]["capability"],
                    i["scores"]["popularity"],
                )
                for i in items
            ),
            tuple(floor) if floor is not None else None,
            floor_basis,
        )
        if floor_key not in self._floor_cache:
            self._floor_cache[floor_key] = self.resolve_floor(items, floor, self.cons, basis=floor_basis)
        levels, _, ok = self._floor_cache[floor_key]
        order_ids = [i["canonical_id"] for i in items]
        scores = {i["canonical_id"]: i["mss_tier"] or 0.0 for i in items}
        tiers = {i["canonical_id"]: i["tier"] for i in items}
        if not ok:
            return {"picks": None, "order": order_ids, "scores": scores, "tiers": tiers}
        key = (floor_key, tuple(order_ids))
        if key not in self._cache:
            pop_cut = self.popularity_cut(items)
            admissible = self._admissible(items, levels, pop_cut, basis=floor_basis)
            problem, order, pool = self._selectable(items, admissible, self.cons)
            forced = [k for k, i in enumerate(pool) if i["canonical_id"] in set(self.params.get("force") or [])]
            try:
                result = sel.run_greedy(problem, [k for k in order if k not in forced], forced=forced)
            except sel.SelectionInfeasibleError:
                result = {"complete": False, "picks": []}
            self._cache[key] = [pool[k]["canonical_id"] for k in result["picks"]] if result["complete"] else None
        return {"picks": self._cache[key], "order": order_ids, "scores": scores, "tiers": tiers}

    def spec_draw(self, rng: random.Random) -> Variant:
        """One S3 specification draw."""
        ratings = {}
        for r in self.scored:
            wd = r["raw"].get("webdev")
            if wd and wd.get("rating_lower") is not None and wd.get("rating_upper") is not None:
                sd = (float(wd["rating_upper"]) - float(wd["rating_lower"])) / 3.92
                ratings[r["canonical_id"]] = rng.gauss(float(wd["rating"]), sd) if sd > 0 else float(wd["rating"])
        return Variant(
            normalisation=rng.choice(["goalposts", "percentile"]),
            popularity=rng.choice(["geo", "volume", "value"]),
            cache_hit=rng.uniform(0.5, 0.95),
            censor_factor=rng.choice([0.1, 0.5, 1.0]),
            missing_capability=rng.choice(["renormalise", "p25"]),
            ratings=ratings,
        )


def _attach_go_limits(scored: list[dict[str, Any]], inputs: dict[str, Any]) -> None:
    limits = {e["canonical_id"]: e.get("go_limit_usd") for e in inputs["frame"]["candidates"]}
    for r in scored:
        r["go_limit_usd"] = limits.get(r["canonical_id"])


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return round(cov / (vx * vy), 4) if vx and vy else None


def validity(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Convergent validity of popularity (reported, never scored)."""
    eligible = [r for r in scored if r["eligible"]]
    arena = [
        (r["scores"]["popularity"], math.log10(r["raw"]["lmarena_interactions"]))
        for r in eligible
        if r["raw"].get("lmarena_interactions")
        and r["scores"]["popularity"] is not None
        and "lmarena_proxy" not in r["flags"]
    ]
    hf = [
        (r["scores"]["popularity"], math.log10(dl))
        for r in eligible
        if r.get("open_weights")
        and (dl := (r.get("inputs") or {}).get("hf_downloads"))
        and r["scores"]["popularity"] is not None
    ]
    code_top10 = sorted(
        r["canonical_id"] for r in eligible if ((r.get("inputs") or {}).get("popularity") or {}).get("code_tags")
    )
    return {
        "spearman_popularity_vs_lmarena_interactions": {
            "rho": _spearman(*zip(*arena, strict=True)) if arena else None,
            "n": len(arena),
        },
        "spearman_popularity_vs_hf_downloads_open_weight": {
            "rho": _spearman(*zip(*hf, strict=True)) if hf else None,
            "n": len(hf),
        },
        "in_code_tag_top10": code_top10,
    }


def sources_for(inputs: dict[str, Any], scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Provenance of every input of a selection (name, url, license, as_of, fetched_at, window)."""
    market = inputs.get("market") or {}
    lmarena_dates = sorted(
        {
            str(((r.get("inputs") or {}).get("lmarena") or {}).get("publish_date"))
            for r in scored
            if ((r.get("inputs") or {}).get("lmarena") or {}).get("publish_date")
        },
    )
    price_dates = sorted(
        {
            str(((r.get("inputs") or {}).get("list_price") or {}).get("fetched_at"))[:10]
            for r in scored
            if (r.get("inputs") or {}).get("list_price")
        },
    )
    budgets = inputs.get("budgets") or {}
    return [
        {
            "name": "OpenRouter Data API rankings-daily (popularity)",
            "url": "https://openrouter.ai/api/v1/datasets/rankings-daily",
            "license": market.get("license", "CC BY 4.0"),
            "citation": market.get("citation"),
            "as_of": market.get("as_of"),
            "fetched_at": market.get("fetched_at"),
            "window": market.get("window"),
            "snapshot_id": inputs.get("market_snapshot_id"),
        },
        {
            "name": "First-party list prices (models.dev provider records; OpenRouter endpoints as fallback)",
            "url": "https://models.dev/api.json",
            "license": "MIT (models.dev)",
            "as_of": price_dates[-1] if price_dates else None,
            "fetched_at": price_dates,
        },
        {
            "name": "LMArena WebDev leaderboard (capability)",
            "url": "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset",
            "license": "CC BY 4.0",
            "as_of": lmarena_dates[-1] if lmarena_dates else None,
            "publish_dates": lmarena_dates,
        },
        {
            "name": "OpenCode Go monthly limits",
            "url": (inputs.get("go_limits") or {}).get("source"),
            "as_of": (inputs.get("go_limits") or {}).get("fetched"),
            "plan": (inputs.get("go_limits") or {}).get("plan"),
        },
        {
            "name": "OpenRouter credit (B-openrouter)",
            "url": "https://openrouter.ai/api/v1/credits",
            "as_of": ((budgets.get("openrouter_credit") or {}) or {}).get("fetched_at"),
            "value_usd": budgets.get("openrouter_available_usd"),
            "source": budgets.get("openrouter_source"),
        },
    ]


def inputs_sha256(inputs: dict[str, Any], params: dict[str, Any]) -> str:
    blob = json.dumps(
        {"inputs": inputs, "params": {k: v for k, v in params.items() if not k.startswith("_")}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def run_selection(
    inputs: dict[str, Any],
    params: dict[str, Any],
    *,
    sensitivity: bool = True,
    progress=None,
) -> dict[str, Any]:
    """The full MSS v2 selection payload from ``inputs`` (pure; see module docstring)."""
    params = dict(params)
    as_of = dt.date.fromisoformat(inputs["as_of"])
    frame = inputs["frame"]
    scored = score_frame(
        frame["candidates"],
        as_of=as_of,
        market=inputs.get("market"),
        variant=NOMINAL,
        weights=params.get("weights") or MSS_WEIGHTS,
    )
    _attach_go_limits(scored, inputs)
    flow = [*frame["flow"], e8_flow_step(scored)]
    excluded_by_researcher = [
        cid for cid in params.get("exclude") or [] if any(r["canonical_id"] == cid for r in scored)
    ]
    if excluded_by_researcher:
        flow.append(
            {
                "step": "R",
                "label": "Excluded by the researcher (--exclude)",
                "remaining": sum(
                    1 for r in scored if r["eligible"] and r["canonical_id"] not in excluded_by_researcher
                ),
                "excluded": [{"id": c, "code": "researcher_exclude"} for c in excluded_by_researcher],
            },
        )

    eligible = sorted((r for r in scored if r["eligible"]), key=_global_key)
    for rank, r in enumerate(eligible, start=1):
        r["mss_rank"] = rank

    strat = Stratifier(inputs, params, scored)
    cons = strat.cons
    items, tier_info = strat.prepare()
    levels, relaxations, feasible = strat.resolve_floor(items, None, cons)
    pop_cut = strat.popularity_cut(items)
    admissible = strat._admissible(items, levels, pop_cut)  # noqa: SLF001
    capability_tiers = {t: [i["scores"]["capability"] for i in items if i["tier"] == t] for t in TIERS}
    sel.capability_percentile_in_tier(items)
    for i in items:
        ok, reason, thr, missing_admitted = admissible[i["canonical_id"]]
        i["admissible"], i["floor_reason"], i["capability_floor"] = ok, reason, thr
        if missing_admitted:
            i["flags"] = sorted(set(i["flags"]) | {"capability_missing_admitted"})
        if i["route"] is not None:
            i["go_usage"] = (
                sel.go_usage(
                    i["raw"],
                    i.get("go_limit_usd"),
                    five_hour_share=float((inputs.get("go_limits") or {}).get("five_hour_share", 0.2)),
                    weekly_share=float((inputs.get("go_limits") or {}).get("weekly_share", 0.5)),
                )
                if i["route"] == "go"
                else None
            )
    tier_rank: dict[str, int] = {}
    counters = dict.fromkeys(TIERS, 0)
    for i in items:
        counters[i["tier"]] += 1
        tier_rank[i["canonical_id"]] = counters[i["tier"]]
    by_id = {r["canonical_id"]: r for r in scored}

    payload: dict[str, Any] = {
        "version": MSS_VERSION,
        "as_of": inputs["as_of"],
        "params": {k: v for k, v in params.items() if not k.startswith("_")},
        "constraints": cons.as_dict(),
        "budgets": inputs.get("budgets") or {},
        "flow": flow,
        "tiers": {
            **tier_info,
            "capability_by_tier": {
                t: sorted(v for v in vals if v is not None) for t, vals in capability_tiers.items()
            },
        },
        "floor": {"levels": levels, "relaxations": relaxations, "popularity_cut_for_missing_capability": pop_cut},
        "sources": sources_for(inputs, scored),
        "validity": validity(scored),
    }

    problem, order, pool = strat._selectable(items, admissible, cons)  # noqa: SLF001
    pool_index = {i["canonical_id"]: k for k, i in enumerate(pool)}
    forced_ids = [cid for cid in params.get("force") or [] if cid in pool_index]
    forced = [pool_index[cid] for cid in forced_ids]
    unknown_forced = sorted(set(params.get("force") or []) - set(forced_ids))
    if unknown_forced:
        payload["warnings"] = [
            f"--force {cid}: not an eligible, routable model that passes the floor" for cid in unknown_forced
        ]

    forced_violation = None
    if feasible:
        try:
            result = sel.run_greedy(problem, [k for k in order if k not in forced], forced=forced)
        except sel.SelectionInfeasibleError as exc:
            # A forced set that breaks a tier quota, the vendor cap or a budget is reported, not raised.
            forced_violation = str(exc)
            result = {"complete": False, "picks": []}
    else:
        result = {"complete": False, "picks": []}
    payload["candidates"] = [_candidate_row(r, items) for r in scored]
    payload["inputs"] = _stored_inputs(inputs)
    if not result["complete"]:
        # Diagnose at the most relaxed floor the procedure would reach: once a budget is raised it
        # relaxes the floor as far as needed, so the minimal budgets are exact only there.
        pcts = strat.floor_percentiles()
        diag_levels = dict.fromkeys(TIERS, pcts[-1]) if pcts else dict.fromkeys(TIERS)
        if feasible:
            diag_levels = levels
        d_problem, d_order, _ = strat._selectable(items, strat._admissible(items, diag_levels, pop_cut), cons)  # noqa: SLF001
        diagnosis = sel.diagnose_infeasible(d_problem, d_order)
        diagnosis["diagnosed_at_floor_percentiles"] = diag_levels
        if forced_violation:
            diagnosis["forced_violation"] = forced_violation
        diagnosis["floor_relaxations_tried"] = relaxations
        diagnosis["feasible_without_quality_floor"] = strat._feasible(items, dict.fromkeys(TIERS), pop_cut, cons)  # noqa: SLF001
        diagnosis["note"] = (
            f"The forced picks cannot all be selected: {forced_violation}."
            if forced_violation
            else "No set of models meets every quota, cap and budget. The relaxations show which single "
            "constraint makes it feasible; the minimal budgets are the effective amounts after the contingency."
        )
        study_months = int(params.get("study_months", 1))
        for months in range(study_months + 1, study_months + 4):
            trial = Stratifier(inputs, {**params, "study_months": months}, scored)
            t_items, _ = trial.prepare()
            _, _, ok = trial.resolve_floor(t_items, None, trial.cons)
            if ok:
                diagnosis["feasible_with_study_months"] = months
                break
        payload.update(
            selection=None,
            infeasible=diagnosis,
            blocked=[],
            alternates={},
            constraint_status=[],
            sum_optimal=None,
            sensitivity=None,
        )
        return payload

    picks = [pool[k]["canonical_id"] for k in result["picks"]]
    pick_set = set(picks)
    trace = {pool[k]["canonical_id"]: v for k, v in result["trace"].items()}

    # Blocked: eligible models ranked above their tier's last pick but not selected.
    last_pick_rank = {t: max((tier_rank[c] for c in picks if by_tier(items, c) == t), default=0) for t in TIERS}
    blocked = []
    lookahead_by_tier: dict[str, list[str]] = {t: [] for t in TIERS}
    for i in items:
        cid = i["canonical_id"]
        if cid in pick_set or tier_rank[cid] > last_pick_rank[i["tier"]]:
            continue
        if i["route"] is None:
            reason = i["route_reason"]
        elif not i["admissible"]:
            reason = i["floor_reason"]
        else:
            t = trace.get(cid) or {}
            reason = t.get("blocked_by") or "not_considered"
            if reason == "lookahead":
                codes = sel.lookahead_codes(problem, t["state"], pool_index[cid], t["rest"])
                reason = "lookahead:" + "+".join(codes)
                lookahead_by_tier[i["tier"]].extend(codes)
        blocked.append(
            {
                "canonical_id": cid,
                "tier": i["tier"],
                "tier_rank": tier_rank[cid],
                "mss_tier": i["mss_tier"],
                "mss": i["mss"],
                "blocked_by": reason,
            },
        )

    pick_rows = []
    for position, cid in enumerate(picks, start=1):
        i = next(x for x in items if x["canonical_id"] == cid)
        r = by_id[cid]
        earlier = [
            b
            for b in blocked
            if b["tier"] == i["tier"] and b["tier_rank"] < tier_rank[cid] and b["blocked_by"].startswith("lookahead")
        ]
        admitted_by = (
            "forced"
            if cid in forced_ids
            else (
                "rank"
                if not earlier
                else "quota:" + "+".join(sorted({b["blocked_by"].split(":", 1)[1] for b in earlier}))
            )
        )
        pick_rows.append(
            {
                "position": position,
                "canonical_id": cid,
                "display_name": r.get("display_name"),
                "tier": i["tier"],
                "tier_rank": tier_rank[cid],
                "mss_rank": r.get("mss_rank"),
                "mss": r["mss"],
                "mss_tier": i["mss_tier"],
                "scores": r["scores"],
                "tier_price_score": i["tier_price_score"],
                "coverage": r["coverage"],
                "missing": r["missing"],
                "flags": i["flags"],
                "admitted_by": admitted_by,
                "vendor": r["vendor"],
                "origin": r["origin"],
                "open_weights": r["open_weights"],
                "capability_pct_in_tier": i.get("capability_pct_in_tier"),
                "route": i["route"],
                "route_reason": i["route_reason"],
                "pinned_version": _route_pin(r, i["route"]),
                "run_cost_usd": r["raw"]["run_cost_usd"],
                "planning_cost_usd": round(i["route_cost"], 4) if i["route_cost"] is not None else None,
                "planning_cost_h0_usd": r["raw"].get("planning_cost_h0_usd"),
                "go_usage": i.get("go_usage"),
                "webdev": r["raw"].get("webdev"),
                "release_date": r.get("release_date"),
            },
        )

    def rerun(code: str | None, *, exclude: str | None = None) -> frozenset | None:
        if code == "Q-quality":
            t_levels = dict.fromkeys(TIERS)
            t_adm = strat._admissible(items, t_levels, pop_cut)  # noqa: SLF001
            p2, o2, pool2 = strat._selectable(items, t_adm, cons)  # noqa: SLF001
        elif code == "B-go":
            g_items, _ = strat.prepare(go_relaxed=True)
            g_adm = strat._admissible(g_items, levels, strat.popularity_cut(g_items))  # noqa: SLF001
            p2, o2, pool2 = strat._selectable(g_items, g_adm, cons)  # noqa: SLF001
        else:
            c2 = cons.relax(code) if code else cons
            pool2 = pool
            p2 = sel.Problem(problem.c, c2)
            o2 = order
        if exclude:
            o2 = [k for k in o2 if pool2[k]["canonical_id"] != exclude]
        idx2 = {i["canonical_id"]: k for k, i in enumerate(pool2)}
        f2 = [idx2[c] for c in forced_ids if c in idx2 and c != exclude]
        try:
            res = sel.run_greedy(p2, [k for k in o2 if k not in f2], forced=f2)
        except sel.SelectionInfeasibleError:
            return None
        return frozenset(pool2[k]["canonical_id"] for k in res["picks"]) if res["complete"] else None

    constraint_status = sel.explain_constraints(problem, result["state"], rerun)

    # Alternates: (a) next models per tier after its last pick, (b) per-pick replacement.
    alternates: dict[str, Any] = {"next_in_tier": {}, "replacements": []}
    for t in TIERS:
        nxt = [
            {
                "canonical_id": i["canonical_id"],
                "tier_rank": tier_rank[i["canonical_id"]],
                "mss_tier": i["mss_tier"],
                "route": i["route"],
                "selectable": bool(i["route"] and i["admissible"]),
                "note": i["route_reason"] if not i["route"] else i["floor_reason"],
            }
            for i in items
            if i["tier"] == t
            and i["canonical_id"] not in pick_set
            and tier_rank[i["canonical_id"]] > last_pick_rank[t]
        ][:3]
        alternates["next_in_tier"][t] = nxt
    base_set = frozenset(picks)
    for cid in picks:
        swapped = rerun(None, exclude=cid)
        alternates["replacements"].append(
            {
                "replace": cid,
                "with": sorted(swapped - base_set) if swapped is not None else None,
                "also_leaves": sorted((base_set - swapped) - {cid}) if swapped is not None else None,
                "feasible": swapped is not None,
            },
        )

    values = {k: pool[k]["mss_tier"] or 0.0 for k in order}
    optimum = sel.sum_optimal(problem, order, values, forced=forced)
    optimum["differs_from_greedy"] = optimum["set"] is not None and set(optimum["set"]) != pick_set
    optimum["sum_mss_tier_greedy"] = round(sum(values[pool_index[c]] for c in picks), 6)

    budget_use = _budget_use(pick_rows, inputs.get("budgets") or {}, params)
    ties = [
        [a["canonical_id"], b["canonical_id"]]
        for a, b in itertools.pairwise(items)
        if a["tier"] == b["tier"]
        and a["mss_tier"] is not None
        and round(a["mss_tier"], 4) == round(b["mss_tier"] or -1, 4)
    ]
    payload.update(
        selection={
            "picks": pick_rows,
            "by_tier": {t: [p["canonical_id"] for p in pick_rows if p["tier"] == t] for t in TIERS},
            "budget_use": budget_use,
            "ties_at_4dp": ties,
            "origin_mix": _mix(pick_rows, "origin"),
            "open_weight_mix": _mix(pick_rows, "open_weights"),
        },
        blocked=blocked,
        alternates=alternates,
        constraint_status=constraint_status,
        sum_optimal=optimum,
        infeasible=None,
    )

    if sensitivity:
        from backend.rankings.services.sensitivity import mss_selection_sensitivity
        from backend.rankings.services.sensitivity import robustness_class

        nominal = {
            "picks": picks,
            "order": [i["canonical_id"] for i in items],
            "scores": {i["canonical_id"]: i["mss_tier"] or 0.0 for i in items},
            "tiers": {i["canonical_id"]: i["tier"] for i in items},
        }
        runs = mss_selection_sensitivity(
            strat,
            nominal=nominal,
            draws=params.get("draws") or SENSITIVITY_DRAWS,
            seed=int(params.get("seed", SENSITIVITY_SEED)),
            tier_weights=params.get("tier_weights") or MSS_TIER_WEIGHTS,
            concentration=float(params.get("dirichlet_concentration", DIRICHLET_CONCENTRATION)),
            progress=progress,
        )
        # Robustness is the weaker of the weight (S1) and specification (S3) stability: S1
        # alone cannot move the floor, S3 perturbs the ratings it is built on.
        by_run = {
            run: {f["canonical_id"]: f["frequency"] for f in (runs.get(run) or {}).get("frequencies", [])}
            for run in ("S1", "S3")
            if (runs.get(run) or {}).get("draws")
        }
        floor_moves = {cid for change in (runs.get("S6") or {}).get("changes", []) for cid in change.get("left") or []}
        for p in pick_rows:
            cid = p["canonical_id"]
            freqs = {run: table.get(cid, 0.0) for run, table in by_run.items()}
            freq = min(freqs.values()) if freqs else None
            p["selection_frequency"] = freq
            p["selection_frequency_by_run"] = freqs
            p["robustness_basis"] = "min(" + ", ".join(sorted(freqs)) + ")" if freqs else None
            p["robustness"] = robustness_class(freq, picked=True) if freq is not None else None
            p["floor_sensitive"] = cid in floor_moves
        runs["robustness_basis"] = "minimum of the S1 and S3 selection frequencies (S1 when S3 did not run)"
        runs["contenders"] = [f for f in (runs.get("S1") or {}).get("frequencies", []) if f["class"] == "contender"]
        payload["sensitivity"] = runs
    else:
        payload["sensitivity"] = None
    warnings = list(payload.get("warnings") or [])
    missing_cap = [p["canonical_id"] for p in pick_rows if "capability_missing_admitted" in (p.get("flags") or [])]
    if missing_cap:
        warnings.append(
            "capability_missing_admitted: "
            + ", ".join(missing_cap)
            + " passed the quality floor on top-quartile popularity without a WebDev rating; "
            "import their LMArena rows before confirming",
        )
    anth = budget_use["anthropic"]
    if anth.get("break_even_cache_hit") is not None and anth["break_even_cache_hit"] > 0:
        warnings.append(
            f"B-anthropic holds only if the Claude runs hit the prompt cache at least "
            f"{anth['break_even_cache_hit']:.0%} of the time (planned at {anth['cache_hit_assumed']:.0%}; "
            f"no Anthropic run measured yet; at 0% the Claude picks cost ${anth['planned_h0_usd']:.2f})",
        )
    if warnings:
        payload["warnings"] = warnings
    return payload


def by_tier(items: list[dict[str, Any]], cid: str) -> str | None:
    return next((i["tier"] for i in items if i["canonical_id"] == cid), None)


def _route_pin(r: dict[str, Any], route: str | None) -> dict[str, Any]:
    info = (r.get("routes") or {}).get(route or "") or {}
    pinned = r.get("pinned_version") or {}
    if route == "openrouter":
        return {
            "route": route,
            "model_id": info.get("model_id"),
            "permaslug": info.get("permaslug") or pinned.get("permaslug"),
        }
    if route == "go":
        return {
            "route": route,
            "model_id": info.get("model_id"),
            "served_name": info.get("served_name"),
            "note": "identity assumed through the catalog twin",
        }
    if route == "anthropic":
        return {
            "route": route,
            "model_id": info.get("model_id"),
            "served_name": info.get("served_name"),
            "openrouter_permaslug": pinned.get("permaslug"),
        }
    return {"route": route}


def _mix(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        value = str(r.get(key))
        out[value] = out.get(value, 0) + 1
    return out


def break_even_cache_hit(cost_h0: float, cost_at_h: float, h: float, budget: float | None) -> float | None:
    """The smallest prompt-cache hit share at which the planned cost fits ``budget``.

    Planning cost is linear in the hit share, so it is interpolated between the
    cost at 0 and at ``h``. 0 when the budget holds even without cache hits,
    None when there is no budget or the cost does not fall with ``h``.
    """
    if budget is None:
        return None
    if cost_h0 <= budget:
        return 0.0
    if h <= 0 or cost_h0 <= cost_at_h:
        return None
    return round(h * (cost_h0 - budget) / (cost_h0 - cost_at_h), 4)


def _budget_use(
    picks: list[dict[str, Any]],
    budgets: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = params or {}
    anth = sum(p["planning_cost_usd"] or 0 for p in picks if p["route"] == "anthropic")
    anth_h0 = sum(p.get("planning_cost_h0_usd") or 0 for p in picks if p["route"] == "anthropic")
    h = params.get("anthropic_cache_hit")
    h = float(PLANNING_BASKET["cache_hit"] if h is None else h)
    orr = sum(p["planning_cost_usd"] or 0 for p in picks if p["route"] == "openrouter")
    return {
        "anthropic": {
            "planned_usd": round(anth, 2),
            "available_usd": budgets.get("anthropic_usd"),
            "effective_usd": budgets.get("anthropic_effective"),
            "cache_hit_assumed": h,
            "planned_h0_usd": round(anth_h0, 2),
            "break_even_cache_hit": (
                break_even_cache_hit(anth_h0, anth, h, budgets.get("anthropic_effective")) if anth else None
            ),
        },
        "openrouter": {
            "planned_usd": round(orr, 2),
            "available_usd": budgets.get("openrouter_available_usd"),
            "effective_usd": budgets.get("openrouter_effective"),
            "source": budgets.get("openrouter_source"),
        },
        "go": [{"canonical_id": p["canonical_id"], **(p.get("go_usage") or {})} for p in picks if p["route"] == "go"],
        "contingency": budgets.get("contingency"),
        "runs_per_model": RUNS_PER_MODEL,
        "retry_factor": RETRY_FACTOR,
    }


def _candidate_row(r: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    item = next((i for i in items if i["canonical_id"] == r["canonical_id"]), None)
    row = {k: v for k, v in r.items() if k not in ("inputs", "members")}
    row["members"] = [m["model_id"] for m in r.get("members") or []]
    if item is not None:
        row.update(
            tier=item["tier"],
            mss_tier=item["mss_tier"],
            tier_price_score=item["tier_price_score"],
            route=item["route"],
            route_reason=item["route_reason"],
            route_cost_usd=item["route_cost"],
            admissible=item.get("admissible"),
            floor_reason=item.get("floor_reason"),
            capability_pct_in_tier=item.get("capability_pct_in_tier"),
            go_usage=item.get("go_usage"),
            # scoring flags plus the selection's own (route, floor admission)
            flags=item["flags"],
        )
    return row


def _stored_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """The inputs a replay needs (frame candidates with their raw inputs, market, budgets)."""
    return {
        "as_of": inputs["as_of"],
        "frame": inputs["frame"],
        "market": inputs.get("market"),
        "market_snapshot_id": inputs.get("market_snapshot_id"),
        "budgets": inputs.get("budgets") or {},
        "go_limits": inputs.get("go_limits") or {},
    }
