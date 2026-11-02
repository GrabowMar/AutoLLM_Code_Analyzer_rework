"""Third-party model metadata beyond the OpenRouter catalog row.

Sources:

* **models.dev** (``https://models.dev/api.json``) — an open model database;
  its ``openrouter`` provider is keyed by OpenRouter model id and adds the
  knowledge cutoff, release / last-updated dates, open-weights flag, family,
  reasoning options and context-tiered pricing. Stored in
  ``metadata["models_dev"]``. The other providers' records (e.g.
  ``opencode-go``) map a served name to a ``canonical_model_id``; they link
  local endpoint models to their catalog twin (``metadata["catalog_twin"]``).
* **Hugging Face Hub** for models whose payload names a ``hugging_face_id``:
  ``/api/models/<id>?expand[]=…`` (license, parameter count, downloads incl.
  all-time, likes, trending, children, base-model relation, inference
  providers, self-reported evals, repo sha), the repo tree (weight bytes /
  format), ``config.json`` (architecture), ``generation_config.json``
  (authors' recommended sampling), ``tokenizer_config.json`` (chat-template
  fingerprint) and ``/api/papers/<arxiv>``. Stored in
  ``metadata["huggingface"]``; ``metadata["params"]`` derives checkpoint vs
  active parameters from it (see :mod:`hf_metadata`). Requests go through
  :class:`~backend.llm_models.services.hf.HfClient` (token, pacing, 429s).
* **OpenRouter endpoints** (``/api/v1/models/<author>/<slug>/endpoints``) —
  the providers serving a model with their own price, quantization, limits,
  uptime, latency and throughput. Live operational data, so it is cached
  briefly instead of stored.

The catalog sync keeps the stored keys (``ENRICHMENT_KEYS``) when it rewrites
``metadata``, so enrichment survives re-syncs.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import requests
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from backend.llm_models.models import LLMModel
from backend.llm_models.services import hf_metadata
from backend.llm_models.services.hf import STATUS_ERROR
from backend.llm_models.services.hf import STATUS_GATED
from backend.llm_models.services.hf import STATUS_OK
from backend.llm_models.services.hf import STATUS_RATE_LIMITED
from backend.llm_models.services.hf import HfClient
from backend.llm_models.services.hf import client_for

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
HF_API_URL = "https://huggingface.co/api/models/{hf_id}"
HF_TREE_URL = "https://huggingface.co/api/models/{hf_id}/tree/{rev}"
HF_RESOLVE_URL = "https://huggingface.co/{hf_id}/resolve/{rev}/{filename}"
HF_PAPER_URL = "https://huggingface.co/api/papers/{arxiv_id}"
OPENROUTER_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{path}/endpoints"

ENRICHMENT_KEYS = ("models_dev", "huggingface", "catalog_twin", "params", "epoch", "lmarena")

MODELS_DEV_CACHE_KEY = "llm_models:models_dev:v1"
MODELS_DEV_PROVIDERS_CACHE_KEY = "llm_models:models_dev_providers:v1"
MODELS_DEV_CACHE_SECONDS = 6 * 3600
ENDPOINTS_CACHE_SECONDS = 600
HF_REFRESH_AFTER_SECONDS = 7 * 24 * 3600
HF_PAPER_CACHE_SECONDS = 7 * 24 * 3600
HF_TREE_MAX_PAGES = 3
# Bumped when the stored Hugging Face record gains fields: older records count as stale.
HF_RECORD_SCHEMA = 2
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
# Kept from a non-OpenRouter provider record for a local model's twin link.
_TWIN_PROVIDER_FIELDS = (
    "name",
    "family",
    "knowledge",
    "release_date",
    "last_updated",
    "open_weights",
    "reasoning_options",
    "modalities",
    "limit",
)
# Sub-fetches whose stored value survives a transient failure (error / 429) of a refetch.
_HF_FILE_KEYS = ("architecture", "generation_config", "tokenizer", "files", "papers")
_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')


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


def _providers_index(data: dict) -> dict[str, dict]:
    """``{provider_id: {api, name, models: {served model key: canonical_model_id | None}}}``."""
    index: dict[str, dict] = {}
    for provider_id, provider in data.items():
        if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
            continue
        index[str(provider_id)] = {
            "api": str(provider.get("api") or ""),
            "name": str(provider.get("name") or provider_id),
            "models": {
                str(key): (entry.get("canonical_model_id") or None)
                for key, entry in provider["models"].items()
                if isinstance(entry, dict)
            },
            "records": {
                str(key): {f: entry[f] for f in _TWIN_PROVIDER_FIELDS if f in entry}
                for key, entry in provider["models"].items()
                if isinstance(entry, dict)
            },
        }
    return index


def _download_models_dev() -> tuple[dict[str, dict], dict[str, dict]]:
    """One download of models.dev: (OpenRouter catalog, providers index); both cached when non-empty."""
    data = _get_json(MODELS_DEV_URL)
    if not isinstance(data, dict):
        return {}, {}
    models = ((data.get("openrouter") or {}).get("models")) or {}
    catalog = {str(key): value for key, value in models.items() if isinstance(value, dict)}
    providers = _providers_index(data)
    if catalog:
        cache.set(MODELS_DEV_CACHE_KEY, catalog, MODELS_DEV_CACHE_SECONDS)
    if providers:
        cache.set(MODELS_DEV_PROVIDERS_CACHE_KEY, providers, MODELS_DEV_CACHE_SECONDS)
    return catalog, providers


def fetch_models_dev(*, use_cache: bool = True) -> dict[str, dict]:
    """models.dev's OpenRouter models keyed by OpenRouter model id (``{}`` when unreachable)."""
    if use_cache:
        cached = cache.get(MODELS_DEV_CACHE_KEY)
        if cached is not None:
            return cached
    return _download_models_dev()[0]


def fetch_models_dev_providers(*, use_cache: bool = True) -> dict[str, dict]:
    """Every models.dev provider's served names → canonical ids (``{}`` when unreachable)."""
    if use_cache:
        cached = cache.get(MODELS_DEV_PROVIDERS_CACHE_KEY)
        if cached is not None:
            return cached
    return _download_models_dev()[1]


def models_dev_entry(catalog: dict[str, dict], model_id: str) -> dict | None:
    """The normalized models.dev record for ``model_id`` (a ``:variant`` falls back to its base model)."""
    entry = catalog.get(model_id)
    if entry is None and ":" in model_id:
        entry = catalog.get(model_id.split(":", 1)[0])
    if entry is None:
        return None
    out = {key: entry[key] for key in _MODELS_DEV_FIELDS if key in entry}
    out["id"] = entry.get("id") or model_id
    if entry.get("canonical_model_id"):
        out["canonical_model_id"] = entry["canonical_model_id"]
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
    """The fields of a Hub model record the model page shows (plus the ``expand[]`` extras)."""
    card = info.get("cardData") or {}
    config = info.get("config") or {}
    safetensors = info.get("safetensors") or {}
    tags = [t for t in info.get("tags") or [] if isinstance(t, str)]
    gated = info.get("gated")
    record = {
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
    record.update(hf_metadata.hf_record_extras(info))
    return record


def _valid_hf_id(hf_id: str) -> str | None:
    hf_id = (hf_id or "").strip().strip("/")
    return hf_id if hf_id and hf_id.count("/") == 1 and " " not in hf_id else None


def _carry(previous: dict | None, key: str) -> Any:
    return (previous or {}).get(key)


def _fetch_tree(client: HfClient, hf_id: str, rev: str) -> tuple[str, list | None, bool]:
    """``(status, entries, complete)``; ``complete`` is False when the page cap cut the listing."""
    url: str | None = HF_TREE_URL.format(hf_id=hf_id, rev=rev)
    params: dict | None = {"recursive": "true"}
    entries: list = []
    status = STATUS_OK
    for _ in range(HF_TREE_MAX_PAGES):
        if url is None:
            break
        result = client.get_json(url, params)
        if not result.ok or not isinstance(result.data, list):
            status = result.status if not result.ok else STATUS_ERROR
            break
        entries += result.data
        match = _LINK_NEXT_RE.search(result.headers.get("link", ""))
        url, params = (match.group(1), None) if match else (None, None)
    return status, (entries or None) if status == STATUS_OK else None, url is None


def _fetch_papers(client: HfClient, arxiv_ids: list[str], paper_cache: dict | None) -> tuple[str, list[dict]]:
    papers: list[dict] = []
    status = STATUS_OK
    for arxiv_id in arxiv_ids[: hf_metadata.MAX_PAPERS]:
        cache_key = f"llm_models:hf_paper:v1:{arxiv_id}"
        cached = (paper_cache or {}).get(arxiv_id) or cache.get(cache_key)
        if cached is None:
            result = client.get_json(HF_PAPER_URL.format(arxiv_id=arxiv_id))
            if result.status in (STATUS_ERROR, STATUS_RATE_LIMITED):
                status = result.status
                continue
            cached = hf_metadata.parse_paper(result.data if result.ok else None, arxiv_id)
            cache.set(cache_key, cached, HF_PAPER_CACHE_SECONDS)
        if paper_cache is not None:
            paper_cache[arxiv_id] = cached
        papers.append(cached)
    return status, papers


def _fetch_file(client: HfClient, hf_id: str, rev: str, filename: str, present: list[str] | None):
    """One repo file as JSON, or a status; skipped (``not_found``) when the tree says it is absent."""
    if present is not None and filename not in present:
        return "not_found", None, None
    result = client.get_json(HF_RESOLVE_URL.format(hf_id=hf_id, rev=rev, filename=filename))
    return result.status, (result.data if result.ok else None), result.repo_commit


def fetch_hf_info(
    hf_id: str,
    *,
    client: HfClient | None = None,
    previous: dict | None = None,
    include_files: bool = True,
    paper_cache: dict | None = None,
) -> dict | None:
    """The Hub record for ``hf_id`` (``None`` when the model itself could not be read).

    Repo files fetched at the record's ``sha``: ``config.json`` (architecture),
    ``generation_config.json``, ``tokenizer_config.json``, the tree, and up to
    five papers. 401/403 on a file is recorded as ``gated_inaccessible``; a
    transient failure keeps that part of ``previous``.
    """
    hf_id = _valid_hf_id(hf_id)
    if hf_id is None:
        return None
    client = client or client_for(None)  # deployment token, else anonymous
    expand = [("expand[]", field) for field in hf_metadata.HF_EXPAND_FIELDS]
    result = client.get_json(HF_API_URL.format(hf_id=hf_id), expand)
    if not result.ok or not isinstance(result.data, dict) or result.data.get("error"):
        logger.info("Hugging Face record unavailable for %s (%s)", hf_id, result.status)
        return None
    info = result.data
    record = normalize_hf_info(info)
    record["schema"] = HF_RECORD_SCHEMA
    record["authenticated"] = client.authenticated
    status: dict[str, str] = {"api": STATUS_OK}
    if include_files:
        rev = record.get("sha") or "main"
        record["revision"] = rev
        status["tree"], tree, complete = _fetch_tree(client, hf_id, rev)
        files = hf_metadata.parse_tree(tree) if tree else None
        if files is not None and not complete:
            # A truncated listing can miss shards and config files: no byte total, no skipping.
            files.update(weights_bytes=None, complete=False)
        present = files["present"] if files and complete else None
        record["files"] = files

        status["config"], config, _ = _fetch_file(client, hf_id, rev, "config.json", present)
        record["architecture"] = hf_metadata.parse_architecture(config)

        status["generation_config"], gen, commit = _fetch_file(client, hf_id, rev, "generation_config.json", present)
        record["generation_config"] = hf_metadata.parse_generation_config(gen, commit=commit or record.get("sha"))

        status["tokenizer_config"], tok, _ = _fetch_file(client, hf_id, rev, "tokenizer_config.json", present)
        record["tokenizer"] = hf_metadata.parse_tokenizer(tok, info.get("config"))

        status["papers"], record["papers"] = _fetch_papers(client, record.get("arxiv") or [], paper_cache)
        # A transient failure keeps what the last good fetch stored.
        transient = (STATUS_ERROR, STATUS_RATE_LIMITED)
        for key, source in (
            ("files", "tree"),
            ("architecture", "config"),
            ("generation_config", "generation_config"),
            ("tokenizer", "tokenizer_config"),
        ):
            if status[source] in transient and _carry(previous, key) is not None:
                record[key] = _carry(previous, key)
        if status["papers"] in transient and not record["papers"] and _carry(previous, "papers"):
            record["papers"] = _carry(previous, "papers")
    else:
        for key in _HF_FILE_KEYS:
            if _carry(previous, key) is not None:
                record[key] = _carry(previous, key)
    record["fetch_status"] = status
    record["gated_inaccessible"] = STATUS_GATED in status.values()
    return record


def _hf_is_fresh(meta: dict) -> bool:
    hf = meta.get("huggingface") or {}
    fetched = hf.get("fetched_at")
    if not fetched or hf.get("schema", 1) < HF_RECORD_SCHEMA:
        return False
    try:
        age = timezone.now() - datetime.fromisoformat(fetched)
    except (TypeError, ValueError):
        return False
    return age.total_seconds() < HF_REFRESH_AFTER_SECONDS


def model_params(model: LLMModel, meta: dict) -> dict | None:
    """``metadata.params`` from the stored Hub record (``None`` without one)."""
    hf = meta.get("huggingface")
    if not isinstance(hf, dict):
        return None
    md = meta.get("models_dev") if isinstance(meta.get("models_dev"), dict) else {}
    params = hf_metadata.compute_params(
        hf,
        names=(model.model_id, model.model_name),
        open_weights=md.get("open_weights") if isinstance(md.get("open_weights"), bool) else None,
    )
    if params is not None:
        params["computed_at"] = _now_iso()
    return params


def _same_params(a: dict | None, b: dict | None) -> bool:
    strip = lambda d: {k: v for k, v in (d or {}).items() if k != "computed_at"}  # noqa: E731
    return strip(a) == strip(b)


# ── Local endpoint models → catalog twin ────────────────────────────────────


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _twin_by_provider(model: LLMModel, providers: dict[str, dict]) -> tuple[str, str, str] | None:
    """``(provider id, served key, canonical id)`` from models.dev provider records."""
    served = model.served_model_name or model.model_id.split("/", 1)[-1]
    endpoint = model.endpoint
    by_slug = providers.get(endpoint.slug) if endpoint else None
    if by_slug and by_slug["models"].get(served):
        return endpoint.slug, served, by_slug["models"][served]
    host = _host(getattr(endpoint, "base_url", "") or "")
    if not host:
        return None
    hits = {
        (pid, served, prov["models"][served])
        for pid, prov in providers.items()
        if prov["models"].get(served) and _host(prov.get("api", "")) == host
    }
    canonicals = {c for _, _, c in hits}
    if len(canonicals) == 1:
        return sorted(hits)[0]
    return None


def _catalog_model_for(canonical: str, providers: dict[str, dict]) -> LLMModel | None:
    catalog = LLMModel.objects.filter(endpoint__isnull=True)
    match = catalog.filter(model_id=canonical).first()
    if match is not None:
        return match
    openrouter = (providers.get("openrouter") or {}).get("models") or {}
    ids = sorted(key for key, value in openrouter.items() if value == canonical and ":" not in key)
    return catalog.filter(model_id__in=ids).order_by("model_id").first() if ids else None


_PARAM_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMBT])\s*$", re.IGNORECASE)
_PARAM_SIZE_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
# A served-name twin whose size differs more than this from the endpoint's reported size is another model.
_TWIN_SIZE_RATIO_LIMIT = 1.5


def _param_size_value(text: str) -> float | None:
    """``"8.2B"`` (Ollama's ``parameter_size``) → 8.2e9; ``None`` when absent or unparseable."""
    match = _PARAM_SIZE_RE.match(text or "")
    return float(match.group(1)) * _PARAM_SIZE_UNIT[match.group(2).upper()] if match else None


def _twin_size_conflicts(model: LLMModel, twin: LLMModel) -> bool:
    """True when the endpoint reports a size far from the twin's checkpoint (Ollama ``deepseek-r1`` is an 8B
    distill, OpenRouter ``deepseek/deepseek-r1`` the 671B model): same name, different weights."""
    local = _param_size_value(model.parameter_size)
    hf = (twin.metadata or {}).get("huggingface") if isinstance(twin.metadata, dict) else None
    remote = (hf or {}).get("parameters") if isinstance(hf, dict) else None
    if not local or not isinstance(remote, (int, float)) or remote <= 0:
        return False
    return max(local, remote) / min(local, remote) > _TWIN_SIZE_RATIO_LIMIT


def _twin_by_served_name(model: LLMModel) -> LLMModel | None:
    served = (model.served_model_name or "").strip().lower()
    if not served or "/" in served:
        return None
    rows = [
        m
        for m in LLMModel.objects.filter(endpoint__isnull=True, model_id__iendswith=f"/{served}")
        if ":" not in m.model_id
    ]
    if len(rows) != 1 or _twin_size_conflicts(model, rows[0]):
        return None
    return rows[0]


def resolve_catalog_twin(model: LLMModel, providers: dict[str, dict]) -> dict | None:
    """``metadata.catalog_twin`` for a local endpoint model, or ``None`` when nothing matches.

    A twin is an assumption (same name ⇒ same weights), so the record says so;
    rows are never merged and statistics never pooled.
    """
    if model.endpoint_id is None:
        return None
    note = (
        "Assumed same weights as the catalog model (same name on the serving provider); "
        "quantization and serving stack may differ. Rows are not merged and statistics are not pooled."
    )
    found = _twin_by_provider(model, providers) if providers else None
    if found is not None:
        provider_id, served, canonical = found
        twin = _catalog_model_for(canonical, providers)
        if twin is not None:
            return {
                "model_id": twin.model_id,
                "source": f"models.dev:{provider_id}",
                "canonical_model_id": canonical,
                "served_name": served,
                "note": note,
                "provider_record": (providers[provider_id].get("records") or {}).get(served) or None,
                "linked_at": _now_iso(),
            }
    twin = _twin_by_served_name(model)
    if twin is not None:
        return {
            "model_id": twin.model_id,
            "source": "served_name",
            "canonical_model_id": None,
            "served_name": model.served_model_name,
            "note": note + " Matched on the served name only.",
            "provider_record": None,
            "linked_at": _now_iso(),
        }
    return None


def link_catalog_twin(model: LLMModel, providers: dict[str, dict]) -> bool:
    """Store (or refresh) ``metadata.catalog_twin``; a ``manual`` link is never overwritten."""
    meta = dict(model.metadata or {})
    current = meta.get("catalog_twin") if isinstance(meta.get("catalog_twin"), dict) else None
    if current and current.get("source") == "manual":
        return False
    twin = resolve_catalog_twin(model, providers)
    if twin is None:
        return False
    if current and all(current.get(k) == twin.get(k) for k in ("model_id", "source", "provider_record")):
        return False
    with transaction.atomic():
        # Merge into the row as it is now: an inventory refresh may have written other keys meanwhile.
        row = LLMModel.objects.select_for_update().filter(pk=model.pk).only("pk", "metadata").first()
        if row is None:
            return False
        meta = dict(row.metadata or {})
        fresh = meta.get("catalog_twin") if isinstance(meta.get("catalog_twin"), dict) else None
        if fresh and fresh.get("source") == "manual":
            return False
        meta["catalog_twin"] = twin
        model.metadata = meta
        model.save(update_fields=["metadata", "updated_at"])
    return True


def link_local_models(providers: dict[str, dict] | None = None, *, model_ids: list[int] | None = None) -> int:
    """Link every local endpoint model to its catalog twin; returns how many links were written."""
    providers = providers if providers is not None else fetch_models_dev_providers()
    qs = LLMModel.objects.filter(endpoint__isnull=False, endpoint__is_system=False).select_related("endpoint")
    if model_ids:
        qs = qs.filter(pk__in=model_ids)
    return sum(link_catalog_twin(model, providers) for model in qs)


# ── Enrichment entry points ─────────────────────────────────────────────────


def _is_catalog_model(model: LLMModel) -> bool:
    return model.endpoint_id is None


def enrich_model(
    model: LLMModel,
    *,
    catalog: dict[str, dict] | None = None,
    include_hf: bool = True,
    force_hf: bool = False,
    hf_client: HfClient | None = None,
    providers: dict[str, dict] | None = None,
    paper_cache: dict | None = None,
    hf_cache: dict | None = None,
) -> dict[str, bool]:
    """Attach models.dev / Hugging Face metadata to one model.

    Catalog models get ``models_dev``, ``huggingface`` and the derived
    ``params``; local endpoint models get a ``catalog_twin`` link (their twin
    carries the metadata). Returns which keys were written. ``hf_cache``
    (``{hf_id: record}``, shared across one batch run) avoids refetching a
    repo several catalog rows point at.
    """
    written = {"models_dev": False, "huggingface": False, "params": False, "catalog_twin": False}
    if not _is_catalog_model(model):
        if model.endpoint is not None and not model.endpoint.is_system:
            prov = providers if providers is not None else fetch_models_dev_providers()
            written["catalog_twin"] = link_catalog_twin(model, prov)
        return written
    meta = dict(model.metadata or {})
    updates: dict[str, Any] = {}

    md_catalog = catalog if catalog is not None else fetch_models_dev()
    entry = models_dev_entry(md_catalog, model.model_id) if md_catalog else None
    if entry is not None:
        updates["models_dev"] = entry

    hf_id = str((model.capabilities_json or {}).get("hugging_face_id") or "")
    if include_hf and hf_id and (force_hf or not _hf_is_fresh(meta)):
        if hf_cache is not None and hf_id in hf_cache:
            # Same repo already fetched in this run (``x`` and ``x:free`` share one): no second fetch.
            info = copy.deepcopy(hf_cache[hf_id])
        else:
            previous = meta.get("huggingface") if isinstance(meta.get("huggingface"), dict) else None
            info = fetch_hf_info(hf_id, client=hf_client, previous=previous, paper_cache=paper_cache)
            if hf_cache is not None:
                hf_cache[hf_id] = copy.deepcopy(info)
        if info is not None:
            updates["huggingface"] = info

    # The Hub fetches above take seconds and a batch run holds ``model`` for minutes:
    # merge into the row as it is now (under a lock) so a concurrent writer's keys
    # (external covariates, a catalog sync) are not overwritten with a stale copy.
    with transaction.atomic():
        row = LLMModel.objects.select_for_update().filter(pk=model.pk).only("pk", "metadata").first()
        if row is None:
            return written
        meta = dict(row.metadata or {})
        meta.update(updates)
        for key in updates:
            written[key] = True
        params = model_params(model, meta)
        if params is not None and not _same_params(params, meta.get("params")):
            meta["params"] = params
            written["params"] = True
        if any(written.values()):
            model.metadata = meta
            model.save(update_fields=["metadata", "updated_at"])
    return written


def enrich_catalog(
    *,
    include_hf: bool = True,
    force_hf: bool = False,
    hf_client: HfClient | None = None,
    model_ids: list[int] | None = None,
    link_local: bool = True,
) -> dict[str, Any]:
    """Enrich catalog models (HF lookups paced by the client, skipped while fresh) and link local models."""
    catalog = fetch_models_dev(use_cache=False)
    providers = fetch_models_dev_providers()
    client = hf_client or client_for(None)
    paper_cache: dict = {}
    hf_cache: dict = {}
    counts = {"models_dev": 0, "huggingface": 0, "params": 0, "catalog_twin": 0}
    gated = 0
    qs = LLMModel.objects.filter(endpoint__isnull=True)
    if model_ids:
        qs = qs.filter(pk__in=model_ids)
    started = time.monotonic()
    for model in qs.iterator():
        written = enrich_model(
            model,
            catalog=catalog,
            include_hf=include_hf,
            force_hf=force_hf,
            hf_client=client,
            paper_cache=paper_cache,
            hf_cache=hf_cache,
        )
        for key, value in written.items():
            counts[key] += value
        if written["huggingface"] and (model.metadata.get("huggingface") or {}).get("gated_inaccessible"):
            gated += 1
    if link_local:
        counts["catalog_twin"] = link_local_models(providers, model_ids=model_ids)
    return {
        "models_dev_matched": counts["models_dev"],
        "huggingface_fetched": counts["huggingface"],
        "params_written": counts["params"],
        "catalog_twins_linked": counts["catalog_twin"],
        "hf_gated_inaccessible": gated,
        "hf_requests": client.requests_made,
        "hf_rate_limited": client.rate_limited,
        "hf_authenticated": client.authenticated,
        "seconds": round(time.monotonic() - started, 1),
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
