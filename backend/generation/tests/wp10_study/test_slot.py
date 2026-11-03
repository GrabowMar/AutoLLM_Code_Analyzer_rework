"""Slot-mode resolve and launch (spec sections 7.10 and 9.4) with fake slot context and rendering."""

from __future__ import annotations

from collections import defaultdict

import pytest

from backend.generation.models import GenerationRun
from backend.generation.models import StudyPostRun
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.testing.factories import SlotSetFactory
from backend.generation.testing.factories import SlotTargetFactory

from .conftest import MODELS

pytestmark = pytest.mark.django_db

VALID = {"status": "ok", "discriminating_checks": ["todos.list.ok"], "dropped_checks": []}


def _slot_design(slot_set, **overrides):
    design = {
        "mode": "slot",
        "kind": "exploratory",
        "factors": [
            {"key": "model", "role": "crossed", "levels": list(MODELS)},
            {"key": "hole_mode", "role": "crossed", "levels": ["stub", "remove"]},
        ],
        "units": {"slot_sets": [str(slot_set.pk)]},
        "repeats": 3,
        "base_seed": 99,
        "order_seed": 5,
        "resource_policy": "frugal@1",
        "evaluation": {"eval_suite": "eval-v2@1", "slot": {"mode": "overlay"}},
    }
    design.update(overrides)
    return design


@pytest.fixture
def slot_set(golden):
    parent = SlotSetFactory.create()
    for _ in range(2):
        SlotTargetFactory.create(slot_set=parent, validation=VALID)
    return parent


def test_slot_plan_pairs_targets_and_varies_the_slot_policy(golden, user, slot_set, fake_study):
    study = svc.create_study(user=user, name="slot", design=_slot_design(slot_set))
    plan = planner.resolve(study)
    main = [c for c in plan.cells if c.stage == "main"]
    assert len(plan.conditions) == 4
    assert len(main) == 4 * 2 * 3
    assert {c.manifest.kind for c in main} == {"slot_single_shot"}
    seeds = defaultdict(set)
    for cell in plan.cells:
        seeds[(cell.unit_key, cell.repeat_index)].add(cell.seed)
    assert all(len(values) == 1 for values in seeds.values())
    by_hole = defaultdict(set)
    for cell in main:
        by_hole[cell.levels["hole_mode"]].add(cell.manifest.identity.treatment_hash)
    assert by_hole["stub"].isdisjoint(by_hole["remove"])
    codes = {i.code: i.severity for i in plan.issues}
    assert codes["single_parent"] == "info"
    assert codes["manual_parent_selection"] == "info"
    assert not [i for i in plan.issues if i.severity == "error"], plan.issues
    cell = main[0]
    assert cell.manifest.slot.target_hash
    assert cell.manifest.planned_evaluation.mode == "overlay"
    assert cell.levels["sampling_preset"].startswith("t07@1#")  # slot studies default to t07


def test_unvalidated_targets_block_launch(golden, user, fake_study):
    parent = SlotSetFactory.create()
    SlotTargetFactory.create(slot_set=parent)  # validation pending
    study = svc.create_study(user=user, name="slot", design=_slot_design(parent))
    plan = planner.resolve(study)
    assert any(i.code == "slot_tests_uninformative" and i.severity == "error" for i in plan.issues)


def test_slot_launch_creates_variants_and_attaches_a_patterns_post_run(golden, user, slot_set, fake_study):
    study = svc.create_study(user=user, name="slot", design=_slot_design(slot_set))
    svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    runs = GenerationRun.objects.filter(study=study)
    assert runs.count() == 24
    run = runs.first()
    assert run.kind == "slot_single_shot"
    assert run.parent_run_id == slot_set.parent_run_id
    assert run.slot_target.slot_set_id == slot_set.pk
    post_run = StudyPostRun.objects.get(study=study)
    assert (post_run.kind, post_run.trigger, post_run.config) == ("patterns", "completed", {"detectors": "default"})


def test_a_confirmatory_single_parent_study_is_blocked_unless_conditional(golden, user, slot_set, fake_study):
    plan = {"primary_metric": "pattern_prevalence", "research_questions": [{"question": "q"}]}
    design = _slot_design(slot_set, kind="confirmatory", analysis_plan=plan, slot_primary_features=["sqli"])
    study = svc.create_study(user=user, name="slot", design=design)
    codes = {i.code: i.severity for i in planner.resolve(study).issues}
    assert codes["single_parent"] == "error"
    conditional = _slot_design(
        slot_set,
        kind="confirmatory",
        analysis_plan={**plan, "estimand": "conditional_on_parent"},
        slot_primary_features=["sqli"],
    )
    other = svc.create_study(user=user, name="slot2", design=conditional)
    codes = {i.code: i.severity for i in planner.resolve(other).issues}
    assert codes["single_parent"] == "info"
