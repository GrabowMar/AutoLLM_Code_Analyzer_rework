"""Builders for MSS v2 tests: plain frame rows (no database) and seeded ``LLMModel`` rows."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from backend.rankings.services.frame import FrameConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = dt.date(2026, 11, 9)

CONFIG = FrameConfig.from_dict(
    {
        "vendors": {
            "anthropic": {"models_dev": "anthropic", "openrouter_provider": "Anthropic", "origin": "US"},
            "openai": {"models_dev": "openai", "openrouter_provider": "OpenAI", "origin": "US"},
            "google": {"models_dev": "google", "openrouter_provider": "Google AI Studio", "origin": "US"},
            "z-ai": {"models_dev": "zai", "openrouter_provider": "Z.AI", "origin": "CN"},
            "deepseek": {"models_dev": "deepseek", "openrouter_provider": "DeepSeek", "origin": "CN"},
            "tencent": {"models_dev": "tencent-tokenhub", "openrouter_provider": "Tencent", "origin": "CN"},
            "qwen": {"models_dev": "alibaba", "openrouter_provider": "Alibaba", "origin": "CN"},
            "xiaomi": {"models_dev": "xiaomi", "openrouter_provider": "Xiaomi", "origin": "CN"},
            "minimax": {"models_dev": "minimax", "openrouter_provider": "Minimax", "origin": "CN"},
            "mistralai": {"models_dev": "mistral", "openrouter_provider": "Mistral", "origin": "EU"},
        },
        "go_limits": {
            "plan": "Go ($10)",
            "source": "https://opencode.ai/docs/go",
            "fetched": "2026-11-09",
            "five_hour_share": 0.2,
            "weekly_share": 0.5,
            "monthly_usd": {"glm-5.3": 15, "glm-5.3-flash": 60, "deepseek-v4.1-flash": 100},
        },
        "twins": {"local-opencode-go/no-twin-but-override": "z-ai/glm-5.3"},
    },
)


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def models_dev(
    *,
    release: str | None = "2026-09-01",
    tools: bool = True,
    context: int = 200_000,
    open_weights: bool | None = True,
    status: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tool_call": tools,
        "limit": {"context": context},
        "modalities": {"input": ["text"], "output": ["text"]},
    }
    if release:
        out["release_date"] = release
    if open_weights is not None:
        out["open_weights"] = open_weights
    if status:
        out["status"] = status
    return out


def list_price(inp: float, out: float, *, cr: float | None = None, cw: float | None = None, **extra: Any) -> dict:
    return {
        "input": inp,
        "output": out,
        "cache_read": cr,
        "cache_write": cw,
        "unit": "USD per 1M tokens",
        "source": "models.dev:test/model",
        "fetched_at": "2026-11-09T09:00:00+00:00",
        "discount_undone": False,
        "tiers": None,
        "first_party_record": None,
        "go": None,
        "openrouter_cheapest": None,
        **extra,
    }


def popularity(
    shares: list[float | None],
    *,
    start: str = "2026-11-02",
    total: int = 10**12,
    limit_share: float = 0.001,
) -> dict[str, Any]:
    """A stored ``metadata.popularity.openrouter`` record: one day per share (None = censored)."""
    day0 = dt.date.fromisoformat(start)
    daily = []
    for i, share in enumerate(shares):
        daily.append(
            {
                "date": (day0 + dt.timedelta(days=i)).isoformat(),
                "tokens": None if share is None else int(share * total),
                "total": total,
                "limit": int(limit_share * total),
                "censored": share is None,
            },
        )
    censored = sum(1 for s in shares if s is None)
    return {
        "source": "OpenRouter Data API datasets/rankings-daily",
        "license": "CC BY 4.0",
        "citation": "Source: OpenRouter (openrouter.ai/rankings), as of 2026-11-09T09:22Z",
        "as_of": "2026-11-09T09:22:19Z",
        "fetched_at": "2026-11-09T09:30:00+00:00",
        "window": {"start": start, "end": daily[-1]["date"] if daily else start, "days": len(shares)},
        "permaslugs": [],
        "days_available": len(daily),
        "days_listed": len(daily) - censored,
        "days_censored": censored,
        "daily": daily,
        "short_exposure": len(daily) < 7,
        "code_tags": {},
    }


def lmarena(
    rating: float | None,
    *,
    lower: float | None = None,
    upper: float | None = None,
    votes: int = 1000,
) -> dict:
    cats: dict[str, Any] = {}
    if rating is not None:
        cats["webdev"] = {
            "rating": rating,
            "rating_lower": lower if lower is not None else rating - 10,
            "rating_upper": upper if upper is not None else rating + 10,
            "vote_count": votes,
            "arena_name": "Test Model",
            "publish_date": "2026-11-08",
        }
    cats["text_coding"] = {"rating": 1400, "vote_count": votes}
    return {"categories": cats, "match": "exact", "publish_date": "2026-11-08"}


def row(
    model_id: str,
    *,
    pk: int | None = None,
    endpoint: str | None = None,
    served: str = "",
    available: bool = True,
    hidden: bool = False,
    free: bool = False,
    context: int = 0,
    tools: bool = False,
    input_per_m: float = 1.0,
    output_per_m: float = 2.0,
    metadata: dict[str, Any] | None = None,
    canonical_slug: str | None = None,
    created: int | None = None,
    expiration_date: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """A frame row exactly as ``frame.row_from_model`` produces it."""
    return {
        "pk": pk if pk is not None else abs(hash(model_id)) % 10**8,
        "model_id": model_id,
        "model_name": name or model_id,
        "provider": model_id.split("/", 1)[0],
        "endpoint": endpoint,
        "served_name": served,
        "is_available": available,
        "is_hidden": hidden,
        "is_free": free,
        "context_window": context,
        "supports_tools": tools,
        "input_per_m": 0.0 if free else input_per_m,
        "output_per_m": 0.0 if free else output_per_m,
        "metadata": metadata or {},
        "payload": {"canonical_slug": canonical_slug, "created": created, "expiration_date": expiration_date},
    }


def eligible_row(
    model_id: str,
    *,
    share: float | None = 0.01,
    price: tuple[float, float] | None = (1.0, 4.0),
    rating: float | None = 1650.0,
    release: str = "2026-09-01",
    open_weights: bool | None = True,
    shares: list[float | None] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """A catalog row that passes E1-E8 (unless an input is set to None)."""
    meta: dict[str, Any] = {
        "openrouter_supported_parameters": ["tools"],
        "models_dev": models_dev(release=release, open_weights=open_weights),
    }
    if price is not None:
        meta["list_price"] = list_price(*price)
    if share is not None or shares is not None:
        meta["popularity"] = {"openrouter": popularity(shares if shares is not None else [share] * 7)}
    if rating is not None:
        meta["lmarena"] = lmarena(rating)
    meta.update(kwargs.pop("metadata", {}) or {})
    return row(model_id, context=kwargs.pop("context", 200_000), metadata=meta, **kwargs)


def go_row(served: str, twin: str, **kwargs: Any) -> dict[str, Any]:
    """A local OpenCode Go route row (subscription: price 0, ``is_free`` set) linked to ``twin``."""
    meta = {"catalog_twin": {"model_id": twin}}
    return row(
        f"local-opencode-go/{served}",
        endpoint="opencode-go",
        served=served,
        free=True,
        tools=True,
        metadata=meta,
        **kwargs,
    )


def anthropic_row(served: str, twin: str, **kwargs: Any) -> dict[str, Any]:
    meta = {"catalog_twin": {"model_id": twin}}
    return row(
        f"local-anthropic/{served}",
        endpoint="anthropic",
        served=served,
        free=True,
        tools=True,
        metadata=meta,
        **kwargs,
    )


def create_eligible_model(model_id: str, **kwargs: Any):
    """An ``LLMModel`` whose metadata passes every MSS v2 filter (database tests)."""
    from backend.llm_models.tests.factories import LLMModelFactory

    spec = eligible_row(model_id, **{k: v for k, v in kwargs.items() if k in _ELIGIBLE_KEYS})
    extra = {k: v for k, v in kwargs.items() if k not in _ELIGIBLE_KEYS}
    return LLMModelFactory(model_id=model_id, metadata=spec["metadata"], context_window=200_000, **extra)


_ELIGIBLE_KEYS = {"share", "price", "rating", "release", "open_weights", "shares"}


def create_route_model(endpoint_slug: str, served: str, twin: str, *, owner=None):
    """A local route ``LLMModel`` (``opencode-go`` or ``anthropic`` endpoint) linked to the catalog row ``twin``."""
    from backend.inference.models import LLMEndpoint
    from backend.llm_models.tests.factories import LLMModelFactory
    from backend.users.tests.factories import UserFactory

    endpoint = LLMEndpoint.objects.filter(slug=endpoint_slug).first() or LLMEndpoint.objects.create(
        slug=endpoint_slug,
        name=endpoint_slug,
        kind=LLMEndpoint.Kind.OPENAI_COMPATIBLE,
        owner=owner or UserFactory(),
        # An OpenAI-compatible endpoint must speak the openai flavor (llmendpoint_flavor_by_kind).
        **({"api_flavor": "openai"} if hasattr(LLMEndpoint, "ApiFlavor") else {}),
    )
    return LLMModelFactory(
        model_id=f"local-{endpoint_slug}/{served}",
        endpoint=endpoint,
        served_model_name=served,
        is_free=True,
        input_price_per_token=0,
        output_price_per_token=0,
        supports_function_calling=True,
        metadata={"catalog_twin": {"model_id": twin}},
    )
