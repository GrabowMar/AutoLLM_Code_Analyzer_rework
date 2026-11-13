"""Row-locked writes of one ``LLMModel.metadata`` key (other keys untouched)."""

from __future__ import annotations

from typing import Any

from django.db import transaction


def set_metadata(model_pk: int, key: str, value: Any, *, subkey: str | None = None) -> None:
    """Set ``metadata[key]`` (or ``metadata[key][subkey]``); ``None`` removes it."""
    from backend.llm_models.models import LLMModel

    with transaction.atomic():
        model = LLMModel.objects.select_for_update().get(pk=model_pk)
        meta = dict(model.metadata or {})
        if subkey is None:
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        else:
            block = dict(meta.get(key) or {}) if isinstance(meta.get(key), dict) else {}
            if value is None:
                block.pop(subkey, None)
            else:
                block[subkey] = value
            if block:
                meta[key] = block
            else:
                meta.pop(key, None)
        model.metadata = meta
        model.save(update_fields=["metadata", "updated_at"])
