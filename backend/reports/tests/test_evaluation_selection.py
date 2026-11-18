"""Versioned reanalysis must keep one instrument and preserve the original sample."""

import pytest

from backend.generation.runs.view import run_view
from backend.generation.testing import factories
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.services.evaluation_selection import selected_suite
from backend.reports.services.report_service import _validate_config
from backend.reports.services.study_report import exploratory_reasons
from backend.reports.services.study_report import generate_study_report
from backend.reports.tests.helpers import evaluate
from backend.reports.tests.helpers import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def planned_run(user, model, suite, **kwargs):
    run = make_run(user, model, **kwargs)
    manifest = factories.run_manifest_content(run)
    manifest["planned_evaluation"]["eval_suite"]["ref"] = suite.pinned_ref
    factories.RunManifestFactory(run=run, manifest=manifest)
    return run


def report(runs, suite=None):
    config = {"run_ids": [str(r.pk) for r in runs], "ranking_draws": 500, "bootstrap": False}
    if suite:
        config["eval_suite"] = suite.pinned_ref
    return generate_study_report(config, user=runs[0].created_by)


def test_explicit_suite_changes_grades_and_provenance_without_mixing_or_changing_samples():
    user, model = UserFactory(), LLMModelFactory()
    first = factories.EvalSuiteFactory()
    second = factories.EvalSuiteFactory(slug=first.slug, version=2)
    graded = planned_run(user, model, first)
    missing = planned_run(user, model, first)
    evaluate(graded, passed=4, failed=0, suite=first)
    evaluate(missing, passed=4, failed=0, suite=first)
    updated = evaluate(graded, passed=1, failed=3, suite=second)

    original = report([graded, missing])
    revised = report([graded, missing], second)
    assert {r["run_id"] for r in original["per_app"]} == {r["run_id"] for r in revised["per_app"]}
    assert all(r["pass_rate"] == 1 for r in original["per_app"])
    rows = {r["run_id"]: r for r in revised["per_app"]}
    assert rows[str(graded.pk)]["pass_rate"] == 0.25
    assert rows[str(missing.pk)]["pass_rate"] is None
    assert rows[str(missing.pk)]["evaluated"] is False
    assert revised["protocol"]["evaluation_selection"]["generated_without_evaluation"] == 1
    assert revised["protocol"]["evaluation_selection"]["changed_from_plan_runs"] == 2
    executions = {r["run_id"]: r for r in revised["protocol"]["execution"]["runs"]}
    assert executions[str(graded.pk)]["evaluation_id"] == updated.pk
    assert executions[str(graded.pk)]["planned_eval_suite"] == first.ref
    assert executions[str(missing.pk)]["evaluation_id"] is None
    for task in revised["protocol"]["requirements"]["tasks"]:
        assert all(i["eval_suite"] == second.ref for i in task["selected_instruments"])
    assert {c["eval_suite"] for c in revised["protocol"]["requirements"]["checks"]} == {second.ref}
    assert "evaluation_suite_changed" in {v["code"] for v in revised["validity"]}


def test_explicit_suite_uses_latest_final_not_pending_or_retest_and_does_not_pick_best():
    user, model = UserFactory(), LLMModelFactory()
    suite = factories.EvalSuiteFactory()
    run = planned_run(user, model, suite)
    final = evaluate(run, passed=1, failed=3, suite=suite)
    factories.RunEvaluationFactory(run=run, eval_suite=suite, status="pending")
    factories.RunEvaluationFactory(run=run, eval_suite=suite, status="measured", retest_of=final)
    assert report([run], suite)["per_app"][0]["pass_rate"] == 0.25
    failed = factories.RunEvaluationFactory(run=run, eval_suite=suite, status="error", result=None)
    assert run_view(run).latest_evaluation(suite.pinned_ref, final_only=True).pk == failed.pk
    assert report([run], suite)["per_app"][0]["pass_rate"] is None


@pytest.mark.parametrize("ref", ["unversioned", "missing@999", True, 12, "x@1#000000000000"])
def test_invalid_suite_rejected_before_dispatch(ref):
    with pytest.raises(ValueError, match="suite"):
        _validate_config("study_report", {"eval_suite": ref})


def test_suite_pin_is_checked_and_normalized():
    suite = factories.EvalSuiteFactory()
    assert selected_suite({"eval_suite": suite.ref}).pinned_ref == suite.pinned_ref
    wrong = f"{suite.ref}#000000000000"
    with pytest.raises(ValueError, match="suite"):
        selected_suite({"eval_suite": wrong})
    run = planned_run(UserFactory(), LLMModelFactory(), suite)
    evaluate(run, suite=suite)
    assert run_view(run).latest_evaluation(wrong, final_only=True) is None


def test_changed_instrument_cannot_inherit_confirmatory_status():
    design = {"plan_locked": True, "interim": False, "stopped_early": False, "evaluation_suite_changed": True}
    assert "the evaluation suite differs from the planned instrument" in exploratory_reasons(
        {"primary_metric": "pass_rate"},
        design,
    )
