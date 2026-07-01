"""Shared helpers for LLM model upsert flows."""

import logging
from typing import Any

from django.db import IntegrityError

from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)


def normalize_model_identifier(value: str) -> str:
    """Normalize a model identifier into the canonical slug format."""
    return value.strip().lower().replace("/", "_").replace(":", "_").replace(" ", "_")


def _build_canonical_slug(model_id: str, raw_slug: str = "") -> str:
    candidate = raw_slug.strip() or model_id
    return normalize_model_identifier(candidate)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _update_or_create_model(
    model_id: str,
    canonical_slug: str,
    defaults: dict[str, Any],
) -> bool:
    try:
        LLMModel.objects.update_or_create(
            model_id=model_id,
            defaults={**defaults, "canonical_slug": canonical_slug},
        )
        return True
    except IntegrityError:
        try:
            LLMModel.objects.update_or_create(
                canonical_slug=canonical_slug,
                defaults={**defaults, "model_id": model_id},
            )
            return True
        except IntegrityError:
            logger.exception("Failed to upsert model %s", model_id)
            return False
