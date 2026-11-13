"""Django Ninja schemas for LLM models."""

from datetime import UTC
from datetime import datetime

from ninja import ModelSchema
from ninja import Schema

from backend.llm_models.models import LLMModel

CONTEXT_MILLION = 1_000_000
CONTEXT_THOUSAND = 1_000


class LLMModelEndpointRef(Schema):
    """Where a local model runs (``None`` for the OpenRouter catalog)."""

    id: int
    slug: str
    name: str
    kind: str
    online: bool


def endpoint_ref(obj: LLMModel, online_cache: dict[int, bool] | None = None) -> dict | None:
    """``{id, slug, name, kind, online}`` for a local model; ``online_cache`` saves one liveness query per endpoint."""
    if obj.endpoint_id is None or obj.endpoint.is_system:
        return None
    endpoint = obj.endpoint
    cache = online_cache if online_cache is not None else {}
    if endpoint.pk not in cache:
        from backend.inference.live import is_online

        cache[endpoint.pk] = is_online(endpoint)
    return {
        "id": endpoint.pk,
        "slug": endpoint.slug,
        "name": endpoint.name,
        "kind": endpoint.kind,
        "online": cache[endpoint.pk],
    }


# What a local model's page may inherit from its catalog twin.
TWIN_METADATA_KEYS = ("models_dev", "huggingface", "params", "epoch", "lmarena")
TWIN_CAPABILITY_KEYS = ("hugging_face_id", "reasoning", "default_parameters", "architecture", "benchmarks")


def twin_payload(obj: LLMModel) -> dict | None:
    """``{link, model, metadata, capabilities}`` of the catalog twin named in ``metadata.catalog_twin``."""
    link = (obj.metadata or {}).get("catalog_twin")
    if obj.endpoint_id is None or not isinstance(link, dict) or not link.get("model_id"):
        return None
    row = LLMModel.objects.filter(endpoint__isnull=True, model_id=link["model_id"]).first()
    if row is None:
        return {"link": link, "model": None, "metadata": {}, "capabilities": {}}
    meta = row.metadata or {}
    caps = row.capabilities_json or {}
    return {
        "link": link,
        "model": {
            "id": row.pk,
            "model_id": row.model_id,
            "canonical_slug": row.canonical_slug,
            "model_name": row.model_name,
            "provider": row.provider,
            "context_window": row.context_window,
            "max_output_tokens": row.max_output_tokens,
        },
        "metadata": {k: meta[k] for k in TWIN_METADATA_KEYS if k in meta},
        "capabilities": {k: caps[k] for k in TWIN_CAPABILITY_KEYS if k in caps},
    }


class LLMModelSchema(ModelSchema):
    """Full model representation for list and detail views."""

    capabilities: list[str]
    input_price_per_million: float
    output_price_per_million: float
    context_window_display: str
    endpoint: LLMModelEndpointRef | None = None
    served_model_name: str = ""
    digest: str = ""
    quantization: str = ""
    parameter_size: str = ""
    is_local: bool = False
    # Local endpoint model linked to a catalog row (metadata.catalog_twin): that row's
    # metadata, shown as inherited ("assumed same weights"). Never merged or pooled.
    twin: dict | None = None

    class Meta:
        model = LLMModel
        fields = [
            "id",
            "model_id",
            "canonical_slug",
            "provider",
            "model_name",
            "description",
            "is_free",
            "context_window",
            "max_output_tokens",
            "input_price_per_token",
            "output_price_per_token",
            "supports_function_calling",
            "supports_vision",
            "supports_streaming",
            "supports_json_mode",
            "cost_efficiency",
            "metadata",
            "capabilities_json",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_capabilities(obj: LLMModel) -> list[str]:
        return obj.get_capabilities_list()

    @staticmethod
    def resolve_endpoint(obj: LLMModel) -> dict | None:
        return endpoint_ref(obj)

    @staticmethod
    def resolve_is_local(obj: LLMModel) -> bool:
        return obj.is_local

    @staticmethod
    def resolve_twin(obj: LLMModel) -> dict | None:
        return twin_payload(obj)

    @staticmethod
    def resolve_input_price_per_million(obj: LLMModel) -> float:
        return round(obj.input_price_per_token * CONTEXT_MILLION, 4)

    @staticmethod
    def resolve_output_price_per_million(obj: LLMModel) -> float:
        return round(obj.output_price_per_token * CONTEXT_MILLION, 4)

    @staticmethod
    def resolve_context_window_display(obj: LLMModel) -> str:
        cw = obj.context_window
        if cw >= CONTEXT_MILLION:
            return f"{cw / CONTEXT_MILLION:.0f}M"
        if cw >= CONTEXT_THOUSAND:
            return f"{cw / CONTEXT_THOUSAND:.0f}K"
        return str(cw)


class LLMModelListSchema(Schema):
    """Lightweight model for list views."""

    id: int
    model_id: str
    canonical_slug: str
    provider: str
    model_name: str
    description: str
    is_free: bool
    context_window: int
    max_output_tokens: int
    context_window_display: str
    input_price_per_million: float
    output_price_per_million: float
    capabilities: list[str]
    cost_efficiency: float
    supports_vision: bool
    supports_function_calling: bool
    supports_streaming: bool
    supports_json_mode: bool
    # OpenRouter lists "reasoning" / "include_reasoning" among the model's
    # supported parameters (catalog sync metadata).
    supports_reasoning: bool = False
    # Local endpoint models (spec §F.3); None / "" / False for the OpenRouter catalog.
    endpoint: LLMModelEndpointRef | None = None
    served_model_name: str = ""
    digest: str = ""
    quantization: str = ""
    parameter_size: str = ""
    is_local: bool = False
    is_available: bool = True
    # Catalog / external facts for the optional table columns (all may be empty).
    released_at: str | None = None
    open_weights: bool | None = None
    tokenizer: str = ""
    input_modalities: list[str] = []
    parameters: int | None = None
    parameters_source: str = ""
    research_data: list[str] = []

    @staticmethod
    def from_model(obj: LLMModel, online_cache: dict[int, bool] | None = None) -> "LLMModelListSchema":
        cw = obj.context_window
        if cw >= CONTEXT_MILLION:
            cw_display = f"{cw / CONTEXT_MILLION:.0f}M"
        elif cw >= CONTEXT_THOUSAND:
            cw_display = f"{cw / CONTEXT_THOUSAND:.0f}K"
        else:
            cw_display = str(cw)

        # Truncate description for list view
        max_desc_len = 120
        desc = obj.description or ""
        if len(desc) > max_desc_len:
            desc = desc[:117] + "..."

        return LLMModelListSchema(
            id=obj.id,
            model_id=obj.model_id,
            canonical_slug=obj.canonical_slug,
            provider=obj.provider,
            model_name=obj.model_name,
            description=desc,
            is_free=obj.is_free,
            context_window=obj.context_window,
            max_output_tokens=obj.max_output_tokens,
            context_window_display=cw_display,
            input_price_per_million=round(
                obj.input_price_per_token * CONTEXT_MILLION,
                4,
            ),
            output_price_per_million=round(
                obj.output_price_per_token * CONTEXT_MILLION,
                4,
            ),
            capabilities=obj.get_capabilities_list(),
            cost_efficiency=obj.cost_efficiency,
            supports_vision=obj.supports_vision,
            supports_function_calling=obj.supports_function_calling,
            supports_streaming=obj.supports_streaming,
            supports_json_mode=obj.supports_json_mode,
            supports_reasoning=model_supports_reasoning(obj),
            endpoint=endpoint_ref(obj, online_cache),
            served_model_name=obj.served_model_name,
            digest=obj.digest,
            quantization=obj.quantization,
            parameter_size=obj.parameter_size,
            is_local=obj.is_local,
            is_available=obj.is_available,
            **list_column_facts(obj.metadata or {}),
        )


def list_column_facts(meta: dict) -> dict:
    """Release date, weights, tokenizer, input types, parameter count and research-data coverage.

    Parameters come from the Hugging Face weights (exact) before Epoch AI's reported figure.
    """
    released = None
    created = meta.get("openrouter_created")
    if isinstance(created, (int, float)) and created > 0:
        released = datetime.fromtimestamp(created, tz=UTC).date().isoformat()
    else:
        md_date = (meta.get("models_dev") or {}).get("release_date")
        if isinstance(md_date, str) and md_date:
            released = md_date[:10]

    weights = (meta.get("models_dev") or {}).get("open_weights")
    modalities = meta.get("architecture_input_modalities")
    tokenizer = meta.get("architecture_tokenizer")

    params, source = None, ""
    for key in ("huggingface", "epoch"):
        value = (meta.get(key) or {}).get("parameters") if isinstance(meta.get(key), dict) else None
        if isinstance(value, (int, float)) and value > 0:
            params, source = int(value), key
            break

    return {
        "released_at": released,
        "open_weights": weights if isinstance(weights, bool) else None,
        "tokenizer": tokenizer if isinstance(tokenizer, str) and tokenizer != "Router" else "",
        "input_modalities": [m for m in modalities if isinstance(m, str)] if isinstance(modalities, list) else [],
        "parameters": params,
        "parameters_source": source,
        "research_data": [k for k in ("lmarena", "epoch", "huggingface") if isinstance(meta.get(k), dict)],
    }


REASONING_PARAMETERS = frozenset({"reasoning", "include_reasoning"})


def model_supports_reasoning(obj: LLMModel) -> bool:
    if obj.endpoint_id is not None and not obj.endpoint.is_system:
        caps = ((obj.metadata or {}).get("ollama") or {}).get("capabilities") or []
        return "thinking" in caps
    supported = (obj.metadata or {}).get("openrouter_supported_parameters")
    return isinstance(supported, list) and not REASONING_PARAMETERS.isdisjoint(supported)


class SyncResultSchema(Schema):
    fetched: int
    upserted: int


class FacetValueSchema(Schema):
    value: str
    count: int


class ModelFacetsSchema(Schema):
    providers: list[FacetValueSchema]
    tokenizers: list[FacetValueSchema]


class StatsSchema(Schema):
    total: int
    providers: int
    free: int
    avg_input_price: float
    avg_output_price: float


class PaginatedModelsSchema(Schema):
    items: list[LLMModelListSchema]
    total: int
    page: int
    per_page: int
    pages: int


class ModelImportResultSchema(Schema):
    count: int
    imported: int


class ModelComparisonSchema(Schema):
    items: list[LLMModelSchema]
    missing: list[str]
