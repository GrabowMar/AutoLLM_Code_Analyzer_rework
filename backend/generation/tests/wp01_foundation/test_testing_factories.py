"""WP-01 testing infrastructure: every factory builds a valid (``full_clean``) row.

Until WP-02a/WP-02b land, run with ``--ds=backend.generation.tests.wp01_foundation.settings_u1``.
"""

from __future__ import annotations

import pytest
from django.apps import apps

from backend.generation.models import ImmutableVersionError
from backend.generation.models import choices
from backend.generation.testing import factories
from backend.generation.tests import factories as shim

pytestmark = pytest.mark.django_db

FACTORY_NAMES = [name for name in factories.__all__ if name.endswith("Factory")]


def _factory(name: str):
    return getattr(factories, name)


def test_every_generation_model_has_a_factory():
    models = {model for model in apps.get_app_config("generation").get_models() if not model._meta.auto_created}
    covered = {_factory(name)._meta.model for name in FACTORY_NAMES}
    assert models - covered == set()


@pytest.mark.parametrize("name", FACTORY_NAMES)
def test_factory_default_is_valid(name):
    instance = _factory(name).create()
    instance.full_clean()
    instance.refresh_from_db()
    instance.full_clean()


@pytest.mark.parametrize("name", FACTORY_NAMES)
def test_factory_builds_twice_without_collisions(name):
    first, second = _factory(name).create_batch(2)
    assert first.pk != second.pk


@pytest.mark.parametrize(
    "traits",
    [
        {"tested": True},
        {"slot_single_shot": True},
        {"slot_agent": True},
        {"reference": True},
    ],
    ids=lambda t: next(iter(t)),
)
def test_run_traits_are_valid(traits):
    run = factories.GenerationRunFactory.create(**traits)
    run.full_clean()
    assert run.protocol == {"app_agentic": "app@1", "reference": "ref@1"}.get(run.kind, "slot@1")
    if run.is_slot_variant:
        assert run.parent_run is not None
        assert run.slot_target is not None
        assert run.parent_run == run.slot_target.slot_set.parent_run
        assert run.task_version == run.parent_run.task_version
        assert run.lane == (choices.Lane.LLM if run.kind == choices.RunKind.SLOT_SINGLE_SHOT else choices.Lane.SANDBOX)
    if traits.get("tested"):
        assert run.usable is True
        assert run.tree_hash


@pytest.mark.parametrize("traits", [{}, {"slot_single_shot": True}, {"reference": True}], ids=["app", "slot", "ref"])
def test_manifest_agrees_with_its_run(traits):
    run = factories.GenerationRunFactory.create(**traits)
    manifest = factories.RunManifestFactory.create(run=run)
    manifest.full_clean()
    assert manifest.protocol == run.protocol
    assert manifest.manifest["kind"] == run.kind


def test_manifest_copies_the_run_identity_keys():
    run = factories.GenerationRunFactory.create(run_fingerprint="c" * 64, task_hash="d" * 64)
    manifest = factories.RunManifestFactory.create(run=run)
    manifest.full_clean()
    assert manifest.manifest["identity"]["run_fingerprint"] == "c" * 64
    assert manifest.manifest["identity"]["task_hash"] == "d" * 64


def test_library_system_trait_and_derived_columns():
    stack = factories.StackProfileFactory.create(system=True)
    stack.full_clean()
    assert (stack.is_system, stack.visibility) == (True, choices.Visibility.SYSTEM)
    assert (stack.backend_framework, stack.frontend_framework, stack.database) == ("flask", "react", "sqlite")
    starter = factories.StarterFactory.create(stack_profile=stack)
    assert starter.content["stack_profile"] == stack.pinned_ref
    assert starter.level == "minimal"
    assert starter.tree_hash


def test_task_versions_number_themselves_and_track_the_header():
    first = factories.TaskSpecVersionFactory.create()
    second = factories.TaskSpecVersionFactory.create(task=first.task)
    assert (first.version, second.version) == (1, 2)
    first.task.refresh_from_db()
    assert first.task.latest_version == 2
    assert second.unit_key == f"task:{first.task.slug}@2"


def test_attempts_number_themselves_per_run():
    run = factories.GenerationRunFactory.create()
    attempts = factories.RunAttemptFactory.create_batch(3, run=run)
    assert [a.attempt for a in attempts] == [1, 2, 3]
    assert factories.RunAttemptFactory.create().attempt == 1


def test_study_tree_rows_share_one_plan_and_study():
    cell = factories.StudyCellFactory.create()
    cell.full_clean()
    assert cell.plan == cell.condition.plan == cell.unit.plan
    assert cell.study == cell.plan.study == cell.unit.study
    assert cell.condition.condition_hash
    plan = factories.StudyPlanFactory.create()
    assert plan.design_hash == plan.study.design_hash


def test_study_design_names_its_budget_preset_and_mirrors_columns():
    study = factories.StudyFactory.create()
    study.full_clean()
    assert study.design["resource_policy"] == study.resource_policy.ref
    assert (study.kind, study.mode, study.repeats) == ("exploratory", "app", 2)
    assert study.design_hash


def test_slot_unit_trait():
    unit = factories.StudyUnitFactory.create(slot=True)
    unit.full_clean()
    assert unit.task_version is None
    assert unit.slot_target is not None
    assert unit.unit_key == f"slot:{unit.slot_target.pk}"


def test_slot_rows_derive_from_their_parent():
    slot_set = factories.SlotSetFactory.create()
    slot_set.full_clean()
    parent = slot_set.parent_run
    assert slot_set.frozen_tree_hash == parent.tree_hash
    assert slot_set.index.run == parent
    target = factories.SlotTargetFactory.create(slot_set=slot_set, path="backend/todos.py")
    target.full_clean()
    assert (target.kind, target.allowlist, target.label) == ("file", ["backend/todos.py"], "backend/todos.py")
    assert target.target_hash
    cluster = factories.VariantClusterFactory.create(slot_target=target)
    assert cluster.representative.slot_target == target


def test_evaluation_measured_trait_is_frozen():
    evaluation = factories.RunEvaluationFactory.create(measured=True)
    evaluation.full_clean()
    assert evaluation.status == choices.EvaluationStatus.MEASURED
    assert evaluation.result is not None
    assert evaluation.result["measured"] is True
    evaluation.status = choices.EvaluationStatus.ERROR
    with pytest.raises(ImmutableVersionError):
        evaluation.save()


@pytest.mark.parametrize("trait", ["error", "present"])
def test_pattern_hit_traits(trait):
    hit = factories.PatternHitFactory.create(**{trait: True})
    hit.full_clean()
    assert hit.feature_key == hit.detector.feature_key
    if trait == "error":
        assert hit.error_reason == choices.HitErrorReason.TOOL_ERROR


def test_cluster_label_uses_a_manual_detector():
    label = factories.ClusterLabelFactory.create()
    assert label.detector.engine == choices.DetectorEngine.MANUAL


def test_tests_factories_shim_reexports_exactly_the_v3_names():
    assert shim.__all__ == factories.__all__
    for name in shim.__all__:
        assert getattr(shim, name) is getattr(factories, name)
    v2_names = {
        "GenerationJobFactory",
        "ExperimentFactory",
        "ExperimentConditionFactory",
        "GenerationProfileFactory",
        "AppRequirementTemplateFactory",
        "ContentBlockFactory",
    }
    assert v2_names.isdisjoint(dir(shim))
