"""Filtering, sorting, and top-N selection of ranking rows."""

from __future__ import annotations

from typing import Any

from llm_lab.rankings.services.aggregator import aggregate_rankings
from llm_lab.rankings.services.constants import SORT_KEY_MAP


def filter_rankings(
    rankings: list[dict[str, Any]],
    *,
    max_price: float | None = None,
    min_context: int | None = None,
    providers: list[str] | None = None,
    include_free: bool = True,
    min_composite: float | None = None,
    has_benchmarks: bool = False,
    search: str = "",
) -> list[dict[str, Any]]:
    out = []
    needle = (search or "").strip().lower()
    provs = [p.lower() for p in (providers or [])]
    for entry in rankings:
        if max_price is not None:
            price = entry.get("price_per_million_input") or 0
            if price > max_price and not entry.get("is_free"):
                continue
        if not include_free and entry.get("is_free"):
            continue
        if min_context is not None:
            c = entry.get("context_length")
            if not c or c < min_context:
                continue
        if provs:
            if (entry.get("provider") or "").lower() not in provs:
                continue
        if min_composite is not None:
            mss = entry.get("mss_score")
            if mss is None or mss < min_composite:
                continue
        if has_benchmarks and not (entry.get("benchmark_score") or 0) > 0:
            continue
        if needle:
            haystack = " ".join(
                [
                    entry.get("model_name") or "",
                    entry.get("model_id") or "",
                    entry.get("provider") or "",
                ],
            ).lower()
            if needle not in haystack:
                continue
        out.append(entry)
    return out


def sort_rankings(
    rankings: list[dict[str, Any]],
    *,
    sort_by: str = "mss",
    sort_dir: str = "desc",
) -> list[dict[str, Any]]:
    key = SORT_KEY_MAP.get(sort_by, sort_by) or "mss_score"
    reverse = sort_dir == "desc"

    def get_val(item: dict[str, Any]) -> Any:
        v = item.get(key)
        if v is None:
            return float("-inf") if reverse else float("inf")
        return v

    return sorted(rankings, key=get_val, reverse=reverse)


def get_top_models(
    count: int = 10,
    weights: dict[str, float] | None = None,
    **filter_kwargs: Any,
) -> list[dict[str, Any]]:
    rankings = filter_rankings(aggregate_rankings(), **filter_kwargs)
    if weights:
        for entry in rankings:
            total, total_weight = 0.0, 0.0
            for k, w in weights.items():
                v = entry.get(k)
                if v is not None:
                    total += float(v) * w
                    total_weight += w
            entry["composite_score"] = round(total / total_weight, 4) if total_weight else None
        rankings.sort(
            key=lambda x: x.get("composite_score") or 0.0,
            reverse=True,
        )
    else:
        rankings = sort_rankings(rankings, sort_by="mss", sort_dir="desc")
    return rankings[: max(1, min(count, 100))]
