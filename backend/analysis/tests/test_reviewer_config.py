"""Reviewer config: defaults, validation, freezing against the model catalog, cost estimate, UI options."""

from __future__ import annotations

import pytest

from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.reviewer import config
from backend.analysis.engine.reviewer import prompt
from backend.analysis.tests.reviewer_support import frozen_section
from backend.llm_models.tests.factories import LLMModelFactory

DEEPSEEK_PARAMS = [
    "frequency_penalty",
    "include_reasoning",
    "max_tokens",
    "reasoning",
    "reasoning_effort",
    "response_format",
    "seed",
    "structured_outputs",
    "temperature",
    "tool_choice",
    "tools",
    "top_p",
]


def deepseek(**kw):
    defaults = {
        "model_id": "deepseek/deepseek-v4.1-flash",
        "canonical_slug": "deepseek_deepseek-v4.1-flash-20260910",
        "provider": "deepseek",
        "model_name": "DeepSeek: DeepSeek V4.1 Flash",
        "context_window": 1_048_576,
        "max_output_tokens": 943_718,
        "input_price_per_token": 4.9e-08,
        "output_price_per_token": 5.25e-07,
        "supports_function_calling": True,
        "supports_json_mode": True,
        "metadata": {"openrouter_supported_parameters": DEEPSEEK_PARAMS},
        "capabilities_json": {
            "canonical_slug": "deepseek/deepseek-v4.1-flash-20260910",
            "pricing": {"prompt": "0.000000049", "completion": "0.000000525", "input_cache_read": "0.0000000014"},
            "reasoning": {
                "mandatory": False,
                "default_effort": "high",
                "default_enabled": True,
                "supported_efforts": ["max", "high", "low", "none"],
            },
        },
    }
    defaults.update(kw)
    return LLMModelFactory(**defaults)


def errors_of(cfg):
    with pytest.raises(ConfigError) as exc:
        config.validate_reviewer_config(cfg)
    return exc.value.field_errors


# --------------------------------------------------------------------------
# Defaults + validation (no database)
# --------------------------------------------------------------------------


def test_defaults_follow_the_contract():
    cfg = config.default_reviewer_config()
    assert cfg["engine"] == "single_shot"
    assert cfg["model"] == "deepseek/deepseek-v4.1-flash"
    assert cfg["samples"] == 3
    assert cfg["temperature"] == 0.2
    assert cfg["reasoning_effort"] == "low"
    assert cfg["max_output_tokens"] == 32000
    assert cfg["provider"] is None
    assert cfg["prompt_version"] == "review-v1"
    assert cfg["include_origins"] == ["authored", "added", "modified"]
    assert cfg["budget"] == {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900}
    assert config.validate_reviewer_config(cfg) == cfg


def test_default_model_comes_from_settings(settings):
    settings.ANALYSIS_DEFAULT_REVIEW_MODEL = "vendor/other-model"
    assert config.default_reviewer_config()["model"] == "vendor/other-model"


def test_none_means_disabled_and_empty_means_defaults():
    assert config.validate_reviewer_config(None)["enabled"] is False
    assert config.validate_reviewer_config({}) == config.default_reviewer_config()


def test_partial_config_is_merged_and_normalized():
    cfg = config.validate_reviewer_config(
        {
            "samples": 5.0,
            "temperature": 1,
            "reasoning_effort": None,
            "include_origins": ["modified", "added"],
            "budget": {"max_cost_usd": 2},
            "model": "  vendor/model:free ",
        },
    )
    assert cfg["samples"] == 5
    assert isinstance(cfg["samples"], int)
    assert cfg["temperature"] == 1.0
    assert isinstance(cfg["temperature"], float)
    assert cfg["reasoning_effort"] is None
    assert cfg["include_origins"] == ["added", "modified"]  # canonical order
    assert cfg["budget"] == {"max_cost_usd": 2.0, "max_llm_requests": 40, "wall_clock_seconds": 900}
    assert cfg["model"] == "vendor/model:free"


def test_temperature_null_is_provider_default():
    assert config.validate_reviewer_config({"temperature": None})["temperature"] is None


def test_none_for_a_non_nullable_key_takes_the_default():
    assert config.validate_reviewer_config({"samples": None})["samples"] == 3


def test_single_shot_drops_harness_choices():
    cfg = config.validate_reviewer_config({"harness": "opencode", "harness_options": {"x": 1}})
    assert cfg["harness"] is None
    assert cfg["harness_options"] == {}


def test_agent_engine_needs_a_known_harness():
    assert "reviewer.harness" in errors_of({"engine": "agent"})
    assert "reviewer.harness" in errors_of({"engine": "agent", "harness": "cursor"})
    cfg = config.validate_reviewer_config({"engine": "agent", "harness": "opencode", "harness_options": {"a": 1}})
    assert (cfg["harness"], cfg["harness_options"]) == ("opencode", {"a": 1})


@pytest.mark.parametrize(
    ("cfg", "field"),
    [
        ({"bogus": 1}, "reviewer.bogus"),
        ({"enabled": "yes"}, "reviewer.enabled"),
        ({"engine": "magic"}, "reviewer.engine"),
        ({"model": "two words"}, "reviewer.model"),
        ({"model": "a/b/c"}, "reviewer.model"),
        ({"model": 7}, "reviewer.model"),
        ({"samples": 0}, "reviewer.samples"),
        ({"samples": 11}, "reviewer.samples"),
        ({"samples": 2.5}, "reviewer.samples"),
        ({"samples": True}, "reviewer.samples"),
        ({"temperature": 3}, "reviewer.temperature"),
        ({"temperature": "hot"}, "reviewer.temperature"),
        ({"reasoning_effort": "extreme"}, "reviewer.reasoning_effort"),
        ({"max_output_tokens": 10}, "reviewer.max_output_tokens"),
        ({"chunk_char_budget": 100}, "reviewer.chunk_char_budget"),
        ({"seed_base": -1}, "reviewer.seed_base"),
        ({"prompt_version": "review-v0"}, "reviewer.prompt_version"),
        ({"schema_version": "findings-v0"}, "reviewer.schema_version"),
        ({"include_origins": []}, "reviewer.include_origins"),
        ({"include_origins": ["aliens"]}, "reviewer.include_origins"),
        ({"include_kinds": ["lockfile"]}, "reviewer.include_kinds"),
        ({"budget": "lots"}, "reviewer.budget"),
        ({"budget": {"max_cost_usd": 0}}, "reviewer.budget.max_cost_usd"),
        ({"budget": {"max_llm_requests": 5000}}, "reviewer.budget.max_llm_requests"),
        ({"budget": {"wall_clock_seconds": 5}}, "reviewer.budget.wall_clock_seconds"),
        ({"budget": {"tokens": 5}}, "reviewer.budget.tokens"),
        ({"provider": "openai"}, "reviewer.provider"),
        ({"provider": {"order": "x"}}, "reviewer.provider.order"),
        ({"provider": {"allow_fallbacks": "no"}}, "reviewer.provider.allow_fallbacks"),
        ({"provider": {"sort": "vibes"}}, "reviewer.provider.sort"),
        ({"provider": {"data_collection": "maybe"}}, "reviewer.provider.data_collection"),
        ({"provider": {"teleport": True}}, "reviewer.provider.teleport"),
        ({"harness_options": []}, "reviewer.harness_options"),
    ],
)
def test_invalid_fields_are_reported_with_paths(cfg, field):
    assert field in errors_of(cfg)


def test_all_errors_are_reported_at_once():
    errors = errors_of({"samples": 0, "temperature": 9, "engine": "x"})
    assert {"reviewer.samples", "reviewer.temperature", "reviewer.engine"} <= set(errors)


def test_non_dict_config():
    assert errors_of(["x"]) == {"reviewer": "must be an object"}


def test_valid_provider_preferences_pass():
    provider = {
        "order": ["parasail", "deepinfra"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "quantizations": ["fp8"],
        "sort": "price",
        "data_collection": "deny",
        "max_price": {"prompt": 1},
    }
    assert config.validate_reviewer_config({"provider": provider})["provider"] == provider


# --------------------------------------------------------------------------
# Freezing (catalog lookup)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_freeze_resolves_the_default_model():
    model = deepseek()
    frozen = config.freeze_reviewer(config.default_reviewer_config())
    assert frozen["enabled"] is True
    assert frozen["required"] is True
    assert frozen["tool_slug"] == "ai_review"
    assert frozen["engine"] == "single_shot"
    assert frozen["harness"] is None
    assert frozen["model"]["slug"] == "deepseek/deepseek-v4.1-flash"
    assert frozen["model"]["id"] == model.id
    assert frozen["model"]["canonical_slug"] == "deepseek_deepseek-v4.1-flash-20260910"
    assert frozen["model"]["openrouter_canonical_slug"] == "deepseek/deepseek-v4.1-flash-20260910"
    assert frozen["model"]["context_window"] == 1_048_576
    assert frozen["model"]["pricing"]["input_per_token"] == 4.9e-08
    assert frozen["model"]["pricing"]["output_per_token"] == 5.25e-07
    assert frozen["model"]["pricing"]["cache_read_per_token"] == pytest.approx(1.4e-09)
    assert frozen["model"]["pricing"]["snapshot_at"]
    supports = frozen["model"]["supports"]
    assert supports["structured_outputs"] is True
    assert supports["response_format"] is True
    assert supports["tools"] is True
    assert supports["reasoning_efforts"] == ["max", "high", "low", "none"]
    assert frozen["params"] == {
        "temperature": 0.2,
        "max_tokens": 32000,
        "reasoning": {"effort": "low"},
        "seed_base": 1,
        "seed_forwarded": True,
        "dropped": {},
    }
    assert frozen["provider"]["require_parameters"] is True
    assert frozen["provider_routing_source"] == "default"
    assert frozen["output_mode"] == "json_schema"
    assert frozen["samples"] == 3
    assert frozen["quorum"] == 2
    assert frozen["prompt_sha256"] == prompt.prompt_sha256()
    assert frozen["schema_sha256"] == prompt.schema_sha256()
    assert frozen["chunk_char_budget"] == 300_000
    assert frozen["chunking"]["effective_chars"] == 300_000
    assert frozen["budget"] == {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900}
    assert frozen["engine_version"] == config.REVIEWER_ENGINE_VERSION
    # The contract's frozen keys (§4.2) are all present.
    for key in (
        "harness_options",
        "harness_version",
        "harness_images",
        "params",
        "provider",
        "samples",
        "prompt_version",
        "schema_version",
        "include_origins",
        "budget",
    ):
        assert key in frozen


@pytest.mark.django_db
def test_freeze_disabled_is_none():
    assert config.freeze_reviewer({"enabled": False}) is None
    assert config.freeze_reviewer(None) is None


@pytest.mark.django_db
def test_freeze_rejects_unknown_models():
    with pytest.raises(ConfigError) as exc:
        config.freeze_reviewer({"model": "vendor/does-not-exist"})
    assert "reviewer.model" in exc.value.field_errors


@pytest.mark.django_db
def test_freeze_finds_a_model_by_canonical_slug():
    deepseek()
    frozen = config.freeze_reviewer({"model": "deepseek_deepseek-v4.1-flash-20260910"})
    assert frozen["model"]["slug"] == "deepseek/deepseek-v4.1-flash"


@pytest.mark.django_db
def test_freeze_without_structured_outputs_uses_json_object():
    LLMModelFactory(model_id="v/json-only", metadata={"openrouter_supported_parameters": ["response_format", "seed"]})
    frozen = config.freeze_reviewer({"model": "v/json-only", "reasoning_effort": None})
    assert frozen["output_mode"] == "json_object"
    # Temperature is not in the model's parameters: dropped and recorded, never sent.
    assert frozen["params"]["temperature"] is None
    assert frozen["params"]["dropped"] == {"temperature": "not supported by this model on OpenRouter"}
    assert any("json_object" in n for n in frozen["notes"])


@pytest.mark.django_db
def test_freeze_without_response_format_uses_prompt_mode():
    LLMModelFactory(
        model_id="v/plain",
        supports_json_mode=False,
        metadata={"openrouter_supported_parameters": ["temperature", "max_tokens"]},
    )
    frozen = config.freeze_reviewer({"model": "v/plain"})
    assert frozen["output_mode"] == "prompt"
    assert frozen["params"]["seed_forwarded"] is False
    assert frozen["params"]["reasoning"] is None
    assert frozen["params"]["dropped"]["reasoning_effort"] == "model does not support reasoning on OpenRouter"


@pytest.mark.django_db
def test_freeze_unknown_parameter_support_keeps_temperature():
    LLMModelFactory(model_id="v/unknown", supports_json_mode=True, metadata={})
    frozen = config.freeze_reviewer({"model": "v/unknown", "reasoning_effort": None})
    assert frozen["params"]["temperature"] == 0.2
    assert frozen["output_mode"] == "json_object"
    assert frozen["model"]["supports"]["parameters_known"] is False


@pytest.mark.django_db
def test_freeze_rejects_an_effort_the_model_does_not_offer():
    deepseek()
    with pytest.raises(ConfigError) as exc:
        config.freeze_reviewer({"reasoning_effort": "medium"})
    assert "reviewer.reasoning_effort" in exc.value.field_errors


@pytest.mark.django_db
def test_freeze_model_default_reasoning_is_recorded():
    deepseek()
    frozen = config.freeze_reviewer({"reasoning_effort": None})
    assert frozen["params"]["reasoning"] is None
    assert any("model default (effort high)" in n for n in frozen["notes"])


@pytest.mark.django_db
def test_freeze_caps_output_tokens_and_limits_chunks_by_context():
    LLMModelFactory(
        model_id="v/small",
        context_window=32_000,
        max_output_tokens=8_000,
        metadata={"openrouter_supported_parameters": ["structured_outputs", "temperature"]},
    )
    frozen = config.freeze_reviewer({"model": "v/small", "reasoning_effort": None})
    assert frozen["params"]["max_tokens"] == 8_000
    expected = int((32_000 - 8_000 - config.CONTEXT_SAFETY_TOKENS) * config.CHARS_PER_TOKEN_SAFE) - (
        prompt.overhead_chars()
    )
    assert frozen["chunking"]["effective_chars"] == expected
    assert any("capped" in n for n in frozen["notes"])
    assert any("limited" in n for n in frozen["notes"])


@pytest.mark.django_db
def test_freeze_rejects_a_context_too_small_for_the_output_reservation():
    LLMModelFactory(model_id="v/tiny", context_window=8_000, max_output_tokens=0)
    with pytest.raises(ConfigError) as exc:
        config.freeze_reviewer({"model": "v/tiny"})
    assert "reviewer.max_output_tokens" in exc.value.field_errors


@pytest.mark.django_db
def test_freeze_without_a_context_window_uses_the_fallback():
    LLMModelFactory(model_id="v/nocontext", context_window=0, metadata={})
    frozen = config.freeze_reviewer({"model": "v/nocontext", "reasoning_effort": None})
    assert frozen["model"]["context_window"] == config.FALLBACK_CONTEXT_WINDOW
    assert frozen["model"]["context_window_source"] == "fallback"
    assert any("no context window" in n for n in frozen["notes"])


@pytest.mark.django_db
def test_freeze_explicit_provider_routing():
    deepseek()
    frozen = config.freeze_reviewer({"provider": {"only": ["parasail"]}})
    assert frozen["provider"] == {"only": ["parasail"], "require_parameters": True}
    assert frozen["provider_routing_source"] == "explicit"
    frozen = config.freeze_reviewer({"provider": {"only": ["parasail"], "require_parameters": False}})
    assert frozen["provider"]["require_parameters"] is False


@pytest.mark.django_db
def test_freeze_floating_alias_is_noted():
    LLMModelFactory(model_id="~deepseek/deepseek-flash-latest", metadata={"openrouter_supported_parameters": ["seed"]})
    frozen = config.freeze_reviewer({"model": "~deepseek/deepseek-flash-latest", "reasoning_effort": None})
    assert any("floating" in n for n in frozen["notes"])


@pytest.mark.django_db
def test_freeze_is_stable_apart_from_the_pricing_timestamp():
    deepseek()
    a = config.freeze_reviewer({})
    b = config.freeze_reviewer({})
    a["model"]["pricing"].pop("snapshot_at")
    b["model"]["pricing"].pop("snapshot_at")
    assert a == b


@pytest.mark.django_db
def test_freeze_agent_engine_uses_the_harness_section(monkeypatch):
    from backend.analysis.engine.reviewer import agentic

    deepseek()
    calls = []

    def freeze_harness(harness, options, *, model, max_output_tokens=None, budget=None):
        calls.append((harness, options, model.model_id, max_output_tokens, budget["max_llm_requests"]))
        return {"version": "1.18.35", "images": {"harness": "llmlab/agent-opencode:abc"}, "options": {"steps": 30}}

    monkeypatch.setattr(agentic, "freeze_harness", freeze_harness)
    frozen = config.freeze_reviewer({"engine": "agent", "harness": "opencode", "harness_options": {"steps": 30}})
    assert calls == [("opencode", {"steps": 30}, "deepseek/deepseek-v4.1-flash", 32000, 40)]
    assert frozen["harness"] == "opencode"
    assert frozen["harness_version"] == "1.18.35"
    assert frozen["harness_images"] == {"harness": "llmlab/agent-opencode:abc"}
    assert frozen["harness_options"] == {"steps": 30}
    assert frozen["agent"]["version"] == "1.18.35"


@pytest.mark.django_db
def test_freeze_agent_harness_errors_become_config_errors(monkeypatch):
    from backend.analysis.engine.reviewer import agentic

    deepseek()

    def freeze_harness(harness, options, *, model, **kw):
        msg = "steps must be an integer"
        raise ValueError(msg)

    monkeypatch.setattr(agentic, "freeze_harness", freeze_harness)
    with pytest.raises(ConfigError) as exc:
        config.freeze_reviewer({"engine": "agent", "harness": "opencode"})
    assert exc.value.field_errors == {"reviewer.harness_options": "steps must be an integer"}


# --------------------------------------------------------------------------
# Cost estimate
# --------------------------------------------------------------------------


def test_estimate_cost_single_shot_matches_the_documented_method():
    frozen = frozen_section(samples=3)
    est = config.estimate_cost(frozen, chars=100_000, files=20)
    rendered = 100_000 * 1.15 + 20 * 90
    overhead = prompt.overhead_chars()
    tokens_in = (rendered + overhead) / 3.5
    tokens_out = config.EXPECTED_ANSWER_TOKENS + config.REASONING_TOKENS["low"]  # one call
    per_sample = (tokens_in * 4.9e-08 + tokens_out * 5.25e-07) * config.ROUTED_PRICE_FACTOR
    assert est["calls"] == 3
    assert est["samples"] == 3
    assert est["expected"] == pytest.approx(per_sample * 3, rel=1e-4)
    assert est["tokens"]["input"] == round(tokens_in * 3)
    assert est["tokens"]["output"] == round(tokens_out * 3)
    assert est["low"] < est["expected"] < est["high"]
    assert est["method"]


def test_estimate_cost_chunks_by_effective_chars():
    frozen = frozen_section(samples=1, chunking={"effective_chars": 50_000})
    assert config.estimate_cost(frozen, chars=100_000, files=10)["calls"] == 3


def test_estimate_cost_high_is_capped_by_the_replicate_budget():
    frozen = frozen_section(
        samples=2,
        budget={"max_cost_usd": 0.001, "max_llm_requests": 40, "wall_clock_seconds": 900},
    )
    est = config.estimate_cost(frozen, chars=2_000_000, files=100)
    assert est["high"] == pytest.approx(0.002)
    assert est["per_sample"]["high"] == pytest.approx(0.001)


def test_review_inputs_match_what_the_engines_send():
    from backend.analysis.tests.reviewer_support import APP_JSX
    from backend.analysis.tests.reviewer_support import APP_PY
    from backend.analysis.tests.reviewer_support import app_sample
    from backend.analysis.tests.reviewer_support import make_file
    from backend.analysis.tests.reviewer_support import make_sample

    assert config.review_inputs(frozen_section(), app_sample()) == (len(APP_PY) + len(APP_JSX), 2)
    sample = make_sample([make_file("a.bin", b"\xff\x00"), make_file("e.py", "\n"), make_file("ok.py", "x = 1\n")])
    assert config.review_inputs(frozen_section(), sample) == (6, 1)
    assert config.review_inputs(frozen_section(include_origins=["scaffold"]), app_sample()) == (
        len("print('hi')\n"),
        1,
    )


def test_estimate_cost_nothing_to_review():
    est = config.estimate_cost(frozen_section(), chars=0, files=0)
    assert (est["low"], est["expected"], est["high"], est["calls"]) == (0.0, 0.0, 0.0, 0)


def test_estimate_cost_reasoning_effort_matters():
    low = config.estimate_cost(frozen_section(), chars=50_000, files=5)
    frozen = frozen_section()
    frozen["params"] = {**frozen["params"], "reasoning": None}  # model default: effort high
    high = config.estimate_cost(frozen, chars=50_000, files=5)
    assert high["expected"] > low["expected"]


def test_estimate_cost_agent_engine_is_bounded_by_budget():
    frozen = frozen_section(engine="agent", samples=1)
    est = config.estimate_cost(frozen, chars=100_000, files=20)
    assert 0 < est["low"] < est["expected"] <= est["high"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# UI options
# --------------------------------------------------------------------------


def test_reviewer_options(monkeypatch):
    from backend.analysis.engine.reviewer import agentic

    harnesses = [{"name": "opencode", "label": "OpenCode", "option_schema": {}, "default_options": {}}]
    monkeypatch.setattr(agentic, "harness_review_options", lambda: harnesses)
    opts = config.reviewer_options()
    assert [e["name"] for e in opts["engines"]] == ["single_shot", "agent"]
    assert opts["harnesses"] == harnesses
    assert opts["default_model"] == "deepseek/deepseek-v4.1-flash"
    assert opts["defaults"] == config.default_reviewer_config()
    assert opts["prompt_versions"][0]["sha256"] == prompt.prompt_sha256()
    assert opts["limits"]["samples"] == {"type": "int", "min": 1, "max": 10}
    assert opts["limits"]["budget"]["max_cost_usd"]["type"] == "float"
    assert "low" in opts["reasoning_efforts"]
    assert opts["include_origins"]["default"] == ["authored", "added", "modified"]


# --------------------------------------------------------------------------
# Reasoning effort vocabulary (xhigh), per-model validation
# --------------------------------------------------------------------------


def reasoning_model(model_id, reasoning, params=("reasoning", "structured_outputs", "temperature"), **kw):
    return LLMModelFactory(
        model_id=model_id,
        metadata={"openrouter_supported_parameters": list(params)},
        capabilities_json={"reasoning": reasoning},
        **kw,
    )


def test_reasoning_vocabulary_has_xhigh_in_ascending_order():
    assert config.REASONING_EFFORTS == ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    assert config.validate_reviewer_config({"reasoning_effort": "xhigh"})["reasoning_effort"] == "xhigh"
    assert "reviewer.reasoning_effort" in errors_of({"reasoning_effort": "ultra"})
    assert config.REASONING_TOKENS["high"] < config.REASONING_TOKENS["xhigh"] < config.REASONING_TOKENS["max"]


@pytest.mark.django_db
def test_freeze_accepts_xhigh_only_when_the_model_lists_it():
    deepseek()  # lists max/high/low/none, not xhigh
    with pytest.raises(ConfigError) as exc:
        config.freeze_reviewer({"reasoning_effort": "xhigh"})
    assert exc.value.field_errors["reviewer.reasoning_effort"] == (
        "deepseek/deepseek-v4.1-flash offers reasoning efforts none, low, high, max (or null for the model default)"
    )
    reasoning_model("v/xhigh", {"mandatory": False, "default_effort": "medium", "supported_efforts": ["xhigh", "low"]})
    frozen = config.freeze_reviewer({"model": "v/xhigh", "reasoning_effort": "xhigh"})
    assert frozen["params"]["reasoning"] == {"effort": "xhigh"}
    assert not any("unverified" in n for n in frozen["notes"])
    with pytest.raises(ConfigError):
        config.freeze_reviewer({"model": "v/xhigh", "reasoning_effort": "max"})


@pytest.mark.django_db
def test_freeze_rejects_none_when_reasoning_is_mandatory():
    # Regression: "none" passed freeze for a model that cannot stop reasoning (no effort list in the catalog).
    reasoning_model("v/always", {"mandatory": True, "default_effort": "high"})
    with pytest.raises(ConfigError) as exc:
        config.freeze_reviewer({"model": "v/always", "reasoning_effort": "none"})
    assert exc.value.field_errors["reviewer.reasoning_effort"].startswith(
        "v/always offers reasoning efforts minimal, low, medium, high, xhigh, max",
    )
    frozen = config.freeze_reviewer({"model": "v/always", "reasoning_effort": "high"})
    assert frozen["params"]["reasoning"] == {"effort": "high"}
    # No effort list in the catalog: sent as requested, and the plan says so.
    assert any("effort high is sent as requested and its effect is unverified" in n for n in frozen["notes"])
    assert frozen["model"]["supports"]["reasoning_mandatory"] is True


@pytest.mark.django_db
def test_freeze_records_reasoning_off_by_default_and_the_estimate_follows():
    reasoning_model("v/opt-in", {"mandatory": False, "default_enabled": False, "default_effort": "medium"})
    frozen = config.freeze_reviewer({"model": "v/opt-in", "reasoning_effort": None})
    assert frozen["params"]["reasoning"] is None
    assert frozen["model"]["supports"]["reasoning_default_enabled"] is False
    assert "reasoning off by default for this model (no effort requested)" in frozen["notes"]
    # Before: estimated at the default effort (medium) although the model would not reason.
    assert config._reasoning_tokens(frozen) == 0
    on = {**frozen, "model": {**frozen["model"], "supports": {**frozen["model"]["supports"]}}}
    on["model"]["supports"]["reasoning_default_enabled"] = True
    assert config._reasoning_tokens(on) == config.REASONING_TOKENS["medium"]


# --------------------------------------------------------------------------
# max_replacements
# --------------------------------------------------------------------------


def test_max_replacements_defaults_to_null_meaning_samples():
    assert config.default_reviewer_config()["max_replacements"] is None
    assert config.validate_reviewer_config({})["max_replacements"] is None
    assert config.validate_reviewer_config({"max_replacements": 0})["max_replacements"] == 0
    assert config.validate_reviewer_config({"max_replacements": 4.0})["max_replacements"] == 4


@pytest.mark.parametrize("value", [-1, 21, 1.5, True, "2", [1]])
def test_max_replacements_must_be_a_small_non_negative_integer(value):
    assert "reviewer.max_replacements" in errors_of({"max_replacements": value})


@pytest.mark.django_db
def test_freeze_resolves_max_replacements():
    deepseek()
    assert config.freeze_reviewer({"samples": 5})["max_replacements"] == 5
    assert config.freeze_reviewer({"samples": 5, "max_replacements": None})["max_replacements"] == 5
    assert config.freeze_reviewer({"samples": 5, "max_replacements": 0})["max_replacements"] == 0
    assert config.freeze_reviewer({"samples": 3, "max_replacements": 7})["max_replacements"] == 7


def test_estimate_cost_high_covers_every_replacement():
    plain = config.estimate_cost(frozen_section(samples=3), chars=50_000, files=5)
    replaced = config.estimate_cost(frozen_section(samples=3, max_replacements=2), chars=50_000, files=5)
    assert plain["max_samples"] == 3  # a plan frozen without the key runs no replacements
    assert replaced["max_samples"] == 5
    assert replaced["high"] == pytest.approx(replaced["per_sample"]["high"] * 5, rel=1e-4)
    assert (replaced["low"], replaced["expected"]) == (plain["low"], plain["expected"])
    assert config.estimate_cost(frozen_section(max_replacements=1), chars=0, files=0)["max_samples"] == 4


def test_reviewer_options_offer_xhigh_and_the_replacement_limit():
    opts = config.reviewer_options()
    assert opts["reasoning_efforts"] == list(config.REASONING_EFFORTS)
    assert opts["limits"]["max_replacements"] == {"type": "int", "min": 0, "max": 20}
    assert opts["defaults"]["max_replacements"] is None


# --------------------------------------------------------------------------
# model_capabilities (public, for the API's capabilities endpoint)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_model_capabilities_of_a_catalog_model():
    model = deepseek()
    caps = config.model_capabilities("deepseek/deepseek-v4.1-flash")
    assert caps == {
        "in_catalog": True,
        "requested": "deepseek/deepseek-v4.1-flash",
        "model_id": "deepseek/deepseek-v4.1-flash",
        "canonical_slug": "deepseek_deepseek-v4.1-flash-20260910",
        "name": "DeepSeek: DeepSeek V4.1 Flash",
        "provider": "deepseek",
        "context_window": 1_048_576,
        "max_output_tokens": 943_718,
        "structured_outputs": True,
        "response_format": True,
        "tools": True,
        "seed": True,
        "temperature": True,
        "parameters_known": True,
        "output_mode": "json_schema",
        "reasoning": {
            "supported": True,
            "supported_efforts": ["none", "low", "high", "max"],  # vocabulary order, not the catalog's
            "effort_choices": ["none", "low", "high", "max"],
            "efforts_known": True,
            "default_effort": "high",
            "default_enabled": True,
            "mandatory": False,
        },
        "pricing": {
            "input_per_token": 4.9e-08,
            "output_per_token": 5.25e-07,
            "cache_read_per_token": pytest.approx(1.4e-09),
            "is_free": False,
        },
    }
    # Canonical slugs and catalog rows resolve to the same answer.
    assert config.model_capabilities("deepseek_deepseek-v4.1-flash-20260910")["model_id"] == model.model_id
    assert config.model_capabilities(model)["reasoning"] == caps["reasoning"]


@pytest.mark.django_db
def test_model_capabilities_effort_choices_are_what_freeze_accepts():
    reasoning_model("v/always", {"mandatory": True, "default_effort": "high"})
    reasoning_model("v/odd", {"mandatory": False, "supported_efforts": ["future", "xhigh", "minimal"]})
    LLMModelFactory(model_id="v/plain", metadata={"openrouter_supported_parameters": ["temperature"]})
    always = config.model_capabilities("v/always")["reasoning"]
    assert always["efforts_known"] is False
    assert always["effort_choices"] == ["minimal", "low", "medium", "high", "xhigh", "max"]
    odd = config.model_capabilities("v/odd")["reasoning"]
    assert odd["supported_efforts"] == ["minimal", "xhigh", "future"]  # unknown tokens kept as data, last
    assert odd["effort_choices"] == ["minimal", "xhigh"]
    plain = config.model_capabilities("v/plain")
    assert plain["reasoning"]["supported"] is False
    assert plain["reasoning"]["effort_choices"] == []
    assert plain["reasoning"]["default_effort"] is None
    assert plain["output_mode"] == "prompt"
    for slug, effort in (("v/always", "none"), ("v/odd", "low")):
        with pytest.raises(ConfigError):
            config.freeze_reviewer({"model": slug, "reasoning_effort": effort})
    for slug, choices in (("v/always", always["effort_choices"]), ("v/odd", odd["effort_choices"])):
        for effort in choices:
            assert config.freeze_reviewer({"model": slug, "reasoning_effort": effort})["params"]["reasoning"] == {
                "effort": effort,
            }


@pytest.mark.django_db
def test_model_capabilities_unknown_is_none_never_false():
    LLMModelFactory(model_id="v/no-limits", context_window=0, max_output_tokens=0)
    caps = config.model_capabilities("  vendor/missing  ")
    assert caps["in_catalog"] is False
    assert caps["requested"] == "vendor/missing"
    for key in ("model_id", "context_window", "structured_outputs", "tools", "output_mode", "pricing"):
        assert caps[key] is None
    assert caps["reasoning"]["supported"] is None
    assert caps["reasoning"]["effort_choices"] == []
    assert config.model_capabilities("")["in_catalog"] is False
    # 0 in the catalog means unknown, not a zero-token model.
    limits = config.model_capabilities("v/no-limits")
    assert (limits["context_window"], limits["max_output_tokens"]) == (None, None)
