"""Third-party model metadata beyond the OpenRouter catalog row.

Three public, keyless sources:

* **models.dev** (``https://models.dev/api.json``) — an open model database;
  its ``openrouter`` provider is keyed by OpenRouter model id and adds the
  knowledge cutoff, release / last-updated dates, open-weights flag, family,
  reasoning options and context-tiered pricing. Stored in
  ``metadata["models_dev"]``.
* **Hugging Face Hub** (``/api/models/<id>``) for models whose payload names
  a ``hugging_face_id`` — license, parameter count, downloads, likes, base
  model, architecture, arXiv papers. Stored in ``metadata["huggingface"]``.
* **OpenRouter endpoints** (``/api/v1/models/<author>/<slug>/endpoints``) —
  the providers serving a model with their own price, quantization, limits,
  uptime, latency and throughput. Live operational data, so it is cached
  briefly instead of stored.

The catalog sync keeps the stored keys (``ENRICHMENT_KEYS``) when it rewrites
``metadata``, so enrichment survives re-syncs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests
from django.core.cache import cache
from django.utils import timezone

from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
HF_API_URL = "https://huggingface.co/api/models/{hf_id}"
OPENROUTER_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{path}/endpoints"

ENRICHMENT_KEYS = ("models_dev", "huggingface")

MODELS_DEV_CACHE_KEY = "llm_models:models_dev:v1"
MODELS_DEV_CACHE_SECONDS = 6 * 3600
ENDPOINTS_CACHE_SECONDS = 600
HF_REFRESH_AFTER_SECONDS = 7 * 24 * 3600
HF_REQUEST_PAUSE_SECONDS = 0.2
REQUEST_TIMEOUT = 20

_MODELS_DEV_FIELDS = (
    "name",
    "family",
    "knowledge",
    "release_date",
    "last_updated",
    "open_weights",
    "reasoning",
    "reasoning_options",
    "tool_call",
    "structured_output",
    "temperature",
    "attachment",
    "modalities",
    "limit",
    "cost",
    "status",
)


def _now_iso() -> str:
    return timezone.now().isoformat()


def _get_json(url: str) -> Any | None:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        # Gated / renamed Hub repos answer 401/404 routinely; one line, no traceback.
        logger.warning("Model enrichment request failed: %s (%s)", url, exc)
        return None


# ── models.dev ──────────────────────────────────────────────────────────────


def fetch_models_dev(*, use_cache: bool = True) -> dict[str, dict]:
    """models.dev's OpenRouter models keyed by OpenRouter model id (``{}`` when unreachable)."""
    if use_cache:
        cached = cache.get(MODELS_DEV_CACHE_KEY)
        if cached is not None:
            return cached
    data = _get_json(MODELS_DEV_URL)
    if not isinstance(data, dict):
        return {}
    models = ((data.get("openrouter") or {}).get("models")) or {}
    catalog = {str(key): value for key, value in models.items() if isinstance(value, dict)}
    if catalog:
        cache.set(MODELS_DEV_CACHE_KEY, catalog, MODELS_DEV_CACHE_SECONDS)
    return catalog


def models_dev_entry(catalog: dict[str, dict], model_id: str) -> dict | None:
    """The normalized models.dev record for ``model_id`` (a ``:variant`` falls back to its base model)."""
    entry = catalog.get(model_id)
    if entry is None and ":" in model_id:
        entry = catalog.get(model_id.split(":", 1)[0])
    if entry is None:
        return None
    out = {key: entry[key] for key in _MODELS_DEV_FIELDS if key in entry}
    out["id"] = entry.get("id") or model_id
    out["fetched_at"] = _now_iso()
    return out


# ── Hugging Face ────────────────────────────────────────────────────────────


def _hf_license(info: dict) -> str:
    card = info.get("cardData") or {}
    lic = card.get("license")
    if isinstance(lic, list):
        lic = lic[0] if lic else ""
    if lic:
        return str(lic)
    for tag in info.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return ""


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


def normalize_hf_info(info: dict) -> dict:
    """The fields of a Hub model record the model page shows."""
    card = info.get("cardData") or {}
    config = info.get("config") or {}
    safetensors = info.get("safetensors") or {}
    tags = [t for t in info.get("tags") or [] if isinstance(t, str)]
    gated = info.get("gated")
    return {
        "id": info.get("id") or info.get("modelId") or "",
        "author": info.get("author") or "",
        "license": _hf_license(info),
        "license_name": card.get("license_name") or "",
        "pipeline_tag": info.get("pipeline_tag") or "",
        "library_name": info.get("library_name") or card.get("library_name") or "",
        "downloads": int(info.get("downloads") or 0),
        "downloads_all_time": info.get("downloadsAllTime"),
        "likes": int(info.get("likes") or 0),
        "parameters": safetensors.get("total"),
        "parameters_by_dtype": safetensors.get("parameters") or {},
        "architectures": _as_list(config.get("architectures")),
        "model_type": config.get("model_type") or "",
        "base_models": _as_list(card.get("base_model")),
        "languages": _as_list(card.get("language")),
        "arxiv": [t.split(":", 1)[1] for t in tags if t.startswith("arxiv:")],
        "gated": bool(gated) and gated != "False",
        "created_at": info.get("createdAt"),
        "last_modified": info.get("lastModified"),
        "spaces_count": len(info.get("spaces") or []),
        "fetched_at": _now_iso(),
    }


def fetch_hf_info(hf_id: str) -> dict | None:
    hf_id = (hf_id or "").strip().strip("/")
    if not hf_id or hf_id.count("/") != 1:
        return None
    data = _get_json(HF_API_URL.format(hf_id=hf_id))
    return normalize_hf_info(data) if isinstance(data, dict) and not data.get("error") else None


def _hf_is_fresh(meta: dict) -> bool:
    fetched = (meta.get("huggingface") or {}).get("fetched_at")
    if not fetched:
        return False
    try:
        age = timezone.now() - datetime.fromisoformat(fetched)
    except (TypeError, ValueError):
        return False
    return age.total_seconds() < HF_REFRESH_AFTER_SECONDS


# ── Enrichment entry points ─────────────────────────────────────────────────


def _is_catalog_model(model: LLMModel) -> bool:
    return model.endpoint_id is None


def enrich_model(
    model: LLMModel,
    *,
    catalog: dict[str, dict] | None = None,
    include_hf: bool = True,
    force_hf: bool = False,
) -> dict[str, bool]:
    """Attach models.dev / Hugging Face metadata to one catalog model.

    Returns which sources were written (``{"models_dev": …, "huggingface": …}``).
    """
    written = {"models_dev": False, "huggingface": False}
    if not _is_catalog_model(model):
        return written
    meta = dict(model.metadata or {})

    md_catalog = catalog if catalog is not None else fetch_models_dev()
    entry = models_dev_entry(md_catalog, model.model_id) if md_catalog else None
    if entry is not None:
        meta["models_dev"] = entry
        written["models_dev"] = True

    hf_id = str((model.capabilities_json or {}).get("hugging_face_id") or "")
    if include_hf and hf_id and (force_hf or not _hf_is_fresh(meta)):
        info = fetch_hf_info(hf_id)
        if info is not None:
            meta["huggingface"] = info
            written["huggingface"] = True

    if any(written.values()):
        model.metadata = meta
        model.save(update_fields=["metadata", "updated_at"])
    return written


def enrich_catalog(*, include_hf: bool = True, force_hf: bool = False) -> dict[str, int]:
    """Enrich every OpenRouter catalog model; HF lookups are rate-paced and skipped while fresh."""
    catalog = fetch_models_dev(use_cache=False)
    counts = {"models_dev": 0, "huggingface": 0}
    for model in LLMModel.objects.filter(endpoint__isnull=True).iterator():
        written = enrich_model(model, catalog=catalog, include_hf=include_hf, force_hf=force_hf)
        for key, value in written.items():
            counts[key] += value
        if written["huggingface"]:
            time.sleep(HF_REQUEST_PAUSE_SECONDS)
    return {
        "models_dev_matched": counts["models_dev"],
        "huggingface_fetched": counts["huggingface"],
        "models_dev_size": len(catalog),
    }


def preserved_enrichment(model_ids: list[str]) -> dict[str, dict]:
    """``{model_id: {key: value}}`` of stored enrichment, for the sync to carry over."""
    out: dict[str, dict] = {}
    rows = LLMModel.objects.filter(endpoint__isnull=True, model_id__in=model_ids).values_list("model_id", "metadata")
    for model_id, meta in rows:
        kept = {k: meta[k] for k in ENRICHMENT_KEYS if isinstance(meta, dict) and k in meta}
        if kept:
            out[model_id] = kept
    return out


# ── OpenRouter provider endpoints (live) ────────────────────────────────────


def _endpoints_path(model: LLMModel) -> str:
    payload = model.capabilities_json or {}
    details = ((payload.get("links") or {}).get("details")) or ""
    prefix = "/api/v1/models/"
    if details.startswith(prefix) and details.endswith("/endpoints"):
        return details[len(prefix) : -len("/endpoints")]
    return model.model_id.split(":", 1)[0]


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _normalize_endpoint(raw: dict) -> dict:
    pricing = raw.get("pricing") or {}
    return {
        "provider_name": raw.get("provider_name") or raw.get("name") or "",
        "tag": raw.get("tag") or "",
        "quantization": raw.get("quantization") or "",
        "context_length": raw.get("context_length"),
        "max_completion_tokens": raw.get("max_completion_tokens"),
        "max_prompt_tokens": raw.get("max_prompt_tokens"),
        "input_price_per_million": (_float_or_none(pricing.get("prompt")) or 0.0) * 1_000_000,
        "output_price_per_million": (_float_or_none(pricing.get("completion")) or 0.0) * 1_000_000,
        "cache_read_per_million": (
            p * 1_000_000 if (p := _float_or_none(pricing.get("input_cache_read"))) is not None else None
        ),
        "discount": _float_or_none(pricing.get("discount")) or 0.0,
        "status": raw.get("status"),
        "uptime_last_5m": _float_or_none(raw.get("uptime_last_5m")),
        "uptime_last_30m": _float_or_none(raw.get("uptime_last_30m")),
        "uptime_last_1d": _float_or_none(raw.get("uptime_last_1d")),
        "latency_last_30m": raw.get("latency_last_30m"),
        "throughput_last_30m": raw.get("throughput_last_30m"),
        "supported_parameters": raw.get("supported_parameters") or [],
        "supports_implicit_caching": bool(raw.get("supports_implicit_caching")),
        "native_tools": sorted((raw.get("native_tools") or {}).keys()),
        "service_tier": raw.get("service_tier"),
    }


def fetch_provider_endpoints(model: LLMModel, *, use_cache: bool = True) -> dict[str, Any]:
    """Providers serving ``model`` on OpenRouter; ``available`` False when it could not be fetched."""
    path = _endpoints_path(model)
    cache_key = f"llm_models:endpoints:v1:{path}"
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    data = _get_json(OPENROUTER_ENDPOINTS_URL.format(path=path))
    raw = ((data or {}).get("data") or {}).get("endpoints") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return {"available": False, "fetched_at": _now_iso(), "endpoints": []}
    result = {
        "available": True,
        "fetched_at": _now_iso(),
        "endpoints": [_normalize_endpoint(e) for e in raw if isinstance(e, dict)],
    }
    cache.set(cache_key, result, ENDPOINTS_CACHE_SECONDS)
    return result
