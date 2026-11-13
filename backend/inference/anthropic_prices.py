"""List prices for Claude models served by an ``api_flavor="anthropic"`` endpoint (the user's own key).

Inventory rows of local endpoints are created priced 0/0 and ``is_free``. For Anthropic's native route
that would make every Claude call cost $0 against the run budget, the cost caps and the spend ledger, so
:func:`price_endpoint` (run by every inventory refresh of such an endpoint) gives the rows Anthropic's
list prices: ``input_price_per_token`` / ``output_price_per_token`` and the OpenRouter-shaped
``capabilities_json.pricing`` (``prompt``, ``completion``, ``input_cache_read``, ``input_cache_write``)
that ``budget.model_facts`` reads, so the plan freezes cache-read and cache-write prices into the manifest
and the gateway / proxy budgets reserve and settle with them.

Sources, in order: :data:`LIST_PRICES` (Anthropic's published prices; a 5-minute cache write costs 1.25x
the input price), then the row's catalog twin (``metadata.catalog_twin.model_id``, its OpenRouter
``capabilities_json.pricing``). A row with neither is left alone.

Not modelled: Haiku 5.5's long-prompt tier (prompts over 100K tokens cost $0.50 / $2.50 per MTok, 5x).
``budget_rules.Pricing`` has one tier, so such calls are under-counted; every call's token split is in
``calls.jsonl`` and can be re-priced offline.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.utils import timezone

from backend.inference.anthropic_native import model_key
from backend.inference.anthropic_native import traits

# USD per MTok: (input, output, cache read). Anthropic list prices, checked 2026-11-09.
LIST_PRICES: dict[str, tuple[str, str, str]] = {
    "claude-fable-5-1": ("10", "50", "0.25"),
    "claude-mythos-5-1": ("10", "50", "0.25"),
    "claude-fable-5": ("10", "50", "1.00"),
    "claude-opus-5-5": ("4", "20", "0.20"),
    "claude-opus-5": ("5", "25", "0.50"),
    "claude-opus-4-8": ("5", "25", "0.50"),
    "claude-opus-4-7": ("5", "25", "0.50"),
    "claude-opus-4-6": ("5", "25", "0.50"),
    "claude-opus-4-5": ("5", "25", "0.50"),
    # The catalog twins (OpenRouter, models.dev) list a $0.10 cache read; the official price is $0.20.
    "claude-sonnet-5-5": ("2", "10", "0.20"),
    "claude-sonnet-5": ("2", "10", "0.20"),
    "claude-sonnet-4-6": ("3", "15", "0.30"),
    "claude-sonnet-4-5": ("3", "15", "0.30"),
    "claude-haiku-5-5": ("0.10", "0.50", "0.01"),  # prompts <= 100K tokens (see the module doc)
    "claude-haiku-4-5": ("1", "5", "0.10"),
}
CACHE_WRITE_FACTOR = Decimal("1.25")  # 5-minute TTL writes (the only TTL the native route sends)
_MTOK = Decimal(1_000_000)
SOURCE_KIND = "anthropic_list_price"
NOTE = (
    "Anthropic list prices (USD); cache write = 1.25x input (5-minute TTL). "
    "Haiku 5.5 prompts over 100K tokens (billed 5x) are not modelled."
)


def _per_token(per_mtok: Decimal) -> str:
    """A per-token decimal string in OpenRouter's shape (``"0.000004"``)."""
    return format((per_mtok / _MTOK).normalize(), "f")


def list_pricing(served_name: str) -> dict[str, str] | None:
    """The OpenRouter-shaped per-token pricing of a served Claude model from :data:`LIST_PRICES`; None if unknown."""
    key = model_key(served_name)
    if key is None or key not in LIST_PRICES:
        return None
    inp, out, read = (Decimal(v) for v in LIST_PRICES[key])
    return {
        "prompt": _per_token(inp),
        "completion": _per_token(out),
        "input_cache_read": _per_token(read),
        "input_cache_write": _per_token(inp * CACHE_WRITE_FACTOR),
    }


def _twin_pricing(row: Any) -> tuple[dict[str, str] | None, str]:
    """``(pricing, twin model_id)`` from the row's catalog twin, when it has positive prices."""
    from backend.llm_models.models import LLMModel

    twin = (row.metadata or {}).get("catalog_twin") if isinstance(row.metadata, dict) else None
    twin_id = str((twin or {}).get("model_id") or "") if isinstance(twin, dict) else ""
    if not twin_id:
        return None, ""
    other = LLMModel.objects.filter(model_id=twin_id).only("capabilities_json").first()
    raw = (other.capabilities_json or {}).get("pricing") if other is not None else None
    if not isinstance(raw, dict):
        return None, twin_id
    try:
        prompt, completion = Decimal(str(raw["prompt"])), Decimal(str(raw["completion"]))
    except (KeyError, ArithmeticError, ValueError):
        return None, twin_id
    if prompt <= 0 or completion <= 0:
        return None, twin_id
    pricing = {"prompt": str(raw["prompt"]), "completion": str(raw["completion"])}
    for key in ("input_cache_read", "input_cache_write"):
        if raw.get(key) is not None:
            pricing[key] = str(raw[key])
    return pricing, twin_id


def apply_prices(row: Any, *, save: bool = True) -> bool:
    """Give an Anthropic-endpoint model row its list prices; True when the row changed (idempotent).

    Also sets ``metadata.supports_reasoning`` for models that take an effort, so a policy's
    ``reasoning.mode=effort`` reaches Claude (``output_config.effort``) instead of being clamped to
    ``provider_default``.
    """
    pricing = list_pricing(row.served_model_name)
    twin_id = ""
    kind = SOURCE_KIND
    if pricing is None:
        pricing, twin_id = _twin_pricing(row)
        kind = "catalog_twin"
        if pricing is None:
            return False
    else:
        twin = (row.metadata or {}).get("catalog_twin") if isinstance(row.metadata, dict) else None
        twin_id = str((twin or {}).get("model_id") or "") if isinstance(twin, dict) else ""
    input_price = float(Decimal(pricing["prompt"]))
    output_price = float(Decimal(pricing["completion"]))
    caps = dict(row.capabilities_json or {})
    meta = dict(row.metadata or {})
    reasoning = bool(traits(row.served_model_name).efforts)
    unchanged = (
        caps.get("pricing") == pricing
        and float(row.input_price_per_token or 0) == input_price
        and float(row.output_price_per_token or 0) == output_price
        and not row.is_free
        and bool(meta.get("supports_reasoning")) == reasoning
    )
    if unchanged:
        return False
    caps["pricing"] = pricing
    caps["pricing_source"] = {
        "kind": kind,
        "set_at": timezone.now().isoformat(),
        "note": NOTE if kind == SOURCE_KIND else "Copied from the catalog twin's OpenRouter pricing.",
        "twin": twin_id or None,
    }
    meta["supports_reasoning"] = reasoning
    row.capabilities_json = caps
    row.metadata = meta
    row.input_price_per_token = input_price
    row.output_price_per_token = output_price
    row.is_free = False
    if save:
        row.save(
            update_fields=[
                "capabilities_json",
                "metadata",
                "input_price_per_token",
                "output_price_per_token",
                "is_free",
                "updated_at",  # the manifest's pricing ``as_of``
            ],
        )
    return True


def price_endpoint(endpoint: Any) -> int:
    """Price every model row of an ``api_flavor="anthropic"`` endpoint (an instance, pk or slug); rows changed."""
    from backend.inference.models import LLMEndpoint
    from backend.llm_models.models import LLMModel

    if not isinstance(endpoint, LLMEndpoint):
        lookup = {"slug": endpoint} if isinstance(endpoint, str) else {"pk": endpoint}
        endpoint = LLMEndpoint.objects.get(**lookup)
    if endpoint.api_flavor != LLMEndpoint.ApiFlavor.ANTHROPIC:
        return 0
    return sum(apply_prices(row) for row in LLMModel.objects.filter(endpoint=endpoint))
