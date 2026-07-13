"""Tests for the layered LLM sampling params (validation, merge, snapshot plumbing)."""

from __future__ import annotations

import pytest

from backend.generation.services.llm_params import SAMPLING_KEYS
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.llm_params import sampling_params
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.profile_resolver import build_resolved_snapshot
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.llm_models.tests.factories import LLMModelFactory

# ── validate_llm_params ───────────────────────────────────────────────


def test_validate_accepts_full_surface():
    params = {
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 40,
        "min_p": 0.05,
        "max_tokens": 16000,
        "frequency_penalty": 0.1,
        "presence_penalty": -0.2,
        "repetition_penalty": 1.1,
        "stop": ["```end"],
        "response_format": {"type": "json_object"},
        "provider": {"order": ["openai"], "allow_fallbacks": False},
        "reasoning": {"effort": "high"},
    }
    assert validate_llm_params(params) == params


def test_validate_strips_none_values():
    assert validate_llm_params({"temperature": 0.5, "top_p": None}) == {"temperature": 0.5}


def test_validate_rejects_unknown_keys():
    with pytest.raises(ValueError, match="Unknown LLM params: max_new_tokens"):
        validate_llm_params({"max_new_tokens": 100})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("temperature", 2.5),
        ("top_p", 1.5),
        ("min_p", -0.1),
        ("frequency_penalty", 3),
        ("presence_penalty", -2.5),
        ("repetition_penalty", 2.5),
        ("top_k", 0),
        ("top_k", 1.5),
        ("max_tokens", 0),
        ("temperature", True),
    ],
)
def test_validate_rejects_out_of_range(key, value):
    with pytest.raises(ValueError, match=key):
        validate_llm_params({key: value})


def test_validate_rejects_bad_stop_sequences():
    with pytest.raises(ValueError, match="stop"):
        validate_llm_params({"stop": ["x"] * 9})
    with pytest.raises(ValueError, match="stop"):
        validate_llm_params({"stop": ["a" * 65]})


def test_validate_rejects_bad_response_format():
    with pytest.raises(ValueError, match="response_format"):
        validate_llm_params({"response_format": {"type": "xml"}})


def test_validate_rejects_unknown_provider_routing_keys():
    with pytest.raises(ValueError, match="provider routing keys: favourite"):
        validate_llm_params({"provider": {"favourite": "openai"}})


def test_validate_rejects_bad_reasoning():
    with pytest.raises(ValueError, match=r"reasoning\.effort"):
        validate_llm_params({"reasoning": {"effort": "extreme"}})
    with pytest.raises(ValueError, match="reasoning"):
        validate_llm_params({"reasoning": {"steps": 5}})


def test_validate_collects_multiple_errors():
    with pytest.raises(ValueError, match=r"temperature.*top_p|top_p.*temperature"):
        validate_llm_params({"temperature": 9, "top_p": 9})


# ── merge_llm_params ──────────────────────────────────────────────────


def test_merge_later_layers_win():
    merged = merge_llm_params(
        {"temperature": 0.3, "max_tokens": 32000},
        {"temperature": 0.9, "top_k": 40},
        {"top_k": 20},
    )
    assert merged == {"temperature": 0.9, "max_tokens": 32000, "top_k": 20}


def test_merge_none_unsets_a_lower_layer():
    assert merge_llm_params({"top_p": 0.9}, {"top_p": None}) == {}


def test_merge_skips_empty_layers():
    assert merge_llm_params(None, {}, {"temperature": 1.0}) == {"temperature": 1.0}


# ── sampling_params ───────────────────────────────────────────────────


def test_sampling_params_extracts_only_forwardable_keys():
    section = {
        "temperature": 0.7,
        "max_tokens": 1000,
        "model_slug": "openai/gpt-4o",
        "context_window": 128000,
        "pricing_snapshot_at": "2026-01-01",
    }
    assert sampling_params(section) == {"temperature": 0.7, "max_tokens": 1000}


def test_sampling_params_drops_v2_provenance_provider_string():
    # Schema-v2 snapshots stored the model's provider name under "provider".
    assert "provider" not in sampling_params({"provider": "openai", "temperature": 0.5})
    routing = {"provider": {"order": ["openai"]}}
    assert sampling_params(routing) == routing


# ── snapshot plumbing ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_snapshot_layers_profile_config_under_run_overrides():
    app = AppRequirementTemplateFactory()
    model = LLMModelFactory()
    profile = GenerationProfileFactory(llm_config={"temperature": 0.9, "top_k": 40})

    snapshot = build_resolved_snapshot(
        app_requirement=app,
        profile=profile,
        scaffolding_slug="flask-react",
        model=model,
        temperature=0.3,
        max_tokens=32000,
        user=None,
        llm_overrides={"top_k": 20, "reasoning": {"effort": "low"}},
    )

    llm = snapshot["llm"]
    assert llm["temperature"] == 0.9  # profile beats legacy base
    assert llm["top_k"] == 20  # run override beats profile
    assert llm["reasoning"] == {"effort": "low"}
    assert llm["max_tokens"] == 32000  # base survives when no layer overrides
    assert llm["model_provider"] == model.provider
    assert snapshot["llm_overrides"] == {"top_k": 20, "reasoning": {"effort": "low"}}
    assert snapshot["bundle_schema_version"] == 3


@pytest.mark.django_db
def test_sampling_params_never_enter_prompt_hash_but_all_enter_fingerprint():
    app = AppRequirementTemplateFactory()
    model = LLMModelFactory()
    profile = GenerationProfileFactory()

    def snap(overrides):
        return build_resolved_snapshot(
            app_requirement=app,
            profile=profile,
            scaffolding_slug="flask-react",
            model=model,
            temperature=0.3,
            max_tokens=32000,
            user=None,
            experiment_seed=42,
            llm_overrides=overrides,
        )

    baseline = snap({})
    for key, value in [
        ("temperature", 1.5),
        ("top_k", 7),
        ("stop", ["END"]),
        ("provider", {"order": ["openai"]}),
        ("reasoning", {"effort": "high"}),
    ]:
        assert key in SAMPLING_KEYS
        varied = snap({key: value})
        assert varied["prompt_hash"] == baseline["prompt_hash"], key
        assert varied["run_fingerprint"] != baseline["run_fingerprint"], key
