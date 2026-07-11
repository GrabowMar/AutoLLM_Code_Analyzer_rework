"""Typed validation and layered merging of OpenRouter sampling parameters.

One vocabulary for every place LLM behavior can be configured:
profile ``llm_config`` → experiment ``llm_defaults`` → condition
``param_overrides`` → per-run ``llm_params`` (later layers win). The merged
result is frozen into the job snapshot's ``llm`` section, and every key here
enters ``run_fingerprint`` — but never ``prompt_hash``, which groups runs
across sampling variations on purpose.
"""

from __future__ import annotations

from typing import Any

# Every sampling/behavior key forwarded to OpenRouter. Provenance keys
# (model_slug, pricing, context_window, ...) live alongside these in the
# snapshot's llm section but are not user-settable.
SAMPLING_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "max_tokens",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
    "stop",
    "response_format",
    "provider",
    "reasoning",
)

_MAX_STOP_SEQUENCES = 8
_MAX_STOP_LENGTH = 64

# OpenRouter provider-routing preference keys
# (https://openrouter.ai/docs/features/provider-routing).
_PROVIDER_KEYS = {
    "order",
    "only",
    "ignore",
    "allow_fallbacks",
    "quantizations",
    "sort",
    "data_collection",
    "require_parameters",
}

_REASONING_KEYS = {"effort", "max_tokens", "enabled", "exclude"}
_REASONING_EFFORTS = {"low", "medium", "high"}


def _check_range(errors: list[str], data: dict, key: str, lo: float, hi: float) -> None:
    value = data.get(key)
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
        errors.append(f"{key} must be a number between {lo} and {hi}")


def validate_llm_params(data: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize a user-supplied params dict.

    Returns a copy with ``None`` values stripped (None means "not set" —
    inherit from the layer below). Raises ``ValueError`` listing every
    problem, so callers can surface one actionable message.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        # ValueError on purpose: callers catch one exception type for every
        # validation problem and surface it as a 400.
        msg = "LLM params must be an object"
        raise ValueError(msg)  # noqa: TRY004

    params = {k: v for k, v in data.items() if v is not None}
    errors: list[str] = []

    unknown = set(params) - set(SAMPLING_KEYS)
    if unknown:
        errors.append(f"Unknown LLM params: {', '.join(sorted(unknown))}")

    _check_range(errors, params, "temperature", 0, 2)
    _check_range(errors, params, "top_p", 0, 1)
    _check_range(errors, params, "min_p", 0, 1)
    _check_range(errors, params, "frequency_penalty", -2, 2)
    _check_range(errors, params, "presence_penalty", -2, 2)
    _check_range(errors, params, "repetition_penalty", 0, 2)

    for int_key, lo, hi in (("top_k", 1, 1000), ("max_tokens", 1, 2_000_000)):
        value = params.get(int_key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi):
            errors.append(f"{int_key} must be an integer between {lo} and {hi}")

    stop = params.get("stop")
    if stop is not None:
        if (
            not isinstance(stop, list)
            or len(stop) > _MAX_STOP_SEQUENCES
            or not all(isinstance(s, str) and 0 < len(s) <= _MAX_STOP_LENGTH for s in stop)
        ):
            errors.append(
                f"stop must be a list of at most {_MAX_STOP_SEQUENCES} "
                f"non-empty strings (max {_MAX_STOP_LENGTH} chars each)",
            )

    response_format = params.get("response_format")
    if response_format is not None:
        if not isinstance(response_format, dict) or response_format.get("type") not in (
            "json_object",
            "json_schema",
        ):
            errors.append('response_format must be {"type": "json_object"} or {"type": "json_schema", ...}')

    provider = params.get("provider")
    if provider is not None:
        if not isinstance(provider, dict):
            errors.append("provider must be an object of OpenRouter routing preferences")
        else:
            bad = set(provider) - _PROVIDER_KEYS
            if bad:
                errors.append(
                    f"Unknown provider routing keys: {', '.join(sorted(bad))} "
                    f"(allowed: {', '.join(sorted(_PROVIDER_KEYS))})",
                )

    reasoning = params.get("reasoning")
    if reasoning is not None:
        if not isinstance(reasoning, dict) or not set(reasoning) <= _REASONING_KEYS:
            errors.append(f"reasoning keys must be a subset of {sorted(_REASONING_KEYS)}")
        elif "effort" in reasoning and reasoning["effort"] not in _REASONING_EFFORTS:
            errors.append(f"reasoning.effort must be one of {sorted(_REASONING_EFFORTS)}")

    if errors:
        raise ValueError("; ".join(errors))
    return params


def merge_llm_params(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge parameter layers; later layers win, ``None`` means unset."""
    merged: dict[str, Any] = {}
    for layer in layers:
        if not layer:
            continue
        for key, value in layer.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
    return merged


def sampling_params(llm_section: dict[str, Any] | None) -> dict[str, Any]:
    """Extract only the OpenRouter-forwardable keys from a snapshot llm section."""
    section = llm_section or {}
    params = {k: section[k] for k in SAMPLING_KEYS if k in section}
    # In schema-v2 snapshots "provider" was model provenance (a string), not
    # routing preferences — never forward it.
    if not isinstance(params.get("provider"), dict):
        params.pop("provider", None)
    return params
