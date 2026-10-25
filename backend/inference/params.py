"""Static parameter tables for non-OpenRouter endpoints (spec §D.2, §B.1). Pure data.

``STATIC_PARAMS[(kind, api_flavor)]`` is the set of sampling/LLM params an
endpoint accepts; it replaces OpenRouter's per-model ``supported_parameters``
lookup for local models (``agent/snapshot.py`` ``_supported_params``).

``SOURCE_OFFSET[source]`` is added to ``LLMRequest.priority`` at claim time.
"""

from __future__ import annotations

_OLLAMA_OPENAI = frozenset(
    {"max_tokens", "temperature", "top_p", "seed", "presence_penalty", "frequency_penalty", "tools"},
)

STATIC_PARAMS: dict[tuple[str, str], frozenset[str]] = {
    ("ollama", "native"): frozenset(
        {
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "seed",
            "repetition_penalty",
            "presence_penalty",
            "frequency_penalty",
            "tools",
        },
    ),
    ("ollama", "openai"): _OLLAMA_OPENAI,
    # vLLM and friends accept these as extras.
    ("openai_compatible", "openai"): _OLLAMA_OPENAI | {"top_k", "min_p", "repetition_penalty"},
}

SOURCE_OFFSET: dict[str, int] = {
    "agent_proxy": 0,
    "scaffolding": 0,
    "warmup": 0,
    "probe": -20,
    "analysis": -40,
    "inventory": -40,
}
