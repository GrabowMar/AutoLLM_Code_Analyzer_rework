"""TOPSIS / WSM rankings and their sensitivity analyses."""

from __future__ import annotations

import pytest

from backend.statistics.services import mcdm

CRITERIA = mcdm.criteria_from_config(
    [
        {"key": "deploy_rate", "direction": "benefit", "weight": 2},
        {"key": "issues_per_kloc", "direction": "cost", "weight": 1},
        {"key": "cost_per_app", "direction": "cost", "weight": 1},
    ],
)


def _matrix():
    return {
        "dominant": {"deploy_rate": 0.9, "issues_per_kloc": 5.0, "cost_per_app": 0.01},
        "middle": {"deploy_rate": 0.6, "issues_per_kloc": 10.0, "cost_per_app": 0.05},
        "worst": {"deploy_rate": 0.1, "issues_per_kloc": 30.0, "cost_per_app": 0.50},
        "missing": {"deploy_rate": None, "issues_per_kloc": 1.0, "cost_per_app": 0.0},
    }


def test_weights_are_renormalised_and_validated():
    assert [c.weight for c in CRITERIA] == [0.5, 0.25, 0.25]
    with pytest.raises(ValueError, match="direction"):
        mcdm.criteria_from_config([{"key": "x", "direction": "up", "weight": 1}])
    with pytest.raises(ValueError, match="sum"):
        mcdm.criteria_from_config([{"key": "x", "weight": 0}])


def test_dominant_alternative_wins_every_analysis():
    result = mcdm.rank_models(_matrix(), CRITERIA, seed=1, draws=200)
    assert result["dropped_missing_criteria"] == ["missing"]
    assert [r["model"] for r in result["ranking"]] == ["dominant", "middle", "worst"]
    assert result["topsis_vs_wsm_kendall_tau"] == 1.0
    near = {r["model"]: r for r in result["monte_carlo_near_weights"]["ranks"]}
    assert near["dominant"]["p_rank_1"] == 1.0
    assert near["worst"]["rank_ci95"] == [3, 3]
    assert all(not o["rank_reversals"] for o in result["one_at_a_time"])


def test_tradeoff_shows_up_as_weight_sensitivity():
    matrix = {
        "fast": {"deploy_rate": 0.9, "issues_per_kloc": 40.0, "cost_per_app": 0.1},
        "clean": {"deploy_rate": 0.5, "issues_per_kloc": 2.0, "cost_per_app": 0.1},
    }
    result = mcdm.rank_models(matrix, CRITERIA, seed=2, draws=400)
    anyw = {r["model"]: r for r in result["monte_carlo_any_weights"]["ranks"]}
    # With arbitrary weights each model wins sometimes.
    assert 0 < anyw["fast"]["p_rank_1"] < 1


def test_data_uncertainty_uses_the_resampler():
    calls = []

    def resample(rng):
        calls.append(1)
        return _matrix()

    result = mcdm.rank_models(_matrix(), CRITERIA, seed=0, draws=200, resample=resample)
    assert len(calls) >= 200
    assert result["data_uncertainty"]["draws"] == len(calls)


def test_reproducible_with_seed():
    a = mcdm.rank_models(_matrix(), CRITERIA, seed=5, draws=100)
    b = mcdm.rank_models(_matrix(), CRITERIA, seed=5, draws=100)
    assert a == b
