"""Import models from exported / generic JSON payloads."""

from typing import Any

from backend.llm_models.models import LLMModel
from backend.llm_models.services.helpers import _build_canonical_slug
from backend.llm_models.services.helpers import _coerce_bool
from backend.llm_models.services.helpers import _coerce_float
from backend.llm_models.services.helpers import _coerce_int
from backend.llm_models.services.helpers import _update_or_create_model
from backend.llm_models.services.sync import upsert_openrouter_models


def extract_import_models(payload: Any) -> list[dict[str, Any]]:
    """Extract a normalized list of model payloads from import JSON."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        if isinstance(payload.get("models"), list):
            return [item for item in payload["models"] if isinstance(item, dict)]
        if isinstance(payload.get("data"), list):
            return [item for item in payload["data"] if isinstance(item, dict)]
        return [payload]

    return []


def upsert_imported_models(models_payload: list[dict[str, Any]]) -> int:
    """Upsert models from an exported/internal JSON representation."""
    upserted = 0

    for model_data in models_payload:
        model_id = str(model_data.get("model_id") or model_data.get("id") or "").strip()
        if not model_id:
            continue

        canonical_slug = _build_canonical_slug(
            model_id,
            str(model_data.get("canonical_slug") or ""),
        )
        provider = str(
            model_data.get("provider") or (model_id.split("/", 1)[0] if "/" in model_id else "unknown"),
        ).strip()
        model_name = str(
            model_data.get("model_name") or model_data.get("name") or model_id.split("/", 1)[-1],
        ).strip()
        description = str(model_data.get("description") or "")

        input_price = _coerce_float(model_data.get("input_price_per_token"))
        output_price = _coerce_float(model_data.get("output_price_per_token"))
        if input_price == 0 and model_data.get("input_price_per_million") not in (
            None,
            "",
        ):
            input_price = _coerce_float(model_data.get("input_price_per_million")) / 1_000_000
        if output_price == 0 and model_data.get("output_price_per_million") not in (
            None,
            "",
        ):
            output_price = _coerce_float(model_data.get("output_price_per_million")) / 1_000_000

        context_window = _coerce_int(model_data.get("context_window"))
        max_output_tokens = _coerce_int(model_data.get("max_output_tokens"))

        capabilities_json = model_data.get("capabilities_json")
        if not isinstance(capabilities_json, dict):
            capabilities_json = {}
            for field in (
                "capabilities",
                "supported_parameters",
                "top_provider",
                "pricing",
                "architecture",
            ):
                if field in model_data:
                    capabilities_json[field] = model_data[field]

        metadata = model_data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        capabilities = model_data.get("capabilities")
        supports_function_calling = _coerce_bool(
            model_data.get("supports_function_calling"),
            isinstance(capabilities, list) and "Function Calling" in capabilities,
        )
        supports_vision = _coerce_bool(
            model_data.get("supports_vision"),
            isinstance(capabilities, list) and "Vision" in capabilities,
        )
        supports_streaming = _coerce_bool(
            model_data.get("supports_streaming"),
            isinstance(capabilities, list) and "Streaming" in capabilities,
        )
        supports_json_mode = _coerce_bool(
            model_data.get("supports_json_mode"),
            isinstance(capabilities, list) and "JSON Mode" in capabilities,
        )
        is_free = _coerce_bool(
            model_data.get("is_free"),
            input_price == 0 and output_price == 0,
        )

        cost_efficiency = _coerce_float(model_data.get("cost_efficiency"))
        if cost_efficiency <= 0:
            cost_efficiency = LLMModel.calculate_cost_efficiency(
                context_window,
                input_price,
                output_price,
            )

        defaults = {
            "provider": provider,
            "model_name": model_name,
            "description": description[:5000],
            "is_free": is_free,
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "input_price_per_token": input_price,
            "output_price_per_token": output_price,
            "supports_function_calling": supports_function_calling,
            "supports_vision": supports_vision,
            "supports_streaming": supports_streaming,
            "supports_json_mode": supports_json_mode,
            "cost_efficiency": cost_efficiency,
            "capabilities_json": capabilities_json,
            "metadata": metadata,
        }

        if _update_or_create_model(model_id, canonical_slug, defaults):
            upserted += 1

    return upserted


def import_models_from_payload(payload: Any) -> dict[str, int]:
    """Import models from OpenRouter payloads or exported model JSON."""
    items = extract_import_models(payload)
    openrouter_items: list[dict[str, Any]] = []
    exported_items: list[dict[str, Any]] = []

    for item in items:
        if "model_id" in item or "canonical_slug" in item:
            exported_items.append(item)
            continue
        if "id" in item:
            openrouter_items.append(item)

    imported = 0
    if openrouter_items:
        imported += upsert_openrouter_models(openrouter_items)
    if exported_items:
        imported += upsert_imported_models(exported_items)

    return {"count": len(items), "imported": imported}
