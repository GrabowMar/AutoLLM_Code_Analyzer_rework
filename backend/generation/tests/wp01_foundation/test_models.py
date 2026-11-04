"""v3 models: library immutability, derived columns, contract validation and database constraints."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.db.models import RestrictedError

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts import outcome as outcome_contracts
from backend.generation.contracts import plan as plan_contracts
from backend.generation.contracts.library import CONTENT_MODELS
from backend.generation.contracts.library import SCHEMA_VERSIONS
from backend.generation.models import LIBRARY_MODELS
from backend.generation.models import Blob
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import ImmutableVersionError
from backend.generation.models import PatternDetector
from backend.generation.models import PatternHit
from backend.generation.models import PatternRun
from backend.generation.models import ResourcePolicy
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import RunManifest
from backend.generation.models import RunSymbolIndex
from backend.generation.models import SlotSet
from backend.generation.models import SlotTarget
from backend.generation.models import SpendCap
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import StackProfile
from backend.generation.models import Starter
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models import StudyPlan
from backend.generation.models import StudyUnit
from backend.generation.models import TaskSpec
from backend.generation.models import TaskSpecVersion
from backend.generation.models import choices
from backend.generation.models import default_stack_slug
from backend.generation.tests.wp01_foundation.test_contracts import LIBRARY_SAMPLES
from backend.generation.tests.wp01_foundation.test_contracts import design_payload
from backend.generation.tests.wp01_foundation.test_contracts import manifest_payload
from backend.inference.models import LLMEndpoint
from backend.llm_models.tests.factories import LLMModelFactory

SHA = "a" * 64
SHA2 = "b" * 64

pytestmark = pytest.mark.django_db


# =====================================================================================
# Builders
# =====================================================================================


def make_entity(model, *, slug: str | None = None, version: int = 1, content: dict | None = None, **extra):
    content = copy.deepcopy(LIBRARY_SAMPLES[model.KIND]) if content is None else content
    return model.objects.create(
        slug=slug or f"{model.KIND.replace('_', '-')}-x",
        version=version,
        name=f"{model.KIND} v{version}",
        content=content,
        **extra,
    )


def make_starter(profile: StackProfile, **extra) -> Starter:
    content = copy.deepcopy(LIBRARY_SAMPLES["starter"])
    content["stack_profile"] = profile.pinned_ref
    return make_entity(Starter, content=content, stack_profile=profile, **extra)


def make_task_version(slug: str = "crud_todo_list", version: int = 1, **extra) -> TaskSpecVersion:
    task, _ = TaskSpec.objects.get_or_create(slug=slug)
    return TaskSpecVersion.objects.create(
        task=task,
        version=version,
        name="Todo",
        content=copy.deepcopy(LIBRARY_SAMPLES["task"]),
        **extra,
    )


def shared_task_version() -> TaskSpecVersion:
    """The v1 of the fixture task, created on first use (runs of one test share it)."""
    existing = TaskSpecVersion.objects.filter(task__slug="crud_todo_list", version=1).first()
    return existing or make_task_version()


def make_policy() -> ResourcePolicy:
    return make_entity(ResourcePolicy, slug="frugal", tier=choices.PolicyTier.FRUGAL)


def make_study(**extra) -> Study:
    policy = extra.pop("resource_policy", None) or make_policy()
    return Study.objects.create(
        name="Study",
        slug="study",
        design=extra.pop("design", design_payload()),
        resource_policy=policy,
        **extra,
    )


def make_run(**extra) -> GenerationRun:
    kind = extra.pop("kind", choices.RunKind.APP_AGENTIC)
    if kind == choices.RunKind.APP_AGENTIC:
        extra.setdefault("task_version", shared_task_version())
    if kind != choices.RunKind.REFERENCE:
        extra.setdefault("model", LLMModelFactory())
    return GenerationRun.objects.create(kind=kind, **extra)


def make_slot_set(parent: GenerationRun | None = None) -> SlotSet:
    parent = parent or make_run(outcome=choices.Outcome.TESTED, usable=True, tree_hash=SHA)
    index = RunSymbolIndex.objects.create(
        run=parent,
        index_version=1,
        blob=SHA2,
        stats={"files": 2, "symbols": 3, "endpoints": 1, "components": 0},
    )
    return SlotSet.objects.create(
        name="parent",
        parent_run=parent,
        task_version=parent.task_version,
        parent_model=parent.model.model_id,
        selection={"rule": "manual"},
        frozen_tree_hash=parent.tree_hash,
        parent_manifest_hash=SHA,
        index=index,
    )


def make_target(slot_set: SlotSet, **extra) -> SlotTarget:
    return SlotTarget.objects.create(
        slot_set=slot_set,
        label="main.py",
        address={"kind": "file", "path": "app/main.py", "content_sha": SHA2},
        language="python",
        original_sha=SHA2,
        original_blob=SHA2,
        **extra,
    )


def make_plan(study: Study) -> StudyPlan:
    return StudyPlan.objects.create(
        study=study,
        plan_hash=SHA,
        design_hash=study.design_hash,
        payload_blob=SHA2,
        n_conditions=1,
        n_units=1,
        n_cells=1,
        n_blocks=1,
        resolver_version=1,
    )


# =====================================================================================
# Choices mirror the contracts
# =====================================================================================


def test_choices_mirror_contract_enums():
    assert set(choices.Outcome.values) == {o.value for o in outcome_contracts.Outcome}
    assert set(choices.Termination.values) == {t.value for t in outcome_contracts.Termination}
    assert set(choices.ExclusionReason.values) == {r.value for r in outcome_contracts.ExclusionReason}
    assert tuple(choices.RunKind.values) == identity.RUN_KINDS
    assert tuple(choices.Protocol.values) == identity.PROTOCOLS
    assert set(choices.CellExclusionReason.values) <= set(choices.ExclusionReason.values)


def test_library_models_cover_every_contract_kind():
    assert set(LIBRARY_MODELS) == set(CONTENT_MODELS)
    for kind, model in LIBRARY_MODELS.items():
        assert kind == model.KIND
        assert model.CONTRACT_FIELDS["content"].adapter.core_schema  # wired to the kind's contract


def test_default_stack_slug_kept_for_migration_0016():
    assert default_stack_slug() == "flask-react"


# =====================================================================================
# Library: content hashing, denormalized columns and immutability
# =====================================================================================


def test_create_normalizes_content_and_fills_content_hash():
    profile = make_entity(StackProfile)
    normalized = CONTENT_MODELS["stack"].model_validate(LIBRARY_SAMPLES["stack"]).model_dump(mode="json")
    assert profile.content == normalized
    assert profile.schema_version == SCHEMA_VERSIONS["stack"]
    assert profile.content_hash == identity.library_content_hash("stack", 1, normalized)
    assert profile.content_hash == StackProfile.compute_content_hash(LIBRARY_SAMPLES["stack"])
    # denormalized query columns come from the content
    assert (profile.backend_framework, profile.frontend_framework, profile.database) == ("flask", "react", "sqlite")
    assert profile.ref == "stack-x@1"
    assert profile.pinned_ref == f"stack-x@1#{profile.content_hash[:12]}"


def test_equal_content_hashes_equal_across_slugs_and_names():
    a = make_entity(StackProfile, slug="a")
    b = make_entity(StackProfile, slug="b", description="other text")
    assert a.content_hash == b.content_hash


@pytest.mark.parametrize("model", list(LIBRARY_MODELS.values()), ids=lambda m: m.KIND)
def test_every_kind_round_trips_through_full_clean(model):
    if model is TaskSpecVersion:
        entity = make_task_version()
    elif model is Starter:
        entity = make_starter(make_entity(StackProfile))
    else:
        entity = make_entity(model)
    entity.full_clean()
    entity.refresh_from_db()
    assert entity.content_hash == identity.library_content_hash(
        model.KIND,
        SCHEMA_VERSIONS[model.KIND],
        entity.content,
    )


def test_content_is_immutable_after_creation():
    profile = make_entity(StackProfile)
    profile.content = {**profile.content, "database": "postgres"}
    with pytest.raises(ImmutableVersionError, match="immutable"):
        profile.save()
    profile.refresh_from_db()
    assert profile.database == "sqlite"


@pytest.mark.parametrize("field", ["slug", "version", "content_hash", "schema_version"])
def test_identity_columns_are_immutable(field):
    profile = make_entity(StackProfile)
    value = {"slug": "renamed", "version": 2, "content_hash": SHA, "schema_version": 2}[field]
    setattr(profile, field, value)
    with pytest.raises(ImmutableVersionError):
        profile.save()


def test_name_description_archive_and_visibility_stay_mutable():
    profile = make_entity(StackProfile)
    profile.name = "Renamed"
    profile.description = "Edited"
    profile.is_archived = True
    profile.visibility = choices.Visibility.SHARED
    profile.save()
    profile.save(update_fields=["name"])
    profile.refresh_from_db()
    assert (profile.name, profile.is_archived, profile.visibility) == ("Renamed", True, "shared")


def test_bulk_update_of_immutable_columns_is_refused():
    make_entity(StackProfile)
    with pytest.raises(ImmutableVersionError):
        StackProfile.objects.update(content={})
    assert StackProfile.objects.update(name="bulk rename") == 1


def test_supplied_content_hash_must_match():
    with pytest.raises(ValidationError) as exc:
        make_entity(StackProfile, content_hash=SHA)
    assert "content_hash" in exc.value.message_dict
    assert not StackProfile.objects.exists()


def test_content_violating_the_contract_is_rejected_on_save_and_full_clean():
    bad = {**LIBRARY_SAMPLES["stack"], "unknown_key": 1}
    with pytest.raises(ValidationError) as exc:
        make_entity(StackProfile, content=bad)
    assert "content" in exc.value.message_dict
    entity = StackProfile(slug="s", version=1, name="s", content=bad)
    with pytest.raises(ValidationError) as exc:
        entity.full_clean()
    assert any("unknown_key" in message for message in exc.value.message_dict["content"])


def test_unique_slug_version():
    make_entity(StackProfile)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_entity(StackProfile)
    make_entity(StackProfile, version=2)
    with pytest.raises(ValidationError) as exc:
        StackProfile(slug="stack-x", version=1, name="dup", content=LIBRARY_SAMPLES["stack"]).full_clean()
    assert "__all__" in exc.value.message_dict


def test_database_refuses_an_empty_content_hash():
    # bulk_create bypasses save(): the CheckConstraint is the last line of defence.
    row = StackProfile(slug="raw", version=1, name="raw", schema_version=1, content={}, content_hash="")
    with pytest.raises(IntegrityError), transaction.atomic():
        StackProfile.objects.bulk_create([row])


def test_system_visibility_matches_is_system():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_entity(StackProfile, is_system=True)  # visibility defaults to private
    make_entity(StackProfile, slug="sys", is_system=True, visibility=choices.Visibility.SYSTEM)
    entity = StackProfile(
        slug="user",
        version=1,
        name="user",
        content=LIBRARY_SAMPLES["stack"],
        visibility=choices.Visibility.SYSTEM,
    )
    with pytest.raises(ValidationError):
        entity.full_clean()


def test_version_must_be_positive():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_entity(StackProfile, version=0)


def test_manager_latest_returns_highest_non_archived_version():
    make_entity(StackProfile, version=1)
    make_entity(StackProfile, version=2)
    make_entity(StackProfile, version=3, is_archived=True)
    assert StackProfile.objects.latest("stack-x").version == 2
    with pytest.raises(StackProfile.DoesNotExist):
        StackProfile.objects.latest("missing")
    assert [p.version for p in StackProfile.objects.latest_versions()] == [2]


def test_derived_from_links_versions():
    v1 = make_entity(StackProfile)
    v2 = make_entity(StackProfile, version=2, derived_from=v1)
    assert list(v1.derivatives.all()) == [v2]


def test_starter_tree_hash_and_profile_ref_are_checked():
    profile = make_entity(StackProfile)
    starter = make_starter(profile)
    files = starter.content["files"]
    assert starter.tree_hash == identity.tree_hash((p, e["mode"], e["blob"]) for p, e in files.items())
    assert starter.level == "minimal"
    starter.full_clean()
    other = make_entity(StackProfile, slug="other")
    mismatched = Starter(
        slug="mismatch",
        version=1,
        name="m",
        content={**LIBRARY_SAMPLES["starter"], "stack_profile": profile.pinned_ref},
        stack_profile=other,
    )
    with pytest.raises(ValidationError) as exc:
        mismatched.full_clean()
    assert "stack_profile" in exc.value.message_dict


def test_denormalized_column_must_match_content():
    with pytest.raises(ValidationError) as exc:
        make_entity(StackProfile, backend_framework="django")
    assert "backend_framework" in exc.value.message_dict


def test_eval_suite_and_detector_columns_come_from_content():
    suite = make_entity(EvalSuite)
    detector = make_entity(PatternDetector)
    assert suite.tester_version == 1
    assert (detector.engine, detector.feature_key) == ("regex", "py-sql-fstring")


# -- task versions --------------------------------------------------------------------------


def test_task_version_derives_its_hashes():
    version = make_task_version()
    content = version.content
    assert version.schema_version == 4
    assert version.content_hash == identity.library_content_hash("task", 4, content)
    assert version.structured_hash == identity.task_structured_hash(content)
    assert version.prose_hashes == identity.task_prose_hashes(content)
    assert content["prose"]["standard"] == "A todo app\r\nwith users."  # stored as given ...
    assert version.prose_hashes["standard"] == identity.text_sha("A todo app\nwith users.")  # ... hashed normalized
    assert version.ref == "crud_todo_list@1"
    assert version.unit_key == "task:crud_todo_list@1"


def test_task_version_content_is_immutable_but_lint_oracle_and_derived_are_not():
    version = make_task_version()
    version.lint = {"status": "ok", "issues": [], "linter_version": 1}
    version.oracle = {"flask-react@1": {"status": "pass", "run_id": "r1", "at": "2026-11-03T00:00:00Z"}}
    version.derived = {
        "endpoint_count": 4,
        "entity_count": 1,
        "field_count": 2,
        "relation_count": 0,
        "difficulty_score": 0.3,
        "size_bucket": "S",
    }
    version.save()
    version.full_clean()
    version.content = {**version.content, "title": "Changed"}
    with pytest.raises(ImmutableVersionError):
        version.save()
    version.refresh_from_db()
    version.structured_hash = SHA
    with pytest.raises(ImmutableVersionError):
        version.save(update_fields=["structured_hash"])


def test_task_version_mutable_json_is_contract_validated():
    version = make_task_version()
    version.lint = {"status": "maybe", "linter_version": 1}
    with pytest.raises(ValidationError) as exc:
        version.save(update_fields=["lint"])
    assert "lint" in exc.value.message_dict
    version.lint = None
    version.oracle = {"flask-react@1": {"status": "pass", "extra": 1}}
    with pytest.raises(ValidationError):
        version.full_clean()


def test_task_version_unique_per_task():
    make_task_version()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_task_version()
    assert make_task_version(version=2).task.versions.count() == 2


def test_supplied_prose_hashes_must_match():
    with pytest.raises(ValidationError) as exc:
        make_task_version(prose_hashes={"minimal": SHA})
    assert "prose_hashes" in exc.value.message_dict


# =====================================================================================
# Studies
# =====================================================================================


def test_study_mirrors_and_hashes_its_design():
    study = make_study()
    dump = plan_contracts.Design.model_validate(design_payload()).model_dump(mode="json")
    assert study.design == dump
    assert study.design_hash == identity.design_hash(dump)
    assert (study.kind, study.mode, study.repeats, study.base_seed, study.order_seed, study.scheduling) == (
        "exploratory",
        "app",
        2,
        12345,
        7,
        "auto",
    )
    study.full_clean()


def test_study_design_change_rewrites_hash_and_mirrors_with_update_fields():
    study = make_study()
    design = copy.deepcopy(study.design)
    design["repeats"] = 5
    study.design = design
    study.save(update_fields=["design"])
    study.refresh_from_db()
    assert study.repeats == 5
    assert study.design_hash == identity.design_hash(design)


def test_study_design_is_contract_validated():
    with pytest.raises(ValidationError) as exc:
        make_study(design={**design_payload(), "factors": "nope"})
    assert "design" in exc.value.message_dict


def test_study_design_policy_must_be_the_resource_policy():
    other = make_entity(ResourcePolicy, slug="generous")
    study = make_study(resource_policy=other)
    with pytest.raises(ValidationError) as exc:
        study.full_clean()
    assert "resource_policy" in exc.value.message_dict


def test_locked_study_refuses_design_policy_and_analysis_plan_changes():
    study = make_study(design={**design_payload(), "kind": "confirmatory"})
    assert study.kind == "confirmatory"
    study.locked_at = study.created_at
    study.save()
    study.name = "renamed"
    study.status = choices.StudyStatus.RUNNING
    study.save()  # other columns stay mutable
    study.analysis_plan = {"primary": "changed"}
    with pytest.raises(ImmutableVersionError, match="locked"):
        study.save()
    study.refresh_from_db()
    design = copy.deepcopy(study.design)
    design["repeats"] = 3
    study.design = design
    with pytest.raises(ImmutableVersionError):
        study.save(update_fields=["design"])


def test_study_json_columns_are_validated():
    study = make_study()
    study.stop_reason = {"code": "budget_stop", "at": "2026-11-03T00:00:00Z", "extra": 1}
    with pytest.raises(ValidationError):
        study.save()
    study.stop_reason = {"code": "budget_stop", "at": "2026-11-03T00:00:00Z"}
    study.execution_options = {"max_resident_models": 1, "warmup": False}
    study.save()
    study.pause_reason = "because"
    with pytest.raises(ValidationError) as exc:
        study.full_clean()
    assert "pause_reason" in exc.value.message_dict
    study.pause_reason = "rule:cost_overrun"
    study.full_clean()


def test_study_repeats_range_is_enforced_by_the_database():
    study = make_study()
    with pytest.raises(IntegrityError), transaction.atomic():
        Study.objects.filter(pk=study.pk).update(repeats=0)


def test_plan_is_immutable_except_status_and_plan_hash_unique():
    study = make_study()
    plan = make_plan(study)
    plan.status = choices.PlanStatus.LAUNCHED
    plan.save()
    plan.n_cells = 2
    with pytest.raises(ImmutableVersionError):
        plan.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_plan(study)


def test_condition_hash_is_derived_and_cells_are_unique():
    study = make_study()
    plan = make_plan(study)
    model = LLMModelFactory()
    levels = {"model": model.model_id, "prose_variant": "standard"}
    condition = StudyCondition.objects.create(
        study=study,
        plan=plan,
        index=0,
        label="c0",
        levels=levels,
        model=model,
        treatment_hash=SHA,
    )
    assert condition.condition_hash == identity.condition_hash(levels)
    unit = StudyUnit.objects.create(
        study=study,
        plan=plan,
        index=0,
        unit_key="task:crud_todo_list@1",
        task_version=make_task_version(),
        unit_order=0,
    )
    cell = {"study": study, "plan": plan, "condition": condition, "unit": unit, "block_index": 0, "seed": 1}
    StudyCell.objects.create(repeat_index=0, sequence=0, **cell)
    with pytest.raises(IntegrityError), transaction.atomic():
        StudyCell.objects.create(repeat_index=0, sequence=1, **cell)
    with pytest.raises(IntegrityError), transaction.atomic():
        StudyCell.objects.create(repeat_index=1, sequence=1, exclusion_reason="cell_budget", **cell)
    StudyCell.objects.create(repeat_index=1, sequence=1, state="excluded", exclusion_reason="cell_budget", **cell)


def test_unit_must_reference_exactly_its_kind():
    study = make_study()
    plan = make_plan(study)
    with pytest.raises(IntegrityError), transaction.atomic():
        StudyUnit.objects.create(study=study, plan=plan, index=0, unit_key="task:x@1", unit_order=0)


def test_deleting_a_study_cascades_through_its_tree_but_a_plan_alone_is_restricted():
    study = make_study()
    plan = make_plan(study)
    model = LLMModelFactory()
    StudyCondition.objects.create(
        study=study,
        plan=plan,
        index=0,
        label="c0",
        levels={"model": model.model_id},
        model=model,
        treatment_hash=SHA,
    )
    with pytest.raises(RestrictedError):
        plan.delete()
    run = make_run(study=study)
    study.delete()
    run.refresh_from_db()
    assert run.study_id is None  # runs survive with SET_NULL
    assert not StudyPlan.objects.exists()


# =====================================================================================
# Runs
# =====================================================================================


def test_run_derives_protocol_and_lane_from_kind():
    run = make_run()
    assert (run.protocol, run.lane) == ("app@1", "sandbox")
    run.full_clean()
    reference = make_run(kind=choices.RunKind.REFERENCE, reference_of=choices.ReferenceOf.ORACLE)
    assert (reference.protocol, reference.model_id) == ("ref@1", None)


def test_run_check_constraints():
    model = LLMModelFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        GenerationRun.objects.create(kind=choices.RunKind.APP_AGENTIC, model=model)  # no task version
    with pytest.raises(IntegrityError), transaction.atomic():
        GenerationRun.objects.create(kind=choices.RunKind.SLOT_SINGLE_SHOT, model=model)  # no target/parent
    with pytest.raises(IntegrityError), transaction.atomic():
        GenerationRun.objects.create(kind=choices.RunKind.APP_AGENTIC, task_version=make_task_version())  # no model
    run = make_run()
    with pytest.raises(IntegrityError), transaction.atomic():
        GenerationRun.objects.filter(pk=run.pk).update(protocol="slot@1")


def test_run_protocol_must_match_kind():
    with pytest.raises(ValidationError) as exc:
        make_run(protocol="slot@1")
    assert "protocol" in exc.value.message_dict


def test_run_usable_is_derived_from_the_outcome():
    run = make_run(outcome=choices.Outcome.BUILD_FAIL, usable=False)
    assert run.usable is True  # build_fail is model-attributable (section 6.13)
    run.full_clean()
    assert make_run().usable is None
    run.outcome = choices.Outcome.INFRA_ERROR
    run.save(update_fields=["outcome"])  # usable is written together with its source
    run.refresh_from_db()
    assert run.usable is False
    run.outcome, run.outcome_reason = choices.Outcome.EXCLUDED, "whatever"
    with pytest.raises(ValidationError) as exc:
        run.full_clean()
    assert "outcome_reason" in exc.value.message_dict
    run.outcome_reason = choices.ExclusionReason.RESOURCE_LIMIT
    run.full_clean()


def test_run_json_contracts_are_validated_on_save():
    run = make_run()
    run.validity = {"termination": "wall_clock", "truncated": True, "notes": [{"code": "wall_clock"}]}
    run.metrics = {"cost_usd": 0.01, "total_tokens": 1000}
    run.save(update_fields=["validity", "metrics"])
    run.validity = {"notes": [{"code": "not_a_registered_note"}]}
    with pytest.raises(ValidationError) as exc:
        run.save()
    assert "validity" in exc.value.message_dict
    run.refresh_from_db()
    run.metrics = {"functional": {"pass_rate": 1.0}}  # a v2 key
    with pytest.raises(ValidationError):
        run.save(update_fields=["metrics"])


def test_slot_variant_needs_a_target_and_a_parent():
    slot_set = make_slot_set()
    target = make_target(slot_set)
    variant = make_run(kind=choices.RunKind.SLOT_SINGLE_SHOT, slot_target=target, parent_run=slot_set.parent_run)
    assert (variant.protocol, variant.lane) == ("slot@1", "llm")
    assert list(slot_set.parent_run.slot_variants.all()) == [variant]


def test_manifest_is_write_once_and_hashed():
    run = make_run()
    payload = manifest_payload()
    manifest = RunManifest.objects.create(
        run=run,
        manifest=payload,
        stimuli=[
            {"role": "brief", "template_ref": "brief@1", "sha": SHA, "blob": SHA2, "chars": 10, "est_tokens": 3},
        ],
    )
    assert manifest.protocol == "app@1"
    assert manifest.manifest_hash == identity.H(manifest.manifest)
    manifest.full_clean()
    manifest.stimuli = []
    with pytest.raises(ImmutableVersionError, match="write-once"):
        manifest.save()
    assert run.manifest == manifest


def test_manifest_must_agree_with_the_run():
    run = make_run(kind=choices.RunKind.REFERENCE, reference_of=choices.ReferenceOf.ORACLE)
    manifest = RunManifest(run=run, manifest=manifest_payload())
    with pytest.raises(ValidationError) as exc:
        manifest.full_clean()
    assert any("kind" in message for message in exc.value.message_dict["manifest"])
    with pytest.raises(ValidationError):
        RunManifest.objects.create(run=run, manifest={**manifest_payload(), "surprise": True})


def test_evaluation_is_frozen_once_final():
    run = make_run()
    suite = make_entity(EvalSuite)
    evaluation = RunEvaluation.objects.create(run=run, eval_suite=suite, eval_level=choices.EvalLevel.SECURITY)
    evaluation.status = choices.EvaluationStatus.RUNNING
    evaluation.stages = {"build": {"status": "ok", "seconds": 12.0}}
    evaluation.save()
    evaluation.status = choices.EvaluationStatus.NOT_MEASURED
    evaluation.save()
    evaluation.stages = {}
    with pytest.raises(ImmutableVersionError, match="final"):
        evaluation.save()
    again = RunEvaluation.objects.create(run=run, eval_suite=suite, eval_level=choices.EvalLevel.SECURITY)
    assert run.evaluations.count() == 2
    assert again.pk != evaluation.pk


def test_run_file_paths_and_blobs():
    run = make_run()
    RunFile.objects.create(run=run, path="app/main.py", blob=SHA, origin=choices.FileOrigin.AUTHORED)
    RunFile.objects.create(run=run, path="old.py", origin=choices.FileOrigin.DELETED)
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFile.objects.create(run=run, path="app/main.py", blob=SHA, origin=choices.FileOrigin.AUTHORED)
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFile.objects.create(run=run, path="nob.py", origin=choices.FileOrigin.AUTHORED)
    for bad in ("/etc/passwd", "../x", "a//b", "a\\b", "./a"):
        with pytest.raises(ValidationError):
            RunFile(run=run, path=bad, blob=SHA, origin=choices.FileOrigin.AUTHORED).full_clean()


def test_attempts_are_unique_and_one_based():
    run = make_run()
    run.attempts.create(attempt=1, usage={"prompt_tokens": 5, "cost_usd": 0.001})
    with pytest.raises(IntegrityError), transaction.atomic():
        run.attempts.create(attempt=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        run.attempts.create(attempt=0)
    with pytest.raises(ValidationError):
        run.attempts.create(attempt=2, usage={"prompt_tokens": -1})


# =====================================================================================
# Slot mode and patterns
# =====================================================================================


def test_slot_target_hash_is_derived_and_unique_per_slot_set():
    slot_set = make_slot_set()
    target = make_target(slot_set)
    expected = identity.target_hash(
        parent_tree=slot_set.frozen_tree_hash,
        address={"kind": "file", "path": "app/main.py", "span": None, "content_sha": SHA2},
        original_sha=SHA2,
        allowlist=[],
        interface={"signature": "", "decorators": [], "imports_in_scope": [], "exported_names": []},
    )
    assert (target.target_hash, target.kind) == (expected, "file")
    assert target.unit_key == f"slot:{target.pk}"
    with pytest.raises(IntegrityError), transaction.atomic():
        make_target(slot_set)
    target.validation = {"status": "ok", "discriminating_checks": ["todos.sorted"]}
    target.linked_checks = ["todos.sorted"]
    target.save()
    target.original_sha = SHA
    with pytest.raises(ImmutableVersionError):
        target.save()


def test_slot_target_kind_must_match_address():
    with pytest.raises(ValidationError) as exc:
        make_target(make_slot_set(), kind=choices.SlotKind.SYMBOL)
    assert "kind" in exc.value.message_dict


def test_slot_set_parent_must_be_usable_agentic_and_tree_pinned():
    slot_set = make_slot_set()
    slot_set.full_clean()
    unusable = make_run(outcome=choices.Outcome.INFRA_ERROR, usable=False, tree_hash=SHA)
    slot_set.parent_run = unusable
    slot_set.frozen_tree_hash = SHA2
    with pytest.raises(ValidationError) as exc:
        slot_set.full_clean()
    assert {"parent_run", "frozen_tree_hash", "index"} <= set(exc.value.message_dict)


def test_pattern_hit_error_is_never_silent():
    study = make_study()
    slot_set = make_slot_set()
    variant = make_run(
        kind=choices.RunKind.SLOT_SINGLE_SHOT,
        slot_target=make_target(slot_set),
        parent_run=slot_set.parent_run,
    )
    detector = make_entity(PatternDetector)
    pattern_run = PatternRun.objects.create(study=study, detector_set_hash=SHA, normalizer_version=1)
    hit = {"pattern_run": pattern_run, "run": variant, "detector": detector, "feature_key": "py-sql-fstring"}
    with pytest.raises(IntegrityError), transaction.atomic():
        PatternHit.objects.create(scope="file", status="error", **hit)  # an error without a reason
    with pytest.raises(IntegrityError), transaction.atomic():
        PatternHit.objects.create(scope="file", status="absent", error_reason="timeout", **hit)
    PatternHit.objects.create(scope="file", status="error", error_reason="tool_error", **hit)
    match = {"path": "a.py", "start_line": 1, "end_line": 2}
    PatternHit.objects.create(scope="span", status="present", matches=[match], **hit)
    with pytest.raises(IntegrityError), transaction.atomic():
        PatternHit.objects.create(scope="span", status="absent", **hit)
    pattern_run.status = choices.WorkStatus.COMPLETED
    pattern_run.save()
    pattern_run.summary = {"rewritten": True}
    with pytest.raises(ImmutableVersionError):
        pattern_run.save()


def test_pattern_run_detectors_must_be_pinned_refs():
    with pytest.raises(ValidationError):
        PatternRun.objects.create(
            study=make_study(),
            detector_set_hash=SHA,
            normalizer_version=1,
            detectors=["py-sql-fstring@1"],  # not hash-pinned
        )


# =====================================================================================
# Spend
# =====================================================================================


def test_ledger_is_append_only_and_deduplicated(user):
    entry = SpendLedgerEntry.objects.create(
        kind=choices.LedgerKind.USAGE,
        user=user,
        amount_usd=Decimal("0.0123"),
        list_cost_usd=Decimal("0.0123"),
        source=choices.LedgerSource.GATEWAY_FINAL,
        dedupe_key="run:1:a1:final",
    )
    entry.amount_usd = Decimal(0)
    with pytest.raises(ImmutableVersionError):
        entry.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        SpendLedgerEntry.objects.create(
            kind=choices.LedgerKind.USAGE,
            user=user,
            source=choices.LedgerSource.GATEWAY_FINAL,
            dedupe_key="run:1:a1:final",
        )


def test_spend_cap_scope_needs_its_target(user):
    SpendCap.objects.create(scope="global", period="month", unit="usd", limit=Decimal(50))
    SpendCap.objects.create(scope="user", user=user, period="day", unit="usd", limit=Decimal(5))
    with pytest.raises(IntegrityError), transaction.atomic():
        SpendCap.objects.create(scope="model", period="5h", unit="list_usd", limit=Decimal(1))
    with pytest.raises(IntegrityError), transaction.atomic():
        SpendCap.objects.create(scope="user", period="day", unit="usd", limit=Decimal(-1), user=user)


# =====================================================================================
# Other apps and the blob store
# =====================================================================================


def test_endpoint_cost_basis_defaults_to_billed(user):
    endpoint = LLMEndpoint.objects.create(
        slug="lan-box",
        name="LAN",
        kind="openai_compatible",
        api_flavor="openai",
        owner=user,
    )
    assert endpoint.cost_basis == LLMEndpoint.CostBasis.BILLED


def test_analysis_run_records_its_code_scope(user):
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import CodeScope

    run = make_run()
    analysis = AnalysisRun.objects.create(created_by=user, generation_job=run, code_scope=CodeScope.APP)
    assert analysis.code_scope == "app"
    assert list(run.analysis_runs.all()) == [analysis]


def test_blob_rows_and_references():
    sha = blobs.put_text("print('hello')\n")
    row = Blob.objects.get(pk=sha)
    assert (row.size, row.codec) == (len("print('hello')\n"), blobs.preferred_codec())
    run = make_run(workspace_blob=SHA)
    RunFile.objects.create(run=run, path="hello.py", blob=sha, origin=choices.FileOrigin.AUTHORED)
    RunManifest.objects.create(
        run=run,
        manifest=manifest_payload(),
        stimuli=[{"role": "brief", "template_ref": "b@1", "sha": SHA, "blob": SHA2, "chars": 1, "est_tokens": 1}],
    )
    starter = make_starter(make_entity(StackProfile))
    refs = blobs.referenced_shas()
    assert {sha, SHA, SHA2} <= refs
    assert {entry["blob"] for entry in starter.content["files"].values()} <= refs
