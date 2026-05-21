"""Django Ninja schemas for LLM models."""

from ninja import ModelSchema
from ninja import Schema

from llm_lab.llm_models.models import LLMModel

CONTEXT_MILLION = 1_000_000
CONTEXT_THOUSAND = 1_000


class LLMModelSchema(ModelSchema):
    """Full model representation for list and detail views."""

    capabilities: list[str]
    input_price_per_million: float
    output_price_per_million: float
    context_window_display: str

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

    @staticmethod
    def from_model(obj: LLMModel) -> "LLMModelListSchema":
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
        )


class SyncResultSchema(Schema):
    fetched: int
    upserted: int


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
