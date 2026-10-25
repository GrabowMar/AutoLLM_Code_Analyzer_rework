"""Snapshot / fingerprint helpers for non-OpenRouter models (spec §D.5).

Every snapshot change for local models is guarded by :func:`is_local`, so
OpenRouter snapshots and fingerprints stay byte-identical.

- ``snapshot["endpoint"] = endpoint_section(model)`` (local only)
- ``_fingerprint_inputs`` / ``run_fingerprint`` gain
  ``llm_source = llm_source(snapshot)`` (local only)
"""

from __future__ import annotations

from typing import Any


def is_local(model) -> bool:
    """True when ``model`` runs on a non-system endpoint (Ollama / OpenAI-compatible)."""
    return model is not None and getattr(model, "endpoint_id", None) is not None and not model.endpoint.is_system


def effective_num_ctx(model) -> int | None:
    """The context length a local model runs with; None for OpenRouter models.

    Ollama: ``num_ctx_override`` or ``min(context_window or cap, cap)`` with
    ``cap = endpoint.num_ctx_cap``. OpenAI-compatible: ``num_ctx_override``
    or the discovered ``context_window`` (the server decides; None if unknown).
    """
    if not is_local(model):
        return None
    if model.num_ctx_override:
        return int(model.num_ctx_override)
    endpoint = model.endpoint
    if endpoint.kind == "ollama":
        cap = int(endpoint.num_ctx_cap)
        return min(int(model.context_window or cap), cap)
    return int(model.context_window) if model.context_window else None


def endpoint_section(model) -> dict[str, Any] | None:
    """The frozen ``snapshot["endpoint"]`` section for a local model; None for OpenRouter.

    Provenance plus what the proxy needs to build upstream bodies from the
    *frozen* snapshot (not the live row). ``client_timeout_s`` is the
    harness client timeout rendered for local models (§D.5); ``max_concurrency``
    is provenance only. Neither is a fingerprint input (see ``llm_source``).
    """
    if not is_local(model):
        return None
    from backend.inference.timeouts import chain

    endpoint = model.endpoint
    return {
        "id": endpoint.pk,
        "slug": endpoint.slug,
        "kind": endpoint.kind,
        "connection": endpoint.connection,
        "api_flavor": endpoint.api_flavor,
        "served_model_name": model.served_model_name,
        "digest": model.digest,
        "quantization": model.quantization,
        "parameter_size": model.parameter_size,
        "num_ctx": effective_num_ctx(model),
        "keep_alive": endpoint.keep_alive,
        "max_concurrency": endpoint.max_concurrency,
        "client_timeout_s": chain(endpoint).harness_client_s,
        # Provenance (not fingerprinted): decides whether ``think`` is sent.
        "capabilities": list(((model.metadata or {}).get("ollama") or {}).get("capabilities") or []),
    }


LLM_SOURCE_KEYS = ("kind", "api_flavor", "served_model_name", "digest", "num_ctx")


def llm_source(snapshot: dict | None) -> dict[str, Any] | None:
    """The ``llm_source`` fingerprint input (``LLM_SOURCE_KEYS`` of ``snapshot["endpoint"]``); None without one."""
    section = (snapshot or {}).get("endpoint")
    if not section:
        return None
    return {key: section.get(key) for key in LLM_SOURCE_KEYS}
