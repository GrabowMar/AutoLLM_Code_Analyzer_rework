"""LLM params of the template modes (scaffolding, single prompt), layer by layer.

Unlike agent runs (which send only explicitly configured params), the
template modes always send a temperature and an output cap: their lowest
layer is the mode default below. Layers, later wins:

* scaffolding: mode default -> profile ``llm_config`` -> sampling preset
  ``llm_params`` -> inline ``llm_params``
* single prompt: mode default -> sampling preset -> inline

A sampling preset's ``llm_params`` are copied into the job's own
``llm_params`` at creation (merged under the inline overrides); its
``sampling_policy`` only concerns agent harnesses and is ignored here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from backend.generation.services.llm_params import merge_llm_params

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import RunPreset

MODE_DEFAULT_TEMPERATURE = 0.3
MODE_DEFAULT_MAX_TOKENS = 32000


class SamplingPresetError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def mode_default_params() -> dict[str, Any]:
    return {"temperature": MODE_DEFAULT_TEMPERATURE, "max_tokens": MODE_DEFAULT_MAX_TOKENS}


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


def sampling_preset_params(user: AbstractUser, preset_id: int | None) -> tuple[RunPreset | None, dict[str, Any]]:
    """The visible sampling preset *preset_id* and its ``llm_params`` (``(None, {})`` without one).

    Template modes have no implicit sampling preset: the system
    ``provider-default`` would misdescribe runs that sample at the mode
    default temperature.
    """
    if preset_id is None:
        return None, {}
    from backend.generation.models import RunPreset
    from backend.generation.services.presets import preset_config_for_use
    from backend.generation.services.presets import visible_q

    preset = RunPreset.objects.filter(visible_q(user), id=preset_id).first()
    if preset is None:
        raise SamplingPresetError(404, f"Unknown preset id {preset_id}")
    try:
        config = preset_config_for_use(preset, "sampling")
    except ValueError as exc:
        raise SamplingPresetError(400, str(exc)) from exc
    return preset, dict(config.get("llm_params") or {})
