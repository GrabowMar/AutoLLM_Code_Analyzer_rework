"""Comparable generation cost: every run's tokens at the public list prices of its model's OpenRouter catalog row.

The billed cost (``run.metrics.cost_usd``) is not comparable across routes: a subscription endpoint and a key
billed outside per-call pricing record 0, and ``metrics.list_cost_usd`` uses whatever the route's own row lists
(0 on several subscription rows; the cache discount applied on some routes only). Here every run is priced
alike, with the gateway's own cache-aware formula (``budget_rules.Pricing.price``)::

    cost = uncached x prompt + cached x input_cache_read + cache_write x input_cache_write + completion x completion

``uncached = prompt_tokens - cached_tokens - cache_write_tokens`` (``prompt_tokens`` counts cache reads and
writes on every route). ``metrics.uncached_prompt_tokens`` is ``prompt - cached``, cache writes included, so
it is not used. A missing cache-read or cache-write price charges those tokens at the prompt price. Reasoning
tokens are part of ``completion_tokens`` on every route (OpenAI-style usage counts them there; Anthropic's
output tokens include thinking), so they are not added again.

**Prices** come from the model's OpenRouter catalog row: the model itself when it is one (``endpoint`` NULL),
else the row its ``metadata.catalog_twin.model_id`` names (a local endpoint model assumed to serve the same
weights). The row's ``metadata.openrouter_pricing`` holds USD per token as strings (``prompt``, ``completion``,
``input_cache_read``, ``input_cache_write``). A model without such a row, or whose row lists no positive prompt
and completion price, is **unpriced**: its list cost is None (unknown, never 0). Per-request price tiers
(``overrides``, e.g. long-context prompts) are not applied: run totals do not record request sizes.

:func:`list_prices` resolves the prices of several models in two queries; :func:`run_list_cost` prices one
run's token totals; :func:`earlier_attempts` counts the retried attempts a run's totals leave out;
:func:`cost_basis` is the report's ``protocol.cost_basis``.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

__all__ = [
    "FORMULA",
    "ListPrice",
    "cost_basis",
    "earlier_attempts",
    "list_prices",
    "map_twin_prices",
    "run_list_cost",
]

_MTOK = 1_000_000
FORMULA = (
    "cost = (prompt_tokens - cached_tokens - cache_write_tokens) x prompt + cached_tokens x input_cache_read + "
    "cache_write_tokens x input_cache_write + completion_tokens x completion, per run from run.metrics; a missing "
    "cache-read or cache-write price charges those tokens at the prompt price (the gateway's budget_rules.Pricing). "
    "Reasoning tokens are already inside completion_tokens and are not added again; "
    "metrics.uncached_prompt_tokens (prompt - cached, cache writes included) is not used. The per-app figure is "
    "the mean over apps of each app's runs; every usable run counts, failures included (their tokens were spent). "
    "A run's tokens are its recorded usage (run.metrics: the generation attempt that produced its outcome, as the "
    "billed cost); attempts retried after an infrastructure error and calls that reported no usage are not "
    "included (cost basis: earlier_attempts)."
)
SOURCE = "OpenRouter list prices of the model's catalog row / catalog twin"


@dataclass(frozen=True)
class ListPrice:
    """The list prices one model's runs are priced at (USD per token; None: not listed)."""

    model: str
    # The catalog row whose prices are used ("" when there is none).
    priced_as: str = ""
    # "own_row" (the model is an OpenRouter row) or "catalog_twin".
    source: str = ""
    prompt: float | None = None
    completion: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    synced_at: str | None = None
    # The row lists per-request price tiers (``overrides``) that run totals cannot apply.
    tiers: bool = False
    # Why the model is unpriced (None when priced).
    missing: str | None = None
    # The model's own route prices when they differ from the catalog row's (``capabilities_json.pricing``).
    route_pricing: dict[str, float | None] = field(default_factory=dict)

    @property
    def priced(self) -> bool:
        return self.missing is None

    def per_mtok(self) -> dict[str, float | None]:
        return {
            key: None if value is None else round(value * _MTOK, 6)
            for key, value in (
                ("prompt", self.prompt),
                ("completion", self.completion),
                ("cache_read", self.cache_read),
                ("cache_write", self.cache_write),
            )
        }


def _price(value: Any) -> float | None:
    """A per-token price string/number as a float; None when absent, malformed or negative."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _openrouter_pricing(row: Any) -> dict[str, Any] | None:
    meta = row.metadata if isinstance(getattr(row, "metadata", None), dict) else {}
    pricing = meta.get("openrouter_pricing")
    return pricing if isinstance(pricing, dict) else None


def _twin_id(row: Any) -> str:
    meta = row.metadata if isinstance(getattr(row, "metadata", None), dict) else {}
    twin = meta.get("catalog_twin")
    return str(twin.get("model_id") or "") if isinstance(twin, dict) else ""


def _route_pricing(model: Any, prices: dict[str, float | None]) -> dict[str, float | None]:
    """The endpoint model's own positive prices (``capabilities_json.pricing``) when they differ from *prices*."""
    caps = model.capabilities_json if isinstance(getattr(model, "capabilities_json", None), dict) else {}
    raw = caps.get("pricing")
    if not isinstance(raw, dict):
        return {}
    own = {
        "prompt": _price(raw.get("prompt")),
        "completion": _price(raw.get("completion")),
        "cache_read": _price(raw.get("input_cache_read")),
        "cache_write": _price(raw.get("input_cache_write")),
    }
    if not own["prompt"] or not own["completion"] or own == prices:
        return {}
    return {k: None if v is None else round(v * _MTOK, 6) for k, v in own.items()}


def _from_row(model: Any, row: Any, source: str) -> ListPrice:
    pricing = _openrouter_pricing(row) or {}
    prices = {
        "prompt": _price(pricing.get("prompt")),
        "completion": _price(pricing.get("completion")),
        "cache_read": _price(pricing.get("input_cache_read")),
        "cache_write": _price(pricing.get("input_cache_write")),
    }
    missing = None
    if not pricing:
        missing = f"{row.model_id} has no OpenRouter pricing"
    elif not prices["prompt"] or not prices["completion"]:
        # A zero list price is not a comparable cost (a free variant or an unpriced row).
        missing = f"{row.model_id} lists no positive prompt and completion price"
    return ListPrice(
        model=model.model_id,
        priced_as=row.model_id,
        source=source,
        **prices,
        synced_at=row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
        tiers=bool(pricing.get("overrides")),
        missing=missing,
        route_pricing=_route_pricing(model, prices) if source == "catalog_twin" else {},
    )


def list_prices(models: Iterable[Any]) -> dict[str, ListPrice]:
    """``{model_id: ListPrice}`` for LLMModel rows: their own OpenRouter row, else their catalog twin."""
    from backend.llm_models.models import LLMModel

    models = {m.model_id: m for m in models if m is not None}
    twin_ids = {_twin_id(m) for m in models.values() if m.endpoint_id is not None} - {""}
    twins = {row.model_id: row for row in LLMModel.objects.filter(model_id__in=twin_ids)}
    out: dict[str, ListPrice] = {}
    for model_id, model in sorted(models.items()):
        if model.endpoint_id is None:
            out[model_id] = _from_row(model, model, "own_row")
            continue
        twin_id = _twin_id(model)
        if not twin_id:
            out[model_id] = ListPrice(model=model_id, missing="an endpoint model without a catalog twin")
        elif twin_id not in twins:
            missing = f"its catalog twin {twin_id} is not in the catalog"
            out[model_id] = ListPrice(model=model_id, priced_as=twin_id, source="catalog_twin", missing=missing)
        else:
            out[model_id] = _from_row(model, twins[twin_id], "catalog_twin")
    return out


def map_twin_prices(models: Iterable[Any], prices: Mapping[str, ListPrice]) -> dict[str, ListPrice]:
    """Per endpoint model, the prices of the twin the curated name map assumes when it is not the catalog twin.

    ``llm_models/data/external_model_map.yaml`` can name another release than ``metadata.catalog_twin`` (an
    assumption about which release a route serves); its prices are recorded beside the catalog twin's, never used.
    """
    from backend.llm_models.models import LLMModel
    from backend.llm_models.services.model_covariates import map_twins

    models = [m for m in models if m is not None]
    curated = map_twins()
    wanted = {
        m.model_id: curated[m.model_id]
        for m in models
        if m.endpoint_id is not None
        and curated.get(m.model_id)
        and curated[m.model_id] != (prices.get(m.model_id) or ListPrice(model=m.model_id)).priced_as
    }
    rows = {row.model_id: row for row in LLMModel.objects.filter(model_id__in=set(wanted.values()))}
    by_id = {m.model_id: m for m in models}
    return {
        model_id: _from_row(by_id[model_id], rows[twin], "external_model_map")
        if twin in rows
        else ListPrice(model=model_id, priced_as=twin, source="external_model_map", missing=f"{twin} is not listed")
        for model_id, twin in sorted(wanted.items())
    }


def _tokens(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def run_list_cost(metrics: Mapping[str, Any] | None, price: ListPrice | None) -> float | None:
    """One run's generation cost at *price* (USD); None when the model is unpriced or the tokens are unknown."""
    if price is None or not price.priced or not isinstance(metrics, dict):
        return None
    cached = _tokens(metrics.get("cached_tokens")) or 0
    cache_write = _tokens(metrics.get("cache_write_tokens")) or 0
    prompt = _tokens(metrics.get("prompt_tokens"))
    if prompt is None and _tokens(metrics.get("uncached_prompt_tokens")) is not None:
        prompt = _tokens(metrics["uncached_prompt_tokens"]) + cached  # uncached_prompt_tokens = prompt - cached
    completion = _tokens(metrics.get("completion_tokens"))
    if prompt is None or completion is None:
        return None
    from backend.inference.proxy_server import budget_rules

    pricing = budget_rules().Pricing(
        input_per_mtok=price.prompt * _MTOK,
        output_per_mtok=price.completion * _MTOK,
        cached_input_per_mtok=None if price.cache_read is None else price.cache_read * _MTOK,
        cache_write_per_mtok=None if price.cache_write is None else price.cache_write * _MTOK,
    )
    return round(pricing.price(prompt, completion, cached, cache_write), 10)


def _synced(prices: Iterable[ListPrice]) -> str:
    days = sorted({p.synced_at[:10] for p in prices if p.synced_at})
    if not days:
        return "unknown"
    return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"


def earlier_attempts(rows: Iterable[Mapping[str, Any]], prices: Mapping[str, ListPrice]) -> dict[str, dict[str, Any]]:
    """Per model, the earlier generation attempts of the report's runs (*rows*: ``run_id``, ``model``) that recorded
    tokens, and their list cost.

    A run's ``metrics`` hold the usage of the attempt that produced its outcome; an attempt retried before it
    (an infrastructure error) spent tokens too (``RunAttempt.usage``). They are disclosed, not added: the run's
    cost, billed and at list price alike, is its recorded attempt's.
    """
    from backend.generation.models import RunAttempt

    model_of = {str(r["run_id"]): r["model"] for r in rows}
    attempts: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for run_id, number, usage in RunAttempt.objects.filter(run_id__in=list(model_of)).values_list(
        "run_id",
        "attempt",
        "usage",
    ):
        attempts.setdefault(str(run_id), []).append((number, usage if isinstance(usage, dict) else {}))
    out: dict[str, dict[str, Any]] = {}
    costs: dict[str, list[float | None]] = {}
    for run_id, items in sorted(attempts.items()):
        last = max(number for number, _usage in items)
        spent = [usage for number, usage in items if number < last and _spent(usage)]
        if not spent:
            continue
        model_id = model_of[run_id]
        entry = out.setdefault(model_id, {"runs": 0, "attempts": 0, "prompt_tokens": 0, "completion_tokens": 0})
        entry["runs"] += 1
        entry["attempts"] += len(spent)
        entry["prompt_tokens"] += sum(_tokens(u.get("prompt_tokens")) or 0 for u in spent)
        entry["completion_tokens"] += sum(_tokens(u.get("completion_tokens")) or 0 for u in spent)
        costs.setdefault(model_id, []).extend(run_list_cost(u, prices.get(model_id)) for u in spent)
    for model_id, entry in out.items():
        # Unknown (None) when the model is unpriced or an attempt's tokens are incomplete, never 0.
        values = costs[model_id]
        entry["list_cost_usd"] = None if None in values else round(sum(values), 10)
    return out


def _spent(usage: Mapping[str, Any]) -> bool:
    return bool((_tokens(usage.get("prompt_tokens")) or 0) + (_tokens(usage.get("completion_tokens")) or 0))


def cost_basis(
    prices: Mapping[str, ListPrice],
    *,
    alternatives: Mapping[str, tuple[ListPrice, float | None]] | None = None,
    earlier: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The report's ``protocol.cost_basis``: the price source, the formula and each model's prices.

    *alternatives*: per model, the prices of the twin the curated name map assumes (:func:`map_twin_prices`)
    and the model's list cost per app at them; recorded beside the catalog twin's, never used.
    *earlier*: per model, the earlier attempts left out of the cost (:func:`earlier_attempts`).
    """
    per_model = []
    notes = [
        (
            "Billed cost (cost_per_app, run.metrics.cost_usd) is kept for reference: it is 0 on subscription "
            "endpoints and on keys billed outside per-call pricing, so it does not compare models. It is averaged "
            "over generated apps only (a run whose output was invalid is left out), while the cost at list price "
            "averages every usable run, so the two differ in denominator where a model has output_invalid runs."
        ),
        (
            "List prices are the catalog's current public per-token prices, not realized marginal costs; a local "
            "endpoint model is priced as the catalog row assumed to serve the same weights (its catalog twin)."
        ),
    ]
    for model_id, price in sorted(prices.items()):
        entry: dict[str, Any] = {
            "model": model_id,
            "priced_as": price.priced_as or None,
            "source": price.source or None,
            **price.per_mtok(),
            "synced_at": price.synced_at,
            "priced": price.priced,
        }
        if price.missing:
            entry["missing"] = price.missing
        if price.tiers:
            entry["tiers_not_applied"] = True
            notes.append(
                f"{price.priced_as} lists per-request price tiers (e.g. long prompts) that run totals cannot apply: "
                f"{model_id} is priced at its base tier.",
            )
        if price.route_pricing:
            entry["route_pricing"] = price.route_pricing
            notes.append(
                f"{model_id}: its route's own list prices differ from its catalog twin {price.priced_as} "
                f"({_differences(price)}); the catalog twin's are used, so every model is priced by one catalog.",
            )
        if model_id in (alternatives or {}):
            other, other_cost = alternatives[model_id]
            entry["alternative_twin"] = {
                "model": other.priced_as,
                "source": "llm_models/data/external_model_map.yaml",
                **other.per_mtok(),
                "synced_at": other.synced_at,
                "priced": other.priced,
                **({"missing": other.missing} if other.missing else {}),
                "list_cost_per_app": other_cost,
            }
            notes.append(
                f"{model_id} is priced as its catalog twin {price.priced_as} (metadata.catalog_twin); the curated "
                f"name map (llm_models/data/external_model_map.yaml) assumes {other.priced_as}. Both prices are "
                "recorded here (alternative_twin, with the cost per app at its prices); the catalog twin is used.",
            )
        if (earlier or {}).get(model_id):
            entry["earlier_attempts"] = dict(earlier[model_id])
        per_model.append(entry)
    if any((earlier or {}).get(m) for m in prices):
        spent = {m: earlier[m] for m in sorted(prices) if (earlier or {}).get(m)}
        notes.append(
            "Not included (each run's recorded attempt only): earlier attempts retried after an infrastructure error "
            "that recorded tokens: "
            + "; ".join(
                f"{m} ({e['runs']} run(s), {e['attempts']} attempt(s), "
                + ("unpriced" if e.get("list_cost_usd") is None else f"{e['list_cost_usd']:.4f} USD at list price")
                + ")"
                for m, e in spent.items()
            )
            + ".",
        )
    return {
        "source": f"{SOURCE} as synced {_synced(prices.values())}",
        "unit": "USD per million tokens",
        "formula": FORMULA,
        "per_model": per_model,
        "notes": notes,
    }


def _differences(price: ListPrice) -> str:
    own = price.route_pricing
    twin = price.per_mtok()
    return ", ".join(f"{k} {own.get(k)} vs {twin[k]} per MTok" for k in twin if own.get(k) != twin[k])
