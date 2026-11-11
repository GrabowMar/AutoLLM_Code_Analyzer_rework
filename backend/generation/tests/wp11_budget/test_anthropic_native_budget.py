"""Spend of Claude runs on the native Anthropic route: cache-aware live ledger, frozen cache prices, no seed."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.generation.budget import ledger
from backend.generation.budget import live
from backend.generation.models import GenerationRun
from backend.generation.models import RunManifest
from backend.generation.study import plan
from backend.generation.testing.factories import run_manifest_content
from backend.generation.testing.fake_gateway import gateway_state
from backend.generation.tests.wp11_budget.conftest import make_study
from backend.inference.anthropic_native import to_chat_completion
from backend.inference.anthropic_prices import apply_prices
from backend.inference.models import LLMEndpoint
from backend.llm_models.models import LLMModel

pytestmark = pytest.mark.django_db
D = Decimal
NATIVE_USAGE = {
    "input_tokens": 1000,
    "cache_creation_input_tokens": 10_000,
    "cache_read_input_tokens": 100_000,
    "output_tokens": 2000,
}
OPUS_PRICING = {
    "input_per_mtok": 4.0,
    "output_per_mtok": 20.0,
    "cached_input_per_mtok": 0.2,
    "cache_write_per_mtok": 5.0,
    "source": "anthropic_list_price",
    "as_of": "2026-11-09",
}


def _run(user, tick, pricing: dict) -> GenerationRun:
    make_study(user, n_conditions=1, n_units=1, repeats=1)
    run = GenerationRun.objects.get(pk=tick(4).claimed[0].run_id)
    manifest = run_manifest_content(run)
    manifest["model"]["endpoint"]["cost_basis"] = "billed"
    manifest["model"]["pricing"] = pricing
    manifest["resources"]["effective"] = {"content": run.resource_policy.content}
    RunManifest.objects.create(run=run, manifest=manifest)
    return GenerationRun.objects.select_related("manifest").get(pk=run.pk)


def _request(run, pk: str):
    response = to_chat_completion({"id": "msg_1", "content": [], "stop_reason": "end_turn", "usage": NATIVE_USAGE})
    usage = response["usage"]
    return SimpleNamespace(
        pk=pk,
        job_id=run.pk,
        job_attempt=0,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        response=response,
    )


def test_live_proxy_usage_prices_cache_reads_and_writes(user, system_ep, tick):
    run = _run(user, tick, OPUS_PRICING)
    entry = live.record_proxy_usage(_request(run, "req-cached"))
    # 1000 x 4 + 10000 x 5 + 100000 x 0.2 + 2000 x 20 per MTok; billed basis books it as spend too.
    assert entry.list_cost_usd == D("0.114")
    assert entry.amount_usd == D("0.114")
    assert (entry.tokens_in, entry.tokens_out) == (111_000, 2000)


def test_live_proxy_usage_without_cache_prices_is_unchanged(user, system_ep, tick):
    pricing = {k: v for k, v in OPUS_PRICING.items() if not k.startswith("cache")}
    run = _run(user, tick, pricing)
    entry = live.record_proxy_usage(_request(run, "req-plain"))
    # The old formula: every prompt token at the input price.
    assert entry.list_cost_usd == D("0.484")  # 111000 x 4 + 2000 x 20 per MTok


def _state(cost: float, calls: int):
    return gateway_state(
        cost=cost,
        list_cost=cost,
        prompt_tokens=111_000 * calls,
        completion_tokens=2000 * calls,
        requests_by_class={"main": calls},
    )


def test_a_proxied_call_is_counted_once_by_the_proxy_row_and_the_gateway_poll(user, system_ep, tick):
    """The gateway's state.json includes the calls it sent through the proxy, which the proxy booked itself:
    the live delta subtracts those ``proxy:`` rows, so the study spend is the call's cost, not twice it."""
    run = _run(user, tick, OPUS_PRICING)
    live.record_proxy_usage(_request(run, "req-1"))
    out = live.record_live(run, attempt=1, state=_state(0.114, 1), seq=1)
    assert out["delta_usd"] == 0.0
    totals = ledger.study_totals(run.study_id)
    assert totals.spent_billed == D("0.114")
    assert totals.spent_list == D("0.114")
    # The proxy books each call before it answers the gateway, so the gateway's totals never run ahead of
    # the proxy rows: later polls add only what the gateway saw beyond them (here: nothing).
    for n in (2, 3):
        live.record_proxy_usage(_request(run, f"req-{n}"))
        live.record_live(run, attempt=1, state=_state(0.114 * n, n), seq=n)
    totals = ledger.study_totals(run.study_id)
    assert totals.spent_billed == D("0.342")
    assert totals.spent_tokens == 3 * 113_000
    # A gateway-only call (not proxied, e.g. failed after forwarding) is still booked by the poll.
    live.record_live(run, attempt=1, state=_state(0.5, 3), seq=4)
    assert ledger.study_totals(run.study_id).spent_billed == D("0.5")


def _anthropic_model(user, served: str = "claude-opus-5-5") -> LLMModel:
    endpoint = LLMEndpoint.objects.create(
        slug="anthropic-plan",
        name="Anthropic",
        kind="openai_compatible",
        connection="direct",
        api_flavor="anthropic",
        base_url="https://api.anthropic.com/v1",
        cost_basis="billed",
        owner=user,
    )
    model = LLMModel.objects.create(
        model_id=f"local-anthropic-plan/{served}",
        canonical_slug=f"local-anthropic-plan_{served}",
        provider=endpoint.slug,
        model_name=served,
        endpoint=endpoint,
        served_model_name=served,
        is_free=True,
        supports_function_calling=True,
    )
    apply_prices(model)
    return LLMModel.objects.select_related("endpoint").get(pk=model.pk)


def test_manifest_freezes_cache_prices_for_the_anthropic_flavor_only(user):
    model = _anthropic_model(user)
    frozen = plan._Resolver._manifest_model(object(), model)
    pricing = frozen.pricing
    assert (pricing.input_per_mtok, pricing.output_per_mtok) == (4.0, 20.0)
    assert pricing.cached_input_per_mtok == pytest.approx(0.2)
    assert pricing.cache_write_per_mtok == pytest.approx(5.0)
    assert pricing.source == "anthropic_list_price"
    assert frozen.endpoint.api_flavor == "anthropic"
    assert frozen.endpoint.cost_basis == "billed"
    # The same row on an OpenAI-flavor endpoint keeps the old manifest pricing (input/output only).
    LLMEndpoint.objects.filter(pk=model.endpoint_id).update(api_flavor="openai")
    other = plan._Resolver._manifest_model(object(), LLMModel.objects.select_related("endpoint").get(pk=model.pk))
    assert other.pricing.cached_input_per_mtok is None
    assert other.pricing.cache_write_per_mtok is None
    assert other.pricing.source == "catalog"


def test_the_anthropic_route_claims_no_forwarded_seed(user):
    model = _anthropic_model(user, "claude-sonnet-5-5")
    assert plan._supports_seed(model) is False
    LLMEndpoint.objects.filter(pk=model.endpoint_id).update(api_flavor="openai")
    assert plan._supports_seed(LLMModel.objects.select_related("endpoint").get(pk=model.pk)) is True


def test_manifest_freezes_the_planned_native_settings(user):
    """``model.endpoint.native``: the effort the route plans to send and why, thinking, replay and caching,
    so a Claude run records its settings after its call rows are purged."""
    model = _anthropic_model(user, "claude-opus-5-5")
    native = plan._Resolver._manifest_model(object(), model).endpoint.native
    assert native["api"] == "anthropic_messages"
    assert (native["effort"], native["effort_source"]) == ("high", "llmlab_default")
    assert native["default_effort"] == "high"
    assert native["thinking"] == "adaptive"
    assert native["thinking_replay"] == "dropped"
    assert native["forced_tool_choice"] is False
    assert native["cache_ttl"] == "5m"
    pinned = plan._Resolver._manifest_model(
        object(),
        model,
        reasoning={"mode": "effort", "effort": "medium", "max_tokens": None, "exclude": False},
    ).endpoint.native
    assert (pinned["effort"], pinned["effort_source"]) == ("medium", "policy")
    LLMEndpoint.objects.filter(pk=model.endpoint_id).update(api_flavor="openai")
    other = plan._Resolver._manifest_model(object(), LLMModel.objects.select_related("endpoint").get(pk=model.pk))
    assert other.endpoint.native is None


def _condition(model, kind: str = "agentic", policy: str = "explicit") -> SimpleNamespace:
    content = {"policy": policy, "params": {"temperature": 0.7, "top_p": 0.9}, "forward_seed": True}
    row = SimpleNamespace(content=content, pinned_ref="t07@1#0123456789ab")
    return SimpleNamespace(rows={"sampling_preset": row}, kind=kind, model=model)


def test_explicit_sampling_on_the_native_route_is_recorded_as_not_sent(user):
    """The gateway still forces the preset (unchanged), but the manifest records that Claude never got it."""
    model = _anthropic_model(user, "claude-sonnet-5-5")
    sampling = plan._Resolver._sampling(object(), _condition(model))
    assert sampling.forced_params == {"temperature": 0.7, "top_p": 0.9}
    assert sampling.not_sent_upstream == ["temperature", "top_p"]
    assert sampling.seed_forwarded is False
    assert plan._Resolver._sampling(object(), _condition(model, policy="provider_default")).not_sent_upstream == []
    LLMEndpoint.objects.filter(pk=model.endpoint_id).update(api_flavor="openai")
    reloaded = LLMModel.objects.select_related("endpoint").get(pk=model.pk)
    other = plan._Resolver._sampling(object(), _condition(reloaded))
    assert other.forced_params == {"temperature": 0.7, "top_p": 0.9}
    assert other.not_sent_upstream == []
