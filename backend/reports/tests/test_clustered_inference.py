"""Inference must preserve shared tasks and distinguish fixed groups from iid trials."""

import random

import pytest

from backend.reports.services import study_report
from backend.reports.services.plan_stats import paired_difference
from backend.reports.services.plan_stats import wilcoxon_p
from backend.reports.tests.test_exploratory_comparisons import observations
from backend.statistics.services import mcdm


def test_shared_task_bootstrap_preserves_identical_paired_response_curves():
    rows = [r for r in observations() if r["group"] in {"a", "b"}]
    for item in rows:
        item["pass_rate"] = 0.1 if int(item["unit_key"].split("-")[1]) % 2 else 0.9
    groups = {group: [r for r in rows if r["group"] == group] for group in ("a", "b")}
    resample = study_report._resampler(groups)
    rng = random.Random(91)  # noqa: S311 - deterministic resampling
    for _ in range(100):
        sampled = resample(rng)
        assert sampled["a"]["pass_rate"] == sampled["b"]["pass_rate"]
    criteria = mcdm.criteria_from_config([{"key": "pass_rate", "weight": 1}])
    result = mcdm.rank_models(
        {group: study_report.model_criteria(items) for group, items in groups.items()},
        criteria,
        seed=1,
        resample=resample,
        draws=20,
    )
    uncertainty = result["data_uncertainty"]
    assert uncertainty["design"]["method"] == "shared_task_block_bootstrap"
    assert uncertainty["design"]["complete_blocks"] == 6
    assert all(r["p_rank_1"] == 1 for r in uncertainty["ranks"])


def test_pooled_funnel_and_fixed_endpoint_task_slices_withhold_iid_intervals():
    result = study_report.funnel_section(observations())
    assert result["overall"]["end_to_end_deploy_rate"]["rate"] == 1
    assert result["overall"]["end_to_end_deploy_rate"]["ci95"] is None
    assert all(r["generation_rate"]["ci95"] is None for r in result["by_task"])
    assert all(r["generation_rate"]["ci95"] is not None for r in result["by_model"])
    assert "exchangeable" in result["by_model"][0]["generation_rate"]["ci_assumptions"]


def test_pooled_correlations_keep_descriptive_effect_without_iid_inference():
    rows = [{"group": group, "x": i, "y": i * 2} for group in ("a", "b") for i in range(6)]
    result = study_report._app_correlation(rows, "x", "y", 1)
    assert result["rho"] == 1
    assert result["n_apps"] == 12
    assert result["p_permutation"] is None
    assert result["ci95"] is None
    assert result["inference"] == "descriptive_crossed_groups"


def test_pooled_security_zero_counts_do_not_imply_independent_risk_bound():
    rows = observations()
    for item in rows:
        item["analyzed"] = True
    result = study_report.security_bounds_section(rows)
    assert all(r["apps_affected"] == 0 for r in result["overall"])
    assert all(r["apps_affected_ci95"] is None for r in result["overall"])
    assert all(r["zero_rate_upper95"] is None for r in result["overall"])
    assert all(r["zero_critical_upper95"] is not None for r in result["by_model"])


def test_signed_rank_ties_survive_subtraction_roundoff():
    first = [0.525, 0.775, 0.325, 0.55]
    second = [0.5, 0.825, 0.375, 0.5]
    deltas = [paired_difference(a, b) for a, b in zip(first, second, strict=True)]
    assert deltas == [-0.025, 0.05, 0.05, -0.05]
    assert wilcoxon_p(deltas, "greater") == 0.5


def test_signed_rank_uses_exact_tied_sign_flips_above_twenty_five_pairs():
    # With equal magnitudes the conditional signed-rank statistic is binomial.
    assert wilcoxon_p([1.0] * 30) == 2 / 2**30
    assert wilcoxon_p([1.0] * 50, "greater") == 1 / 2**50


def test_model_correlations_adjust_unrounded_permutation_probabilities(monkeypatch):
    def probability(xs, ys, *, seed, digits):
        assert digits is None
        return 0.01234567 if xs else None

    monkeypatch.setattr(study_report.inf, "spearman_permutation_p", probability)
    params = {str(i): {**dict.fromkeys(study_report.PREDICTORS), "context_window_nominal": i + 1} for i in range(3)}
    outcomes = {str(i): dict.fromkeys(study_report.OUTCOMES, i + 1) for i in range(3)}
    result = study_report.correlations_section(params, outcomes, 0)
    measured = [r for r in result["tests"] if r["p_permutation"] is not None]
    assert result["family_size"] == 4
    assert all(r["p_permutation"] == 0.01235 for r in measured)
    assert all(r["p_holm"] == 0.04938 for r in measured)


def test_report_ranking_draws_are_bounded_and_exportable():
    assert study_report.ranking_draws({}) == 5000
    assert study_report.ranking_draws({"ranking_draws": 1000}) == 1000
    with pytest.raises(ValueError, match="between"):
        study_report.ranking_draws({"ranking_draws": 499})
    with pytest.raises(ValueError, match="between"):
        study_report.ranking_draws({"ranking_draws": 20001})
