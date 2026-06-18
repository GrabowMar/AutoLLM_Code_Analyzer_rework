"""Rankings API endpoints."""

from __future__ import annotations

from typing import Any

from django.http import HttpResponse
from ninja import Router

from llm_lab.rankings import services
from llm_lab.rankings.api.schema import RankingsResponse
from llm_lab.rankings.api.schema import StatusResponse
from llm_lab.rankings.api.schema import TopModelsResponse

router = Router(tags=["rankings"])


@router.get("/", response=RankingsResponse)
def list_rankings(
    request,
    page: int = 1,
    per_page: int = 25,
    sort_by: str = "mss",
    sort_dir: str = "desc",
    search: str = "",
    provider: str | None = None,
    max_price: float | None = None,
    min_context: int | None = None,
    min_composite: float | None = None,
    include_free: bool = True,
    has_benchmarks: bool = False,
):
    per_page = min(max(per_page, 10), 100)
    page = max(page, 1)

    rankings = services.aggregate_rankings()
    filtered = services.filter_rankings(
        rankings,
        max_price=max_price,
        min_context=min_context,
        providers=[provider] if provider else None,
        include_free=include_free,
        min_composite=min_composite,
        has_benchmarks=has_benchmarks,
        search=search,
    )
    sorted_rows = services.sort_rankings(
        filtered,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )

    total = len(sorted_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    paginated = sorted_rows[start : start + per_page]

    providers = {r.get("provider") for r in filtered if r.get("provider")}
    with_benchmarks = sum(1 for r in filtered if (r.get("benchmark_score") or 0) > 0)
    free_models = sum(1 for r in filtered if r.get("is_free"))
    avg_mss = (
        sum(r.get("mss_score", 0) for r in filtered) / len(filtered)
        if filtered
        else 0.0
    )

    return {
        "count": len(paginated),
        "rankings": paginated,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        "statistics": {
            "total": total,
            "with_benchmarks": with_benchmarks,
            "unique_providers": len(providers),
            "free_models": free_models,
            "avg_mss": round(avg_mss, 4),
        },
    }


@router.get("/top/", response=TopModelsResponse)
def top_models(request, count: int = 10):
    models = services.get_top_models(count=count)
    return {"count": len(models), "models": models}


@router.get("/status/", response=StatusResponse)
def status(request):
    return services.get_status()


@router.post("/refresh/", response=StatusResponse)
def refresh(request):
    """Recompute rankings (no-op cache refresh; aggregation is computed live)."""

    return services.get_status()


@router.get("/export/")
def export_csv(request):
    """Export current rankings as CSV."""

    import csv
    import io

    rankings = services.aggregate_rankings()
    buf = io.StringIO()
    fieldnames = [
        "model_id",
        "model_name",
        "provider",
        "mss_score",
        "adoption_score",
        "benchmark_score",
        "cost_efficiency_score",
        "accessibility_score",
        "is_free",
        "context_length",
        "price_per_million_input",
        "price_per_million_output",
        "apps",
        "apps_completed",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for entry in rankings:
        row: dict[str, Any] = {k: entry.get(k) for k in fieldnames}
        writer.writerow(row)
    response = HttpResponse(buf.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="model_rankings.csv"'
    return response
