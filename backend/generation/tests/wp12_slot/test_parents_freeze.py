"""Parent pool, reproducible stratified draws and freezing (spec section 7.2)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.generation.contracts.slot import ParentSelection
from backend.generation.models import GenerationRun
from backend.generation.models import RunManifest
from backend.generation.models.choices import Outcome
from backend.generation.slot import parents
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.freeze import freeze
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import TaskSpecVersionFactory
from backend.generation.tests.wp12_slot.helpers import make_parent
from backend.llm_models.tests.factories import LLMModelFactory

pytestmark = pytest.mark.django_db


def _pool(task: Any, models: list[Any], per_model: int) -> list[GenerationRun]:
    runs = []
    for model in models:
        runs += [GenerationRunFactory.create(tested=True, task_version=task, model=model) for _ in range(per_model)]
    return runs


def test_pool_is_usable_tested_app_runs_of_the_task() -> None:
    task = TaskSpecVersionFactory.create()
    model = LLMModelFactory.create()
    good = GenerationRunFactory.create(tested=True, task_version=task, model=model)
    GenerationRunFactory.create(task_version=task, model=model)  # pending
    GenerationRunFactory.create(tested=True, task_version=task, model=model, outcome=Outcome.BUILD_FAIL)
    GenerationRunFactory.create(tested=True, task_version=task, model=model, beyond_plan=True)
    GenerationRunFactory.create(tested=True, model=model)  # another task
    selection = ParentSelection(pool_query={"task": task.ref})
    assert list(parents.pool(selection.pool_query)) == [good]
    assert list(parents.pool_for(task=task.ref, models=["nope/none"])) == []


def test_random_draw_is_reproducible_and_stratified_by_parent_model() -> None:
    task = TaskSpecVersionFactory.create()
    models = [LLMModelFactory.create() for _ in range(2)]
    _pool(task, models, per_model=4)
    selection = ParentSelection(pool_query={"task": task.ref}, k=4, seed=7)
    first = parents.draw(selection)
    assert [r.pk for r in first] == [r.pk for r in parents.draw(selection)]
    assert len(first) == 4
    by_model: dict[str, int] = {}
    for run in first:
        by_model[run.model.model_id] = by_model.get(run.model.model_id, 0) + 1
    assert sorted(by_model.values()) == [2, 2]
    draws = {tuple(r.pk for r in parents.draw(selection.model_copy(update={"seed": seed}))) for seed in range(10)}
    assert len(draws) > 1  # the seed matters
    unstratified = parents.draw(selection.model_copy(update={"stratify_by": "none", "k": 3}))
    assert len(unstratified) == 3


def test_draw_rules_all_manual_and_missing_seed() -> None:
    task = TaskSpecVersionFactory.create()
    runs = _pool(task, [LLMModelFactory.create()], per_model=3)
    everything = parents.draw_with_record(ParentSelection(rule="all", pool_query={"task": task.ref}))
    assert {r.pk for r in everything.parents} == {r.pk for r in runs}
    assert everything.pool_size == 3
    with pytest.raises(SlotInvalidError):
        parents.draw(ParentSelection(pool_query={"task": task.ref}))  # random needs a seed
    dry = parents.draw_with_record(ParentSelection(pool_query={"task": task.ref}, k=2))
    assert dry.seed is not None
    assert len(dry.parents) == 2
    with pytest.raises(SlotInvalidError):
        parents.draw_with_record(ParentSelection(rule="manual", pool_query={"task": task.ref}))


def test_freeze_pins_the_parent_and_builds_the_index(golden: Any, researcher: Any, acceptance: Any) -> None:
    parent = make_parent(golden, researcher)
    selection = ParentSelection(pool_query={"task": parent.task_version.ref}, k=1, seed=3)
    slot_set = freeze(parent, name="p", user=researcher, selection=selection, pool_size=1)
    assert slot_set.frozen_tree_hash == parent.tree_hash
    assert slot_set.parent_manifest_hash == RunManifest.objects.get(run=parent).manifest_hash
    assert slot_set.parent_model == parent.model.model_id
    assert slot_set.parent_eval is not None
    assert slot_set.index.stats["endpoints"] >= 6
    assert slot_set.selection["rule"] == "random"
    assert slot_set.selection["seed"] == 3
    # A second slot set of the same parent reuses the stored index.
    again = freeze(parent, name="q", user=researcher)
    assert again.index_id == slot_set.index_id
    assert again.selection["rule"] == "manual"
    assert again.selection["picked_by"] == researcher.username


def test_freeze_refuses_unusable_parents(golden: Any, researcher: Any) -> None:
    parent = make_parent(golden, researcher)
    GenerationRun.objects.filter(pk=parent.pk).update(outcome=Outcome.BUILD_FAIL)
    parent.refresh_from_db()
    with pytest.raises(SlotInvalidError, match="outcome tested"):
        freeze(parent, name="p", user=researcher)
    slot_set = freeze(parent, name="p", user=researcher, allow_unbuilt_parent=True)
    assert "no runtime slot tests" in slot_set.notes
    no_manifest = GenerationRunFactory.create(tested=True)
    with pytest.raises(SlotInvalidError, match="no manifest"):
        freeze(no_manifest, name="p", user=researcher)
