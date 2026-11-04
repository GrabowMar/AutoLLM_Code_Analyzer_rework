"""Parents drawn from ``Design.parent_selection`` at plan time (spec 7.2; WP-12 ask): ``study/parents.py``."""

from __future__ import annotations

import pytest

from backend.generation.models import RunSymbolIndex
from backend.generation.models import SlotSet
from backend.generation.models import SlotTarget
from backend.generation.slot import freeze as slot_freeze
from backend.generation.slot import parents as slot_parents
from backend.generation.slot import selection as slot_selection
from backend.generation.study import launch as svc
from backend.generation.study import parents as study_parents
from backend.generation.study import plan as planner
from backend.generation.study.design import DesignError
from backend.generation.study.design import parse_design
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import SlotSetFactory
from backend.generation.testing.factories import SlotTargetFactory
from backend.llm_models.tests.factories import LLMModelFactory

from .conftest import MODELS

pytestmark = pytest.mark.django_db

VALID = {"status": "ok", "discriminating_checks": ["todos.list.ok"], "dropped_checks": []}
MATCH_KEY = "endpoint:todos.list"


@pytest.fixture
def pool(golden, user, monkeypatch):
    """A template target on a manually picked parent, four more tested parents of the task by two models.

    ``slot.freeze.freeze`` and ``slot.selection.match`` are faked (index building and part matching are
    WP-12's and tested there); the draw itself is the real, seeded ``slot.parents.draw_with_record``.
    """
    template_set = SlotSetFactory.create(parent_run__created_by=user)
    template = SlotTargetFactory.create(slot_set=template_set, validation=VALID, match_key=MATCH_KEY)
    task = template_set.task_version
    authors = [LLMModelFactory.create(model_id=f"author/m{i}") for i in range(2)]
    parents = [
        GenerationRunFactory.create(tested=True, task_version=task, model=authors[i % 2], created_by=user)
        for i in range(4)
    ]
    calls = {"freeze": 0, "match": 0}

    def fake_freeze(parent_run, *, name, user, selection=None, pool_size=None, seed=None, **_):
        calls["freeze"] += 1
        record = slot_parents.selection_record(selection, user=user, pool_size=pool_size, seed=seed)
        extra = {}
        index = RunSymbolIndex.objects.filter(run=parent_run).first()
        if index is not None:  # the template's parent may be drawn too: one index per run
            extra["index"] = index
        return SlotSetFactory.create(parent_run=parent_run, name=name, selection=record, **extra)

    def fake_match(target, slot_sets, *, user=None):
        calls["match"] += 1
        created = [
            SlotTargetFactory.create(slot_set=s, validation=VALID, match_key=target.match_key) for s in slot_sets
        ]
        return {"created": created, "unmatched": []}

    monkeypatch.setattr(slot_freeze, "freeze", fake_freeze)
    monkeypatch.setattr(slot_selection, "match", fake_match)
    return {"template": template, "task": task, "parents": parents, "calls": calls}


def _design(pool, **selection):
    task = pool["task"]
    return {
        "mode": "slot",
        "kind": "exploratory",
        "factors": [{"key": "model", "role": "crossed", "levels": list(MODELS)}],
        "units": {"slot_targets": [str(pool["template"].pk)]},
        "repeats": 2,
        "base_seed": 99,
        "order_seed": 5,
        "resource_policy": "frugal@1",
        "evaluation": {"eval_suite": "eval-v2@1", "slot": {"mode": "overlay"}},
        "parent_selection": {
            "rule": "random",
            "k": 3,
            "pool_query": {"task": f"{task.task.slug}@{task.version}"},
            **selection,
        },
    }


def test_the_seed_derives_from_base_seed_unless_given(pool):
    design = parse_design(_design(pool))
    seed = study_parents.selection_seed(design)
    assert seed == study_parents.selection_seed(parse_design(_design(pool)))
    assert seed != study_parents.selection_seed(parse_design({**_design(pool), "base_seed": 100}))
    assert study_parents.selection_seed(parse_design(_design(pool, seed=7))) == 7


def test_ephemeral_resolve_draws_but_writes_nothing(pool, user, fake_study):
    study = svc.create_study(user=user, name="draw", design=_design(pool))
    sets, targets = SlotSet.objects.count(), SlotTarget.objects.count()
    with pytest.raises(DesignError) as refused:
        planner.resolve(study)
    assert refused.value.path == "parent_selection"
    assert (SlotSet.objects.count(), SlotTarget.objects.count()) == (sets, targets)
    assert pool["calls"] == {"freeze": 0, "match": 0}


def test_freeze_draws_parents_matches_the_template_and_is_reproducible(pool, user, fake_study):
    study = svc.create_study(user=user, name="draw", design=_design(pool))
    row = planner.freeze(study)
    assert pool["calls"]["freeze"] == 3
    plan = planner.load_bundle(row).plan
    assert len(plan.units) == 3  # k parents x 1 template
    targets = SlotTarget.objects.filter(pk__in=[u.slot_target for u in plan.units]).select_related("slot_set")
    seed = study_parents.selection_seed(parse_design(study.design))
    for target in targets:
        assert target.match_key == MATCH_KEY
        assert target.slot_set.selection["rule"] == "random"
        assert target.slot_set.selection["seed"] == seed
    drawn = {t.slot_set.parent_run_id for t in targets}
    assert len(drawn) == 3
    # Stratified by parent model: both authors are represented.
    authors = {p.model_id for p in pool["parents"] if p.pk in drawn}
    assert len(authors) == 2
    # The same draw again: nothing new is frozen or matched, and the preview equals the frozen plan.
    calls = dict(pool["calls"])
    planner.clear_cache()
    assert planner.resolve(study).plan_hash == row.plan_hash
    assert planner.freeze(study).pk == row.pk
    assert pool["calls"] == calls
    codes = {i.code for i in plan.issues}
    assert "manual_parent_selection" not in codes
    assert "single_parent" not in codes


def test_launch_of_a_drawing_design_persists_the_drawn_plan(pool, user, fake_study):
    study = svc.create_study(user=user, name="draw", design=_design(pool, k=2))
    row = planner.freeze(study)
    svc.launch(study, row.plan_hash, pilot_override=True)
    study.refresh_from_db()
    assert study.active_plan_id == row.pk
    assert study.runs.count() == 2 * 2 * 2  # 2 models x 2 drawn parents x 2 repeats


def test_manual_rule_keeps_the_listed_targets(pool, user, fake_study):
    study = svc.create_study(user=user, name="manual", design=_design(pool, rule="manual"))
    plan = planner.resolve(study)
    assert [u.slot_target for u in plan.units] == [str(pool["template"].pk)]
    assert pool["calls"] == {"freeze": 0, "match": 0}


def test_the_draw_is_pinned_at_the_first_freeze_and_redrawn_only_on_request(pool, user, fake_study):
    """E2E-14: a re-freeze after the pool grew reuses the parents of the first freeze."""
    study = svc.create_study(user=user, name="pinned", design=_design(pool))
    row = planner.freeze(study)
    study.refresh_from_db()
    pin = dict(study.parent_draw)
    selection = study_parents._seeded(parse_design(study.design))
    before = slot_parents.draw_with_record(selection).pool_size  # the four parents and the template's parent
    assert pin["pool_size"] == before
    assert pin["seed"] == study_parents.selection_seed(parse_design(study.design))
    assert pin["selection_hash"] == study_parents.selection_hash(parse_design(study.design))
    first = list(pin["parents"])
    assert len(first) == 3

    # Another study adds tested runs of the task (new authors): the pool grows by 12.
    task = pool["task"]
    newcomers = [LLMModelFactory.create(model_id=f"author/new{i}") for i in range(3)]
    for i in range(12):
        GenerationRunFactory.create(tested=True, task_version=task, model=newcomers[i % 3], created_by=user)
    assert slot_parents.draw_with_record(selection).pool_size == before + 12

    # Ephemeral resolve and re-freeze keep the pinned parents: same plan, nothing new frozen.
    calls = dict(pool["calls"])
    planner.clear_cache()
    assert planner.resolve(study).plan_hash == row.plan_hash
    again = planner.freeze(study)
    assert again.plan_hash == row.plan_hash
    assert pool["calls"] == calls
    study.refresh_from_db()
    assert study.parent_draw == pin
    units = planner.load_bundle(again).plan.units
    parents = SlotTarget.objects.filter(pk__in=[u.slot_target for u in units]).values_list(
        "slot_set__parent_run_id",
        flat=True,
    )
    assert {str(p) for p in parents} == set(first)

    # An explicit redraw draws from the current pool and pins the new draw.
    planner.freeze(study, redraw_parents=True)
    study.refresh_from_db()
    assert study.parent_draw["pool_size"] == before + 12
    assert study.parent_draw["parents"] == [str(p.pk) for p in slot_parents.draw_with_record(selection).parents]

    # A changed parent_selection is another selection: the old pin is not reused.
    changed = parse_design({**_design(pool, k=2)})
    assert study_parents.pinned_draw(study, changed) is None


def test_redraw_is_refused_once_launched(pool, user, fake_study):
    study = svc.create_study(user=user, name="pinned", design=_design(pool, k=2))
    row = planner.freeze(study)
    svc.launch(study, row.plan_hash, pilot_override=True)
    study.refresh_from_db()
    pin = dict(study.parent_draw)
    with pytest.raises(DesignError) as refused:
        planner.freeze(study, redraw_parents=True)
    assert refused.value.path == "parent_selection"
    study.refresh_from_db()
    assert study.parent_draw == pin


def test_a_refreeze_after_target_validation_replaces_the_stale_draft(pool, user, fake_study, monkeypatch):
    """E2E-21: with the draw pinned the plan hash stays the same, so the stored issues must be refreshed."""
    pending = {"status": "pending", "dropped_checks": []}

    def match_pending(target, slot_sets, *, user=None):
        created = [
            SlotTargetFactory.create(slot_set=s, validation=pending, match_key=target.match_key) for s in slot_sets
        ]
        return {"created": created, "unmatched": []}

    monkeypatch.setattr(slot_selection, "match", match_pending)
    study = svc.create_study(user=user, name="validate", design=_design(pool, k=2))
    first = planner.freeze(study)
    errors = [i for i in first.issues if i["severity"] == "error"]
    assert {i["code"] for i in errors} == {"slot_tests_uninformative"}
    with pytest.raises(svc.StudyError) as refused:
        svc.launch(study, first.plan_hash, pilot_override=True)
    assert refused.value.code == "plan_has_errors"

    # The matched targets finish validation; the re-freeze (same pinned parents) has the same hash.
    SlotTarget.objects.filter(validation__status="pending").update(validation=VALID)
    again = planner.freeze(study)
    assert again.plan_hash == first.plan_hash
    assert again.pk != first.pk
    assert not [i for i in again.issues if i["severity"] == "error"]
    assert not type(first).objects.filter(pk=first.pk).exists()
    # Unchanged diagnostics keep the row.
    assert planner.freeze(study).pk == again.pk
    svc.launch(study, again.plan_hash, pilot_override=True)
    study.refresh_from_db()
    assert study.active_plan_id == again.pk
