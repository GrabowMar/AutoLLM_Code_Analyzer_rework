"""MSS v2 components and aggregation (spec §2; Amendment A1 for the within-tier score).

Each component is a score in [0, 1], higher = more preferred, on fixed
goalposts (``GOALPOSTS``):

* popularity P: geometric mean of the OpenRouter token-volume share and the
  estimated spend-value share over the window, log scale;
* price C: log USD cost of one reference agentic run at first-party list
  prices, cheaper scores higher;
* capability Q: LMArena WebDev rating, linear;
* recency R: linear over 365 days since the pinned version's release.

``MSS = Σ w_k s_k / Σ w_k`` over the components present; a missing component
is omitted and the weights renormalised, never scored 0. P and C are
required (E8): a model without them is ``insufficient_evidence``.

Everything here is a pure function of the frame entries and the market
block, so the sensitivity analysis can rescore under variants and a stored
selection can be replayed.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any

from backend.rankings.services.constants import CENSOR_FACTOR
from backend.rankings.services.constants import COMPONENTS
from backend.rankings.services.constants import GOALPOSTS
from backend.rankings.services.constants import MSS_WEIGHTS
from backend.rankings.services.constants import PLANNING_BASKET
from backend.rankings.services.constants import REFERENCE_BASKET
from backend.rankings.services.constants import REQUIRED_COMPONENTS
from backend.rankings.services.pricing import planning_cost
from backend.rankings.services.pricing import run_cost


@dataclass(frozen=True)
class Variant:
    """A scoring specification; ``NOMINAL`` is the pre-registered one (S3 varies the rest)."""

    normalisation: str = "goalposts"  # goalposts | percentile
    popularity: str = "geo"  # geo | volume | value
    cache_hit: float = REFERENCE_BASKET["cache_hit"]
    censor_factor: float = CENSOR_FACTOR
    missing_capability: str = "renormalise"  # renormalise | p25
    ratings: dict[str, float] = field(default_factory=dict)  # canonical id -> perturbed WebDev rating

    def describe(self) -> dict[str, Any]:
        return {
            "normalisation": self.normalisation,
            "popularity": self.popularity,
            "cache_hit": round(self.cache_hit, 4),
            "censor_factor": self.censor_factor,
            "missing_capability": self.missing_capability,
            "rating_perturbed": bool(self.ratings),
        }


NOMINAL = Variant()


def clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (``pct`` in 0..100) of ``values``; None when empty."""
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * pct / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def mid_rank_percentiles(values: dict[str, float], *, higher_is_better: bool = True) -> dict[str, float]:
    """Mid-rank percentile in [0, 1] of each value (ties averaged); the percentile-normalisation variant."""
    n = len(values)
    if n == 0:
        return {}
    if n == 1:
        return dict.fromkeys(values, 0.5)
    items = sorted(values.items(), key=lambda kv: kv[1] if higher_is_better else -kv[1])
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        rank = (i + j) / 2
        for k in range(i, j + 1):
            out[items[k][0]] = rank / (n - 1)
        i = j + 1
    return out


# ── Components ─────────────────────────────────────────────────────────────


def price_of(entry: dict[str, Any]) -> dict[str, Any] | None:
    price = (entry.get("inputs") or {}).get("list_price")
    return price if isinstance(price, dict) and price.get("input") is not None else None


def basket(cache_hit: float) -> dict[str, float]:
    return {**REFERENCE_BASKET, "cache_hit": cache_hit}


def popularity_raw(
    entry: dict[str, Any],
    *,
    u_by_canon: dict[str, float],
    spend_by_day: dict[str, float],
    variant: Variant,
) -> dict[str, Any] | None:
    """Volume share, value share and the raw log10 popularity of one model (None without a record)."""
    record = (entry.get("inputs") or {}).get("popularity")
    if not isinstance(record, dict) or not record.get("daily"):
        return None
    canon = entry["canonical_id"]
    u = u_by_canon.get(canon)
    vol_terms, val_terms = [], []
    for day in record["daily"]:
        total = day.get("total") or 0
        if total <= 0:
            continue
        tokens = day["tokens"] if day.get("tokens") is not None else variant.censor_factor * (day.get("limit") or 0)
        vol_terms.append(tokens / total)
        spend = spend_by_day.get(day["date"])
        if u is not None and spend:
            val_terms.append(tokens * u / spend)
    if not vol_terms:
        return None
    vol = sum(vol_terms) / len(vol_terms)
    val = sum(val_terms) / len(val_terms) if val_terms else None
    if variant.popularity == "volume" or (variant.popularity == "geo" and val is None):
        raw = math.log10(vol) if vol > 0 else None
        definition = "volume" if variant.popularity == "volume" else "volume (no price for value share)"
    elif variant.popularity == "value":
        raw = math.log10(val) if val and val > 0 else None
        definition = "value"
    else:
        raw = 0.5 * math.log10(vol) + 0.5 * math.log10(val) if vol > 0 and val and val > 0 else None
        definition = "geometric mean of volume and value share"
    return {
        "volume_share": vol,
        "value_share": val,
        "p_raw": raw,
        "definition": definition,
        "days_available": record.get("days_available"),
        "days_listed": record.get("days_listed"),
        "days_censored": record.get("days_censored"),
        "censored": bool(record.get("days_censored")),
        "short_exposure": bool(record.get("short_exposure")),
        "as_of": record.get("as_of"),
        "window": record.get("window"),
    }


def spend_by_day(market: dict[str, Any] | None, u_by_canon: dict[str, float]) -> dict[str, float]:
    """S_d: estimated spend of the models listed each day that have a price."""
    out: dict[str, float] = {}
    for date, day in ((market or {}).get("days") or {}).items():
        out[date] = sum(tokens * u_by_canon[c] for c, tokens in (day.get("listed") or {}).items() if c in u_by_canon)
    return out


def lmarena_interactions(record: dict[str, Any] | None) -> float | None:
    """WebDev votes + text-coding votes + agent sessions (the popularity proxy)."""
    cats = (record or {}).get("categories") if isinstance(record, dict) else None
    if not isinstance(cats, dict):
        return None
    total = 0.0
    for name, key in (("webdev", "vote_count"), ("text_coding", "vote_count"), ("agent", "session_count")):
        value = (cats.get(name) or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total or None


def webdev(entry: dict[str, Any]) -> dict[str, Any] | None:
    record = (entry.get("inputs") or {}).get("lmarena")
    cat = ((record or {}).get("categories") or {}).get("webdev") if isinstance(record, dict) else None
    if not isinstance(cat, dict) or not isinstance(cat.get("rating"), (int, float)):
        return None
    return {
        "rating": float(cat["rating"]),
        "rating_lower": cat.get("rating_lower"),
        "rating_upper": cat.get("rating_upper"),
        "arena_name": cat.get("arena_name"),
        "publish_date": cat.get("publish_date") or (record or {}).get("publish_date"),
        "match": (record or {}).get("match"),
        "from_model": (entry.get("inputs") or {}).get("lmarena_from"),
    }


def age_days(entry: dict[str, Any], as_of: dt.date) -> int | None:
    released = entry.get("release_date")
    if not released:
        return None
    return (as_of - dt.date.fromisoformat(released[:10])).days


def popularity_score(p_raw: float | None) -> float | None:
    lo, hi = GOALPOSTS["popularity_log10_share"]
    return None if p_raw is None else clip01((p_raw - lo) / (hi - lo))


def price_score(cost: float | None) -> float | None:
    lo, hi = GOALPOSTS["price_log10_usd"]
    if cost is None or cost <= 0:
        return None
    return clip01(1 - (math.log10(cost) - lo) / (hi - lo))


def band_price_score(cost: float | None, band: tuple[float, float]) -> float | None:
    """Price re-normalised inside a tier band (log scale; the band edges are the goalposts)."""
    lo, hi = band
    if cost is None or cost <= 0 or lo <= 0 or hi <= lo:
        return None
    return clip01(1 - (math.log10(cost) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))


def capability_score(rating: float | None) -> float | None:
    lo, hi = GOALPOSTS["capability_rating"]
    return None if rating is None else clip01((rating - lo) / (hi - lo))


def recency_score(days: int | None) -> float | None:
    return None if days is None else clip01(1 - days / GOALPOSTS["recency_days"])


def compute_mss(scores: dict[str, float | None], weights: dict[str, float] = MSS_WEIGHTS) -> dict[str, Any]:
    """Weighted mean over the components present: ``{mss, coverage, missing}`` (mss None when nothing is)."""
    num = den = 0.0
    missing = []
    for key in COMPONENTS:
        w = weights.get(key, 0.0)
        s = scores.get(key)
        if s is None:
            if w > 0:
                missing.append(key)
            continue
        num += w * s
        den += w
    total = sum(weights.get(k, 0.0) for k in COMPONENTS) or 1.0
    return {
        "mss": round(num / den, 6) if den > 0 else None,
        "coverage": round(den / total, 4),
        "missing": missing,
    }


# ── Frame scoring ──────────────────────────────────────────────────────────


def _u_by_canon(candidates: list[dict[str, Any]], extra_prices: dict[str, dict], h: float) -> dict[str, float]:
    """USD per basket token of every priced model (frame candidates + other listed models)."""
    tokens = REFERENCE_BASKET["prompt_mtok"] + REFERENCE_BASKET["output_mtok"]
    out: dict[str, float] = {}
    for canon, price in (extra_prices or {}).items():
        cost = run_cost(price, basket(h))
        if cost:
            out[canon] = cost / (tokens * 1e6)
    for entry in candidates:
        cost = run_cost(price_of(entry), basket(h))
        if cost:
            out[entry["canonical_id"]] = cost / (tokens * 1e6)
    return out


def score_frame(
    candidates: list[dict[str, Any]],
    *,
    as_of: dt.date,
    market: dict[str, Any] | None,
    market_prices: dict[str, dict] | None = None,
    variant: Variant = NOMINAL,
    weights: dict[str, float] = MSS_WEIGHTS,
) -> list[dict[str, Any]]:
    """Score every candidate; eligible ones without P or C are excluded at E8.

    Returns new dicts: the frame entry's identity fields plus ``scores``,
    ``raw``, ``flags``, ``mss``, ``coverage``, ``missing``, ``eligible`` and
    ``exclusion`` (E8 added).
    """
    h = variant.cache_hit
    u = _u_by_canon(candidates, market_prices or {}, h)
    spend = spend_by_day(market, u)

    rows: list[dict[str, Any]] = []
    for entry in candidates:
        price = price_of(entry)
        cost = run_cost(price, basket(h))
        pop = popularity_raw(entry, u_by_canon=u, spend_by_day=spend, variant=variant)
        wd = webdev(entry)
        rating = variant.ratings.get(entry["canonical_id"], wd["rating"] if wd else None) if wd else None
        age = age_days(entry, as_of)
        go = (price or {}).get("go") or {}
        go_price = go if go.get("input") is not None else price
        rows.append(
            {
                "canonical_id": entry["canonical_id"],
                "entry": entry,
                "raw": {
                    "run_cost_usd": cost,
                    "planning_cost_usd": planning_cost(price, basket=PLANNING_BASKET),
                    "planning_cost_h0_usd": planning_cost(price, basket={**PLANNING_BASKET, "cache_hit": 0.0}),
                    "go_planning_cost_usd": planning_cost(go_price, basket=PLANNING_BASKET) if go_price else None,
                    "go_run_cost_usd": run_cost(go_price, PLANNING_BASKET) if go_price else None,
                    "price_source": (price or {}).get("source"),
                    "popularity": pop,
                    "p_raw": pop["p_raw"] if pop else None,
                    "webdev": wd,
                    "webdev_rating": rating,
                    "age_days": age,
                    "lmarena_interactions": lmarena_interactions((entry.get("inputs") or {}).get("lmarena")),
                },
                "flags": [],
            },
        )

    eligible = [r for r in rows if r["entry"]["exclusion"] is None]

    # Popularity proxy for models with no OpenRouter presence at all: quantile-map
    # log10(LMArena interactions) onto the P distribution of frame models with both.
    reference = [
        (math.log10(r["raw"]["lmarena_interactions"]), r["raw"]["p_raw"])
        for r in eligible
        if r["raw"]["p_raw"] is not None and r["raw"]["lmarena_interactions"]
    ]
    for r in rows:
        presence = (r["entry"].get("inputs") or {}).get("popularity")
        if r["raw"]["p_raw"] is None and not presence and r["raw"]["lmarena_interactions"] and len(reference) >= 3:
            x = math.log10(r["raw"]["lmarena_interactions"])
            xs = sorted(v[0] for v in reference)
            q = sum(1 for v in xs if v <= x) / len(xs)
            r["raw"]["p_raw"] = percentile([v[1] for v in reference], 100 * q)
            r["flags"].append("lmarena_proxy")
            r["raw"]["popularity_source"] = "lmarena_proxy"
        elif r["raw"]["p_raw"] is not None:
            r["raw"]["popularity_source"] = "openrouter"
        if r["raw"]["popularity"] and r["raw"]["popularity"]["censored"]:
            r["flags"].append("censored")
        if r["raw"]["popularity"] and r["raw"]["popularity"]["short_exposure"]:
            r["flags"].append("short_exposure")

    # Normalisation.
    if variant.normalisation == "percentile":
        pool = [r for r in eligible if r["raw"]["p_raw"] is not None and r["raw"]["run_cost_usd"]]
        p_pct = mid_rank_percentiles({r["canonical_id"]: r["raw"]["p_raw"] for r in pool})
        c_pct = mid_rank_percentiles(
            {r["canonical_id"]: r["raw"]["run_cost_usd"] for r in pool},
            higher_is_better=False,
        )
        q_pct = mid_rank_percentiles(
            {r["canonical_id"]: r["raw"]["webdev_rating"] for r in pool if r["raw"]["webdev_rating"] is not None},
        )
        r_pct = mid_rank_percentiles(
            {r["canonical_id"]: r["raw"]["age_days"] for r in pool if r["raw"]["age_days"] is not None},
            higher_is_better=False,
        )
    for r in rows:
        cid, raw = r["canonical_id"], r["raw"]
        if variant.normalisation == "percentile":
            r["scores"] = {
                "popularity": p_pct.get(cid),
                "price": c_pct.get(cid),
                "capability": q_pct.get(cid),
                "recency": r_pct.get(cid),
            }
        else:
            r["scores"] = {
                "popularity": popularity_score(raw["p_raw"]),
                "price": price_score(raw["run_cost_usd"]),
                "capability": capability_score(raw["webdev_rating"]),
                "recency": recency_score(raw["age_days"]),
            }

    if variant.missing_capability == "p25":
        q_values = [r["scores"]["capability"] for r in eligible if r["scores"]["capability"] is not None]
        p25 = percentile(q_values, 25.0)
        for r in rows:
            if r["scores"]["capability"] is None and p25 is not None:
                r["scores"]["capability"] = p25
                r["flags"].append("capability_imputed_p25")

    out = []
    for r in rows:
        entry = r["entry"]
        if r["scores"]["capability"] is None:
            r["flags"].append("capability_missing")
        agg = compute_mss(r["scores"], weights)
        exclusion = entry["exclusion"]
        missing_required = [k for k in REQUIRED_COMPONENTS if r["scores"].get(k) is None]
        if exclusion is None and missing_required:
            exclusion = {
                "step": "E8",
                "code": "insufficient_evidence",
                "detail": f"missing {', '.join(missing_required)}",
            }
        out.append(
            {
                **{k: v for k, v in entry.items() if k not in ("inputs",)},
                "inputs": entry.get("inputs"),
                "scores": {k: (round(v, 6) if v is not None else None) for k, v in r["scores"].items()},
                "raw": raw_round(r["raw"]),
                "flags": sorted(set(r["flags"])),
                # An excluded model (E1-E8) has no MSS, so no rank and no composite; its
                # component scores stay for the breakdown.
                "mss": agg["mss"] if exclusion is None else None,
                "coverage": agg["coverage"],
                "missing": agg["missing"],
                "eligible": exclusion is None,
                "exclusion": exclusion,
            },
        )
    return out


def raw_round(raw: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in raw.items():
        if isinstance(v, float):
            out[k] = round(v, 8)
        elif isinstance(v, dict):
            out[k] = {kk: (round(vv, 10) if isinstance(vv, float) else vv) for kk, vv in v.items()}
        else:
            out[k] = v
    return out


def e8_flow_step(scored: list[dict[str, Any]]) -> dict[str, Any]:
    excluded = [
        {"id": r["canonical_id"], "code": "insufficient_evidence"}
        for r in scored
        if (r.get("exclusion") or {}).get("step") == "E8"
    ]
    return {
        "step": "E8",
        "label": "Popularity and price evidence present",
        "remaining": sum(1 for r in scored if r["eligible"]),
        "excluded": excluded,
    }


def with_variant(variant: Variant, **changes: Any) -> Variant:
    return replace(variant, **changes)
