"""Effective policy (4.3): overrides, auto resolution, every clamp, the treatment view, and each lint rule."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.generation import identity
from backend.generation.budget import policy
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.budget.model_facts import is_model_alias
from backend.generation.budget.model_facts import model_facts
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import preset_content

MODEL = ModelFacts(
    slug="vendor/m1",
    family="vendor",
    context_length=100_000,
    max_output=10_000,
    supports_reasoning=True,
    supports_tools=True,
    input_per_mtok=2.0,
    output_per_mtok=8.0,
)
HARNESS = policy.HarnessInfo(name="opencode", context_knob="context", min_call_output_tokens=4096, known=True)


def _codes(issues) -> set[str]:
    return {i.code for i in issues}


class TestOverrides:
    def test_dotted_and_nested_overrides(self):
        content = policy.apply_overrides(
            preset_content("frugal"),
            {"run.max_cost_usd": 0.5, "pilot.gates.min_build_rate": 0.4, "concurrency": {"per_model": 3}},
        )
        assert content.run.max_cost_usd == 0.5
        assert content.pilot.gates.min_build_rate == 0.4
        assert content.concurrency.per_model == 3
        assert content.concurrency.study_max_parallel == 2  # untouched siblings stay

    def test_reasoning_group_knob(self):
        content = policy.apply_overrides(
            preset_content("frugal"),
            {"reasoning": {"mode": "max_tokens", "effort": None, "max_tokens": 2048}},
        )
        assert (content.reasoning.mode, content.reasoning.max_tokens, content.reasoning.exclude) == (
            "max_tokens",
            2048,
            False,
        )

    def test_unknown_knob_is_refused(self):
        with pytest.raises(KeyError):
            policy.apply_overrides(preset_content("frugal"), {"run.max_dollars": 1})
        with pytest.raises(KeyError):
            policy.apply_overrides(preset_content("frugal"), {"nonsense": 1})

    def test_schema_violations_are_refused(self):
        with pytest.raises(ValidationError):
            policy.apply_overrides(preset_content("frugal"), {"retries.upstream_retries": 99})


class TestResolution:
    def test_budget_basis_auto(self):
        app = policy.effective_policy(preset_content("frugal"))
        assert app.budget_basis == "usd"
        confirmatory = policy.effective_policy(
            preset_content("frugal"),
            study_ctx=policy.StudyPolicyContext(study_kind="confirmatory", n_model_levels=2),
        )
        assert confirmatory.budget_basis == "tokens"
        assert confirmatory.content.study.budget_basis == "tokens"

    def test_cap_basis_auto(self):
        assert policy.effective_policy(preset_content("frugal"), model=MODEL).cap_basis == "billed"
        listed = ModelFacts(slug="go/m", cost_basis="list_price", endpoint_kind="openai")
        assert policy.effective_policy(preset_content("frugal"), model=listed).cap_basis == "list"
        ctx = policy.StudyPolicyContext(any_non_billed_endpoint=True)
        assert policy.effective_policy(preset_content("frugal"), study_ctx=ctx).cap_basis == "list"

    def test_auto_batch_resolves_per_study(self, settings):
        slot = policy.StudyPolicyContext(mode="slot", executor="single_shot")
        settings.BATCH_BACKENDS_VERIFIED = False
        unverified = policy.effective_policy(preset_content("frugal"), model=MODEL, study_ctx=slot)
        assert unverified.serving_tier == "standard"
        assert "batch_unsupported" in unverified.notes
        settings.BATCH_BACKENDS_VERIFIED = True
        batch_model = ModelFacts(slug="vendor/b", supports_batch=True, input_per_mtok=1, output_per_mtok=1)
        assert (
            policy.effective_policy(preset_content("frugal"), model=batch_model, study_ctx=slot).serving_tier
            == "batch"
        )
        # A study-level resolution wins (one tier for every condition).
        fixed = policy.StudyPolicyContext(mode="slot", executor="single_shot", serving_tier="standard")
        assert (
            policy.effective_policy(preset_content("frugal"), model=batch_model, study_ctx=fixed).serving_tier
            == "standard"
        )

    def test_resolve_serving_tier_for_the_study(self, settings):
        settings.BATCH_BACKENDS_VERIFIED = True
        ok = ModelFacts(slug="a/b", supports_batch=True)
        no = ModelFacts(slug="a/c", supports_batch=False)
        frugal = preset_content("frugal")
        assert policy.resolve_serving_tier([frugal], slot_executors=["single_shot"], models=[ok])[0] == "batch"
        tier, notes = policy.resolve_serving_tier([frugal], slot_executors=["single_shot"], models=[ok, no])
        assert (tier, notes) == ("standard", ["batch_unsupported"])
        tier, _ = policy.resolve_serving_tier(
            [frugal],
            slot_executors=["single_shot", "restricted_agent"],
            models=[ok],
        )
        assert tier == "standard"

    def test_agentic_runs_are_always_standard(self):
        assert policy.effective_policy(preset_content("frugal")).serving_tier == "standard"

    def test_context_for_study(self):
        from backend.generation.testing.content import design_content

        ctx = policy.context_for_study(design_content(kind="confirmatory"))
        assert (ctx.study_kind, ctx.mode, ctx.executor, ctx.n_model_levels) == ("confirmatory", "app", "agentic", 2)


class TestClamps:
    def test_output_cap_below_model_output_minus_reasoning(self):
        content = policy.apply_overrides(
            preset_content("generous"),
            {"reasoning": {"mode": "max_tokens", "effort": None, "max_tokens": 3000}},
        )
        effective = policy.effective_policy(content, model=MODEL)
        assert effective.content.call.max_output_tokens == 7000  # 10k - 3k
        assert "call.max_output_tokens" in {c.key for c in effective.clamps}

    def test_context_to_model_length(self):
        effective = policy.effective_policy(preset_content("standard"), model=MODEL)
        assert effective.content.context.max_context_tokens == 100_000

    def test_reasoning_dropped_without_support(self):
        plain = ModelFacts(slug="vendor/plain", supports_reasoning=False, input_per_mtok=1, output_per_mtok=1)
        effective = policy.effective_policy(preset_content("frugal"), model=plain)
        assert effective.content.reasoning.mode == "provider_default"
        assert effective.content.reasoning.effort is None
        assert "reasoning_unsupported" in effective.notes

    def test_max_price_model_multiplier(self):
        effective = policy.effective_policy(preset_content("frugal"), model=MODEL)
        max_price = effective.content.routing.max_price
        assert (max_price.prompt, max_price.completion) == (2.5, 10.0)

    def test_max_price_unresolved_without_prices(self):
        unpriced = ModelFacts(slug="vendor/x", supports_reasoning=True)
        effective = policy.effective_policy(preset_content("frugal"), model=unpriced)
        assert effective.content.routing.max_price is None
        assert "max_price_unresolved" in effective.notes

    def test_sandbox_machine_max_then_stack_minimum(self):
        ctx = policy.StudyPolicyContext(stack_min_memory="3g", stack_min_nano_cpus=2_000_000_000)
        effective = policy.effective_policy(
            preset_content("frugal"),
            study_ctx=ctx,
            machine_memory="4g",
            machine_nano_cpus=4_000_000_000,
        )
        assert (effective.content.sandbox.memory, effective.content.sandbox.nano_cpus) == ("3g", 2_000_000_000)
        over = policy.StudyPolicyContext(stack_min_memory="6g")
        capped = policy.effective_policy(preset_content("frugal"), study_ctx=over, machine_memory="4g")
        assert capped.content.sandbox.memory == "4g"
        assert "stack_minimum_exceeds_machine" in capped.notes

    def test_tokens_basis_run_cap_becomes_a_backstop(self):
        ctx = policy.StudyPolicyContext(study_kind="confirmatory", n_model_levels=2, p99_run_usd=1.0)
        effective = policy.effective_policy(preset_content("frugal"), study_ctx=ctx)
        assert effective.content.run.max_cost_usd == 1.5
        assert "run.max_cost_usd" not in policy.treatment_view(effective)  # class O under tokens

    def test_equal_clamps_hash_equal(self):
        a = policy.apply_overrides(preset_content("standard"), {"context.max_context_tokens": 128_000})
        b = policy.apply_overrides(preset_content("standard"), {"context.max_context_tokens": 120_000})
        ea, eb = (policy.effective_policy(c, model=MODEL) for c in (a, b))
        assert policy.treatment_view(ea) == policy.treatment_view(eb)
        assert (
            policy.generation_policy_hash(ea)
            == policy.generation_policy_hash(eb)
            == identity.H(policy.treatment_view(ea))
        )

    def test_treatment_view_has_only_c_and_r(self):
        effective = policy.effective_policy(preset_content("frugal"), model=MODEL)
        view = policy.treatment_view(effective)
        assert "concurrency.study_max_parallel" not in view  # O
        assert "study.stop_rules" not in view  # S
        assert view["run.max_cost_usd"] == 0.3  # R under usd
        assert view["routing.serving_tier"] == "standard"  # resolved, not "auto_batch"

    def test_class_o_change_keeps_the_hash(self):
        a = policy.effective_policy(preset_content("frugal"), model=MODEL)
        b = policy.effective_policy(
            policy.apply_overrides(preset_content("frugal"), {"concurrency.study_max_parallel": 7}),
            model=MODEL,
        )
        c = policy.effective_policy(
            policy.apply_overrides(preset_content("frugal"), {"run.max_llm_requests": 41}),
            model=MODEL,
        )
        assert policy.generation_policy_hash(a) == policy.generation_policy_hash(b)
        assert policy.generation_policy_hash(a) != policy.generation_policy_hash(c)


# --- lint: every rule fires on a fixture ---------------------------------------------------------------


def _lint(overrides=None, **kw):
    return _codes(policy.lint(policy.apply_overrides(preset_content("frugal"), overrides or {}), **kw))


LINT_CASES = [
    ("context_cap_above_call_cap", {"call.max_input_tokens": 70_000}, {}),
    ("call_cap_without_compaction", {}, {"harness": policy.HarnessInfo(name="aider", context_knob=None, known=True)}),
    ("reasoning_mode_invalid", {"reasoning": {"mode": "effort", "effort": "low", "max_tokens": 2048}}, {}),
    ("reasoning_budget_too_large", {"reasoning": {"mode": "max_tokens", "effort": None, "max_tokens": 9000}}, {}),
    (
        "reasoning_budget_too_small",
        {"reasoning": {"mode": "max_tokens", "effort": None, "max_tokens": 512}},
        {"model": ModelFacts(slug="anthropic/claude-x", family="anthropic", supports_reasoning=True)},
    ),
    (
        "output_cap_below_harness_min",
        {},
        {"harness": policy.HarnessInfo(name="h", context_knob="c", min_call_output_tokens=10_000, known=True)},
    ),
    (
        "slot_output_cap_tight",
        {},
        {
            "study_ctx": policy.StudyPolicyContext(
                mode="slot",
                executor="single_shot",
                slot_original_tokens=3000,
                temperature=0.7,
            ),
        },
    ),
    ("brief_exceeds_call_cap", {}, {"study_ctx": policy.StudyPolicyContext(brief_tokens=70_000)}),
    (
        "slot_context_exceeds_model",
        {},
        {
            "model": MODEL,
            "study_ctx": policy.StudyPolicyContext(
                mode="slot",
                executor="single_shot",
                slot_prompt_tokens=150_000,
                temperature=0.7,
            ),
        },
    ),
    ("model_lacks_tools", {}, {"model": ModelFacts(slug="vendor/notools", supports_tools=False)}),
    ("batch_requires_single_shot", {"routing.serving_tier": "batch"}, {}),
    ("provider_unpinned", {}, {"model": MODEL}),
    (
        "usd_basis_multi_model",
        {"study.budget_basis": "usd"},
        {"study_ctx": policy.StudyPolicyContext(study_kind="confirmatory", n_model_levels=2)},
    ),
    (
        "model_alias_in_confirmatory",
        {},
        {
            "model": ModelFacts(slug="vendor/m-latest"),
            "study_ctx": policy.StudyPolicyContext(study_kind="confirmatory"),
        },
    ),
    (
        "slot_sampling_provider_default",
        {},
        {
            "study_ctx": policy.StudyPolicyContext(
                mode="slot",
                executor="single_shot",
                sampling_policy="provider_default",
            ),
        },
    ),
    (
        "adaptive_stop_in_confirmatory",
        {"study.stop_rules": [{"code": "ci_width_reached", "threshold": 0.1, "action": "stop", "features": ["f"]}]},
        {"study_ctx": policy.StudyPolicyContext(study_kind="confirmatory")},
    ),
]


@pytest.mark.parametrize(("code", "overrides", "kw"), LINT_CASES, ids=[c[0] for c in LINT_CASES])
def test_each_lint_rule_fires(code, overrides, kw):
    assert code in _lint(overrides, **kw)


def test_provider_pinned_is_clean_and_confirmatory_unpinned_is_an_error():
    pinned = {"routing.only": ["DeepInfra"], "routing.allow_fallbacks": False}
    assert "provider_unpinned" not in _lint(pinned, model=MODEL)
    issues = policy.lint(
        preset_content("frugal"),
        model=MODEL,
        study_ctx=policy.StudyPolicyContext(study_kind="confirmatory"),
    )
    assert {i.severity for i in issues if i.code == "provider_unpinned"} == {"error"}
    random = policy.StudyPolicyContext(study_kind="confirmatory", random_effects=("provider",))
    assert "provider_unpinned" not in _codes(policy.lint(preset_content("frugal"), model=MODEL, study_ctx=random))


def test_context_cap_issue_offers_the_fix():
    issues = policy.lint(policy.apply_overrides(preset_content("frugal"), {"call.max_input_tokens": 70_000}))
    issue = next(i for i in issues if i.code == "context_cap_above_call_cap")
    assert issue.severity == "error"
    assert issue.fix[0].params == {"call.max_input_tokens": 70_400}


def test_cross_condition_rules():
    a = policy.effective_policy(preset_content("frugal"))
    b = policy.effective_policy(policy.apply_overrides(preset_content("frugal"), {"sandbox.memory": "1g"}))
    issues = policy.lint_study(
        [policy.ConditionPolicy(0, a, cells=["c0"]), policy.ConditionPolicy(1, b, cells=["c1"])],
        study_kind="confirmatory",
    )
    assert {i.code: i.severity for i in issues} == {"resources_vary_by_stack": "error"}
    tiered = EffectivePolicy.model_validate({**a.model_dump(mode="json", by_alias=True), "serving_tier": "batch"})
    issues = policy.lint_study([policy.ConditionPolicy(0, a), policy.ConditionPolicy(1, tiered)])
    assert "mixed_serving_tier" in _codes(issues)


def test_effective_policy_carries_the_lint():
    effective = policy.effective_policy(
        policy.apply_overrides(preset_content("frugal"), {"call.max_input_tokens": 70_000}),
    )
    assert "context_cap_above_call_cap" in _codes(effective.issues)


# --- helpers -----------------------------------------------------------------------------------------


def test_execution_options_for_launch():
    out = policy.execution_options(policy.effective_policy(preset_content("frugal")))
    assert out["max_parallel"] == 2
    assert out["execution_options"] == {
        "max_resident_models": 1,
        "per_model": 1,
        "per_endpoint": 1,
        "per_provider": None,
        "rpm_per_model": 20,
        "eval_parallel": 1,
        "warmup": True,
    }


def test_diff_from_tier_and_summary():
    content = policy.apply_overrides(preset_content("frugal"), {"run.max_cost_usd": 0.5})
    diff = policy.diff_from_tier(content)
    assert diff == [{"key": "run.max_cost_usd", "tier": "frugal", "tier_value": 0.3, "value": 0.5, "class": "R"}]
    assert policy.summary_line(content, name="Mine", version=2, tier="custom").endswith(
        "$0.50/run · auto study cap · 2 parallel · effort low · 1 override",
    )


def test_model_aliases():
    assert is_model_alias("openai/gpt-x-latest")
    assert is_model_alias("ollama/qwen:latest")
    assert is_model_alias("openrouter/auto")
    assert not is_model_alias("deepseek/deepseek-v4.1-flash")


@pytest.mark.django_db
def test_model_facts_from_a_catalog_row():
    from backend.generation.tests.wp11_budget.conftest import model

    row = model(
        capabilities_json={
            "supported_parameters": ["tools", "reasoning"],
            "pricing": {"input_cache_read": "0.0000001"},
        },
    )
    facts = model_facts(row)
    assert facts.supports_reasoning
    assert facts.supports_tools
    assert (facts.input_per_mtok, facts.output_per_mtok) == (1.0, 2.0)
    assert facts.cached_input_per_mtok == pytest.approx(0.1)
    assert facts.cache_discount == pytest.approx(0.9)
    assert facts.context_length == 128_000
    assert facts.endpoint_kind == "openrouter"


def test_explain_payload():
    payload = policy.explain(preset_content("frugal"), model=MODEL, harness=HARNESS)
    assert payload["generation_policy_hash"] == identity.H(payload["treatment_view"])
    assert payload["effective"]["content"]["routing"]["max_price"] == {"prompt": 2.5, "completion": 10.0}
    assert {row["key"] for row in payload["knob_meta"]} >= {"run.max_cost_usd"}
