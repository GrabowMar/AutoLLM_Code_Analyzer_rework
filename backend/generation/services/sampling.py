"""Layered LLM params: merge named layers and record which layer each effective key came from.

v3 sampling comes from the frozen ``SamplingPreset`` in the run manifest (``ManifestSampling``); callers
that combine a preset with per-study or per-run overrides use :func:`layered_llm_params` so the resolved
params can say where each value came from (later layers win, ``None`` unsets a key).
"""

from __future__ import annotations

from typing import Any

from backend.generation.services.llm_params import merge_llm_params


def layered_llm_params(layers: list[tuple[str, dict[str, Any] | None]]) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge named layers (later wins, None unsets) and name the layer each effective key came from."""
    merged = merge_llm_params(*(layer for _name, layer in layers))
    sources: dict[str, str] = {}
    for name, layer in layers:
        for key, value in (layer or {}).items():
            if value is None:
                sources.pop(key, None)
            else:
                sources[key] = name
    return merged, {key: sources[key] for key in merged if key in sources}
