"""OpenRouter integration — fetch models and upsert into the database."""

import logging
import time

import requests
from django.conf import settings

from llm_lab.llm_models.models import LLMModel
from llm_lab.llm_models.services.helpers import _build_canonical_slug
from llm_lab.llm_models.services.helpers import _update_or_create_model
from llm_lab.llm_models.services.helpers import normalize_model_identifier

logger = logging.getLogger(__name__)


def fetch_openrouter_models(api_key: str) -> list[dict]:
    """Fetch all models from the OpenRouter API. Returns raw model dicts."""
    api_key = api_key.strip()
    if not api_key:
        logger.error("OpenRouter API key not provided")
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://llm-lab.local",
        "X-Title": "LLM Eval Lab",
        "Content-Type": "application/json",
    }

    logger.info("Fetching models from OpenRouter API…")
    start = time.time()

    try:
        resp = requests.get(
            settings.OPENROUTER_API_URL,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("OpenRouter API request failed")
        return []

    data = resp.json()
    models = data.get("data", [])
    duration = time.time() - start
    logger.info("Fetched %d models from OpenRouter in %.2fs", len(models), duration)
    return models


def upsert_openrouter_models(models_payload: list[dict]) -> int:
    """Upsert a list of OpenRouter model payloads into LLMModel.

    Returns count upserted.
    """
    upserted = 0
    for model_data in models_payload:
        model_id = model_data.get("id")
        if not model_id:
            continue

        # Build canonical slug
        canonical = model_id.replace("/", "_").replace(":", "_")
        raw_cs = model_data.get("canonical_slug", "")
        if raw_cs:
            canonical = raw_cs
        canonical = _build_canonical_slug(model_id, canonical)

        provider = model_id.split("/")[0] if "/" in model_id else "unknown"
        model_name = model_data.get("name") or model_id.split("/")[-1]
        description = model_data.get("description", "") or ""

        # Pricing
        pricing = model_data.get("pricing") or {}
        try:
            prompt_price = float(
                pricing.get("prompt") or pricing.get("prompt_tokens") or 0,
            )
        except (TypeError, ValueError):
            prompt_price = 0.0
        try:
            completion_price = float(
                pricing.get("completion") or pricing.get("completion_tokens") or 0,
            )
        except (TypeError, ValueError):
            completion_price = 0.0

        # Context window
        top_provider = model_data.get("top_provider") or {}
        context_window = 0
        context_sources = [
            top_provider.get("context_length"),
            model_data.get("context_length"),
        ]
        for src in context_sources:
            if src:
                try:
                    context_window = int(src)
                    break
                except (TypeError, ValueError):
                    pass

        # Max output tokens
        max_output = 0
        for src in [
            top_provider.get("max_completion_tokens"),
            model_data.get("max_output_tokens"),
        ]:
            if src:
                try:
                    max_output = int(src)
                    break
                except (TypeError, ValueError):
                    pass

        # Capability booleans
        arch = model_data.get("architecture") or {}
        modality = (arch.get("modality") or "").lower()
        supported_params = model_data.get("supported_parameters") or []

        supports_vision = bool(
            model_data.get("supports_vision") or "image" in modality,
        )
        supports_function_calling = bool(
            model_data.get("supports_tool_calling")
            or model_data.get("supports_function_calling")
            or any(p in ["tools", "tool_choice"] for p in supported_params),
        )
        supports_json_mode = bool(
            model_data.get("supports_json")
            or model_data.get("supports_json_mode")
            or "response_format" in supported_params,
        )
        supports_streaming = bool(
            model_data.get("supports_streaming") or "stream" in supported_params,
        )
        is_free = bool(
            model_data.get(
                "is_free",
                prompt_price == 0 and completion_price == 0,
            ),
        )

        cost_eff = LLMModel.calculate_cost_efficiency(
            context_window,
            prompt_price,
            completion_price,
        )

        # Build metadata
        meta: dict = {}
        meta["openrouter_model_id"] = model_data.get("id")
        meta["openrouter_name"] = model_data.get("name")
        meta["openrouter_created"] = model_data.get("created")
        meta["openrouter_description"] = model_data.get("description")
        meta["openrouter_pricing"] = model_data.get("pricing")
        meta["openrouter_top_provider"] = model_data.get("top_provider")
        meta["openrouter_supported_parameters"] = model_data.get("supported_parameters")
        if arch:
            meta["architecture_modality"] = arch.get("modality")
            meta["architecture_tokenizer"] = arch.get("tokenizer")
            meta["architecture_instruct_type"] = arch.get("instruct_type")
            meta["architecture_input_modalities"] = arch.get("input_modalities")
            meta["architecture_output_modalities"] = arch.get("output_modalities")

        defaults = {
            "provider": provider,
            "model_name": model_name,
            "description": description[:5000],
            "is_free": is_free,
            "context_window": context_window,
            "max_output_tokens": max_output,
            "input_price_per_token": prompt_price,
            "output_price_per_token": completion_price,
            "supports_function_calling": supports_function_calling,
            "supports_vision": supports_vision,
            "supports_streaming": supports_streaming,
            "supports_json_mode": supports_json_mode,
            "cost_efficiency": cost_eff,
            "capabilities_json": model_data,
            "metadata": meta,
        }

        if _update_or_create_model(model_id, canonical, defaults):
            upserted += 1

    return upserted


def refresh_model_from_openrouter(model: LLMModel, api_key: str) -> bool:
    """Refresh a single model from the OpenRouter catalog."""
    models_payload = fetch_openrouter_models(api_key)
    if not models_payload:
        return False

    candidates = {
        model.model_id.strip().lower(),
        model.canonical_slug.strip().lower(),
        normalize_model_identifier(model.model_id),
        normalize_model_identifier(model.canonical_slug),
    }

    for model_data in models_payload:
        model_id = str(model_data.get("id") or "").strip()
        if not model_id:
            continue

        canonical_slug = _build_canonical_slug(
            model_id,
            str(model_data.get("canonical_slug") or ""),
        )
        if model_id.lower() in candidates or canonical_slug in candidates:
            return upsert_openrouter_models([model_data]) == 1

    return False


def sync_models_from_openrouter(api_key: str) -> dict:
    """Full sync: fetch from OpenRouter and upsert into DB."""
    models_payload = fetch_openrouter_models(api_key)
    if not models_payload:
        return {"fetched": 0, "upserted": 0}
    upserted = upsert_openrouter_models(models_payload)
    return {"fetched": len(models_payload), "upserted": upserted}
