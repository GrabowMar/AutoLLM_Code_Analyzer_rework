"""WP-01 review round 3: pilot/main cell keys, the confirmatory lock, run columns pinned by the manifest.

1. pilot and main cells of the same (condition, unit, repeat_index) coexist; ``cell_key`` names the stage;
2. a locked study cannot be unlocked through ``save()`` and its frozen columns cannot be bulk-updated;
3. once a run has a manifest, its kind, protocol, seed and identity columns are write-once and equal
   to the manifest (appendix A #13);
4. ``RunView`` keeps the run it was built from (for WP-02a's accessors);
5. ``EvalBaseline.reference_run`` is a starter-baseline reference run.

Run with ``--ds=backend.generation.tests.wp01_foundation.settings_u1`` until WP-02a/WP-02b land.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from pydantic import ValidationError as PydanticValidationError

from backend.generation.contracts import plan as plan_contracts
from backend.generation.models import GenerationRun
from backend.generation.models import ImmutableVersionError
from backend.generation.models import RunManifest
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models import StudyUnit
from backend.generation.models import choices
from backend.generation.runs.view import RunView
from backend.generation.testing import factories
from backend.generation.tests.wp01_foundation.test_contracts import design_payload
from backend.generation.tests.wp01_foundation.test_contracts import manifest_payload
from backend.generation.tests.wp01_foundation.test_contracts import plan_payload
from backend.generation.tests.wp01_foundation.test_models import SHA
from backend.generation.tests.wp01_foundation.test_models import make_plan
from backend.generation.tests.wp01_foundation.test_models import make_run
from backend.generation.tests.wp01_foundation.test_models import make_study
from backend.generation.tests.wp01_foundation.test_models import make_task_version
from backend.llm_models.tests.factories import LLMModelFactory

pytestmark = pytest.mark.django_db

OTHER_SHA = "c" * 64


# -- 1. pilot and main cells ------------------------------------------------------------------------


def _cell_parts() -> dict:
    study = make_study()
    plan = make_plan(study)
    model = LLMModelFactory()
    condition = StudyCondition.objects.create(
        study=study,
        plan=plan,
        index=0,
        label="c0",
        levels={"model": model.model_id},
        model=model,
        treatment_hash=SHA,
    )
    unit = StudyUnit.objects.create(
        study=study,
        plan=plan,
        index=0,
        unit_key="task:crud_todo_list@1",
        task_version=make_task_version(),
        unit_order=0,
    )
    return {"study": study, "plan": plan, "condition": condition, "unit": unit, "block_index": 0, "seed": 1}


def test_pilot_and_main_cells_of_the_same_repeat_coexist():
    parts = _cell_parts()
    pilot = StudyCell.objects.create(repeat_index=0, sequence=0, stage=choices.Stage.PILOT, **parts)
    StudyCell.objects.create(repeat_index=0, sequence=1, stage=choices.Stage.MAIN, **parts)
    with pytest.raises(IntegrityError), transaction.atomic():
        StudyCell.objects.create(repeat_index=0, sequence=2, stage=choices.Stage.PILOT, **parts)
    # reuse_as_main flips the stage in place; flipping onto an existing main repeat collides (WP-10 skips it).
    pilot.stage = choices.Stage.MAIN
    with pytest.raises(IntegrityError), transaction.atomic():
        pilot.save()


def test_reuse_as_main_flips_a_pilot_cell_in_place():
    parts = _cell_parts()
    pilot = StudyCell.objects.create(repeat_index=0, sequence=0, stage=choices.Stage.PILOT, **parts)
    pilot.stage = choices.Stage.MAIN
    pilot.save(update_fields=["stage"])
    StudyCell.objects.create(repeat_index=1, sequence=1, stage=choices.Stage.MAIN, **parts)
    assert StudyCell.objects.filter(stage=choices.Stage.MAIN).count() == 2


def test_cell_key_names_the_stage_and_must_match_the_cell():
    assert plan_contracts.cell_key(1, 2, 0, "pilot") == "c1-u2-r0-pilot"
    assert plan_contracts.cell_key(1, 2, 0, "pilot") != plan_contracts.cell_key(1, 2, 0, "main")
    with pytest.raises(ValueError, match="stage"):
        plan_contracts.cell_key(0, 0, 0, "warmup")
    cell = plan_payload()["cells"][0]
    plan_contracts.ResolvedCell.model_validate(cell)
    with pytest.raises(PydanticValidationError, match="cell_key"):
        plan_contracts.ResolvedCell.model_validate({**cell, "stage": "pilot"})


# -- 2. the confirmatory lock ------------------------------------------------------------------------


def _locked_study() -> Study:
    study = make_study(design={**design_payload(), "kind": "confirmatory"})
    plan = make_plan(study)
    study.locked_at = study.created_at
    study.prereg_hash = SHA
    study.analysis_plan_hash = SHA
    study.active_plan = plan
    study.save()
    return study


def test_lock_cannot_be_removed_by_save():
    study = _locked_study()
    study.locked_at = None
    with pytest.raises(ImmutableVersionError, match="locked_at"):
        study.save()
    study.refresh_from_db()
    study.locked_at = None
    with pytest.raises(ImmutableVersionError):
        study.save(update_fields=["locked_at"])
    study.refresh_from_db()
    assert study.is_locked
    design = copy.deepcopy(study.design)
    design["repeats"] = 3
    study.design = design
    with pytest.raises(ImmutableVersionError):
        study.save()


@pytest.mark.parametrize(
    "field",
    ["prereg_hash", "analysis_plan_hash", "active_plan", "kind"],
)
def test_lock_freezes_the_certified_columns(field):
    study = _locked_study()
    values = {"prereg_hash": OTHER_SHA, "analysis_plan_hash": OTHER_SHA, "active_plan": None, "kind": "exploratory"}
    setattr(study, field, values[field])
    with pytest.raises(ImmutableVersionError, match=field):
        study.save(update_fields=[field])


def test_locked_study_refuses_bulk_updates_of_frozen_columns():
    study = _locked_study()
    open_study = make_study(resource_policy=study.resource_policy)
    rows = Study.objects.filter(pk=study.pk)
    for update in ({"design": design_payload()}, {"locked_at": None}, {"repeats": 3}, {"active_plan": None}):
        with pytest.raises(ImmutableVersionError, match="locked"):
            rows.update(**update)
    with pytest.raises(ImmutableVersionError):
        Study.objects.all().update(prereg_hash=OTHER_SHA)  # one locked row is enough
    assert rows.update(name="renamed", status=choices.StudyStatus.RUNNING) == 1
    assert Study.objects.filter(pk=open_study.pk).update(prereg_hash=OTHER_SHA) == 1
    study.refresh_from_db()
    assert study.is_locked
    assert study.prereg_hash == SHA


# -- 3. run columns pinned by the manifest ------------------------------------------------------------


def _manifest(seed: int | None = 42) -> dict:
    payload = manifest_payload()
    payload["sampling"] = {**payload["sampling"], "seed": seed}
    return payload


def test_manifest_must_equal_the_run_columns_on_create():
    run = make_run(seed=7)
    with pytest.raises(ValidationError, match="seed"):
        RunManifest.objects.create(run=run, manifest=_manifest(seed=42))
    other = make_run(run_fingerprint=OTHER_SHA)
    with pytest.raises(ValidationError, match="run_fingerprint"):
        RunManifest.objects.create(run=other, manifest=_manifest())
    assert not RunManifest.objects.exists()


def test_run_identity_and_seed_are_write_once_after_the_manifest():
    run = make_run()
    RunManifest.objects.create(run=run, manifest=_manifest(seed=42))
    run.refresh_from_db()
    # Empty columns may be filled, but only with the manifest's values.
    run.seed = 41
    with pytest.raises(ImmutableVersionError, match="seed"):
        run.save()
    run.seed = 42
    run.run_fingerprint = SHA
    run.save()
    run.run_fingerprint = OTHER_SHA
    with pytest.raises(ImmutableVersionError, match="run_fingerprint"):
        run.save(update_fields=["run_fingerprint"])
    run.refresh_from_db()
    run.seed = None
    with pytest.raises(ImmutableVersionError, match="seed"):
        run.save()
    run.refresh_from_db()
    run.status = choices.RunStatus.RUNNING
    run.attempt = 2
    run.save()  # a retry keeps the manifest and the seed; the queue columns stay mutable
    assert (run.seed, run.run_fingerprint) == (42, SHA)


def test_run_bulk_update_of_pinned_columns_is_refused_once_a_manifest_exists():
    run = make_run()
    rows = GenerationRun.objects.filter(pk=run.pk)
    assert rows.update(seed=5) == 1  # no manifest yet
    rows.update(seed=None)
    RunManifest.objects.create(run=run, manifest=_manifest())
    for update in ({"seed": 42}, {"task_hash": OTHER_SHA}, {"protocol": "slot@1"}):
        with pytest.raises(ImmutableVersionError, match="manifest"):
            rows.update(**update)
    assert rows.update(status=choices.RunStatus.RUNNING, lease_owner="exec-1") == 1


# -- 4. RunView carries its run ------------------------------------------------------------------------


def test_run_view_keeps_its_run_out_of_repr_and_equality():
    fields = {f.name: f for f in dataclasses.fields(RunView)}
    assert "_run" in fields
    assert fields["_run"].repr is False
    assert fields["_run"].compare is False


# -- minor: EvalBaseline.reference_run ------------------------------------------------------------------


def test_baseline_reference_run_must_be_a_starter_baseline_reference():
    baseline = factories.EvalBaselineFactory.create()
    baseline.full_clean()
    baseline.reference_run = make_run()
    with pytest.raises(ValidationError) as exc:
        baseline.full_clean()
    assert "reference_run" in exc.value.message_dict
