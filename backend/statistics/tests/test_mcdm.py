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


TIE_CRITERIA = mcdm.criteria_from_config(
    [
        {"key": "deploy_rate", "direction": "benefit", "weight": 1},
        {"key": "pass_rate", "direction": "benefit", "weight": 1},
    ],
)


def test_identical_models_share_rank_one_everywhere():
    # The ceiling case: every model deploys and passes everything.
    matrix = {m: {"deploy_rate": 1.0, "pass_rate": 1.0} for m in ("deepseek/v4", "qwen/q3", "z-ai/glm")}
    result = mcdm.rank_models(matrix, TIE_CRITERIA, seed=1, draws=200, resample=lambda rng: matrix)
    assert {r["model"]: (r["topsis_rank"], r["wsm_rank"]) for r in result["ranking"]} == {
        "deepseek/v4": (1, 1),
        "qwen/q3": (1, 1),
        "z-ai/glm": (1, 1),
    }
    assert result["models_tied"] is True
    assert result["tied_groups"] == [["deepseek/v4", "qwen/q3", "z-ai/glm"]]
    for section in ("monte_carlo_near_weights", "monte_carlo_any_weights", "data_uncertainty"):
        for r in result[section]["ranks"]:
            assert r["p_rank_1"] == 1.0
            assert r["rank_ci95"] == [1, 1]
    # Nothing is ordered, so there is no agreement to report and no single leader.
    assert result["topsis_vs_wsm_kendall_tau"] is None
    assert result["equal_weights"]["kendall_tau"] is None
    assert all(o["leader"] is None and len(o["leaders"]) == 3 for o in result["one_at_a_time"])
    assert all(not o["rank_reversals"] for o in result["one_at_a_time"])


def test_partial_tie_gives_both_tied_models_rank_one():
    matrix = {
        "a": {"deploy_rate": 1.0, "pass_rate": 1.0},
        "b": {"deploy_rate": 1.0, "pass_rate": 1.0},
        "c": {"deploy_rate": 0.5, "pass_rate": 0.5},
    }
    result = mcdm.rank_models(matrix, TIE_CRITERIA, seed=3, draws=200)
    ranks = {r["model"]: r["topsis_rank"] for r in result["ranking"]}
    assert ranks == {"a": 1, "b": 1, "c": 3}
    anyw = {r["model"]: r for r in result["monte_carlo_any_weights"]["ranks"]}
    assert anyw["a"]["p_rank_1"] == anyw["b"]["p_rank_1"] == 1.0
    assert anyw["c"]["p_rank_1"] == 0.0
    assert result["tied_groups"] == [["a", "b"]]


def test_ranking_does_not_depend_on_model_names():
    # Two models with the same per-app outcome distribution: renaming them must not move the result.
    def run(names):
        first, second = names
        outcomes = [0.0, 1.0]

        def resample(rng):
            return {
                first: {"deploy_rate": 1.0, "pass_rate": rng.choice(outcomes)},
                second: {"deploy_rate": 1.0, "pass_rate": rng.choice(outcomes)},
            }

        matrix = {first: {"deploy_rate": 1.0, "pass_rate": 0.5}, second: {"deploy_rate": 1.0, "pass_rate": 0.5}}
        result = mcdm.rank_models(matrix, TIE_CRITERIA, seed=7, draws=800, resample=resample)
        return {r["model"]: r for r in result["data_uncertainty"]["ranks"]}

    plain = run(("a-model", "z-model"))
    renamed = run(("z-model", "a-model"))
    # The first-listed model gets the same summary whatever it is called.
    assert plain["a-model"]["p_rank_1"] == renamed["z-model"]["p_rank_1"]
    assert plain["z-model"]["p_rank_1"] == renamed["a-model"]["p_rank_1"]
    # Ties in a draw credit both models, so neither is favoured by its name.
    assert plain["a-model"]["median_rank"] == plain["z-model"]["median_rank"] == 1
