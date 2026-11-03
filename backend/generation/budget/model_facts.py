"""What the budget code needs to know about a model: limits, capabilities, prices and its endpoint.

``model_facts(model)`` reads an ``LLMModel`` row (or a dict, or passes ``ModelFacts`` through), so the
policy clamps, the lint, the estimator and the gateway config all see the same facts. Prices are USD per
million tokens. Unknown values stay ``None`` (never 0): an unknown context length is not a clamp, and an
unknown price makes the estimate say so instead of returning a free run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any
from typing import Final

__all__ = [
    "EXPLICIT_CACHE_FAMILIES",
    "ModelFacts",
    "is_model_alias",
    "model_facts",
]

_MTOK: Final = 1_000_000
# Providers whose caching needs explicit cache_control markers (spec 4.2 caching.prompt_cache=on).
EXPLICIT_CACHE_FAMILIES: Final = frozenset({"anthropic", "qwen", "google"})
# Families with provider-side prompt caching (implicit or explicit) when no cached price is listed.
_CACHING_FAMILIES: Final = frozenset(
    {"anthropic", "openai", "deepseek", "google", "qwen", "x-ai", "moonshotai", "z-ai"},
)
_ROUTER_PREFIXES: Final = ("openrouter/", "~")


@dataclass(frozen=True)
class ModelFacts:
    slug: str
    family: str = ""
    provider: str = ""
    context_length: int | None = None
    max_output: int | None = None
    supports_reasoning: bool = False
    supports_tools: bool = True
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None
    cached_input_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    endpoint_id: int | None = None
    endpoint_kind: str = "openrouter"
    cost_basis: str = "billed"  # billed | list_price | free
    served_model_name: str = ""
    supports_batch: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_anthropic(self) -> bool:
        return self.family == "anthropic" or self.slug.startswith("anthropic/")

    @property
    def is_local(self) -> bool:
        return self.endpoint_kind not in ("openrouter", "")

    @property
    def explicit_cache(self) -> bool:
        return self.family in EXPLICIT_CACHE_FAMILIES

    @property
    def caches(self) -> bool:
        """Whether prompt caching is expected to discount repeated prefixes on this model."""
        return self.cached_input_per_mtok is not None or self.family in _CACHING_FAMILIES

    @property
    def priced(self) -> bool:
        return self.input_per_mtok is not None and self.output_per_mtok is not None

    @property
    def cache_discount(self) -> float | None:
        """``d = 1 - cached_price / input_price`` when both are known."""
        if self.cached_input_per_mtok is None or not self.input_per_mtok:
            return None
        return max(0.0, 1.0 - self.cached_input_per_mtok / self.input_per_mtok)

    def with_pricing(self, pricing: Mapping[str, Any] | None) -> ModelFacts:
        """These facts with the manifest's frozen pricing (``ManifestPricing``) taking precedence."""
        if not pricing:
            return self
        return replace(
            self,
            input_per_mtok=_num(pricing.get("input_per_mtok"), self.input_per_mtok),
            output_per_mtok=_num(pricing.get("output_per_mtok"), self.output_per_mtok),
            cached_input_per_mtok=_num(pricing.get("cached_input_per_mtok"), self.cached_input_per_mtok),
            cache_write_per_mtok=_num(pricing.get("cache_write_per_mtok"), self.cache_write_per_mtok),
        )


def _num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _per_mtok(per_token: Any) -> float | None:
    value = _num(per_token)
    return None if value is None else value * _MTOK


def is_model_alias(slug: str) -> bool:
    """``-latest`` / ``:latest`` aliases and router slugs (refused in confirmatory studies)."""
    lowered = slug.lower()
    return lowered.endswith(("-latest", ":latest")) or lowered.startswith(_ROUTER_PREFIXES)


def _from_row(model: Any) -> ModelFacts:
    raw = model.capabilities_json if isinstance(getattr(model, "capabilities_json", None), dict) else {}
    meta = model.metadata if isinstance(getattr(model, "metadata", None), dict) else {}
    params = {str(p) for p in raw.get("supported_parameters") or [] if isinstance(p, str)}
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    slug = str(model.model_id)
    family = (getattr(model, "family", "") or "").lower() or slug.split("/", 1)[0].lower()
    endpoint = getattr(model, "endpoint", None) if getattr(model, "endpoint_id", None) else None
    input_price = _num(getattr(model, "input_price_per_token", None))
    output_price = _num(getattr(model, "output_price_per_token", None))
    is_free = bool(getattr(model, "is_free", False))
    # A row with 0/0 prices that is not marked free has *unknown* prices (catalog not enriched).
    unknown_price = not is_free and not input_price and not output_price and endpoint is None
    reasoning = "reasoning" in params or "include_reasoning" in params or bool(meta.get("supports_reasoning"))
    tools = bool(getattr(model, "supports_function_calling", False)) or "tools" in params
    if endpoint is not None and not params:
        tools = bool(getattr(model, "supports_function_calling", True))
    return ModelFacts(
        slug=slug,
        family=family,
        provider=str(getattr(model, "provider", "") or ""),
        context_length=int(model.context_window) if getattr(model, "context_window", 0) else None,
        max_output=int(model.max_output_tokens) if getattr(model, "max_output_tokens", 0) else None,
        supports_reasoning=reasoning,
        supports_tools=tools,
        input_per_mtok=None if unknown_price else (input_price or 0.0) * _MTOK,
        output_per_mtok=None if unknown_price else (output_price or 0.0) * _MTOK,
        cached_input_per_mtok=_per_mtok(pricing.get("input_cache_read")),
        cache_write_per_mtok=_per_mtok(pricing.get("input_cache_write")),
        endpoint_id=getattr(model, "endpoint_id", None),
        endpoint_kind=str(endpoint.kind) if endpoint is not None else "openrouter",
        cost_basis=str(getattr(endpoint, "cost_basis", "billed")) if endpoint is not None else "billed",
        served_model_name=str(getattr(model, "served_model_name", "") or ""),
        supports_batch=bool(meta.get("supports_batch", False)),
    )


def model_facts(model: Any) -> ModelFacts:
    """``ModelFacts`` from an ``LLMModel`` row, a mapping of the dataclass fields, or ``ModelFacts`` itself."""
    if isinstance(model, ModelFacts):
        return model
    if isinstance(model, Mapping):
        known = set(ModelFacts.__dataclass_fields__)
        values = {key: value for key, value in model.items() if key in known}
        values.setdefault("slug", str(model.get("slug") or model.get("model_id") or "unknown"))
        values.setdefault("family", values["slug"].split("/", 1)[0].lower())
        return ModelFacts(**values)
    if model is None:
        msg = "model facts need a model"
        raise ValueError(msg)
    return _from_row(model)
