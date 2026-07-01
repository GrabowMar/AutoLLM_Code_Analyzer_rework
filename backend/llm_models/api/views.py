"""Django Ninja API views for LLM models."""

from __future__ import annotations

import csv
import json
from io import StringIO

from django.db.models import Avg
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja import Router
from ninja.errors import HttpError
from ninja.responses import Status

from backend.common.pagination import paginate_queryset
from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import get_openrouter_key
from backend.llm_models.api.schema import LLMModelListSchema
from backend.llm_models.api.schema import LLMModelSchema
from backend.llm_models.api.schema import ModelComparisonSchema
from backend.llm_models.api.schema import ModelImportResultSchema
from backend.llm_models.api.schema import PaginatedModelsSchema
from backend.llm_models.api.schema import StatsSchema
from backend.llm_models.api.schema import SyncResultSchema
from backend.llm_models.models import LLMModel
from backend.llm_models.services import import_models_from_payload
from backend.llm_models.services import normalize_model_identifier
from backend.llm_models.services import refresh_model_from_openrouter
from backend.llm_models.services import sync_models_from_openrouter

router = Router(tags=["models"])

SORT_FIELD_MAP = {
    "model_name": "model_name",
    "provider": "provider",
    "context_window": "context_window",
    "input_price": "input_price_per_token",
    "output_price": "output_price_per_token",
    "cost_efficiency": "cost_efficiency",
    "created_at": "created_at",
    "max_output": "max_output_tokens",
}

PRICE_PER_MILLION_THRESHOLDS = {
    "free": (None, None, True),  # is_free=True
    "low": (0, 1e-6, False),  # <$1/1M → <$0.000001/token
    "medium": (1e-6, 10e-6, False),  # $1-$10/1M
    "high": (10e-6, None, False),  # >$10/1M
}


def _resolve_openrouter_catalog_key(request) -> str | Status[dict[str, str]]:
    try:
        return get_openrouter_key(request.auth, allow_global_fallback=False)
    except MissingApiKeyError as exc:
        return Status(403, {"detail": str(exc), "remediation": exc.remediation})


CONTEXT_RANGE_THRESHOLDS = {
    "small": (0, 8_000),
    "medium": (8_000, 32_000),
    "large": (32_000, 128_000),
    "xlarge": (128_000, None),
}

EXPORT_CSV_FIELDS = [
    "provider",
    "model_name",
    "model_id",
    "canonical_slug",
    "context_window",
    "max_output_tokens",
    "input_price_per_million",
    "output_price_per_million",
    "is_free",
    "supports_function_calling",
    "supports_vision",
    "supports_streaming",
    "supports_json_mode",
    "cost_efficiency",
]


def _resolve_model_queryset(identifier: str):
    raw = (identifier or "").strip()
    normalized = normalize_model_identifier(raw)
    query = Q(canonical_slug__iexact=raw) | Q(model_id__iexact=raw)

    if normalized:
        query |= Q(canonical_slug__iexact=normalized)

    if raw and "/" not in raw and "_" in raw:
        provider, model_name = raw.split("_", 1)
        query |= Q(model_id__iexact=f"{provider}/{model_name}")

    return LLMModel.objects.filter(query)


def _get_model_by_identifier(identifier: str) -> LLMModel:
    return get_object_or_404(
        _resolve_model_queryset(identifier).order_by("canonical_slug"),
    )


def _serialize_export_model(model: LLMModel) -> dict:
    return {
        "id": model.id,
        "model_id": model.model_id,
        "canonical_slug": model.canonical_slug,
        "provider": model.provider,
        "model_name": model.model_name,
        "description": model.description,
        "is_free": model.is_free,
        "context_window": model.context_window,
        "max_output_tokens": model.max_output_tokens,
        "input_price_per_token": model.input_price_per_token,
        "output_price_per_token": model.output_price_per_token,
        "input_price_per_million": round(model.input_price_per_token * 1_000_000, 4),
        "output_price_per_million": round(model.output_price_per_token * 1_000_000, 4),
        "supports_function_calling": model.supports_function_calling,
        "supports_vision": model.supports_vision,
        "supports_streaming": model.supports_streaming,
        "supports_json_mode": model.supports_json_mode,
        "cost_efficiency": model.cost_efficiency,
        "capabilities": model.get_capabilities_list(),
        "capabilities_json": model.capabilities_json,
        "metadata": model.metadata,
        "created_at": model.created_at.isoformat(),
        "updated_at": model.updated_at.isoformat(),
    }


def _build_csv_export(models: list[LLMModel]) -> HttpResponse:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_CSV_FIELDS)
    writer.writeheader()

    for model in models:
        writer.writerow(
            {
                "provider": model.provider,
                "model_name": model.model_name,
                "model_id": model.model_id,
                "canonical_slug": model.canonical_slug,
                "context_window": model.context_window,
                "max_output_tokens": model.max_output_tokens,
                "input_price_per_million": round(
                    model.input_price_per_token * 1_000_000,
                    4,
                ),
                "output_price_per_million": round(
                    model.output_price_per_token * 1_000_000,
                    4,
                ),
                "is_free": model.is_free,
                "supports_function_calling": model.supports_function_calling,
                "supports_vision": model.supports_vision,
                "supports_streaming": model.supports_streaming,
                "supports_json_mode": model.supports_json_mode,
                "cost_efficiency": round(model.cost_efficiency, 6),
            },
        )

    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="models_export.csv"'
    return response


def _apply_search(qs, search: str):
    if search:
        qs = qs.filter(
            Q(model_name__icontains=search)
            | Q(provider__icontains=search)
            | Q(model_id__icontains=search)
            | Q(description__icontains=search),
        )
    return qs


def _apply_capability_filter(qs, capability: str):
    cap_map = {
        "vision": "supports_vision",
        "function_calling": "supports_function_calling",
        "streaming": "supports_streaming",
        "json_mode": "supports_json_mode",
    }
    field = cap_map.get(capability)
    if field:
        qs = qs.filter(**{field: True})
    return qs


def _apply_price_range(qs, price_range: str):
    if price_range not in PRICE_PER_MILLION_THRESHOLDS:
        return qs
    low, high, is_free = PRICE_PER_MILLION_THRESHOLDS[price_range]
    if is_free:
        return qs.filter(is_free=True)
    if low is not None:
        qs = qs.filter(input_price_per_token__gte=low)
    if high is not None:
        qs = qs.filter(input_price_per_token__lt=high)
    return qs


def _apply_context_range(qs, context_range: str):
    if context_range not in CONTEXT_RANGE_THRESHOLDS:
        return qs
    low, high = CONTEXT_RANGE_THRESHOLDS[context_range]
    if low is not None:
        qs = qs.filter(context_window__gte=low)
    if high is not None:
        qs = qs.filter(context_window__lt=high)
    return qs


def _apply_sorting(qs, sort_by: str, sort_dir: str):
    if sort_by and sort_by in SORT_FIELD_MAP:
        order_field = SORT_FIELD_MAP[sort_by]
        if sort_dir == "desc":
            order_field = f"-{order_field}"
        return qs.order_by(order_field, "model_name")
    return qs.order_by("provider", "model_name")


@router.get("/", response=PaginatedModelsSchema)
def list_models(
    request,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    provider: str = Query(""),
    capability: str = Query(""),
    free_only: bool = Query(False),  # noqa: FBT003
    sort_by: str = Query(""),
    sort_dir: str = Query("asc"),
    price_range: str = Query(""),
    context_range: str = Query(""),
    used_in_apps: bool = Query(False),  # noqa: FBT003
):
    """List models with filtering, search, sorting, and pagination."""
    qs = LLMModel.objects.all()
    qs = _apply_search(qs, search)

    if provider:
        qs = qs.filter(provider__iexact=provider)
    if free_only:
        qs = qs.filter(is_free=True)
    if capability:
        qs = _apply_capability_filter(qs, capability)
    if price_range:
        qs = _apply_price_range(qs, price_range)
    if context_range:
        qs = _apply_context_range(qs, context_range)
    if used_in_apps:
        qs = qs.filter(generation_jobs__isnull=False).distinct()

    qs = _apply_sorting(qs, sort_by, sort_dir)

    page_qs, total, page, pages = paginate_queryset(qs, page, per_page)

    items = [LLMModelListSchema.from_model(m) for m in page_qs]
    return PaginatedModelsSchema(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/stats/", response=StatsSchema)
def get_stats(request):
    """Get aggregate statistics about loaded models."""
    qs = LLMModel.objects.all()
    # Exclude models with negative sentinel prices (e.g. -1) from averages
    paid_qs = qs.filter(input_price_per_token__gte=0)
    agg = paid_qs.aggregate(
        avg_in=Avg("input_price_per_token"),
        avg_out=Avg("output_price_per_token"),
    )
    return StatsSchema(
        total=qs.count(),
        providers=qs.values("provider").distinct().count(),
        free=qs.filter(is_free=True).count(),
        avg_input_price=round((agg["avg_in"] or 0) * 1_000_000, 4),
        avg_output_price=round((agg["avg_out"] or 0) * 1_000_000, 4),
    )


@router.get("/providers/", response=list[str])
def list_providers(request):
    """Return distinct provider names."""
    return list(
        LLMModel.objects.values_list("provider", flat=True).distinct().order_by("provider"),
    )


@router.post("/sync/", response={200: SyncResultSchema, 403: dict})
def sync_models(request):
    """Fetch models from OpenRouter and upsert into DB."""
    api_key = _resolve_openrouter_catalog_key(request)
    if not isinstance(api_key, str):
        return api_key
    return Status(200, sync_models_from_openrouter(api_key))


@router.post("/import/", response=ModelImportResultSchema)
def import_models(request):
    """Import models from exported JSON or OpenRouter payload JSON."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(400, "Invalid JSON payload.") from exc

    result = import_models_from_payload(payload)
    if result["count"] == 0:
        raise HttpError(400, "No model records found in payload.")

    return result


@router.get("/export/")
def export_models(request, format: str = Query("csv")):
    """Export models as CSV or JSON."""
    export_format = format.lower()
    models = list(LLMModel.objects.all())

    if export_format == "csv":
        return _build_csv_export(models)
    if export_format == "json":
        return {
            "format": "json",
            "count": len(models),
            "models": [_serialize_export_model(model) for model in models],
        }

    raise HttpError(400, "Unsupported export format.")


@router.get("/comparison/", response=ModelComparisonSchema)
def get_model_comparison(request, models: str = Query("")):
    """Return ordered model data for side-by-side comparison."""
    identifiers = [item.strip() for item in models.split(",") if item.strip()][:6]
    missing: list[str] = []
    selected_models: list[LLMModel] = []
    seen_slugs: set[str] = set()

    for identifier in identifiers:
        match = _resolve_model_queryset(identifier).first()
        if not match:
            missing.append(identifier)
            continue
        if match.canonical_slug in seen_slugs:
            continue
        seen_slugs.add(match.canonical_slug)
        selected_models.append(match)

    return {
        "items": selected_models,
        "missing": missing,
    }


# Slug-based routes MUST come after all fixed-path routes
@router.get("/detail/{slug}/", response=LLMModelSchema)
def get_model(request, slug: str):
    """Get a single model by its canonical slug."""
    return _get_model_by_identifier(slug)


@router.post("/detail/{slug}/refresh/", response={200: LLMModelSchema, 403: dict})
def refresh_model(request, slug: str):
    """Refresh a single model from the OpenRouter model catalog."""
    model = _get_model_by_identifier(slug)
    api_key = _resolve_openrouter_catalog_key(request)
    if not isinstance(api_key, str):
        return api_key
    if not refresh_model_from_openrouter(model, api_key):
        raise HttpError(404, "Model not found in OpenRouter catalog.")
    model.refresh_from_db()
    return Status(200, model)


@router.delete("/detail/{slug}/")
def delete_model(request, slug: str):
    """Delete a model by slug."""
    model = _get_model_by_identifier(slug)
    model.delete()
    return {"success": True}


@router.get("/detail/{slug}/related/", response=list[LLMModelListSchema])
def get_related_models(request, slug: str, limit: int = Query(10, ge=1, le=20)):
    """Get models from the same provider, excluding the current model."""
    model = _get_model_by_identifier(slug)
    related = (
        LLMModel.objects.filter(provider=model.provider)
        .exclude(pk=model.pk)
        .order_by("-cost_efficiency", "model_name")[:limit]
    )
    return [LLMModelListSchema.from_model(m) for m in related]
