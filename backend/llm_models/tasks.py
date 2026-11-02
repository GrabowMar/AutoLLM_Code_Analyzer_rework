"""Celery tasks for the model catalog."""

from __future__ import annotations

from celery import shared_task


@shared_task(acks_late=True)
def enrich_catalog_task(include_hf: bool = True, hf_user_id: int | None = None) -> dict:
    """Attach models.dev / Hugging Face metadata to every catalog model (see ``services.enrichment``).

    Hugging Face requests use ``hf_user_id``'s stored token when given (the
    staff user who triggered a catalog sync), else ``HUGGINGFACE_API_TOKEN``,
    else go anonymous. Only the user id travels through the broker.
    """
    from django.contrib.auth import get_user_model

    from backend.llm_models.services.enrichment import enrich_catalog
    from backend.llm_models.services.hf import client_for

    user = get_user_model().objects.filter(pk=hf_user_id).first() if hf_user_id else None
    return enrich_catalog(include_hf=include_hf, hf_client=client_for(user))
