"""Filtering, sorting, and top-N selection of ranking rows."""

from __future__ import annotations

from typing import Any

from backend.rankings.services.aggregator import aggregate_rankings
from backend.rankings.services.constants import DEFAULT_SORT_KEY
from backend.rankings.services.constants import SORT_KEY_MAP


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
            composite = entry.get("composite_score")
            if composite is None or composite < min_composite:
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
    """Sort by a public key (see SORT_KEY_MAP); missing values always sort last.

    Ties (and the None tail) are broken by ``model_id`` so the order is
    deterministic. An unknown key sorts by MSS rather than leaving the list
    in arbitrary order.
    """
    key = SORT_KEY_MAP.get(sort_by) or (sort_by if sort_by in SORT_KEY_MAP.values() else DEFAULT_SORT_KEY)
    reverse = sort_dir == "desc"
    present = [r for r in rankings if r.get(key) is not None]
    missing = [r for r in rankings if r.get(key) is None]
    # Two stable passes: model_id ascending first, then the key in the
    # requested direction, so equal keys keep the model_id order.
    present.sort(key=lambda r: str(r.get("model_id") or ""))
    present.sort(key=lambda r: r[key], reverse=reverse)
    missing.sort(key=lambda r: str(r.get("model_id") or ""))
    return present + missing


def get_top_models(
    count: int = 10,
    weights: dict[str, float] | None = None,
    *,
    user: Any = None,
    rows: list[dict[str, Any]] | None = None,
    **filter_kwargs: Any,
) -> list[dict[str, Any]]:
    """The top *count* ranking rows (by MSS, or by a custom *weights* composite).

    *rows* reuses rows already built (``build_rankings(user)["rows"]``), so a
    caller can report the same rows' ``comparability``.
    """
    source = rows if rows is not None else aggregate_rankings(user)
    rankings = filter_rankings([dict(r) for r in source], **filter_kwargs)
    if weights:
        for entry in rankings:
            total, total_weight = 0.0, 0.0
            for k, w in weights.items():
                v = entry.get(k)
                if v is not None:
                    total += float(v) * w
                    total_weight += w
            entry["composite_score"] = round(total / total_weight, 4) if total_weight else None
        rankings = sort_rankings(rankings, sort_by="composite", sort_dir="desc")
    else:
        rankings = sort_rankings(rankings, sort_by="mss", sort_dir="desc")
    return rankings[: max(1, min(count, 100))]
