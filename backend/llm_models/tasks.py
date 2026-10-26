"""Celery tasks for the model catalog."""

from __future__ import annotations

from celery import shared_task


@shared_task(acks_late=True)
def enrich_catalog_task(include_hf: bool = True) -> dict:
    """Attach models.dev / Hugging Face metadata to every catalog model (see ``services.enrichment``)."""
    from backend.llm_models.services.enrichment import enrich_catalog

    return enrich_catalog(include_hf=include_hf)
