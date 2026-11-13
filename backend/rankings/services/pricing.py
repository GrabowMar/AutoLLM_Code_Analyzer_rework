"""Price input of MSS v2: first-party list prices and the reference run cost (spec §2.2).

Resolution order for a canonical model's list price (USD per 1M tokens):

1. the vendor's own provider record in models.dev (``mss_frame.yaml: vendors``);
2. the OpenCode Go record in models.dev for a Go-served model (Go list prices
   equal the first-party price wherever both exist; used only when step 1 has
   no record, e.g. DeepSeek's dated release);
3. the vendor's own OpenRouter endpoint, discount undone;
4. the median over OpenRouter endpoints quantised at fp8 or better (any
   quantisation when none is), discount undone.

``/api/v1/models[].pricing`` is never used: it is the cheapest endpoint, often
quantised or discounted (glm-5.3 0.07/1.4 there vs 1.4/4.4 list). It is
stored as ``openrouter_cheapest`` for reference only.

``run_cost`` prices one agentic run: uncached prompt tokens at
``max(input, cache_write)`` (a listed cache write below input is a storage fee,
not a write price), cached ones at ``cache_read``.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable
from typing import Any

import requests
from django.core.cache import cache

from backend.rankings.services.constants import PLANNING_BASKET
from backend.rankings.services.constants import REFERENCE_BASKET
from backend.rankings.services.constants import RETRY_FACTOR
from backend.rankings.services.constants import RUNS_PER_MODEL

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_RAW_CACHE_KEY = "rankings:models_dev_raw:v1"
MODELS_DEV_CACHE_SECONDS = 6 * 3600
REQUEST_TIMEOUT = (10, 60)
UNIT = "USD per 1M tokens"
GOOD_QUANTIZATIONS = frozenset({"fp8", "bf16", "fp16", "fp32"})
_TIER_KEYS = ("tiers", "context_over_200k")


def download_models_dev_raw(*, use_cache: bool = True, session: Any = None) -> dict[str, Any]:
    """The raw models.dev ``api.json`` (every provider, with ``cost``); ``{}`` when unreachable."""
    if use_cache:
        cached = cache.get(MODELS_DEV_RAW_CACHE_KEY)
        if cached is not None:
            return cached
    http = session or requests
    try:
        resp = http.get(MODELS_DEV_URL, timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("models.dev download failed: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    cache.set(MODELS_DEV_RAW_CACHE_KEY, data, MODELS_DEV_CACHE_SECONDS)
    return data


def _norm(text: str) -> str:
    return str(text or "").lower().replace(".", "-").replace("_", "-")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def price_from_cost(cost: dict[str, Any]) -> dict[str, Any] | None:
    """``{input, output, cache_read, cache_write, tiers}`` from a models.dev ``cost`` block."""
    if not isinstance(cost, dict):
        return None
    inp, out = _num(cost.get("input")), _num(cost.get("output"))
    if inp is None or out is None:
        return None
    cache_write = _num(cost.get("cache_write"))
    tiers = {k: cost[k] for k in _TIER_KEYS if k in cost}
    return {
        "input": inp,
        "output": out,
        "cache_read": _num(cost.get("cache_read")),
        "cache_write": cache_write if cache_write else None,
        "tiers": tiers or None,
    }


def _candidate_keys(entry: dict[str, Any]) -> set[str]:
    keys = {_norm(entry["canonical_id"].split("/", 1)[-1])}
    for member in entry.get("members") or []:
        keys.add(_norm(member["model_id"].split("/", 1)[-1].split(":", 1)[0]))
        if member.get("served_name"):
            keys.add(_norm(member["served_name"]))
        if member.get("permaslug"):
            keys.add(_norm(str(member["permaslug"]).split("/", 1)[-1]))
    return {k for k in keys if k}


def first_party_record(
    entry: dict[str, Any],
    raw: dict[str, Any],
    vendors: dict[str, dict[str, Any]],
) -> tuple[str, str, dict[str, Any]] | None:
    """``(provider id, model key, record)`` of the vendor's own models.dev record for ``entry``.

    Matched on the record key or on the slug of its ``canonical_model_id``
    (``.`` and ``-`` compared alike); the pinned (newest) dated version wins.
    """
    provider_id = (vendors.get(entry["vendor"]) or {}).get("models_dev")
    provider = raw.get(provider_id) if provider_id else None
    models = provider.get("models") if isinstance(provider, dict) else None
    if not isinstance(models, dict):
        return None
    wanted = _candidate_keys(entry)
    pinned = _norm(str((entry.get("pinned_version") or {}).get("permaslug") or "").split("/", 1)[-1])
    hits = []
    for key, record in models.items():
        if not isinstance(record, dict) or price_from_cost(record.get("cost")) is None:
            continue
        canonical = _norm(str(record.get("canonical_model_id") or "").split("/", 1)[-1])
        if _norm(key) in wanted or (canonical and canonical in wanted):
            exact = _norm(key) == _norm(entry["canonical_id"].split("/", 1)[-1])
            hits.append((canonical == pinned and bool(pinned), exact, str(record.get("release_date") or ""), key))
    if not hits:
        return None
    hits.sort(reverse=True)
    key = hits[0][3]
    return str(provider_id), key, models[key]


def go_record(entry: dict[str, Any], raw: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The models.dev ``opencode-go`` record of the model's Go route (by served name)."""
    route = (entry.get("routes") or {}).get("go")
    served = (route or {}).get("served_name")
    models = ((raw.get("opencode-go") or {}).get("models")) if isinstance(raw.get("opencode-go"), dict) else None
    if not served or not isinstance(models, dict) or not isinstance(models.get(served), dict):
        return None
    return served, models[served]


def undo_discount(value: float | None, discount: float | None) -> float | None:
    if value is None:
        return None
    d = discount or 0.0
    return value / (1.0 - d) if 0.0 < d < 1.0 else value


def _endpoint_price(endpoint: dict[str, Any]) -> dict[str, Any]:
    discount = endpoint.get("discount") or 0.0
    return {
        "input": undo_discount(endpoint.get("input_price_per_million"), discount),
        "output": undo_discount(endpoint.get("output_price_per_million"), discount),
        "cache_read": undo_discount(endpoint.get("cache_read_per_million"), discount),
        "cache_write": undo_discount(endpoint.get("cache_write_per_million"), discount) or None,
        "tiers": None,
    }


def endpoint_price(
    endpoints: list[dict[str, Any]],
    own_provider: str | None,
) -> tuple[str, dict[str, Any], bool] | None:
    """``(source, price, discount_undone)`` from normalized OpenRouter endpoints (steps 3-4)."""
    paid = [
        e
        for e in endpoints
        if (e.get("input_price_per_million") or 0) > 0 or (e.get("output_price_per_million") or 0) > 0
    ]
    if not paid:
        return None
    if own_provider:
        own = [e for e in paid if str(e.get("provider_name") or "").lower() == own_provider.lower()]
        if own:
            own.sort(key=lambda e: (e.get("input_price_per_million") or 0, e.get("output_price_per_million") or 0))
            chosen = own[0]
            return (
                f"openrouter-endpoint:{chosen['provider_name']}",
                _endpoint_price(chosen),
                bool(chosen.get("discount")),
            )
    good = [e for e in paid if str(e.get("quantization") or "").lower() in GOOD_QUANTIZATIONS]
    pool, source = (good, "openrouter-endpoints-median(fp8+)") if good else (paid, "openrouter-endpoints-median(any)")
    prices = [_endpoint_price(e) for e in pool]

    def med(field: str) -> float | None:
        values = [p[field] for p in prices if p[field] is not None]
        return round(statistics.median(values), 6) if values else None

    price = {
        "input": med("input"),
        "output": med("output"),
        "cache_read": med("cache_read"),
        "cache_write": None,
        "tiers": None,
    }
    return f"{source}:n={len(pool)}", price, any(e.get("discount") for e in pool)


def cheapest_openrouter(row_metadata: dict[str, Any]) -> dict[str, float] | None:
    pricing = (row_metadata or {}).get("openrouter_pricing")
    if not isinstance(pricing, dict):
        return None
    inp, out = _num(pricing.get("prompt")), _num(pricing.get("completion"))
    if inp is None or out is None:
        return None
    return {"input": round(inp * 1_000_000, 6), "output": round(out * 1_000_000, 6)}


def resolve_list_price(
    entry: dict[str, Any],
    *,
    raw: dict[str, Any],
    vendors: dict[str, dict[str, Any]],
    endpoints: Callable[[], list[dict[str, Any]]] | None,
    fetched_at: str,
    go_limit: float | None = None,
    go_limit_source: dict[str, Any] | None = None,
    cheapest: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """The ``metadata["list_price"]`` record of a canonical model (``None`` when no source prices it)."""
    price = source = first_party = None
    discount_undone = False
    found = first_party_record(entry, raw, vendors)
    if found:
        provider_id, key, record = found
        price, source = price_from_cost(record["cost"]), f"models.dev:{provider_id}/{key}"
        first_party = {
            "provider": provider_id,
            "key": key,
            "canonical_model_id": record.get("canonical_model_id"),
            "release_date": record.get("release_date"),
            "open_weights": record.get("open_weights"),
            "status": record.get("status"),
            # Identity facts for frame models without a catalog row yet (E2, E3).
            "tool_call": record.get("tool_call"),
            "context": (record.get("limit") or {}).get("context") if isinstance(record.get("limit"), dict) else None,
        }
    go = go_record(entry, raw)
    if price is None and go is not None and price_from_cost(go[1].get("cost")):
        price, source = price_from_cost(go[1]["cost"]), f"models.dev:opencode-go/{go[0]}"
    if price is None and endpoints is not None:
        own = (vendors.get(entry["vendor"]) or {}).get("openrouter_provider")
        resolved = endpoint_price(endpoints(), own)
        if resolved:
            source, price, discount_undone = resolved
    if price is None:
        return None
    record = {
        "input": price["input"],
        "output": price["output"],
        "cache_read": price["cache_read"],
        "cache_write": price["cache_write"],
        "unit": UNIT,
        "source": source,
        "fetched_at": fetched_at,
        "discount_undone": discount_undone,
        "tiers": price.get("tiers"),
        "first_party_record": first_party,
        "go": None,
        "openrouter_cheapest": cheapest,
    }
    if go is not None:
        go_price = price_from_cost(go[1].get("cost")) or {}
        record["go"] = {
            "served_name": go[0],
            "input": go_price.get("input"),
            "output": go_price.get("output"),
            "cache_read": go_price.get("cache_read"),
            "cache_write": go_price.get("cache_write"),
            "monthly_limit_usd": go_limit,
            **(go_limit_source or {}),
        }
    return record


def run_cost(price: dict[str, Any] | None, basket: dict[str, float] = REFERENCE_BASKET) -> float | None:
    """USD for one run of ``basket`` (M prompt tokens, M output tokens, cache-hit share) at ``price``."""
    if not price or price.get("input") is None or price.get("output") is None:
        return None
    inp, out = float(price["input"]), float(price["output"])
    cw = price.get("cache_write")
    cr = price.get("cache_read")
    miss = max(inp, float(cw)) if cw else inp
    hit = float(cr) if cr is not None else inp
    h = float(basket["cache_hit"])
    return basket["prompt_mtok"] * ((1 - h) * miss + h * hit) + basket["output_mtok"] * out


def planning_cost(
    price: dict[str, Any] | None,
    *,
    basket: dict[str, float] = PLANNING_BASKET,
    runs: int = RUNS_PER_MODEL,
    retry: float = RETRY_FACTOR,
) -> float | None:
    """π: USD for ``runs`` runs of the verbose planning profile, with the retry factor."""
    per_run = run_cost(price, basket)
    return None if per_run is None else per_run * runs * retry
