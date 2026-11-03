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
