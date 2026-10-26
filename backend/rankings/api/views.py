"""Rankings API endpoints.

Every endpoint computes over the requesting user's generation jobs and
honours the same filters, so the list, the sensitivity analysis and the CSV
export always describe the same population (and the same analysis plan).
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from typing import Any

from django.http import HttpResponse
from ninja import Query
from ninja import Router
from ninja import Schema
from pydantic import field_validator

from backend.rankings import services
from backend.rankings.api.schema import RankingsResponse
from backend.rankings.api.schema import SensitivityResponse
from backend.rankings.api.schema import StatusResponse
from backend.rankings.api.schema import TopModelsResponse

router = Router(tags=["rankings"])


class RankingFilters(Schema):
    """Local-stat scope (which jobs/plan) + row filters shared by list/sensitivity/export."""

    search: str = ""
    provider: str | None = None
    max_price: float | None = None
    min_context: int | None = None
    min_composite: float | None = None
    include_free: bool = True
    has_benchmarks: bool = False
    prompt_hash: str | None = None
    bundle_key: str | None = None
    # A UUID (validated: a malformed id is a 422, never a 500 from the ORM).
    experiment_id: str | None = None
    plan_hash: str | None = None

    @field_validator("experiment_id", mode="before")
    @classmethod
    def _experiment_uuid(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None
        try:
            return str(uuid.UUID(str(value)))
        except ValueError as exc:
            msg = "experiment_id must be a UUID"
            raise ValueError(msg) from exc


@dataclass
class _Computed:
    rows: list[dict[str, Any]]
    comparability: dict[str, Any]
    filters: dict[str, Any]


def _compute(request, filters: RankingFilters) -> _Computed:
    built = services.build_rankings(
        request.auth,
        prompt_hash=filters.prompt_hash,
        bundle_key=filters.bundle_key,
        experiment_id=filters.experiment_id,
        plan_hash=filters.plan_hash,
    )
    rows = services.filter_rankings(
        built["rows"],
        max_price=filters.max_price,
        min_context=filters.min_context,
        providers=[filters.provider] if filters.provider else None,
        include_free=filters.include_free,
        min_composite=filters.min_composite,
        has_benchmarks=filters.has_benchmarks,
        search=filters.search,
    )
    applied = {k: v for k, v in filters.model_dump().items() if v not in (None, "")}
    return _Computed(rows=rows, comparability=built["comparability"], filters=applied)


@router.get("/", response=RankingsResponse)
def list_rankings(
    request,
    filters: Query[RankingFilters],
    page: int = 1,
    per_page: int = 25,
    sort_by: str = "mss",
    sort_dir: str = "desc",
):
    per_page = min(max(per_page, 10), 100)
    page = max(page, 1)

    computed = _compute(request, filters)
    filtered = computed.rows
    sorted_rows = services.sort_rankings(filtered, sort_by=sort_by, sort_dir=sort_dir)

    total = len(sorted_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    paginated = sorted_rows[start : start + per_page]

    providers = {r.get("provider") for r in filtered if r.get("provider")}
    with_benchmarks = sum(1 for r in filtered if (r.get("benchmark_score") or 0) > 0)
    free_models = sum(1 for r in filtered if r.get("is_free"))
    avg_mss = sum(r.get("mss_score", 0) for r in filtered) / len(filtered) if filtered else 0.0

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
            "measured_models": sum(1 for r in filtered if r.get("empirical_quality_score") is not None),
        },
        "filters_applied": {**computed.filters, "sort_by": sort_by, "sort_dir": sort_dir},
        "comparability": computed.comparability,
    }


@router.get("/top/", response=TopModelsResponse)
def top_models(request, count: int = 10):
    built = services.build_rankings(request.auth)
    models = services.get_top_models(count=count, user=request.auth, rows=built["rows"])
    return {"count": len(models), "models": models, "comparability": built["comparability"]}


@router.get("/status/", response=StatusResponse)
def status(request):
    return services.get_status()


@router.get("/sensitivity/", response=SensitivityResponse)
def sensitivity(request, filters: Query[RankingFilters]):
    """Empirical-ranking stability under alternative severity weightings (same filters as the list)."""

    computed = _compute(request, filters)
    return {
        **services.compute_weight_sensitivity(computed.rows),
        "comparability": computed.comparability,
    }


@router.post("/refresh/", response=StatusResponse)
def refresh(request):
    """Rankings are computed live on every request; this only reports the benchmark status."""

    return services.get_status()


# Identity, then the metadata decision aid (MSS + components), then the
# locally measured empirical columns. New columns are only ever appended.
EXPORT_COLUMNS = [
    "model_id",
    "model_name",
    "provider",
    "is_free",
    "context_length",
    "price_per_million_input",
    "price_per_million_output",
    "mss_score",
    "adoption_score",
    "benchmark_score",
    "cost_efficiency_score",
    "accessibility_score",
    "composite_score",
    "empirical_quality_score",
    "smoke_pass_rate",
    "n_trials",
    "empirical_density_stdev",
    "smoke_pass_rate_stdev",
    "findings_total_static",
    "ai_findings_total",
    "apps",
    "apps_completed",
    "issues_total_static",
    "measured_sloc",
    "empirical_density_mean",
    "functional_n",
    "n_static",
    "n_ai",
    "analysis_excluded",
    "analysis_incomplete",
    "analysis_other_plan",
    "analysis_not_analysed",
    "plan_hash",
    "scoring_version",
]


@router.get("/export/")
def export_csv(
    request,
    filters: Query[RankingFilters],
    sort_by: str = "composite",
    sort_dir: str = "desc",
):
    """Export the rankings (same filters/sort as the list) as CSV."""

    computed = _compute(request, filters)
    rows = services.sort_rankings(computed.rows, sort_by=sort_by, sort_dir=sort_dir)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for entry in rows:
        row: dict[str, Any] = {k: entry.get(k) for k in EXPORT_COLUMNS}
        variance = entry.get("variance") or {}
        analysis = entry.get("analysis") or {}
        measured = entry.get("n_static", 0) > 0
        row["smoke_pass_rate"] = entry.get("functional_pass_rate")
        row["empirical_density_stdev"] = variance.get("density_per_kloc_stdev")
        row["empirical_density_mean"] = variance.get("density_per_kloc_mean")
        # Unmeasured models get empty cells, not zeros: no data is not clean data.
        row["findings_total_static"] = sum((entry.get("findings") or {}).values()) if measured else None
        row["issues_total_static"] = sum((entry.get("issues") or {}).values()) if measured else None
        row["ai_findings_total"] = sum((entry.get("ai_findings") or {}).values()) if entry.get("n_ai") else None
        row["smoke_pass_rate_stdev"] = variance.get("smoke_pass_rate_stdev")
        row["analysis_excluded"] = analysis.get("excluded")
        row["analysis_incomplete"] = analysis.get("incomplete")
        row["analysis_other_plan"] = analysis.get("other_plan")
        row["analysis_not_analysed"] = analysis.get("not_analysed")
        writer.writerow(row)
    response = HttpResponse(buf.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="model_rankings.csv"'
    plan_hash = computed.comparability.get("plan_hash")
    if plan_hash:
        response["X-Analysis-Plan-Hash"] = plan_hash
    return response
