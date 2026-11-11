"""Results (ported v2 ``experiment_results`` semantics plus v3 ITT, pairing, attrition, EMMs; spec 9.4, 13.4)."""

from __future__ import annotations

import itertools

import pytest

from backend.generation.models import GenerationRun
from backend.generation.models import RunManifest
from backend.generation.models import StackProfile
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models.choices import Outcome
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study import results as results_svc
from backend.generation.study import stats
from backend.generation.study.results import ResultFilters
from backend.generation.study.results import compute
from backend.generation.testing.factories import EvalSuiteFactory
from backend.generation.testing.factories import RunEvaluationFactory
from backend.generation.testing.factories import TaskSpecVersionFactory

from .conftest import MODELS
from .conftest import app_design
from .test_launch import finish

pytestmark = pytest.mark.django_db

BASE, OTHER = MODELS
PLAN = {
    "primary_metric": "pass_rate",
    "baseline": {"model": BASE},
    "research_questions": [{"question": "Is the other model better?", "metric": "pass_rate"}],
}


def _tasks(n: int) -> list[str]:
    oracle = {stack.ref: {"status": "pass"} for stack in StackProfile.objects.all()}
    refs = []
    for _ in range(n):
        version = TaskSpecVersionFactory.create(task__system=True)
        version.oracle = oracle
        version.save(update_fields=["oracle"])
        refs.append(version.ref)
    return refs


def _two_models(make_study, *, n_tasks: int = 6, repeats: int = 1, **overrides):
    factors = [
        {"key": "model", "role": "crossed", "levels": list(MODELS)},
        {"key": "stack_profile", "role": "fixed", "levels": ["flask-react@1"]},
        {"key": "style", "role": "fixed", "levels": ["tailwind@1"]},
    ]
    study = make_study(factors=factors, units={"tasks": _tasks(n_tasks)}, repeats=repeats, **overrides)
    svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    study.refresh_from_db()
    return study


def _runs(study, model_id):
    return list(
        GenerationRun.objects.filter(study=study, model__model_id=model_id)
        .select_related("cell")
        .order_by("sequence"),
    )


def _complete(study):
    Study.objects.filter(pk=study.pk).update(status="completed")
    study.refresh_from_db()


def test_known_answer_paired_comparison(make_study, fake_study):
    study = _two_models(make_study)
    for run in _runs(study, BASE):
        finish(run, Outcome.TESTED, pass_rate=0.5)
    for run in _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    _complete(study)
    res = compute(study)
    assert res["baseline_source"] == "post_hoc"  # exploratory, no locked plan
    board = {row["model"]["model_id"]: row for row in res["leaderboard"]}
    assert board[OTHER]["rank"] == 1
    assert board[OTHER]["pass_rate_mean"] == pytest.approx(1.0)
    (row,) = res["paired"]["rows"]
    assert row["method"] == "paired_crn"  # both conditions forward the CRN seed
    assert row["primary_test"] == "permutation"  # 6 < 20 units
    assert (row["n_pairs"], row["mean_delta"], row["wins"], row["losses"]) == (6, 0.5, 6, 0)
    assert row["sign_test_p"] == pytest.approx(2 / 64)
    assert row["permutation_p"] == pytest.approx(2 / 64)
    assert row["ci95"] is None  # few clusters: no cluster bootstrap
    assert any(i["code"] == "few_clusters" for i in res["issues"])
    assert res["itt"] is True


def test_interim_withholds_p_values(make_study, fake_study):
    study = _two_models(make_study, n_tasks=3)
    for run in _runs(study, BASE):
        finish(run, Outcome.TESTED, pass_rate=0.0)
    for run in _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    res = compute(study)
    row = res["paired"]["rows"][0]
    assert res["interim"] is True
    assert (row["sign_test_p"], row["wilcoxon_p"], row["p_value"], row["significant"]) == (None, None, None, None)
    assert results_svc.NOTE_INTERIM in res["notes"]


def test_itt_keeps_truncated_and_model_failures_sensitivity_drops_truncated(make_study, fake_study):
    study = _two_models(make_study, n_tasks=2)
    base, other = _runs(study, BASE), _runs(study, OTHER)
    finish(base[0], Outcome.TESTED, pass_rate=1.0)
    finish(base[1], Outcome.TESTED, pass_rate=1.0)
    finish(other[0], Outcome.BUILD_FAIL)  # model-attributable: scores 0 under ITT
    finish(other[1], Outcome.TESTED, pass_rate=1.0, validity={"termination": "budget_exhausted", "truncated": True})
    _complete(study)
    itt = compute(study)
    board = {r["model"]["model_id"]: r for r in itt["leaderboard"]}
    assert board[OTHER]["n_graded"] == 2
    assert board[OTHER]["pass_rate_mean"] == 0.5
    assert board[OTHER]["model_failures_scored_zero"] == 1
    assert board[OTHER]["truncated_in_sample"] == 1
    assert itt["paired"]["sensitivity"]["exclude_truncated"] is True
    excluded = compute(study, filters=ResultFilters(sensitivity="exclude_truncated"))
    board = {r["model"]["model_id"]: r for r in excluded["leaderboard"]}
    assert board[OTHER]["n_missing"]["excluded_truncated"] == 1
    assert board[BASE]["n_missing"]["not_common"] == 1  # symmetric through the common cells
    assert excluded["itt"] is False


def test_infra_errors_are_attrition_and_not_measured_is_never_zero(make_study, fake_study):
    study = _two_models(make_study, n_tasks=2)
    base, other = _runs(study, BASE), _runs(study, OTHER)
    finish(base[0], Outcome.TESTED, pass_rate=1.0)
    finish(base[1], Outcome.INFRA_ERROR)
    GenerationRun.objects.filter(pk=base[1].pk).update(outcome_reason="platform_cpu")  # e.g. a SIGILL build
    finish(other[0], Outcome.TESTED)  # tested, evaluation not measured
    finish(other[1], Outcome.EXCLUDED, reason="budget_stop")
    _complete(study)
    res = compute(study)
    board = {r["model"]["model_id"]: r for r in res["leaderboard"]}
    assert board[OTHER]["pass_rate_mean"] is None
    assert board[OTHER]["n_missing"]["not_measured"] == 1
    assert board[BASE]["n_missing"]["infra"] == 1
    rows = {r["condition_id"]: r for r in res["attrition"]}
    base_row = rows[board[BASE]["condition_id"]]
    other_row = rows[board[OTHER]["condition_id"]]
    assert base_row["infra_error"] == 1
    assert base_row["infra_error_by_reason"] == {"platform_cpu": 1}  # reported by reason, never scored 0
    assert base_row["attrition_rate"] == 0.5
    assert other_row["excluded_by_reason"] == {"budget_stop": 1}
    assert other_row["infra_error_by_reason"] == {}
    assert res["stopped_early"] is True


def test_beyond_plan_runs_are_never_in_the_results(make_study, fake_study):
    study = _two_models(make_study, n_tasks=2)
    base = _runs(study, BASE)
    for run in base + _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=0.0)
    extra = GenerationRun.objects.get(pk=base[0].pk)
    extra.pk = extra.id = None
    extra.beyond_plan = True
    extra.cell_attempt = 2
    extra.save()
    finish(GenerationRun.objects.get(pk=extra.pk), Outcome.TESTED, pass_rate=1.0)
    res = compute(study, filters=ResultFilters(common_cells=False))
    board = {r["model"]["model_id"]: r for r in res["leaderboard"]}
    assert board[BASE]["pass_rate_mean"] == 0.0
    beyond = {r["condition_id"]: r["beyond_plan_runs"] for r in res["attrition"]}
    assert beyond[board[BASE]["condition_id"]] == 1


def test_incomplete_blocks_stay_out_of_the_comparison(make_study, fake_study):
    study = _two_models(make_study, n_tasks=2)
    base, other = _runs(study, BASE), _runs(study, OTHER)
    finish(base[0], Outcome.TESTED, pass_rate=1.0)
    finish(other[0], Outcome.TESTED, pass_rate=0.0)
    finish(base[1], Outcome.TESTED, pass_rate=1.0)  # its block partner (other[1]) is still pending
    res = compute(study, filters=ResultFilters(common_cells=False))
    board = {r["model"]["model_id"]: r for r in res["leaderboard"]}
    assert board[BASE]["n_graded"] == 1
    assert board[BASE]["n_missing"]["incomplete_block"] == 1
    assert len(res["gate"]["complete_blocks"]) == 1


def _confirmatory(make_study, **kw):
    return _two_models(make_study, kind="confirmatory", analysis_plan=PLAN, **kw)


def test_confirmatory_gate_and_deviations(make_study, fake_study):
    study = _confirmatory(make_study)
    for run in _runs(study, BASE):
        finish(run, Outcome.TESTED, pass_rate=0.0)
    for run in _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    _complete(study)
    res = compute(study)
    assert res["baseline_source"] == "preregistered"
    assert res["paired"]["status"] == "confirmatory"
    assert res["gate"]["deviations"] == []
    assert res["gate"]["significance_unreachable"] is None  # 6 units: 2/64 < 0.05
    row = res["paired"]["rows"][0]
    assert row["significant"] is True
    # Excluding truncated runs is not the plan's rule (ITT): exploratory.
    assert compute(study, filters=ResultFilters(sensitivity="exclude_truncated"))["paired"]["status"] == "exploratory"
    # A post-hoc baseline is exploratory.
    other_condition = next(r for r in res["leaderboard"] if r["model"]["model_id"] == OTHER)["condition_id"]
    assert compute(study, baseline=other_condition)["paired"]["status"] == "exploratory"


def test_stopped_early_and_attrition_are_deviations(make_study, fake_study):
    study = _confirmatory(make_study)
    base, other = _runs(study, BASE), _runs(study, OTHER)
    for run in base:
        finish(run, Outcome.TESTED, pass_rate=0.0)
    for run in other[:-2]:
        finish(run, Outcome.TESTED, pass_rate=1.0)
    finish(other[-2], Outcome.EXCLUDED, reason="budget_stop")
    finish(other[-1], Outcome.INFRA_ERROR)
    _complete(study)
    res = compute(study)
    assert {"stopped_early", "exclusion_rate", "differential_attrition"} <= set(res["gate"]["deviations"])
    assert res["paired"]["status"] == "exploratory"
    assert {"exclusion_rate", "differential_attrition"} <= {i["code"] for i in res["issues"]}


def test_significance_unreachable_with_too_few_units(make_study, fake_study):
    study = _confirmatory(make_study, n_tasks=3)
    for run in _runs(study, BASE):
        finish(run, Outcome.TESTED, pass_rate=0.0)
    for run in _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    _complete(study)
    gate = compute(study)["gate"]
    assert gate["significance_unreachable"]["units_needed"] == 6


def test_mixed_config_fingerprint_is_flagged_and_majority_drops_the_minority(make_study, fake_study):
    study = _two_models(make_study, n_tasks=1, repeats=3)
    base = _runs(study, BASE)
    for run in base:
        finish(run, Outcome.TESTED, pass_rate=1.0)
    for run in _runs(study, OTHER):
        finish(run, Outcome.TESTED, pass_rate=0.0)
    RunManifest.objects.filter(run=base[0]).delete()
    GenerationRun.objects.filter(pk=base[0].pk).update(config_fingerprint="f" * 64)
    _complete(study)
    res = compute(study, filters=ResultFilters(fingerprint="all"))
    row = next(r for r in res["leaderboard"] if r["model"]["model_id"] == BASE)
    assert row["mixed_fingerprints"] == {"count": 2, "units": 1, "minority_cells": 1, "majority_share": 0.6667}
    assert "mixed_config_fingerprint" in {i["code"] for i in res["issues"]}
    majority = compute(study)
    row = next(r for r in majority["leaderboard"] if r["model"]["model_id"] == BASE)
    assert row["n_missing"]["excluded_fingerprint"] == 1


def test_mixed_eval_suites_are_flagged(make_study, fake_study):
    study = _two_models(make_study, n_tasks=2)
    suite_a, suite_b = EvalSuiteFactory(slug="suite-a"), EvalSuiteFactory(slug="suite-b")
    for model_id, suite, rate in ((BASE, suite_a, 0.0), (OTHER, suite_b, 1.0)):
        for run in _runs(study, model_id):
            finish(run, Outcome.TESTED, pass_rate=rate)
            RunEvaluationFactory(run=run, eval_suite=suite)
    _complete(study)
    issues = [i for i in compute(study)["issues"] if i["code"] == "mixed_eval_suite"]
    assert len(issues) == 1
    assert f"suite-a@{suite_a.version}" in issues[0]["message"]
    assert f"suite-b@{suite_b.version}" in issues[0]["message"]


def test_single_eval_suite_is_not_flagged(make_study, fake_study):
    study = _two_models(make_study, n_tasks=2)
    suite = EvalSuiteFactory(slug="suite-a")
    for model_id, rate in ((BASE, 0.0), (OTHER, 1.0)):
        for run in _runs(study, model_id):
            finish(run, Outcome.TESTED, pass_rate=rate)
            RunEvaluationFactory(run=run, eval_suite=suite)
    _complete(study)
    assert "mixed_eval_suite" not in {i["code"] for i in compute(study)["issues"]}


def test_done_when_results_show_by_factor_slices_and_attrition(make_study, fake_study):
    study = make_study()
    svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    study.refresh_from_db()
    for index, run in enumerate(
        GenerationRun.objects.filter(study=study).select_related("model").order_by("sequence"),
    ):
        if index == 0:
            finish(run, Outcome.INFRA_ERROR)
        else:
            finish(run, Outcome.TESTED, pass_rate=1.0 if run.model.model_id == OTHER else 0.5)
    _complete(study)
    res = compute(study)
    factors = res["by_factor"]["factors"]
    assert set(factors) == {"model", "stack_profile", "style"}
    model = {level.split("@")[0]: value["emm"] for level, value in factors["model"].items()}
    assert model[OTHER] == 1.0
    assert model[BASE] == 0.5
    assert len(res["attrition"]) == 8
    assert sum(row["infra_error"] for row in res["attrition"]) == 1
    assert StudyCell.objects.filter(study=study, state="infra_pending").count() == 1


class TestStatistics:
    @staticmethod
    def _brute_wilcoxon(deltas):
        nonzero = [d for d in deltas if d != 0]
        ranks = stats.ranks([abs(d) for d in nonzero])
        observed = sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
        centre = sum(ranks) / 2
        extreme = 0
        for signs in itertools.product((0, 1), repeat=len(ranks)):
            w = sum(r for r, s in zip(ranks, signs, strict=True) if s)
            if abs(w - centre) >= abs(observed - centre) - 1e-9:
                extreme += 1
        return extreme / 2 ** len(ranks)

    @pytest.mark.parametrize(
        "deltas",
        [[0.1, 0.2, 0.3, 0.4, 0.5, -0.6], [0.5, 0.5, -0.5, 0.25, 0.75, 1.0, -0.1], [1.0, 1.0, 1.0, -1.0]],
    )
    def test_exact_wilcoxon_matches_enumeration(self, deltas):
        assert stats.wilcoxon_p(deltas) == pytest.approx(self._brute_wilcoxon(deltas))

    def test_sign_flip_permutation(self):
        assert stats.sign_flip_p([1.0] * 6, seed=1) == pytest.approx(2 / 64)
        assert stats.sign_flip_p([0.0, 0.0], seed=1) is None
        large = stats.sign_flip_p([1.0] * 25, seed=1)
        assert 0 < large < 0.001
        assert stats.sign_flip_p([1.0] * 25, seed=1) == large  # seeded

    def test_sign_test_and_holm(self):
        assert stats.sign_test_p(5, 0) == pytest.approx(2 / 32)
        assert stats.sign_test_p(0, 0) is None
        assert stats.holm([0.01, None, 0.04, 0.03]) == [0.03, None, 0.06, 0.06]


def test_results_validate_filters(make_study, fake_study):
    study = make_study()
    with pytest.raises(ValueError, match="Unknown metric"):
        compute(study, filters=ResultFilters(metric="loc"))
    with pytest.raises(ValueError, match="fingerprint"):
        compute(study, filters=ResultFilters(fingerprint="some"))
    assert app_design()["repeats"] == 2
