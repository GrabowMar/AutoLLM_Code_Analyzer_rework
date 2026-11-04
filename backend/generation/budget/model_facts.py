"""What the budget code needs to know about a model: limits, capabilities, prices and its endpoint.

``model_facts(model)`` reads an ``LLMModel`` row (or a dict, or passes ``ModelFacts`` through), so the
policy clamps, the lint, the estimator and the gateway config all see the same facts. Prices are USD per
million tokens. Unknown values stay ``None`` (never 0): an unknown context length is not a clamp, and an
unknown price makes the estimate say so instead of returning a free run.

A model on a local or remote endpoint (OpenCode Go, Ollama, ...) often has no capability metadata of its
own (0 context / output, no ``supported_parameters``). When it is linked to a catalog row, the link keeps
the **serving provider's** own catalogue record (``metadata.catalog_twin.provider_record``, e.g. models.dev's
``opencode-go`` entry): its ``limit`` fills the unknown context / output limits and its ``reasoning_options``
mark the model as a ``reasoning_model`` (:func:`serving_record_facts`, E2E-15). The record describes the
endpoint actually called, so it is used instead of the catalog twin's OpenRouter limits; a row's own values
always win. It does **not** set ``supports_reasoning``: the gateway forwards the policy's reasoning control
as OpenRouter's ``reasoning`` object, which OpenCode Go accepts and ignores (it honours ``reasoning_effort``),
so the policy keeps clamping such a model to ``provider_default`` and the recorded treatment stays true.
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
    "serving_record_facts",
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
    supports_reasoning: bool = False  # the reasoning control the gateway sends is honoured (policy keeps it)
    # The model reasons (spends reasoning tokens) even when the control is not honoured: an endpoint model whose
    # serving provider lists reasoning options (E2E-15). Implied by ``supports_reasoning``.
    reasoning_model: bool = False
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


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def serving_record_facts(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Capabilities from the serving provider's catalogue record of a linked endpoint model.

    ``{"source", "context_length", "max_output", "supports_reasoning", "reasoning_options"}`` with only the
    values the record states (``{}`` without a record). ``supports_reasoning`` is true when the provider
    lists any reasoning option (toggle, effort or budget), false for an empty list, and absent when the
    record says nothing about reasoning.
    """
    twin = (metadata or {}).get("catalog_twin")
    record = twin.get("provider_record") if isinstance(twin, Mapping) else None
    if not isinstance(record, Mapping):
        return {}
    out: dict[str, Any] = {"source": str(twin.get("source") or "metadata.catalog_twin.provider_record")}
    limit = record.get("limit") if isinstance(record.get("limit"), Mapping) else {}
    context = _positive_int(limit.get("context"))
    output = _positive_int(limit.get("output"))
    if context is not None:
        out["context_length"] = context
    if output is not None:
        out["max_output"] = output
    options = record.get("reasoning_options")
    if isinstance(record.get("reasoning"), bool):
        out["supports_reasoning"] = record["reasoning"]
    if isinstance(options, list):
        out["reasoning_options"] = [dict(o) for o in options if isinstance(o, Mapping)]
        out.setdefault("supports_reasoning", bool(out["reasoning_options"]))
    return out


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
    context_length = int(model.context_window) if getattr(model, "context_window", 0) else None
    max_output = int(model.max_output_tokens) if getattr(model, "max_output_tokens", 0) else None
    extra: dict[str, Any] = {}
    reasoning_model = reasoning
    if endpoint is not None:
        # The serving provider's catalogue record fills what the endpoint row leaves unknown (E2E-15).
        served = serving_record_facts(meta)
        if served:
            if context_length is None:
                context_length = served.get("context_length")
            if max_output is None:
                max_output = served.get("max_output")
            if not params and "supports_reasoning" not in meta and served.get("supports_reasoning"):
                reasoning_model = True
            extra = {"capability_source": served["source"]}
            if "reasoning_options" in served:
                extra["reasoning_options"] = served["reasoning_options"]
    return ModelFacts(
        slug=slug,
        family=family,
        provider=str(getattr(model, "provider", "") or ""),
        context_length=context_length,
        max_output=max_output,
        supports_reasoning=reasoning,
        reasoning_model=reasoning_model,
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
        extra=extra,
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
