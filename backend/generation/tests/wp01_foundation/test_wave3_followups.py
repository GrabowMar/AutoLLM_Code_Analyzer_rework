"""Wave-3 follow-ups of WP-01 (CHANGES "Wave 3 follow-up status"): manifest pins, RunFile modes, fakes."""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.db import transaction
from pydantic import ValidationError

from backend.generation.contracts.manifest import RunManifest as ManifestContract
from backend.generation.evaluation import reuse
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.generation.testing.content import manifest_content
from backend.generation.testing.fake_eval import TESTER_VERSIONS
from backend.generation.testing.fake_eval import FakeEvaluator

pytestmark = pytest.mark.django_db


def test_runfile_mode_holds_permission_bits_only():
    factories.RunFileFactory.create(mode=0o755)
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.RunFileFactory.create(mode=-1)
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.RunFileFactory.create(mode=0o10000)


def test_sample_manifests_carry_pinned_refs_only():
    payload = manifest_content()
    ManifestContract.model_validate(payload)
    assert "#" in payload["task"]["ref"]
    assert "#" in payload["planned_evaluation"]["eval_suite"]["ref"]
    with pytest.raises(ValidationError):
        ManifestContract.model_validate(manifest_content(refs={"sampling": "t07@1"}))


def test_run_manifest_names_the_runs_own_library_rows():
    run = factories.GenerationRunFactory.create(tested=True)
    manifest = factories.RunManifestFactory.create(run=run).manifest
    assert manifest["task"]["ref"] == run.task_version.pinned_ref
    for key, row in (("sampling", run.sampling_preset), ("resources", run.resource_policy)):
        if row is not None:
            assert manifest[key]["ref"] == row.pinned_ref


def test_fake_evaluator_writes_the_real_selection_detail_and_is_reusable():
    run = factories.GenerationRunFactory.create(tested=True, tree_hash="c" * 64)
    twin = factories.GenerationRunFactory.create(tested=True, tree_hash="c" * 64)
    suite = factories.EvalSuiteFactory.create()
    evaluation = FakeEvaluator().evaluate(run, suite, only_checks=["b", "a"], eval_level="security")
    detail = evaluation.stages["tester"]["detail"]
    expected = reuse.selection_detail(only_checks=["b", "a"], probes=None)
    assert {key: detail[key] for key in expected} == expected
    assert detail["tester_rules_version"] == TESTER_VERSIONS["tester_rules_version"]
    assert detail["tester_version"] == suite.tester_version
    found = reuse.find_reusable(twin, suite, only_checks=["a", "b"], eval_level="security", adopt=False)
    assert found == evaluation
    assert reuse.find_reusable(twin, suite, only_checks=["a"], eval_level="security", adopt=False) is None


def test_fake_evaluator_fills_a_pending_row_and_marks_retests():
    run = factories.GenerationRunFactory.create(tested=True)
    suite = factories.EvalSuiteFactory.create()
    pending = RunEvaluation.objects.create(run=run, eval_suite=suite, eval_level=choices.EvalLevel.SECURITY)
    filled = FakeEvaluator().evaluate(run, suite, evaluation=pending, eval_level="full")
    assert filled.pk == pending.pk
    pending.refresh_from_db()
    assert pending.status == choices.EvaluationStatus.MEASURED
    assert pending.eval_level == "full"
    retest = FakeEvaluator().evaluate(run, suite, retest_of=pending)
    assert retest.retest_of_id == pending.pk
    assert RunEvaluation.objects.filter(run=run).count() == 2
