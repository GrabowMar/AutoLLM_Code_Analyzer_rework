"""``budget.gateway_config.build(run)``: the frozen manifest and effective policy, capped at the reservation."""

from __future__ import annotations

import pytest

from backend.generation.budget import gateway_config
from backend.generation.budget import policy
from backend.generation.contracts.resources import preset_content
from backend.generation.models import GenerationRun
from backend.generation.testing.fake_gateway import FakeGateway
from backend.generation.tests.wp11_budget.conftest import attach_manifest
from backend.generation.tests.wp11_budget.conftest import make_study
from backend.generation.tests.wp11_budget.conftest import model

pytestmark = pytest.mark.django_db


def _effective(content, m):
    return policy.effective_policy(content, model=m).model_dump(mode="json", by_alias=True)


def test_build_from_the_manifest_capped_at_the_reservation(user, system_ep, tick):
    m = model(model_id="vendor/gw-model", context_window=100_000, max_output_tokens=20_000)
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap_usd=0.12, run_cap=0.30, models=[m])
    run = tree.runs[0]
    attach_manifest(run, pricing=(1.0, 2.0), effective=_effective(run.resource_policy.content, m))
    run = GenerationRun.objects.select_related("manifest", "model").get(pk=run.pk)
    before = gateway_config.build(run)
    assert before.budget.max_cost_usd == 0.30  # no reservation yet: the run cap
    tick(1)
    run.refresh_from_db()
    config = gateway_config.build(run)
    assert config.budget.max_cost_usd == pytest.approx(0.12)  # the gateway cap equals the run reservation
    assert config.attempt == 1
    assert config.run_id == str(run.pk)
    assert config.session_id == str(run.pk)
    assert config.model.upstream_model == "vendor/gw-model"
    assert "gw-model" in config.model.presented_names
    assert (config.pricing.input_per_mtok, config.pricing.output_per_mtok) == (1.0, 2.0)
    frugal = preset_content("frugal")
    assert config.budget.max_llm_requests == frugal.run.max_llm_requests
    assert config.budget.max_total_tokens == frugal.run.max_total_tokens
    assert config.call.max_output_tokens == frugal.call.max_output_tokens
    assert config.reasoning.mode == "provider_default"  # the catalog row has no reasoning support: dropped
    provider = config.routing.provider
    assert provider["sort"] == "price"
    assert provider["max_price"] == {"prompt": 1.25, "completion": 2.5}  # model x 1.25
    assert provider["quantizations"] == ["fp8", "bf16", "fp16", "fp32"]
    assert "only" not in provider  # empty lists are left out
    assert config.rate_limit.rpm_per_model == 20
    assert config.rate_limit.bucket_key == f"{tree.study.pk}:vendor/gw-model"
    assert config.sampling.forced == {"temperature": 0.7}
    assert config.retries.retry_on == ["429", "5xx"]
    # The config drives the fake gateway as is.
    with FakeGateway(config) as gw:
        assert gw.state()["cost"] == 0.0


def test_single_shot_uses_the_slot_output_cap(user, system_ep):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    run = tree.runs[0]
    attach_manifest(run)
    run = GenerationRun.objects.select_related("manifest").get(pk=run.pk)
    run.kind = "slot_single_shot"
    assert gateway_config.build(run).call.max_output_tokens == preset_content("frugal").slot.max_output_tokens


def test_local_endpoints_have_no_provider_routing(user, system_ep):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    run = tree.runs[0]
    manifest = attach_manifest(run, cost_basis="free")
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE generation_runmanifest SET manifest = jsonb_set(manifest, '{model,endpoint}', "
            '\'{"id": 7, "kind": "ollama", "served_model_name": "qwen:7b", "cost_basis": "free"}\'::jsonb) '
            "WHERE run_id = %s",
            [str(manifest.pk)],
        )
    run = GenerationRun.objects.select_related("manifest").get(pk=run.pk)
    config = gateway_config.build(run)
    assert config.upstream.kind == "local_proxy"
    assert config.model.upstream_model == "qwen:7b"
    assert config.routing.provider == {}
    assert config.budget.cost_basis == "free"


def test_a_run_without_a_manifest_is_refused(user, system_ep):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    with pytest.raises(ValueError, match="no manifest"):
        gateway_config.build(tree.runs[0])


def test_provider_object_pins():
    content = policy.apply_overrides(
        preset_content("standard"),
        {"routing.only": ["DeepInfra"], "routing.allow_fallbacks": False, "routing.zdr": True},
    )
    provider = gateway_config.provider_object(content)
    assert provider["only"] == ["DeepInfra"]
    assert provider["allow_fallbacks"] is False
    assert provider["zdr"] is True
    assert "sort" not in provider  # provider_sort none


def test_native_fronts_without_a_seed_never_forward_one(user, system_ep):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
    run = tree.runs[0]
    manifest = attach_manifest(run)
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE generation_runmanifest SET manifest = jsonb_set(jsonb_set(manifest, "
            "'{gateway,front_protocol}', '\"anthropic_messages\"'::jsonb), '{sampling,seed}', '42'::jsonb) "
            "WHERE run_id = %s",
            [str(manifest.pk)],
        )
    run = GenerationRun.objects.select_related("manifest").get(pk=run.pk)
    config = gateway_config.build(run)
    assert config.front_protocol == "anthropic_messages"
    assert config.sampling.forward_seed is False
    assert config.sampling.seed is None
