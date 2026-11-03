"""The three budget presets (``data/resource_policies/*.yaml``): equal to the 4.2 columns and lint clean."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.generation.budget import policy
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.contracts.resources import TIERS
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.resources import preset_content

DATA = Path(__file__).resolve().parents[2] / "data" / "resource_policies"
REASONING_MODEL = ModelFacts(
    slug="vendor/reasoner",
    family="vendor",
    context_length=200_000,
    max_output=64_000,
    supports_reasoning=True,
    input_per_mtok=1.0,
    output_per_mtok=2.0,
)
HARNESS = policy.HarnessInfo(name="opencode", context_knob="context", min_call_output_tokens=4096, known=True)


@pytest.mark.parametrize("tier", TIERS)
def test_yaml_equals_the_spec_column(tier):
    data = yaml.safe_load((DATA / f"{tier}.yaml").read_text(encoding="utf-8"))
    assert data["tier"] == tier
    assert data["name"] == tier.capitalize()
    assert data["description"]
    content = ResourcePolicyContent.model_validate(data["content"])
    assert content == preset_content(tier)
    assert set(data) == {"name", "description", "tier", "content"}


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize(
    "ctx",
    [
        policy.StudyPolicyContext(),
        policy.StudyPolicyContext(study_kind="confirmatory", n_model_levels=1),
        policy.StudyPolicyContext(mode="slot", executor="single_shot", sampling_policy="explicit", temperature=0.7),
    ],
    ids=["app", "confirmatory", "slot"],
)
def test_presets_pass_the_lint(tier, ctx):
    issues = policy.lint(preset_content(tier), harness=HARNESS, study_ctx=ctx)
    blocking = [i for i in issues if i.severity in ("error", "warn")]
    assert blocking == []


@pytest.mark.parametrize("tier", TIERS)
def test_presets_pass_with_a_model_and_keep_the_cap_ratio(tier):
    effective = policy.effective_policy(preset_content(tier), model=REASONING_MODEL, harness=HARNESS)
    codes = {i.code for i in effective.issues if i.severity in ("error", "warn")}
    assert "context_cap_above_call_cap" not in codes
    assert "reasoning_mode_invalid" not in codes
    assert codes <= {"provider_unpinned"}  # info in exploratory, never blocking
    content = effective.content
    if content.call.max_input_tokens is not None and content.context.max_context_tokens is not None:
        assert content.call.max_input_tokens >= 1.1 * content.context.max_context_tokens


def test_frugal_is_the_cheap_one():
    frugal, standard, generous = (preset_content(t) for t in TIERS)
    assert frugal.run.max_cost_usd < standard.run.max_cost_usd < generous.run.max_cost_usd
    assert frugal.reasoning.effort == "low"
    assert frugal.routing.provider_sort == "price"
    assert frugal.routing.serving_tier == "auto_batch"
    assert frugal.study.on_budget_exhausted == "stop_balanced"


def test_generous_sandbox_is_clamped_on_the_default_machine():
    """WP-01 A1: generous asks for 4g / 4e9 but the default machine allows 3g / 2e9 (a clamp, not an error)."""
    effective = policy.effective_policy(
        preset_content("generous"),
        machine_memory="3g",
        machine_nano_cpus=2_000_000_000,
    )
    assert effective.content.sandbox.memory == "3g"
    assert effective.content.sandbox.nano_cpus == 2_000_000_000
    assert "sandbox_clamped_to_machine" in effective.notes
    assert {c.key for c in effective.clamps} >= {"sandbox.memory", "sandbox.nano_cpus"}


@pytest.mark.parametrize("tier", TIERS)
def test_summary_line(tier):
    line = policy.summary_line(preset_content(tier), tier=tier, version=1)
    assert line.startswith(f"{tier.capitalize()} v1 · $")
    assert line.endswith("no overrides")
