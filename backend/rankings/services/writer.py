"""Filtering, sorting, and top-N selection of ranking rows."""

from __future__ import annotations

from typing import Any

from backend.rankings.services.aggregator import aggregate_rankings
from backend.rankings.services.constants import SORT_KEY_MAP


def filter_rankings(
    rankings: list[dict[str, Any]],
    *,
    max_price: float | None = None,
    min_context: int | None = None,
    providers: list[str] | None = None,
    include_free: bool = True,
    min_composite: float | None = None,
    min_mss: float | None = None,
    eligible_only: bool = False,
    has_capability: bool = False,
    search: str = "",
) -> list[dict[str, Any]]:
    """Rows matching every given filter.

    ``max_price`` compares the catalog input price per 1M tokens (free models pass).
    ``eligible_only`` keeps models that pass every eligibility filter (E1-E8).
    """
    out = []
    needle = (search or "").strip().lower()
    provs = [p.lower() for p in (providers or [])]
    for entry in rankings:
        if eligible_only and not entry.get("eligible"):
            continue
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
        if (
            provs
            and (entry.get("provider") or "").lower() not in provs
            and (entry.get("vendor") or "").lower() not in provs
        ):
            continue
        if min_composite is not None:
            composite = entry.get("composite_score")
            if composite is None or composite < min_composite:
                continue
        if min_mss is not None:
            mss = entry.get("mss_score")
            if mss is None or mss < min_mss:
                continue
        if has_capability and entry.get("capability_score") is None:
            continue
        if needle:
            haystack = " ".join(
                [
                    entry.get("model_name") or "",
                    entry.get("model_id") or "",
                    entry.get("canonical_id") or "",
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
    """Sort by a ``SORT_KEY_MAP`` key (or one of its row fields); missing values always sort last.

    Any other name falls back to the MSS: rows also carry dict fields (routes,
    popularity, ...) that cannot be ordered.
    """
    key = SORT_KEY_MAP.get(sort_by) or (sort_by if sort_by in SORT_KEY_MAP.values() else "mss_score")
    reverse = sort_dir == "desc"
    present = [r for r in rankings if r.get(key) is not None]
    missing = [r for r in rankings if r.get(key) is None]
    present.sort(key=lambda r: r["model_id"])
    present.sort(key=lambda r: r[key], reverse=reverse)
    return present + sorted(missing, key=lambda r: r["model_id"])


def get_top_models(count: int = 10, **filter_kwargs: Any) -> list[dict[str, Any]]:
    """The top eligible canonical models by MSS (route copies excluded)."""
    filter_kwargs.setdefault("eligible_only", True)
    rankings = [r for r in filter_rankings(aggregate_rankings(), **filter_kwargs) if not r.get("mss_inherited_from")]
    rankings = sort_rankings(rankings, sort_by="mss", sort_dir="desc")
    return rankings[: max(1, min(count, 100))]
