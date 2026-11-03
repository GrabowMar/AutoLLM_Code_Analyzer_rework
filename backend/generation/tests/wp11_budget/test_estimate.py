"""Estimator vectors (4.4): prior fallback chain and drift, the agentic and slot heuristics, batch factor, the
per-attempt cap, plan totals, by_condition / by_level, fit, the auto cap, ``estimate_exceeds_cap`` and the
pilot re-estimate. Every expected number is worked out by hand in the comments."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from backend.generation.budget import estimate
from backend.generation.budget import policy
from backend.generation.budget.model_facts import ModelFacts
from backend.generation.contracts.plan import CellEstimate
from backend.generation.contracts.plan import ResolvedCell
from backend.generation.contracts.plan import ResolvedPlan
from backend.generation.contracts.plan import cell_key
from backend.generation.contracts.resources import preset_content
from backend.generation.testing.content import fake_sha
from backend.generation.testing.content import manifest_content

CHEAP = ModelFacts(
    slug="vendor/cheap",
    family="vendor",
    input_per_mtok=0.1,
    output_per_mtok=0.3,
    context_length=128_000,
)
CACHING = ModelFacts(
    slug="deepseek/ds",
    family="deepseek",
    input_per_mtok=0.1,
    output_per_mtok=0.3,
    cached_input_per_mtok=0.025,
    context_length=128_000,
)
FRUGAL = preset_content("frugal")
NO_PRIORS: dict = {}


def _agentic(model=CHEAP, **kw):
    values = {"kind": "app_agentic", "model": model, "policy": FRUGAL, "harness": "opencode", "brief_tokens": 1000}
    values.update(kw)
    return estimate.CellInputs(**values)


class TestHeuristics:
    def test_agentic_vector(self):
        # R = min(40, r0[opencode]=45) = 40; P0 = 1000 + 9000; C = min(64k context, 72k call cap) = 64k.
        # IN = sum_{k=1..30}(10000 + 1800k) + 10 x 64000 = 1,137,000 + 640,000 = 1,777,000
        # OUT = 40 x min(8192, 900) = 36,000; RSN = 40 x r_avg(low)=400 = 16,000
        # expected = 1.777 x 0.1 + 52,000 x 0.3e-6 = 0.1777 + 0.0156 = 0.1933
        est = estimate.estimate_inputs(_agentic(), priors=NO_PRIORS)
        assert est.expected_usd == pytest.approx(0.1933)
        assert est.p10_usd == pytest.approx(0.1933 * 0.4)
        # p90 = 2.2 x 0.1933 = 0.425 > the per-attempt cap 0.30: truncated at the cap.
        assert est.p90_usd == pytest.approx(0.30)
        assert est.cap_usd == pytest.approx(0.30)
        assert est.expected_tokens == 1_777_000 + 36_000 + 16_000
        assert est.basis == "heuristic"

    def test_caching_discount(self):
        # h = 0.8, d = 1 - 0.025 / 0.1 = 0.75: input cost x (1 - 0.6) = 0.07108; + 0.0156
        est = estimate.estimate_inputs(_agentic(model=CACHING), priors=NO_PRIORS)
        assert est.expected_usd == pytest.approx(0.1777 * 0.4 + 0.0156)

    def test_caching_off(self):
        off = policy.apply_overrides(FRUGAL, {"caching.prompt_cache": "off"})
        est = estimate.estimate_inputs(_agentic(model=CACHING, policy=off), priors=NO_PRIORS)
        assert est.expected_usd == pytest.approx(0.1933)

    def test_cap_usd_is_the_theoretical_worst_case(self):
        # per call: 64k x 0.1e-6 + 8192 x 0.3e-6 = 0.0088576; x (40 main + 10 aux) = 0.44288 > run cap 0.30.
        raised = policy.apply_overrides(FRUGAL, {"run.max_cost_usd": 1.0})
        est = estimate.estimate_inputs(_agentic(policy=raised), priors=NO_PRIORS)
        assert est.cap_usd == pytest.approx(0.44288)

    def test_slot_single_shot_and_batch_factor(self):
        # OUT = min(4096, 1.6 x 500 + 150) + 400 = 1350; context share 3000/4000; h = 29/30 x 0.75 = 0.725
        # prompt = 4000 x 0.1e-6 x (1 - 0.725 x 0.75) = 0.0001825; out = 1350 x 0.3e-6 = 0.000405
        inputs = estimate.CellInputs(
            kind="slot_single_shot",
            model=CACHING,
            policy=FRUGAL,
            brief_tokens=4000,
            context_tokens=3000,
            original_tokens=500,
            n_replicates=30,
        )
        standard = estimate.estimate_inputs(inputs, priors=NO_PRIORS)
        assert standard.expected_usd == pytest.approx(0.0005875)
        assert standard.p10_usd == pytest.approx(0.0005875 * 0.7)
        assert standard.p90_usd == pytest.approx(0.0005875 * 1.6)
        # One call's worst case: 4000 x 0.1e-6 + 4096 x 0.3e-6 = 0.0016288
        assert standard.cap_usd == pytest.approx(0.0016288)
        batch = estimate.estimate_inputs(
            estimate.CellInputs(**{**inputs.__dict__, "serving_tier": "batch"}),
            priors=NO_PRIORS,
        )
        assert batch.expected_usd == pytest.approx(0.0005875 * 0.5)

    def test_slot_agent_halves_r0(self):
        slot_agent = estimate.estimate_inputs(_agentic(kind="slot_agent"), priors=NO_PRIORS)
        app = estimate.estimate_inputs(_agentic(), priors=NO_PRIORS)
        assert slot_agent.expected_usd < app.expected_usd
        assert slot_agent.expected_wall_s == pytest.approx(app.expected_wall_s * 0.5)

    def test_unpriced_model_is_never_free(self):
        unpriced = ModelFacts(slug="vendor/unknown")
        est = estimate.estimate_inputs(_agentic(model=unpriced), priors=NO_PRIORS)
        assert est.expected_usd == pytest.approx(0.30)
        assert est.basis.endswith(":unpriced")

    def test_subscription_endpoint_bills_nothing_but_has_a_list_cost(self):
        est = estimate.estimate_inputs(_agentic(cost_basis="list_price"), priors=NO_PRIORS)
        assert est.expected_usd == 0.0
        assert est.expected_list_usd == pytest.approx(0.1933)

    def test_wall_clock_from_size_and_capped(self):
        est = estimate.estimate_inputs(_agentic(size_bucket="XL"), priors=NO_PRIORS)
        assert est.expected_wall_s == pytest.approx(1200.0)  # 2100 s capped at frugal wall_clock_s 1200
        assert est.p90_wall_s == pytest.approx(1200.0)


def _prior(n, cost_p50, **kw):
    values = {
        "n": n,
        "cost_p10": None,
        "cost_p50": cost_p50,
        "cost_p90": None,
        "list_cost_p50": None,
        "prompt_tokens_p50": None,
        "cached_tokens_p50": None,
        "completion_tokens_p50": None,
        "wall_s_p50": None,
        "wall_s_p90": None,
        "price_at": {},
    }
    values.update(kw)
    return SimpleNamespace(**values)


class TestPriors:
    def _inputs(self):
        return _agentic(size_bucket="M", tier="frugal")

    def test_chain_skips_keys_below_n_min(self):
        priors = {
            "app_agentic|opencode|vendor/cheap|M|frugal": _prior(3, 0.01),
            "app_agentic|opencode|vendor/cheap|*|frugal": _prior(6, 0.05),
        }
        est = estimate.estimate_inputs(self._inputs(), priors=priors)
        assert est.basis == "prior:app_agentic|opencode|vendor/cheap|*|frugal(6)"
        assert est.expected_usd == pytest.approx(0.05)
        assert est.p10_usd == pytest.approx(0.05 * 0.4)

    def test_family_and_wildcard_levels(self):
        family = {"app_agentic|opencode|vendor|M|*": _prior(5, 0.07)}
        assert estimate.estimate_inputs(self._inputs(), priors=family).basis.startswith(
            "prior:app_agentic|opencode|vendor|M|*",
        )
        anything = {"app_agentic|opencode|*|M|*": _prior(9, 0.08, cost_p90=0.2, wall_s_p50=500.0)}
        est = estimate.estimate_inputs(self._inputs(), priors=anything)
        assert (est.expected_usd, est.p90_usd, est.expected_wall_s) == (pytest.approx(0.08), pytest.approx(0.2), 500.0)

    def test_pricing_drift(self):
        # then = 1M x 0.05 + 0.1M x 0.15 = 65,000; now = 1M x 0.1 + 0.1M x 0.3 = 130,000: x 2.
        prior = _prior(
            8,
            0.05,
            prompt_tokens_p50=1_000_000,
            completion_tokens_p50=100_000,
            price_at={"input_per_mtok": 0.05, "output_per_mtok": 0.15},
        )
        est = estimate.estimate_inputs(self._inputs(), priors={"app_agentic|opencode|vendor/cheap|M|frugal": prior})
        assert est.expected_usd == pytest.approx(0.10)

    def test_heuristic_method_ignores_priors(self):
        heuristic = policy.apply_overrides(FRUGAL, {"estimate.method": "heuristic"})
        priors = {"app_agentic|opencode|vendor/cheap|M|frugal": _prior(50, 0.01)}
        est = estimate.estimate_inputs(_agentic(policy=heuristic, tier="frugal"), priors=priors)
        assert est.basis == "heuristic"

    def test_routing_calibration_and_failure_overhead(self):
        priors = {
            "calibration|vendor/cheap|price|*|*": _prior(10, 0.5),
            "overhead|app_agentic|opencode|*|*": _prior(
                10,
                None,
                price_at={"infra_retry_rate": 0.1, "mean_partial_cost": 0.05, "failed_call_cost_share": 0.0},
            ),
        }
        est = estimate.estimate_inputs(self._inputs(), priors=priors)
        # 0.1933 x 0.5 (billed / catalog) + 0.1 x 0.05 (expected partial-retry cost)
        assert est.expected_usd == pytest.approx(0.1933 * 0.5 + 0.005)


def test_token_estimate_fallback():
    assert estimate.est_tokens("a" * 36) == (10, "chars/3.6")


# --- plan ---------------------------------------------------------------------------------------------------


def _cell(c: int, u: int, r: int, n_units: int = 2) -> ResolvedCell:
    body = manifest_content(kind="app_agentic")
    body.pop("created_at")
    block = r * n_units + u
    return ResolvedCell(
        cell_key=cell_key(c, u, r, "main"),
        condition_index=c,
        unit_index=u,
        repeat_index=r,
        block_index=block,
        sequence=block * 2 + c,
        stage="main",
        seed=1,
        unit_key=f"task:t{u}@1",
        levels={"model": f"m{c}"},
        manifest=body,
    )


def _plan(repeats: int = 3) -> ResolvedPlan:
    cells = [_cell(c, u, r) for r in range(repeats) for u in range(2) for c in range(2)]
    return ResolvedPlan(
        resolver_version=1,
        design_hash=fake_sha("d"),
        plan_hash=fake_sha("p"),
        conditions=[],
        units=[],
        cells=cells,
        n_blocks=repeats * 2,
    )


def _fixed(cell: ResolvedCell) -> CellEstimate:
    usd = 0.10 if cell.condition_index == 0 else 0.20
    return CellEstimate(
        expected_usd=usd,
        expected_list_usd=usd,
        p10_usd=usd * 0.5,
        p90_usd=usd * 1.5,
        cap_usd=0.3,
        expected_tokens=1000,
        expected_wall_s=600.0,
        p90_wall_s=900.0,
        basis="heuristic",
    )


class TestPlan:
    def test_totals_by_condition_by_level_and_fit(self):
        content = policy.apply_overrides(FRUGAL, {"study.max_cost_usd": 2.0})
        est = estimate.estimate_plan(_plan(), policy=content, tier="frugal", cell_estimator=_fixed)
        assert est.expected_total == pytest.approx(1.8)  # 6 x 0.1 + 6 x 0.2
        assert est.p90_total == pytest.approx(2.7)
        assert [(c.condition_index, c.n_cells, c.expected_usd) for c in est.by_condition] == [(0, 6, 0.6), (1, 6, 1.2)]
        assert est.by_level == {"model": {"m0": 0.6, "m1": 1.2}}
        # Blocks cost 0.3 each: 0.3, 0.6, ... 1.8 -> 6 blocks under 2.0; a repeat costs 0.6 -> 3 repeats;
        # a unit costs 0.3 x 3 repeats = 0.9 -> 2 units (1.8 <= 2.0).
        assert (est.fit.blocks_under_cap, est.fit.max_repeats_under_cap, est.fit.max_units_under_cap) == (6, 3, 2)
        # worst case: min(cap, sum of min(cell cap 0.6, 0.3 x (1 + infra_retries 1)))
        assert est.worst_case_total == pytest.approx(2.0)
        assert est.time.effective_parallel == 2
        assert est.time.expected_wall_s_low == pytest.approx(12 * 600 / 2)
        assert est.time.eval_machine_minutes == pytest.approx(12 * 7.0)

    def test_estimate_exceeds_cap_warn_and_error(self):
        warn = estimate.estimate_plan(
            _plan(),
            policy=policy.apply_overrides(FRUGAL, {"study.max_cost_usd": 2.0}),
            cell_estimator=_fixed,
            tier="frugal",
        )
        assert [(i.code, i.severity) for i in warn.issues] == [("estimate_exceeds_cap", "warn")]
        error = estimate.estimate_plan(
            _plan(),
            policy=policy.apply_overrides(FRUGAL, {"study.max_cost_usd": 1.0}),
            cell_estimator=_fixed,
            tier="frugal",
        )
        issue = error.issues[0]
        assert (issue.code, issue.severity) == ("estimate_exceeds_cap", "error")
        assert [f.action for f in issue.fix] == ["set_study_cap", "set_repeats"]
        assert issue.fix[0].params == {"study_cap": 2.97}
        assert issue.fix[1].params == {"repeats": 1}
        assert error.fit.blocks_under_cap == 3
        ok = estimate.estimate_plan(
            _plan(),
            policy=policy.apply_overrides(FRUGAL, {"study.max_cost_usd": 3.0}),
            cell_estimator=_fixed,
            tier="frugal",
        )
        assert ok.issues == []

    def test_auto_cap(self):
        est = estimate.estimate_plan(_plan(), policy=FRUGAL, tier="frugal", cell_estimator=_fixed)
        assert est.cap_usd == pytest.approx(2.97)  # round-up of 2.7 x 1.1 to the cent (float noise ignored)
        assert est.issues == []
        assert estimate.auto_cap(10.0, "generous") == 15.0
        assert estimate.auto_cap(10.0, "custom") == 12.5
        assert (
            estimate.estimate_plan(
                _plan(),
                policy=FRUGAL,
                tier="frugal",
                cell_estimator=_fixed,
                cap_override=1.0,
            ).cap_usd
            == 1.0
        )

    def test_reestimate_after_pilot(self):
        est = estimate.estimate_plan(_plan(), policy=FRUGAL, tier="frugal", cell_estimator=_fixed)
        new, changes = estimate.reestimate_after_pilot(est, {0: [0.2] * 5})
        # prior ln(0.1) weight 5, observed ln(0.2) n=5, no variance: per cell exp((ln .1 + ln .2) / 2) = 0.141421
        assert changes == [
            {"condition_index": 0, "before": 0.6, "after": pytest.approx(0.6 * math.sqrt(2), rel=1e-5), "n": 5},
        ]
        assert new.by_condition[1] == est.by_condition[1]
        assert new.inputs["reestimated_after_pilot"] is True


@pytest.mark.django_db
def test_estimate_cell_from_a_resolved_cell():
    from backend.generation.tests.wp11_budget.conftest import model

    row = model(
        model_id="vendor/estimate-me",
        input_price_per_token=1e-7,
        output_price_per_token=3e-7,
        context_window=128_000,
    )
    cell = _cell(0, 0, 0)
    body = cell.manifest.model_dump(mode="json")
    body["model"]["slug"] = row.model_id
    body["model"]["pricing"].update(input_per_mtok=0.1, output_per_mtok=0.3)
    effective = policy.effective_policy(FRUGAL, model=row).model_dump(mode="json", by_alias=True)
    cell = cell.model_copy(
        update={"manifest": type(cell.manifest).model_validate(body), "effective_policy": effective},
    )
    est = estimate.estimate_cell(cell, priors=NO_PRIORS)
    assert est.basis == "heuristic"
    assert 0 < est.expected_usd <= est.cap_usd <= 0.30
