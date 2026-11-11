"""Rankings API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpResponse
from ninja import Router
from ninja.errors import HttpError

from backend.rankings import services
from backend.rankings.api.schema import ConfirmIn
from backend.rankings.api.schema import RankingsResponse
from backend.rankings.api.schema import SelectionCreateIn
from backend.rankings.api.schema import SelectionSnapshotDetail
from backend.rankings.api.schema import SelectionSnapshotSummary
from backend.rankings.api.schema import SensitivityResponse
from backend.rankings.api.schema import SourcesResponse
from backend.rankings.api.schema import TopModelsResponse

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
    min_mss: float | None = None,
    eligible_only: bool = False,
    include_free: bool = True,
    has_capability: bool = False,
    prompt_hash: str | None = None,
    config_fingerprint: str | None = None,
    study_id: str | None = None,
):
    per_page = min(max(per_page, 10), 100)
    page = max(page, 1)
    if study_id:
        try:
            study_id = str(uuid.UUID(study_id))
        except ValueError as exc:
            # A malformed id is the caller's error, not a server error.
            raise HttpError(400, "study_id must be a UUID") from exc

    rankings = services.aggregate_rankings(
        prompt_hash=prompt_hash,
        config_fingerprint=config_fingerprint,
        study_id=study_id,
    )
    filtered = services.filter_rankings(
        rankings,
        max_price=max_price,
        min_context=min_context,
        providers=[provider] if provider else None,
        include_free=include_free,
        min_composite=min_composite,
        min_mss=min_mss,
        eligible_only=eligible_only,
        has_capability=has_capability,
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

    vendors = {r.get("vendor") or r.get("provider") for r in filtered if r.get("vendor") or r.get("provider")}
    # Exercised route copies (``mss_inherited_from``) repeat their canonical model's MSS: count each model once.
    canonical = [r for r in filtered if not r.get("mss_inherited_from")]
    mss_values = [r["mss_score"] for r in canonical if r.get("mss_score") is not None]

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
            "eligible": sum(1 for r in canonical if r.get("eligible")),
            "with_popularity": sum(1 for r in canonical if r.get("popularity_score") is not None),
            "with_capability": sum(1 for r in canonical if r.get("capability_score") is not None),
            "unique_vendors": len(vendors),
            "avg_mss": round(sum(mss_values) / len(mss_values), 4) if mss_values else None,
        },
    }


@router.get("/top/", response=TopModelsResponse)
def top_models(request, count: int = 10):
    """The top eligible canonical models by MSS v2."""
    models = services.get_top_models(count=count)
    return {"count": len(models), "models": models}


@router.get("/sources/", response=SourcesResponse)
def sources(request):
    """Provenance of every MSS v2 input (as_of, fetched_at, window, license, citation) and coverage counts."""
    return services.mss_sources()


@router.get("/sensitivity/", response=SensitivityResponse)
def sensitivity(request):
    """Empirical-ranking stability under alternative severity weightings."""

    return services.compute_weight_sensitivity(services.aggregate_rankings())


@router.get("/export/")
def export_csv(request):
    """Export current rankings as CSV."""

    import csv
    import io

    rankings = services.aggregate_rankings()
    buf = io.StringIO()
    # Identity, then the metadata decision aid (MSS + components), then the
    # locally measured empirical columns.
    fieldnames = [
        "model_id",
        "model_name",
        "provider",
        "is_free",
        "context_length",
        "price_per_million_input",
        "price_per_million_output",
        "canonical_id",
        "vendor",
        "open_weights",
        "eligible",
        "exclusion",
        "mss_score",
        "popularity_score",
        "price_score",
        "capability_score",
        "recency_score",
        "mss_coverage",
        "run_cost_usd",
        "price_tier",
        "composite_score",
        "empirical_quality_score",
        "smoke_pass_rate",
        "n_trials",
        "empirical_density_stdev",
        "smoke_pass_rate_stdev",
        "findings_total_static",
        "dependency_findings",
        "ai_findings_total",
        "apps",
        "apps_completed",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for entry in rankings:
        row: dict[str, Any] = {k: entry.get(k) for k in fieldnames}
        variance = entry.get("variance") or {}
        row["smoke_pass_rate"] = entry.get("functional_pass_rate")
        row["empirical_density_stdev"] = variance.get("density_per_kloc_stdev")
        row["smoke_pass_rate_stdev"] = variance.get("smoke_pass_rate_stdev")
        # Unmeasured stays empty in the CSV (None), never 0.
        findings = entry.get("findings")
        row["findings_total_static"] = sum(findings.values()) if findings is not None else None
        row["ai_findings_total"] = sum((entry.get("ai_findings") or {}).values())
        exclusion = entry.get("exclusion") or {}
        row["exclusion"] = exclusion.get("code") or ""
        writer.writerow(row)
    response = HttpResponse(buf.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="model_rankings.csv"'
    return response


# ── MSS v2 study selection ─────────────────────────────────────────────────


def _require_staff(request) -> None:
    user = getattr(request, "auth", None) or getattr(request, "user", None)
    if not getattr(user, "is_staff", False):
        raise HttpError(403, "staff only")


def _selection_params(
    *,
    n: int | None,
    tier_quotas: dict[str, int] | None,
    include_previews: bool,
    exclude: list[str] | None = None,
    force: list[str] | None = None,
    anthropic_budget: float | None = None,
    study_months: float | None = None,
    draws: dict[str, int] | None = None,
) -> dict[str, Any]:
    from backend.rankings.services.constants import TIERS
    from backend.rankings.services.study_selection import default_params

    params = default_params(
        n=n,
        tier_quotas=tier_quotas,
        include_previews=include_previews,
        exclude=exclude or [],
        force=force or [],
        anthropic_budget=anthropic_budget,
        study_months=study_months,
        draws=draws,
    )
    quotas = params["tier_quotas"]
    if set(quotas) != set(TIERS) or sum(int(quotas[t]) for t in TIERS) != int(params["n"]):
        raise HttpError(400, f"tier quotas {quotas} must cover {list(TIERS)} and sum to n={params['n']}")
    return params


def _budgets(request, params: dict[str, Any], openrouter_budget: float | None) -> dict[str, Any]:
    """Hard budgets: an explicit OpenRouter amount, else the live credit.

    Staff may read the live credit; others get the cached value.
    """
    from backend.rankings.services.study_selection import openrouter_credit
    from backend.rankings.services.study_selection import resolve_budgets

    user = getattr(request, "auth", None) or getattr(request, "user", None)
    live = None
    if openrouter_budget is None:
        live = openrouter_credit(allow_network=bool(getattr(user, "is_staff", False)))
    return resolve_budgets(
        {**params, "openrouter_budget": openrouter_budget},
        live_credit=live,
        fallback_openrouter=None,
    )


def _snapshot_summary(snap) -> dict[str, Any]:
    payload = snap.payload or {}
    selection = payload.get("selection") or {}
    return {
        "id": snap.pk,
        "created_at": snap.created_at.isoformat(),
        "kind": snap.kind,
        "version": snap.version,
        "status": snap.status,
        "as_of": payload.get("as_of"),
        "inputs_sha256": snap.inputs_sha256,
        "code_version": snap.code_version,
        "picks": [p["canonical_id"] for p in selection.get("picks") or []],
        "by_tier": selection.get("by_tier") or {},
        "created_by": getattr(snap.created_by, "username", None),
        "confirmed_at": snap.confirmed_at.isoformat() if snap.confirmed_at else None,
        "confirmed_by": getattr(snap.confirmed_by, "username", None),
        "confirmation": snap.confirmation or {},
    }


def _snapshot_detail(snap, *, include_inputs: bool = False) -> dict[str, Any]:
    """Summary + params + payload. The stored replay inputs (about 1 MB) only with ``include_inputs``."""
    payload = dict(snap.payload or {})
    if not include_inputs:
        payload.pop("inputs", None)
    return {**_snapshot_summary(snap), "params": snap.params or {}, "payload": payload}


@router.get("/selection/", response=dict)
def selection_preview(
    request,
    n: int = 10,
    frontier: int | None = None,
    mid: int | None = None,
    budget: int | None = None,
    include_previews: bool = False,
    sensitivity: bool = False,
    openrouter_budget: float | None = None,
    anthropic_budget: float | None = None,
    study_months: float | None = None,
):
    """Live preview of the MSS v2 selection; writes nothing.

    With ``sensitivity=true`` the draws are capped (S1 1,000, S2 500, S3 0)
    and the payload says so (``preview.draws_capped``).
    """
    from django.utils import timezone

    from backend.rankings.services.constants import SENSITIVITY_PREVIEW_DRAWS
    from backend.rankings.services.study_selection import build_inputs
    from backend.rankings.services.study_selection import run_selection

    quotas = None
    if frontier is not None or mid is not None or budget is not None:
        quotas = {"frontier": frontier or 0, "mid": mid or 0, "budget": budget or 0}
    params = _selection_params(
        n=n,
        tier_quotas=quotas,
        include_previews=include_previews,
        anthropic_budget=anthropic_budget,
        study_months=study_months,
        draws=dict(SENSITIVITY_PREVIEW_DRAWS),
    )
    budgets = _budgets(request, params, openrouter_budget)
    inputs = build_inputs(timezone.now().date(), include_previews=include_previews, budgets=budgets)
    payload = run_selection(inputs, params, sensitivity=sensitivity)
    payload.pop("inputs", None)  # the preview is not a record; snapshots keep the inputs
    payload["preview"] = {
        "written": False,
        "sensitivity": sensitivity,
        "draws_capped": bool(sensitivity),
        "draws": params["draws"],
    }
    return payload


@router.get("/selection/snapshots/", response=list[SelectionSnapshotSummary])
def selection_snapshots(request, limit: int = 50):
    from backend.rankings.models import RankingSnapshot

    qs = RankingSnapshot.objects.filter(kind=RankingSnapshot.Kind.SELECTION).select_related(
        "created_by",
        "confirmed_by",
    )
    return [_snapshot_summary(s) for s in qs.order_by("-created_at", "-pk")[: max(1, min(limit, 200))]]


@router.get("/selection/snapshots/{snapshot_id}/", response=SelectionSnapshotDetail)
def selection_snapshot(request, snapshot_id: int, include_inputs: bool = False):
    from backend.rankings.models import RankingSnapshot

    snap = RankingSnapshot.objects.filter(kind=RankingSnapshot.Kind.SELECTION, pk=snapshot_id).first()
    if snap is None:
        raise HttpError(404, "selection snapshot not found")
    return _snapshot_detail(snap, include_inputs=include_inputs)


@router.get("/selection/latest/", response={200: SelectionSnapshotDetail, 404: dict})
def selection_latest(request, include_inputs: bool = False):
    """The confirmed selection, else the newest draft."""
    from backend.rankings.services.snapshot import latest_selection

    snap = latest_selection()
    if snap is None:
        return 404, {"detail": "no selection snapshot yet"}
    return 200, _snapshot_detail(snap, include_inputs=include_inputs)


@router.post("/selection/snapshots/", response={201: SelectionSnapshotDetail})
def create_selection(request, body: SelectionCreateIn):
    """Create a draft selection snapshot with the full sensitivity draws (staff only)."""
    from django.utils import timezone

    from backend.rankings.services.snapshot import create_selection_snapshot
    from backend.rankings.services.study_selection import build_inputs
    from backend.rankings.services.study_selection import run_selection

    _require_staff(request)
    params = _selection_params(
        n=body.n,
        tier_quotas=body.tier_quotas,
        include_previews=body.include_previews,
        exclude=body.exclude,
        force=body.force,
        anthropic_budget=body.anthropic_budget,
        study_months=body.study_months,
    )
    budgets = _budgets(request, params, body.openrouter_budget)
    inputs = build_inputs(timezone.now().date(), include_previews=body.include_previews, budgets=budgets)
    payload = run_selection(inputs, params, sensitivity=body.sensitivity)
    user = getattr(request, "auth", None) or getattr(request, "user", None)
    snap = create_selection_snapshot(payload, params=params, inputs=inputs, user=user)
    return 201, _snapshot_detail(snap)


@router.post("/selection/snapshots/{snapshot_id}/confirm/", response=SelectionSnapshotDetail)
def confirm_selection(request, snapshot_id: int, body: ConfirmIn):
    """Record the researcher's confirmation (staff only); an earlier confirmation is superseded."""
    from backend.rankings.models import RankingSnapshot
    from backend.rankings.services.snapshot import confirm_selection as confirm

    _require_staff(request)
    snap = RankingSnapshot.objects.filter(kind=RankingSnapshot.Kind.SELECTION, pk=snapshot_id).first()
    if snap is None:
        raise HttpError(404, "selection snapshot not found")
    user = getattr(request, "auth", None) or getattr(request, "user", None)
    try:
        confirm(snap, user, final_ids=body.final_ids, overrides=body.overrides, notes=body.notes)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return _snapshot_detail(snap)
