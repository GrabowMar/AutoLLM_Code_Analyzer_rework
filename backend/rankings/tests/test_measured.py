"""Canonical measured-run selection and the empirical ranking built on it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings import services
from backend.rankings.services.measured import select_measured
from backend.rankings.tests.measured import PLAN_A
from backend.rankings.tests.measured import PLAN_B
from backend.rankings.tests.measured import make_run
from backend.rankings.tests.measured import measurement_json
from backend.rankings.tests.measured import sev
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _job(user, model=None, **kwargs):
    return GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED, **kwargs)


def _age(run, minutes: int) -> None:
    AnalysisRun.objects.filter(id=run.id).update(created_at=timezone.now() - timedelta(minutes=minutes))


# ── select_measured ───────────────────────────────────────────────────────────


def test_reference_plan_is_the_one_measuring_most_jobs():
    user = UserFactory()
    jobs = [_job(user) for _ in range(4)]
    for job in jobs[:3]:
        make_run(job, plan_hash=PLAN_A)
    make_run(jobs[3], plan_hash=PLAN_B)  # newest, but measures one job only

    selection = select_measured([j.id for j in jobs])

    assert selection.plan_hash == PLAN_A
    assert selection.plan_counts == {PLAN_A: 3, PLAN_B: 1}
    assert set(selection.measured) == {j.id for j in jobs[:3]}
    assert selection.other_plan == {jobs[3].id}
    coverage = selection.coverage()
    assert coverage["mixed_plans"] is True
    assert coverage["measured"] == 3
    assert coverage["other_plan"] == 1


def test_explicit_plan_hash_pins_the_comparison():
    user = UserFactory()
    job_a, job_b = _job(user), _job(user)
    make_run(job_a, plan_hash=PLAN_A)
    make_run(job_b, plan_hash=PLAN_A)
    make_run(job_b, plan_hash=PLAN_B)

    selection = select_measured([job_a.id, job_b.id], plan_hash=PLAN_B)

    assert selection.plan_hash == PLAN_B
    assert selection.explicit_plan is True
    assert set(selection.measured) == {job_b.id}
    assert selection.other_plan == {job_a.id}


def test_latest_complete_run_under_the_plan_wins_and_other_plans_never_replace_it():
    user = UserFactory()
    job, other = _job(user), _job(user)
    stale = make_run(job, plan_hash=PLAN_A, findings={"high": 10})
    _age(stale, 30)
    fresh = make_run(job, plan_hash=PLAN_A, findings={"high": 2})
    make_run(other, plan_hash=PLAN_A)
    # A newer single-tool re-run under another plan must not replace the measurement.
    make_run(job, plan_hash=PLAN_B, findings={"high": 99})

    selection = select_measured([job.id, other.id])

    assert selection.plan_hash == PLAN_A
    assert selection.measured[job.id].run_id == fresh.id
    assert selection.measured[job.id].findings_by_severity()["high"] == 2


def test_incomplete_failed_running_and_unanalysed_jobs_are_reported_not_zeroed():
    user = UserFactory()
    measured, partial, failed, running, never = (_job(user) for _ in range(5))
    make_run(measured)
    make_run(partial, status=AnalysisRun.Status.PARTIAL, is_complete=False, static_available=False)
    make_run(failed, status=AnalysisRun.Status.FAILED, is_complete=False)
    make_run(running, status=AnalysisRun.Status.RUNNING, is_complete=False)

    selection = select_measured([j.id for j in (measured, partial, failed, running, never)])

    assert set(selection.measured) == {measured.id}
    assert selection.incomplete == {partial.id}
    assert selection.analysis_failed == {failed.id}
    assert selection.in_progress == {running.id}
    assert selection.not_analysed == {never.id}
    assert selection.status_of(partial.id) == "incomplete"
    assert selection.status_of(never.id) == "not_analysed"


def test_complete_only_false_keeps_partial_runs_with_unavailable_static_as_missing():
    user = UserFactory()
    job = _job(user)
    make_run(job, status=AnalysisRun.Status.PARTIAL, is_complete=False, static_available=False, findings={"high": 1})

    selection = select_measured([job.id], complete_only=False)

    run = selection.measured[job.id]
    assert run.static_available is False
    assert run.findings_by_severity() is None  # missing data, never zeros
    assert run.issues_total() is None


def test_eligibility_rules_exclude_on_the_run_snapshot():
    user = UserFactory()
    ok, truncated = _job(user), _job(user)
    make_run(ok)
    make_run(truncated, target_meta={"truncated": True})

    selection = select_measured([ok.id, truncated.id])
    assert set(selection.measured) == {ok.id}
    assert selection.excluded == {truncated.id: ["truncated"]}
    assert selection.coverage()["excluded_reasons"] == {"truncated": 1}

    unfiltered = select_measured([ok.id, truncated.id], rules={})
    assert set(unfiltered.measured) == {ok.id, truncated.id}


def test_unusable_measurements_are_excluded_with_a_reason(monkeypatch):
    from backend.analysis.engine import measurement as measurement_engine

    user = UserFactory()
    empty, future, broken = _job(user), _job(user), _job(user)
    make_run(empty, measurement={}, is_complete=True)
    make_run(future, measurement={**measurement_json(), "schema_version": 99}, is_complete=True)
    bad = make_run(broken)

    real = measurement_engine.outcomes

    def flaky(data):
        if data is not None and data.get("sample_hash") == "broken":
            msg = "boom"
            raise ValueError(msg)
        return real(data)

    AnalysisRun.objects.filter(id=bad.id).update(measurement={**bad.measurement, "sample_hash": "broken"})
    monkeypatch.setattr(measurement_engine, "outcomes", flaky)

    selection = select_measured([empty.id, future.id, broken.id])

    assert selection.excluded == {
        empty.id: ["no_measurement"],
        future.id: ["unsupported_measurement"],
        broken.id: ["measurement_error"],
    }
    assert selection.measured == {}


def test_canary_and_legacy_v1_runs_are_never_canonical():
    user = UserFactory()
    job = _job(user)
    make_run(job, purpose=AnalysisRun.Purpose.CANARY)
    make_run(job, plan_hash="")

    selection = select_measured([job.id])

    assert selection.plan_hash is None
    assert selection.measured == {}


# ── aggregate_rankings on measured runs ───────────────────────────────────────


def _row(user, model_id, **filters):
    return next(r for r in services.aggregate_rankings(user, **filters) if r["model_id"] == model_id)


def test_density_numerator_and_denominator_cover_the_same_analysed_jobs():
    user = UserFactory()
    model = LLMModelFactory(model_id="openai/gpt-4o")
    analysed = _job(user, model, metrics={"lines_of_code": 4630})
    _job(user, model, metrics={"lines_of_code": 9999})  # never analysed: must not dilute the density
    make_run(analysed, issues={"high": 2}, findings={"high": 3}, measured_sloc=1000)

    row = _row(user, model.model_id)

    assert row["apps_completed"] == 2
    assert row["n_trials"] == 1
    assert row["measured_sloc"] == 1000
    assert row["local_loc"] == 1000
    assert row["findings"] == sev(high=3)
    assert row["issues"] == sev(high=2)
    # issues: 2 high x 5 = 10 per 1000 SLOC -> density 10 -> 1 - 10/150
    assert row["empirical_quality_score"] == pytest.approx(round(1 - 10 / 150, 4))
    assert row["analysis"]["not_analysed"] == 1
    assert row["plan_hash"] == PLAN_A
    assert row["scoring_version"] == services.SCORING_VERSION


def test_ai_findings_are_reported_separately_and_never_scored():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/ai")
    job = _job(user, model)
    make_run(job, issues={"high": 2}, ai={"critical": 5}, measured_sloc=1000, functional_pass_rate=1.0)

    row = _row(user, model.model_id)

    assert row["ai_findings"] == sev(critical=5)
    assert row["n_ai"] == 1
    assert row["empirical_quality_score"] == pytest.approx(round(0.6 * (1 - 10 / 150) + 0.4 * 1.0, 4))


def test_unknown_functional_pass_rate_is_none_safe():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/fn")
    unknown = _job(user, model, metrics={"functional": {"status": "infra_error", "pass_rate": None}})
    known = _job(user, model)
    make_run(unknown, functional_pass_rate=None, functional_status="infra_error")
    make_run(known, functional_pass_rate=0.5)

    row = _row(user, model.model_id)

    assert row["functional_pass_rate"] == 0.5
    assert row["functional_n"] == 1
    assert row["n_trials"] == 2


def test_trial_variance_over_measured_densities():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/var")
    for n_high, rate in ((2, 1.0), (4, 0.5)):
        make_run(_job(user, model), issues={"high": n_high}, measured_sloc=1000, functional_pass_rate=rate)

    row = _row(user, model.model_id)

    variance = row["variance"]
    assert row["n_trials"] == 2
    assert variance["n_jobs"] == 2
    assert variance["density_per_kloc_mean"] == pytest.approx(15.0)
    assert variance["density_per_kloc_stdev"] == pytest.approx(7.0711, abs=1e-3)
    assert variance["smoke_pass_rate_stdev"] == pytest.approx(0.3536, abs=1e-3)


def test_tiny_samples_do_not_get_a_per_trial_density():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/tiny")
    make_run(_job(user, model), issues={"high": 1}, measured_sloc=10)

    row = _row(user, model.model_id)

    assert row["variance"]["n_jobs"] == 0
    assert row["variance"]["density_per_kloc_stdev"] is None
    # the pooled density still uses the same job for numerator and denominator
    assert row["measured_sloc"] == 10


def test_unanalysed_model_keeps_composite_equal_to_mss():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/none")
    _job(user, model, metrics={"lines_of_code": 1000})

    row = _row(user, model.model_id)

    assert row["n_trials"] == 0
    assert row["empirical_quality_score"] is None
    assert row["measured_sloc"] is None
    assert row["composite_score"] == row["mss_score"]
    assert row["variance"]["n_jobs"] == 0


def test_rankings_compare_models_under_one_plan():
    user = UserFactory()
    good = LLMModelFactory(model_id="good/model")
    bad = LLMModelFactory(model_id="bad/model")
    make_run(_job(user, good), issues={"low": 10}, plan_hash=PLAN_A)
    make_run(_job(user, good), issues={"low": 10}, plan_hash=PLAN_A)
    make_run(_job(user, bad), issues={"critical": 10}, plan_hash=PLAN_A)
    # bad/model's only other job was analysed with another plan: left out, not mixed in.
    make_run(_job(user, bad), issues={}, plan_hash=PLAN_B)

    built = services.build_rankings(user)
    rows = {r["model_id"]: r for r in built["rows"]}

    assert built["comparability"]["plan_hash"] == PLAN_A
    assert built["comparability"]["other_plan"] == 1
    assert rows["bad/model"]["n_trials"] == 1
    assert rows["bad/model"]["analysis"]["other_plan"] == 1
    assert rows["good/model"]["empirical_quality_score"] > rows["bad/model"]["empirical_quality_score"]
    order = [r["model_id"] for r in built["rows"]]
    assert order.index("good/model") < order.index("bad/model")


def test_rankings_are_scoped_to_the_user():
    me, other = UserFactory(), UserFactory()
    model = LLMModelFactory(model_id="m/scope")
    make_run(_job(me, model), issues={"high": 1})
    make_run(_job(other, model), issues={"high": 50})

    mine = _row(me, model.model_id)

    assert mine["apps"] == 1
    assert mine["issues"] == sev(high=1)


def test_experiment_filter_scopes_measurements():
    from backend.generation.tests.factories import ExperimentFactory

    user = UserFactory()
    model = LLMModelFactory(model_id="m/exp")
    experiment = ExperimentFactory(created_by=user)
    make_run(_job(user, model, experiment=experiment), issues={"high": 1})
    make_run(_job(user, model), issues={"high": 9})

    scoped = _row(user, model.model_id, experiment_id=str(experiment.id))

    assert scoped["apps"] == 1
    assert scoped["issues"] == sev(high=1)


# ── writer ────────────────────────────────────────────────────────────────────


def test_sort_rankings_maps_ui_keys_and_puts_missing_values_last():
    rows = [
        {"model_id": "a", "cost_efficiency_score": 0.2, "empirical_quality_score": None},
        {"model_id": "b", "cost_efficiency_score": 0.9, "empirical_quality_score": 0.5},
        {"model_id": "c", "cost_efficiency_score": 0.5, "empirical_quality_score": 0.7},
    ]
    assert [r["model_id"] for r in services.sort_rankings(rows, sort_by="cost_efficiency")] == ["b", "c", "a"]
    assert [r["model_id"] for r in services.sort_rankings(rows, sort_by="empirical", sort_dir="asc")] == [
        "b",
        "c",
        "a",
    ]
    assert [r["model_id"] for r in services.sort_rankings(rows, sort_by="empirical")] == ["c", "b", "a"]


def test_sort_rankings_unknown_key_falls_back_to_mss_with_id_tiebreak():
    rows = [
        {"model_id": "b", "mss_score": 0.5},
        {"model_id": "a", "mss_score": 0.5},
        {"model_id": "c", "mss_score": 0.9},
    ]
    assert [r["model_id"] for r in services.sort_rankings(rows, sort_by="nonsense")] == ["c", "a", "b"]


def test_min_composite_filters_on_composite_not_mss():
    rows = [
        {"model_id": "a", "mss_score": 0.9, "composite_score": 0.3},
        {"model_id": "b", "mss_score": 0.1, "composite_score": 0.8},
    ]
    assert [r["model_id"] for r in services.filter_rankings(rows, min_composite=0.5)] == ["b"]


def test_sensitivity_flags_degenerate_comparisons():
    out = services.compute_weight_sensitivity(
        [{"model_id": "only/one", "empirical_quality_score": 0.5, "issues": sev(high=1), "measured_sloc": 1000}],
    )
    assert out["models_evaluated"] == 1
    assert out["defined"] is False
    assert all(s["defined"] is False for s in out["schemes"])


def test_min_measured_sloc_rule_reads_the_stored_measurement():
    user = UserFactory()
    small, big = _job(user), _job(user)
    make_run(small, measured_sloc=20)
    make_run(big, measured_sloc=500)

    selection = select_measured([small.id, big.id], rules={"min_measured_sloc": 100})

    assert set(selection.measured) == {big.id}
    assert selection.excluded == {small.id: ["below_min_sloc"]}


def test_selection_accepts_job_ids_as_strings():
    user = UserFactory()
    job = _job(user)
    make_run(job)

    selection = select_measured([str(job.id)])

    assert set(selection.measured) == {job.id}
    assert selection.status_of(str(job.id)) == "measured"
    assert [r.job_id for r in selection.runs_for([str(job.id)])] == [job.id]


# ── reference plan: plans that can produce the ranked outcome win ─────────────


def _ai_only_run(job, plan_hash, **kwargs):
    """A complete AI-reviewer-only run: no static tool, static measurement unavailable."""
    return make_run(
        job,
        plan_hash=plan_hash,
        tools={"ai_review": "succeeded"},
        static_available=False,
        ai={"high": 1},
        **kwargs,
    )


def test_ai_only_re_review_of_more_jobs_never_becomes_the_reference_plan():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/ref")
    jobs = [_job(user, model) for _ in range(3)]
    for job in jobs[:2]:
        make_run(job, plan_hash=PLAN_A, issues={"high": 2})
    for job in jobs:
        _ai_only_run(job, PLAN_B)  # newer, and covers one job more

    selection = select_measured([j.id for j in jobs])

    assert selection.plan_hash == PLAN_A
    assert list(selection.plan_counts) == [PLAN_A, PLAN_B]  # reference first
    assert selection.plan_details[PLAN_A] == {
        "jobs": 2,
        "static_jobs": 2,
        "static_tools": 1,
        "last_used": selection.plan_details[PLAN_A]["last_used"],
    }
    assert selection.plan_details[PLAN_B]["static_jobs"] == 0
    assert selection.plan_details[PLAN_B]["static_tools"] == 0
    coverage = selection.coverage()
    assert coverage["plan_rule"] == "most_static_measured_jobs"
    assert coverage["plan_details"][PLAN_B]["jobs"] == 3

    built = services.build_rankings(user)
    row = next(r for r in built["rows"] if r["model_id"] == model.model_id)
    assert built["comparability"]["plan_hash"] == PLAN_A
    assert row["n_static"] == 2
    assert row["empirical_quality_score"] is not None


def test_ai_only_re_review_of_the_same_jobs_does_not_win_the_tie():
    user = UserFactory()
    jobs = [_job(user) for _ in range(3)]
    for job in jobs:
        _age(make_run(job, plan_hash=PLAN_A), 30)
        _ai_only_run(job, PLAN_B)  # same jobs, newer

    selection = select_measured([j.id for j in jobs])

    assert selection.plan_hash == PLAN_A
    assert selection.plan_counts == {PLAN_A: 3, PLAN_B: 3}
    assert all(r.static_available for r in selection.measured.values())


def test_narrower_re_run_of_the_same_jobs_does_not_replace_the_full_plan():
    user = UserFactory()
    jobs = [_job(user) for _ in range(2)]
    full = {"bandit": "succeeded", "semgrep": "succeeded", "ruff": "succeeded"}
    for job in jobs:
        _age(make_run(job, plan_hash=PLAN_A, tools=full), 30)
        make_run(job, plan_hash=PLAN_B, tools={"ruff": "succeeded"})  # quick check, newer

    selection = select_measured([j.id for j in jobs])

    assert selection.plan_hash == PLAN_A
    assert selection.plan_details[PLAN_A]["static_tools"] == 3
    assert selection.plan_details[PLAN_B]["static_tools"] == 1


def test_only_ai_only_plans_still_select_one_and_explicit_plan_wins():
    user = UserFactory()
    job, other = _job(user), _job(user)
    _ai_only_run(job, PLAN_B)
    make_run(other, plan_hash=PLAN_A)

    assert select_measured([job.id]).plan_hash == PLAN_B
    pinned = select_measured([job.id, other.id], plan_hash=PLAN_B)
    assert pinned.plan_hash == PLAN_B
    assert pinned.coverage()["plan_rule"] == "explicit"


# ── functional: one instrument, missing is missing ────────────────────────────


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"status": "passed", "pass_rate": 1.0, "source": "conformance"}, 1.0),
        ({"status": "failed", "pass_rate": 0.25, "source": "conformance"}, 0.25),
        ({"status": "build_failed", "pass_rate": 0.0, "source": "conformance"}, 0.0),
        ({"status": "not_applicable", "pass_rate": 0.0, "source": "conformance"}, None),
        ({"status": "infra_error", "pass_rate": None, "source": "conformance"}, None),
        ({"status": "not_evaluated", "pass_rate": 0.0, "source": None}, None),
        ({"status": "passed", "pass_rate": 1.0, "source": "smoke"}, None),
        ({"status": "passed", "pass_rate": 1.7, "source": "conformance"}, None),
        ({}, None),
    ],
)
def test_functional_rate_counts_only_real_conformance_rates(block, expected):
    from backend.rankings.services.measured import functional_rate

    assert functional_rate(block) == expected


def test_rankings_functional_ignores_not_applicable_and_other_instruments():
    user = UserFactory()
    model = LLMModelFactory(model_id="m/fn2")
    make_run(_job(user, model), functional_pass_rate=0.0, functional_status="not_applicable")
    smoke = measurement_json(functional_pass_rate=1.0, functional_status="passed")
    smoke["functional"]["source"] = "smoke"
    make_run(_job(user, model), measurement=smoke)
    make_run(_job(user, model), functional_pass_rate=0.5, functional_status="failed")

    row = _row(user, model.model_id)

    assert row["functional_pass_rate"] == 0.5
    assert row["functional_n"] == 1
    assert row["n_trials"] == 3


# ── descriptive totals: a narrower re-run never hides the full measurement ────


def test_descriptive_runs_prefer_the_static_measurement_over_a_newer_ai_only_run():
    from backend.rankings.services.measured import descriptive_runs
    from backend.rankings.services.measured import latest_measured

    user = UserFactory()
    job = _job(user)
    full = make_run(job, plan_hash=PLAN_A, findings={"high": 5}, ai={"low": 1})
    _age(full, 60)
    review = _ai_only_run(job, PLAN_B)  # newer AI-only re-review

    described = descriptive_runs([job.id])

    assert described.runs[job.id].run_id == full.id
    assert described.static[job.id].findings_total() == 5
    assert described.ai[job.id].run_id == review.id  # the latest AI opinion
    assert latest_measured([job.id])[job.id].run_id == full.id


def test_descriptive_runs_prefer_the_broader_tool_set_then_the_newest():
    from backend.rankings.services.measured import descriptive_runs

    user = UserFactory()
    job, rerun_job, ai_only_job = _job(user), _job(user), _job(user)
    full = make_run(job, plan_hash=PLAN_A, tools={"bandit": "succeeded", "ruff": "succeeded"}, findings={"high": 5})
    _age(full, 60)
    make_run(job, plan_hash=PLAN_B, tools={"ruff": "succeeded"}, findings={"high": 1})  # newer quick check
    old = make_run(rerun_job, plan_hash=PLAN_A, findings={"high": 9})
    _age(old, 60)
    new = make_run(rerun_job, plan_hash=PLAN_A, findings={"high": 3})  # same plan re-run: newest wins
    only_ai = _ai_only_run(ai_only_job, PLAN_B)

    described = descriptive_runs([job.id, rerun_job.id, ai_only_job.id])

    assert described.runs[job.id].run_id == full.id
    assert described.runs[rerun_job.id].run_id == new.id
    # A job with no static measurement at all is still represented (counted
    # as measured, never as zero static findings).
    assert described.runs[ai_only_job.id].run_id == only_ai.id
    assert ai_only_job.id not in described.static
    assert described.ai[ai_only_job.id].run_id == only_ai.id
