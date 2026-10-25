"""Shared helpers for LLM model upsert flows."""

import logging
from typing import Any

from django.db import IntegrityError
from django.db.models import Q

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
    """Upsert one OpenRouter catalog row; returns False when it cannot be stored.

    Only catalog rows (``endpoint`` NULL) are ever matched or overwritten:
    models discovered on local endpoints are owned by their inventory, so a
    catalog sync or import whose id or canonical slug collides with one is
    refused (logged, skipped) instead of rewriting it.
    """
    local_clash = LLMModel.objects.filter(endpoint__isnull=False).filter(
        Q(model_id=model_id) | Q(canonical_slug=canonical_slug),
    )
    if local_clash.exists():
        logger.error("Refusing to upsert catalog model %s: it collides with a local endpoint model", model_id)
        return False
    try:
        LLMModel.objects.update_or_create(
            model_id=model_id,
            endpoint=None,
            defaults={**defaults, "canonical_slug": canonical_slug},
        )
        return True
    except IntegrityError:
        try:
            # A renamed catalog id keeps its canonical slug: update that row.
            LLMModel.objects.update_or_create(
                canonical_slug=canonical_slug,
                endpoint=None,
                defaults={**defaults, "model_id": model_id},
            )
            return True
        except IntegrityError:
            logger.exception("Failed to upsert model %s", model_id)
            return False
