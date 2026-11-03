"""``budget.policy.resolve_study_policies``: one call per study gives every condition its effective policy with
the full study context, one serving tier and the cross-condition lint (the wave-3 resolver integration)."""

from __future__ import annotations

from backend.generation.budget import policy
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.contracts.resources import preset_content
from backend.generation.testing.content import design_content

BATCH = ModelFacts(slug="vendor/b", supports_batch=True, input_per_mtok=1, output_per_mtok=1)
NO_BATCH = ModelFacts(slug="vendor/n", supports_batch=False, input_per_mtok=1, output_per_mtok=1)
LOCAL = ModelFacts(slug="local/q", endpoint_kind="ollama", cost_basis="free")


def _codes(issues) -> set[str]:
    return {i.code for i in issues}


def test_one_serving_tier_for_the_whole_study(settings):
    settings.BATCH_BACKENDS_VERIFIED = True
    design = design_content(mode="slot")
    frugal = preset_content("frugal")
    assert frugal.routing.serving_tier == "auto_batch"
    only_batch = policy.resolve_study_policies(
        design,
        [policy.ConditionInput(0, frugal, model=BATCH, executor="single_shot")],
    )
    assert only_batch.serving_tier == "batch"
    assert only_batch.by_condition[0].serving_tier == "batch"
    # One condition cannot batch: every auto_batch condition falls back to standard, so the tiers never mix.
    mixed = policy.resolve_study_policies(
        design,
        [
            policy.ConditionInput(0, frugal, model=BATCH, executor="single_shot"),
            policy.ConditionInput(1, frugal, model=NO_BATCH, executor="single_shot"),
        ],
    )
    assert (mixed.serving_tier, mixed.tier_notes) == ("standard", ["batch_unsupported"])
    assert {p.serving_tier for p in mixed.by_condition.values()} == {"standard"}
    assert "mixed_serving_tier" not in _codes(mixed.issues)


def test_explicit_tiers_are_kept_and_the_mix_is_reported():
    design = design_content(mode="slot")
    flex = policy.apply_overrides(preset_content("frugal"), {"routing.serving_tier": "flex"})
    standard = policy.apply_overrides(preset_content("frugal"), {"routing.serving_tier": "standard"})
    out = policy.resolve_study_policies(
        design,
        [
            policy.ConditionInput(0, flex, model=NO_BATCH, executor="single_shot"),
            policy.ConditionInput(1, standard, model=NO_BATCH, executor="single_shot"),
        ],
    )
    assert {i: p.serving_tier for i, p in out.by_condition.items()} == {0: "flex", 1: "standard"}
    assert "mixed_serving_tier" in _codes(out.issues)


def test_study_lint_and_study_facts():
    design = design_content(kind="confirmatory")
    small = policy.apply_overrides(preset_content("frugal"), {"sandbox.memory": "1g"})
    out = policy.resolve_study_policies(
        design,
        [
            policy.ConditionInput(0, preset_content("frugal"), model=NO_BATCH, stack="flask-react"),
            policy.ConditionInput(1, small, model=LOCAL, stack="express-vue", cells=["c1", "c3"]),
        ],
        brief_tokens=70_000,
    )
    vary = next(i for i in out.issues if i.code == "resources_vary_by_stack")
    assert vary.severity == "error"
    assert set(vary.cells) == {"c0", "c1", "c3"}
    # Token estimates reach the per-condition lint; a non-billed endpoint makes the study cap list-priced.
    assert all("brief_exceeds_call_cap" in _codes(p.issues) for p in out.by_condition.values())
    assert out.context.any_non_billed_endpoint
    assert {p.cap_basis for p in out.by_condition.values()} == {"list"}
    assert all(p.serving_tier == "standard" for p in out.by_condition.values())


def test_stack_minimum_is_applied_and_identical_inputs_share_a_resolution():
    design = design_content()
    out = policy.resolve_study_policies(
        design,
        [
            policy.ConditionInput(0, preset_content("frugal"), model=NO_BATCH),
            policy.ConditionInput(1, preset_content("frugal"), model=NO_BATCH),
        ],
        stack_min_memory="6g",
        machine_memory="4g",
    )
    assert out.by_condition[0] is out.by_condition[1]
    assert out.by_condition[0].content.sandbox.memory == "4g"
    assert "stack_minimum_exceeds_machine" in out.by_condition[0].notes
    assert out.issues == []
