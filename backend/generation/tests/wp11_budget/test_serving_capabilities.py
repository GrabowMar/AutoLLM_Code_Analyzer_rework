"""E2E-15: endpoint models take their limits and reasoning from the serving provider's catalogue record,
and ``slot_output_cap_tight`` reserves unbounded reasoning for a reasoning model in single-shot slot calls.
"""

from __future__ import annotations

import pytest

from backend.generation.budget import constants as k
from backend.generation.budget import policy
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.budget.model_facts import model_facts
from backend.generation.budget.model_facts import serving_record_facts
from backend.generation.contracts.resources import preset_content

# The models.dev ``opencode-go`` record kept on dev1's local-opencode-go/qwen3.8-flash (trimmed).
QWEN_RECORD = {
    "name": "Qwen3.8 Flash",
    "limit": {"output": 131072, "context": 1000000},
    "reasoning_options": [
        {"type": "toggle"},
        {"type": "effort", "values": ["low", "medium", "xhigh"]},
        {"type": "budget_tokens"},
    ],
}


def _twin(record):
    link = {"model_id": "qwen/qwen3.8-flash", "source": "models.dev:opencode-go", "provider_record": record}
    return {"catalog_twin": link}


def test_serving_record_facts():
    facts = serving_record_facts(_twin(QWEN_RECORD))
    assert facts["source"] == "models.dev:opencode-go"
    assert (facts["context_length"], facts["max_output"]) == (1_000_000, 131_072)
    assert facts["supports_reasoning"] is True
    assert facts["reasoning_options"][1]["values"] == ["low", "medium", "xhigh"]
    # An empty option list: no reasoning knob; no record at all: nothing is claimed.
    assert serving_record_facts(_twin({"limit": {}, "reasoning_options": []}))["supports_reasoning"] is False
    assert "supports_reasoning" not in serving_record_facts(_twin({"limit": {"context": 0}}))
    assert "context_length" not in serving_record_facts(_twin({"limit": {"context": 0}}))
    assert serving_record_facts({}) == {}
    assert serving_record_facts({"catalog_twin": {"model_id": "x/y"}}) == {}


def _endpoint_model(user, *, metadata, **fields):
    from backend.inference.models import LLMEndpoint
    from backend.llm_models.models import LLMModel

    endpoint = LLMEndpoint.objects.create(
        slug="opencode-e2f3",
        name="OpenCode Go",
        kind="ollama",
        connection="relay",
        api_flavor="native",
        max_concurrency=2,
        owner=user,
    )
    return LLMModel.objects.create(
        model_id="local-opencode-e2f3/qwen3.8-flash",
        canonical_slug="local-opencode-e2f3_qwen3.8-flash",
        provider=endpoint.slug,
        model_name="qwen3.8-flash",
        endpoint=endpoint,
        served_model_name="qwen3.8-flash",
        metadata=metadata,
        **fields,
    )


@pytest.mark.django_db
def test_endpoint_model_without_metadata_uses_the_serving_record(user):
    row = _endpoint_model(user, metadata=_twin(QWEN_RECORD))
    facts = model_facts(row)
    assert facts.is_local
    assert (facts.context_length, facts.max_output) == (1_000_000, 131_072)
    assert facts.reasoning_model
    # The gateway's reasoning control is not honoured by the provider: the policy still clamps it.
    assert not facts.supports_reasoning
    assert facts.extra["capability_source"] == "models.dev:opencode-go"
    effective = policy.effective_policy(preset_content("frugal"), model=facts)
    assert effective.content.reasoning.mode == "provider_default"


@pytest.mark.django_db
def test_the_rows_own_values_win(user):
    row = _endpoint_model(user, metadata=_twin(QWEN_RECORD), context_window=32_000, max_output_tokens=8_000)
    facts = model_facts(row)
    assert (facts.context_length, facts.max_output) == (32_000, 8_000)


def _slot_ctx(original):
    return policy.StudyPolicyContext(
        mode="slot",
        executor="single_shot",
        slot_original_tokens=original,
        temperature=0.7,
    )


def _tight(model, original=500):
    issues = policy.lint(preset_content("frugal"), model=model, study_ctx=_slot_ctx(original))
    return [i for i in issues if i.code == "slot_output_cap_tight"]


def test_slot_output_cap_tight_reserves_unbounded_reasoning_for_a_reasoning_model():
    cap = preset_content("frugal").slot.max_output_tokens
    assert cap < k.SLOT_OUTPUT_FACTOR * 500 + k.R_UNBOUNDED_REASONING  # the frugal 4096 cap is tight
    plain = ModelFacts(slug="vendor/plain", endpoint_kind="openai_compat")
    reasoning = ModelFacts(slug="local-opencode-go/qwen3.8-flash", endpoint_kind="openai_compat", reasoning_model=True)
    controlled = ModelFacts(slug="vendor/r", supports_reasoning=True)
    assert _tight(plain) == []  # 1.6 x 500 + the policy's r_avg fits
    (issue,) = _tight(reasoning)
    assert "reasoning model" in issue.message
    assert f"reasoning ({k.R_UNBOUNDED_REASONING})" in issue.message
    assert _tight(controlled)  # single-shot sends no reasoning parameter either way
    # A cap with room for the reasoning headroom is not tight.
    roomy = policy.apply_overrides(preset_content("frugal"), {"slot.max_output_tokens": 16_000})
    issues = policy.lint(roomy, model=reasoning, study_ctx=_slot_ctx(500))
    assert "slot_output_cap_tight" not in {i.code for i in issues}
