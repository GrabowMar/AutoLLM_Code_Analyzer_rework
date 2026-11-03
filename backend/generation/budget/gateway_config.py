"""``GatewayConfig`` for one run attempt (spec 4.2, 4.5, 6.6): the budget and routing the gateway enforces.

Everything comes from the frozen run: the manifest's model pin, pricing, sampling and gateway block, and the
effective (clamped) policy in ``manifest.resources.effective``. The run's reservation is the gateway's cost
cap: ``budget.max_cost_usd = min(run.max_cost_usd, reservation of this attempt)``, so the gateway's
pre-call reservation and the scheduler's ledger reservation bound the same amount (4.5).

The OpenRouter ``provider`` object is built from the routing knobs and never uses the object form of
``sort`` that can switch models; empty lists and unset knobs are left out. Local endpoints get no provider
routing (there is no upstream provider choice).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings

from backend.generation.budget import ledger
from backend.generation.budget.model_facts import EXPLICIT_CACHE_FAMILIES
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.budget.model_facts import model_facts
from backend.generation.contracts.resources import GatewayConfig
from backend.generation.contracts.resources import ResourcePolicyContent

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

__all__ = ["OPENROUTER_BASE_URL", "build", "provider_object"]

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_SEEDLESS_FRONTS = frozenset({"anthropic_messages", "openai_responses"})


def provider_object(content: ResourcePolicyContent) -> dict[str, Any]:
    """The OpenRouter ``provider`` routing object of a clamped policy (``max_price`` already resolved)."""
    routing = content.routing
    out: dict[str, Any] = {
        "allow_fallbacks": routing.allow_fallbacks,
        "require_parameters": routing.require_parameters,
        "data_collection": routing.data_collection,
    }
    if routing.provider_sort != "none":
        out["sort"] = routing.provider_sort
    if isinstance(routing.max_price, dict) or hasattr(routing.max_price, "prompt"):
        price = routing.max_price if isinstance(routing.max_price, dict) else routing.max_price.model_dump()
        out["max_price"] = {"prompt": price["prompt"], "completion": price["completion"]}
    for name in ("only", "ignore", "order", "quantizations"):
        values = list(getattr(routing, name))
        if values:
            out[name] = values
    if routing.zdr:
        out["zdr"] = True
    if routing.preferred_min_throughput is not None:
        out["preferred_min_throughput"] = routing.preferred_min_throughput
    if routing.preferred_max_latency is not None:
        out["preferred_max_latency"] = routing.preferred_max_latency
    return out


def _facts(run: GenerationRun, manifest: dict[str, Any]) -> ModelFacts:
    model_block = manifest.get("model") or {}
    facts = model_facts(run.model) if run.model_id else model_facts({"slug": model_block.get("slug") or "unknown"})
    return facts.with_pricing(model_block.get("pricing"))


def _request_class_source(harness: str | None) -> str:
    """``HarnessFacts.request_class_source`` (a header name, or ``default:<class>``); ``default:main`` when unknown."""
    if not harness:
        return "default:main"
    try:
        from backend.generation.harnesses import registry

        return registry.get(harness).facts.request_class_source or "default:main"
    except (NotImplementedError, KeyError, ImportError, AttributeError):
        return "default:main"


def build(run: GenerationRun, *, attempt: int | None = None) -> GatewayConfig:
    """The gateway configuration of one run attempt (raises ``ValueError`` for a run without a manifest)."""
    if not ledger._has_manifest(run):  # noqa: SLF001
        msg = f"run {run.pk} has no manifest: the gateway config is built from the frozen manifest only"
        raise ValueError(msg)
    manifest = run.manifest.manifest or {}
    a = attempt if attempt is not None else ledger.attempt_no(run)
    content = ledger.run_policy(run)
    effective = (manifest.get("resources") or {}).get("effective") or {}
    facts = _facts(run, manifest)
    model_block = manifest.get("model") or {}
    endpoint = model_block.get("endpoint") or {}
    pricing = model_block.get("pricing") or {}
    gateway = manifest.get("gateway") or {}
    sampling = manifest.get("sampling") or {}
    local = str(endpoint.get("kind") or "openrouter") != "openrouter"
    reserved = ledger.reservation(run.pk, a)
    cost_cap = content.run.max_cost_usd
    if reserved > 0:
        cost_cap = min(cost_cap, float(reserved))
    single_shot = run.kind == "slot_single_shot"
    call_cap = (
        (content.slot.max_output_tokens or content.call.max_output_tokens)
        if single_shot
        else content.call.max_output_tokens
    )
    serving_tier = effective.get("serving_tier") or gateway.get("serving_tier") or "standard"
    if serving_tier not in ("standard", "flex", "batch"):
        serving_tier = "standard"
    served_name = endpoint.get("served_model_name") or facts.served_model_name or None
    upstream_model = (served_name if local and served_name else model_block.get("slug")) or facts.slug
    presented = sorted({facts.slug, facts.slug.rsplit("/", 1)[-1], *([served_name] if served_name else [])})
    rpm = content.concurrency.rpm_per_model
    harness = manifest.get("harness") or {}
    request_class_source = _request_class_source(harness.get("name"))
    caching = content.caching
    front = gateway.get("front_protocol") or "openai_chat"
    # Native Anthropic Messages and OpenAI Responses bodies have no seed; the gateway never adds one.
    forward_seed = bool(sampling.get("seed_forwarded", True)) and front not in _SEEDLESS_FRONTS
    config = {
        "run_id": str(run.pk),
        "attempt": a,
        "session_id": str(run.pk) if caching.sticky_session else None,
        "front_protocol": front,
        "protocol_mode": gateway.get("protocol_mode") or "native",
        "request_class_source": request_class_source,
        "upstream": {
            "kind": "local_proxy" if local else "openrouter",
            "base_url": settings.LLM_PROXY_UPSTREAM if local else OPENROUTER_BASE_URL,
            "endpoint_id": endpoint.get("id"),
        },
        "model": {
            "upstream_model": upstream_model,
            "served_model_name": served_name,
            "presented_names": presented,
            "context_length": facts.context_length,
            "max_output": facts.max_output,
            "supports_reasoning": facts.supports_reasoning,
        },
        "pricing": {
            "input_per_mtok": float(pricing.get("input_per_mtok") or 0.0),
            "output_per_mtok": float(pricing.get("output_per_mtok") or 0.0),
            "cached_input_per_mtok": pricing.get("cached_input_per_mtok"),
            "cache_write_per_mtok": pricing.get("cache_write_per_mtok"),
            "source": str(pricing.get("source") or "unknown"),
            "as_of": str(pricing.get("as_of") or "unknown"),
        },
        "sampling": {
            "forced": dict(sampling.get("forced_params") or {}),
            "stripped": list(sampling.get("stripped_params") or []),
            "seed": sampling.get("seed") if forward_seed else None,
            "forward_seed": forward_seed,
        },
        "reasoning": content.reasoning.model_dump(mode="json"),
        "budget": {
            "max_cost_usd": round(cost_cap, 8),
            "cost_basis": str(endpoint.get("cost_basis") or "billed"),
            "max_total_tokens": content.run.max_total_tokens,
            "max_uncached_input_tokens": content.run.max_uncached_input_tokens,
            "max_output_tokens": content.run.max_output_tokens,
            "max_llm_requests": content.run.max_llm_requests,
            "max_aux_requests": content.run.max_aux_requests,
            "count_failed_calls": content.retries.count_failed_calls,
        },
        "call": {"max_output_tokens": call_cap, "max_input_tokens": content.call.max_input_tokens},
        "routing": {"provider": {} if local else provider_object(content), "serving_tier": serving_tier},
        "caching": {
            "prompt_cache": caching.prompt_cache,
            "ttl": caching.ttl,
            "sticky_session": caching.sticky_session,
            "cache_control_providers": (
                [facts.family] if caching.prompt_cache == "on" and facts.family in EXPLICIT_CACHE_FAMILIES else []
            ),
        },
        "retries": {
            "upstream_retries": content.retries.upstream_retries,
            "backoff_s": content.retries.backoff_s,
            "retry_on": list(content.retries.retry_on),
            "llm_call_timeout_s": content.time.llm_call_timeout_s,
        },
        "rate_limit": {
            "rpm_per_model": rpm,
            "bucket_key": f"{run.study_id or 'adhoc'}:{model_block.get('slug') or facts.slug}" if rpm else None,
        },
    }
    return GatewayConfig.model_validate(config)
