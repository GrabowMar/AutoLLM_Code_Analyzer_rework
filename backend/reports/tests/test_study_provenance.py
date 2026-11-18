from __future__ import annotations

import json

import pytest

from backend.generation.models import GenerationRun
from backend.generation.testing import content
from backend.generation.testing import factories
from backend.reports.services import study_report
from backend.reports.services.study_provenance import study_provenance
from backend.reports.tests.helpers import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _report(runs):
    rows, _issues = study_report.app_table(runs)
    study_report.label_groups(rows)
    return study_provenance(runs, rows), rows


def _evaluation(run, *, probes=(), suite=None, stages=None, **kwargs):
    result = content.eval_result_content()
    result["probes"] = list(probes)
    result["plan_sha256"] = "a" * 64
    result["fixtures_sha256"] = "b" * 64
    result["checks"][0].update(endpoint="todos.list", source="compiler", stated_in=["standard"], overlay=True)
    return factories.RunEvaluationFactory(
        run=run,
        status="measured",
        result=result,
        stages=stages or {},
        **({"eval_suite": suite} if suite else {}),
        **kwargs,
    )


def test_execution_distinguishes_unknown_attempts_and_records_lock_and_task_evidence():
    user = UserFactory()
    first = make_run(user, factories.LLMModelFactory.create())
    second = make_run(user, first.model)
    manifest = factories.RunManifestFactory(run=first)
    GenerationRun.objects.filter(pk=first.pk).update(lock_source="platform")
    first.refresh_from_db()
    factories.RunAttemptFactory(run=first, termination="harness_crash", usage={"requests_by_class": {"main": 2}})
    factories.RunAttemptFactory(run=first, termination="completed", usage={"requests_by_class": {"main": 3}})
    suite_ref = manifest.manifest["planned_evaluation"]["eval_suite"]["ref"].split("#")[0]
    slug, version = suite_ref.split("@")
    suite = factories.EvalSuiteFactory(slug=slug, version=int(version))
    evaluation = _evaluation(
        first,
        suite=suite,
        stages={
            "lock": {"status": "ok"},
            "build": {"status": "ok", "detail": {"attempts": [{"ok": False}, {"ok": True}]}},
            "tester": {"status": "ok", "detail": {"attempts": 1}},
        },
    )
    data, rows = _report([first, second])
    execution = {r["run_id"]: r for r in data["execution"]["runs"]}
    known, unknown = execution[str(first.pk)], execution[str(second.pk)]
    assert known["generation_attempts"] == 2
    assert known["calls_by_class"] == {"main": 5}
    assert known["lock_source"] == "platform"
    assert known["source_policy"] == "collected_source_unchanged"
    assert known["build_attempts"] == 2
    assert known["tester_attempts"] == 1
    assert known["evaluation_id"] == evaluation.pk
    assert known["pricing"]["source"] == "openrouter"
    assert known["cost_basis"] == "billed"
    assert unknown["generation_attempts"] is None
    assert unknown["source_policy"] == "unknown"
    assert unknown["build_attempts"] is None
    assert data["execution"]["coverage"]["runs_with_attempt_records"] == 1
    assert data["execution"]["coverage"]["manifests_recorded"] == 1
    task = next(t for t in data["requirements"]["tasks"] if t["task"] == first.task_version.ref)
    assert task["endpoints"]
    assert task["structured_hash"] == first.task_version.structured_hash
    assert task["oracle"] == {}
    assert task["selected_instruments"] == [{"stack": rows[0]["stack"], "eval_suite": suite.ref}]
    check = next(c for c in data["requirements"]["checks"] if c["endpoint"] == "todos.list")
    assert check["plan_sha256"] == "a" * 64
    assert check["stated_in"] == ["standard"]
    assert check["statuses"] == {"pass": 1}
    assert rows[0]["lock_source"] == "platform"
    assert any(s["deployed_rate"] == 1 for s in data["execution"]["lock_source_strata"])
    json.dumps(data)  # portable report payload, without model instances or UUID objects


def test_probe_bounds_exclude_skips_errors_and_retests_and_count_app_repeats_once():
    user = UserFactory()
    first = make_run(user, factories.LLMModelFactory.create())
    second = make_run(user, first.model, first.task_version)
    third = make_run(user, first.model)
    clean = {"id": "idor", "kind": "idor_enumeration", "cwe": "CWE-639", "status": "not_exploited"}
    evaluation = _evaluation(first, probes=[clean])
    _evaluation(first, probes=[{**clean, "status": "exploited"}], suite=evaluation.eval_suite, retest_of=evaluation)
    _evaluation(second, probes=[clean], suite=evaluation.eval_suite)
    _evaluation(third, probes=[{**clean, "status": "skipped", "skip_reason": "precondition_failed"}])
    data, _rows = _report([first, second, third])
    measured = next(r for r in data["runtime_security"]["by_probe"] if r["task"] == first.task_version.ref)
    assert measured["runs_measured"] == 2
    assert measured["apps_measured"] == 1
    assert measured["apps_exploited"] == 0
    assert measured["zero_rate_upper95"] is None
    assert measured["ci95"] is None
    assert measured["inference_status"] == "descriptive_fixed_task_endpoint_set"
    skipped = next(r for r in data["runtime_security"]["by_probe"] if r["task"] == third.task_version.ref)
    assert skipped["apps_measured"] == 0
    assert skipped["zero_rate_upper95"] is None
    assert skipped["skip_reasons"] == {"precondition_failed": 1}


def test_provenance_scope_does_not_include_unselected_runs_and_flags_mixed_studies():
    user = UserFactory()
    first = make_run(user, factories.LLMModelFactory.create(), study=factories.StudyFactory(created_by=user))
    second = make_run(user, first.model)
    outsider = make_run(UserFactory(), first.model)
    factories.RunAttemptFactory(run=outsider, termination="completed")
    data, _rows = _report([first, second])
    coverage = data["execution"]["coverage"]
    assert coverage["mixed_study_scope"]
    assert coverage["includes_ad_hoc"]
    assert coverage["studies"] == [str(first.study_id)]
    assert not data["execution"]["attempts"]
    assert coverage["runs_with_attempt_records"] == 0
    assert data["execution"]["study_history"] is None
    assert {row["task"] for row in data["requirements"]["coverage"]} == {
        first.task_version.ref,
        second.task_version.ref,
    }


def test_task_oracle_keeps_recorded_stack_and_suite_and_does_not_assume_current_validation():
    run = make_run(UserFactory(), factories.LLMModelFactory.create())
    oracle = {
        "another-stack@1": {
            "status": "pass",
            "run_id": "recorded-reference",
            "eval_suite_ref": "older-suite@1",
            "at": None,
        },
    }
    run.task_version.oracle = oracle
    run.task_version.save(update_fields=["oracle"])
    evaluation = _evaluation(run)
    data, _rows = _report([run])
    task = data["requirements"]["tasks"][0]
    assert task["oracle"] == oracle
    assert task["selected_instruments"][0]["eval_suite"] == evaluation.eval_suite.ref
    assert task["selected_instruments"][0]["eval_suite"] != oracle["another-stack@1"]["eval_suite_ref"]


def test_missing_evaluation_does_not_erase_the_frozen_planned_instrument():
    run = make_run(UserFactory(), factories.LLMModelFactory.create(), outcome="output_invalid")
    manifest = factories.RunManifestFactory(run=run)
    planned = manifest.manifest["planned_evaluation"]["eval_suite"]["ref"].split("#", 1)[0]
    data, _rows = _report([run])
    assert data["requirements"]["tasks"][0]["selected_instruments"][0]["eval_suite"] == planned
    assert data["execution"]["runs"][0]["planned_eval_suite"] == planned
    assert data["execution"]["runs"][0]["evaluation_id"] is None


def test_selected_study_history_discloses_unselected_infrastructure_attempts():
    user = UserFactory()
    study = factories.StudyFactory(created_by=user)
    selected = make_run(user, factories.LLMModelFactory.create(), study=study)
    extra = make_run(user, selected.model, study=study, outcome="infra_error", status="failed")
    GenerationRun.objects.filter(pk=extra.pk).update(outcome_reason="llm_error")
    outsider = make_run(user, selected.model)
    factories.RunAttemptFactory(run=extra, termination="harness_crash")
    rows, _ = study_report.app_table([selected])
    study_report.label_groups(rows)
    history = study_provenance([selected], rows, study=study)["execution"]["study_history"]
    assert (history["recorded_runs"], history["selected_runs"], history["nonselected_runs"]) == (2, 1, 1)
    assert history["nonselected_by_outcome"] == {"infra_error": 1}
    assert history["nonselected_by_reason"] == {"llm_error": 1}
    assert history["recorded_harness_attempts"] == 1
    assert {r["run_id"] for r in history["nonselected"]} == {str(extra.pk)}
    assert str(outsider.pk) not in json.dumps(history)
