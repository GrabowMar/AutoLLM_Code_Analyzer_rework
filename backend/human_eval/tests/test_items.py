"""services/items.py: pairing strategies, build_items, blinded labels (provisional + two-phase relabel), item sync."""

from __future__ import annotations

import itertools
from collections import Counter

import pytest

from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import design as design_service
from backend.human_eval.services import items
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import EvalTaskAppFactory
from backend.human_eval.tests.factories import EvalTaskFactory

pytestmark = pytest.mark.django_db

GROUP = "todo-app:0123456789ab"


def _task(mode: str = EvalTask.Mode.PAIRWISE, *, seed: int | None = None, experiment=None, **pairing) -> EvalTask:
    settings, errors = design_service.validate_settings({"pairing": pairing} if pairing else {})
    assert not errors
    fields = {"seed": seed} if seed is not None else {}
    return EvalTaskFactory(mode=mode, settings=settings, experiment=experiment, **fields)


def _app(task, *, condition=None, model="model-a", repeat=0, group=GROUP, active=True) -> EvalTaskApp:
    return EvalTaskAppFactory(
        task=task,
        group_key=group,
        is_active=active,
        snapshot={"condition_id": condition, "model_id": model, "repeat_index": repeat},
    )


def _ids(pairs) -> set[tuple[str, str]]:
    return {(str(a.id), str(b.id)) for a, b in pairs}


def _degrees(pairs) -> Counter:
    degree: Counter = Counter()
    for a, b in pairs:
        degree[a.id] += 1
        degree[b.id] += 1
    return degree


# --- single mode --------------------------------------------------------------


def test_single_mode_has_one_item_per_active_app():
    task = _task(mode=EvalTask.Mode.SINGLE)
    active = [_app(task), _app(task, group="other:000000000000")]
    _app(task, active=False)
    units = items.plan_items(task)
    assert {unit[0].id for unit in units} == {app.id for app in active}
    assert all(unit[1] is None for unit in units)


# --- all_pairs -------------------------------------------------------------------


def test_all_pairs_forms_every_pair_within_a_group_only():
    task = _task(strategy="all_pairs", cross_condition_only=False)
    group_a = [_app(task, condition=n) for n in (1, 2, 3)]
    group_b = [_app(task, condition=n, group="other:000000000000") for n in (1, 2)]
    pairs = items.plan_pairs(task, group_a + group_b)
    assert len(pairs) == 3 + 1
    for a, b in pairs:
        assert a.group_key == b.group_key
        assert str(a.id) < str(b.id)  # canonical orientation


def test_all_pairs_cross_condition_only_drops_same_condition_pairs():
    task = _task(strategy="all_pairs", cross_condition_only=True)
    apps = [
        _app(task, condition=1),
        _app(task, condition=1, repeat=1),
        _app(task, condition=2),
        _app(task, condition=2),
    ]
    pairs = items.plan_pairs(task, apps)
    assert len(pairs) == 4
    assert all(a.snapshot["condition_id"] != b.snapshot["condition_id"] for a, b in pairs)


def test_cross_condition_falls_back_to_model_without_conditions():
    task = _task(strategy="all_pairs", cross_condition_only=True)
    apps = [_app(task, model="m1"), _app(task, model="m1"), _app(task, model="m2")]
    pairs = items.plan_pairs(task, apps)
    assert len(pairs) == 2
    assert all(a.snapshot["model_id"] != b.snapshot["model_id"] for a, b in pairs)


def test_apps_without_a_spec_are_never_paired():
    task = _task(strategy="all_pairs", cross_condition_only=False)
    apps = []
    for model in ("m1", "m2"):
        app = EvalTaskAppFactory(task=task, snapshot={"model_id": model, "app_slug": "", "spec_content_hash": None})
        app.group_key = snapshot_service.group_key(app.snapshot, app.id)
        app.save(update_fields=["group_key"])
        apps.append(app)
    assert apps[0].group_key == str(apps[0].id)
    assert items.plan_pairs(task, apps) == []


# --- random_k -------------------------------------------------------------------------


def test_random_k_covers_every_app_k_times_without_duplicate_pairs():
    task = _task(strategy="random_k", k=3, cross_condition_only=False, seed=1234)
    apps = [_app(task, condition=n) for n in range(10)]
    pairs = items.plan_pairs(task, apps)
    ids = [(str(a.id), str(b.id)) for a, b in pairs]
    assert len(ids) == len(set(ids)), "duplicate pair"
    assert all(a < b for a, b in ids)
    assert all(_degrees(pairs)[app.id] >= 3 for app in apps)
    assert len(pairs) < 45  # not simply every pair
    # Seeded: the same seed gives the same plan.
    assert _ids(items.plan_pairs(task, apps)) == set(ids)


def test_random_k_respects_cross_condition_only():
    task = _task(strategy="random_k", k=2, cross_condition_only=True, seed=7)
    apps = [_app(task, condition=1 + n % 2, repeat=n // 2) for n in range(6)]
    pairs = items.plan_pairs(task, apps)
    assert pairs
    assert all(a.snapshot["condition_id"] != b.snapshot["condition_id"] for a, b in pairs)
    assert all(_degrees(pairs)[app.id] >= 2 for app in apps)
    assert len(_ids(pairs)) == len(pairs)


def test_random_k_stops_when_eligible_pairs_run_out():
    task = _task(strategy="random_k", k=5, cross_condition_only=False)
    apps = [_app(task, condition=n) for n in range(3)]
    pairs = items.plan_pairs(task, apps)
    assert _ids(pairs) == {tuple(sorted((str(a.id), str(b.id)))) for a, b in itertools.combinations(apps, 2)}


# --- vs_baseline ------------------------------------------------------------------------


def test_vs_baseline_pairs_with_the_experiment_baseline_preferring_the_same_repeat():
    experiment = ExperimentFactory()
    base, treat, third = (ExperimentConditionFactory(experiment=experiment) for _ in range(3))
    experiment.baseline_condition = base
    experiment.save(update_fields=["baseline_condition"])
    task = _task(strategy="vs_baseline", experiment=experiment)
    b0, b1 = _app(task, condition=base.id, repeat=0), _app(task, condition=base.id, repeat=1)
    t0, t1 = _app(task, condition=treat.id, repeat=0), _app(task, condition=treat.id, repeat=1)
    lone = _app(task, condition=third.id, repeat=5)  # no baseline app with repeat 5: paired with every one
    pairs = items.plan_pairs(task, [b0, b1, t0, t1, lone])
    expected = [(t0, b0), (t1, b1), (lone, b0), (lone, b1)]
    assert _ids(pairs) == {tuple(sorted((str(a.id), str(b.id)))) for a, b in expected}


def test_vs_baseline_setting_overrides_the_experiment_baseline():
    experiment = ExperimentFactory()
    first, second = (
        ExperimentConditionFactory(experiment=experiment),
        ExperimentConditionFactory(experiment=experiment),
    )
    experiment.baseline_condition = first
    experiment.save(update_fields=["baseline_condition"])
    task = _task(strategy="vs_baseline", experiment=experiment, baseline_condition_id=second.id)
    assert items.baseline_condition_id(task) == second.id
    a, b, c = _app(task, condition=first.id), _app(task, condition=second.id), _app(task, condition=first.id, repeat=1)
    pairs = items.plan_pairs(task, [a, b, c])
    assert all(second.id in (x.snapshot["condition_id"], y.snapshot["condition_id"]) for x, y in pairs)
    assert len(pairs) == 2


def test_vs_baseline_without_any_baseline_forms_no_pairs():
    task = _task(strategy="vs_baseline")
    assert items.baseline_condition_id(task) is None
    assert items.plan_pairs(task, [_app(task, condition=1), _app(task, condition=2)]) == []


# --- build_items ---------------------------------------------------------------------------


def test_build_items_stores_canonical_pairs_with_shuffled_positions():
    task = _task(strategy="all_pairs", cross_condition_only=False)
    apps = [_app(task, condition=n) for n in range(4)]
    _app(task, condition=9, active=False)
    built = items.build_items(task)
    assert len(built) == 6
    rows = list(EvalItem.objects.filter(task=task))
    assert sorted(item.position for item in rows) == list(range(6))
    assert all(str(item.app_a_id) < str(item.app_b_id) for item in rows)
    assert all(item.group_key == GROUP for item in rows)
    assert {item.app_a_id for item in rows} | {item.app_b_id for item in rows} == {app.id for app in apps}
    with pytest.raises(HumanEvalServiceError) as excinfo:
        items.build_items(task)
    assert excinfo.value.code == "items_exist"


def test_sync_app_items_follows_activation():
    task = _task(strategy="all_pairs", cross_condition_only=False)
    a, b, c = (_app(task, condition=n) for n in range(3))
    items.build_items(task)
    a.is_active = False
    a.save(update_fields=["is_active"])
    assert items.sync_app_items(a) == 2
    assert EvalItem.objects.filter(task=task, is_active=True).count() == 1
    b.is_active = False
    b.save(update_fields=["is_active"])
    items.sync_app_items(b)
    a.is_active = True
    a.save(update_fields=["is_active"])
    # Only the (a, c) item comes back: (a, b) still has an inactive app.
    assert items.sync_app_items(a) == 1
    active = EvalItem.objects.get(task=task, is_active=True)
    assert {active.app_a_id, active.app_b_id} == {a.id, c.id}


# --- labels -------------------------------------------------------------------------------


def test_label_width_pads_to_at_least_two_digits():
    assert items.label_width(3) == 2
    assert items.label_width(99) == 2
    assert items.label_width(100) == 3
    assert items.format_label(7, 2) == "App 07"
    assert items.format_label(7, 3) == "App 007"


def test_provisional_labels_number_from_the_highest_existing():
    task = _task(mode=EvalTask.Mode.SINGLE)
    for label in ("App 01", "App 03", "Custom"):
        EvalTaskAppFactory(task=task, label=label)
    assert items.provisional_labels(task, 2) == ["App 04", "App 05"]


def test_relabel_is_seeded_unique_and_survives_label_swaps():
    task = _task(mode=EvalTask.Mode.SINGLE, seed=42)
    apps = [EvalTaskAppFactory(task=task, label=f"App {n:02d}", position=n) for n in range(1, 13)]
    mapping = items.relabel_apps(task)
    assert sorted(mapping.values()) == [f"App {n:02d}" for n in range(1, 13)]
    rows = {str(app.id): app for app in EvalTaskApp.objects.filter(task=task)}
    assert {key: rows[key].label for key in rows} == mapping
    # position follows the new label order
    assert all(rows[key].position == int(label.split()[1]) - 1 for key, label in mapping.items())
    # the shuffle changed something (seeded; a no-op permutation of 12 is ~1 in 479 million)
    assert any(mapping[str(app.id)] != f"App {n:02d}" for n, app in enumerate(apps, start=1))
    # seeded: relabelling again gives the same labels
    assert items.relabel_apps(task) == mapping


def test_relabel_pads_to_the_width_of_the_pool():
    task = _task(mode=EvalTask.Mode.SINGLE)
    EvalTaskApp.objects.bulk_create([EvalTaskApp(task=task, label=f"x{n}", position=n) for n in range(100)])
    labels = set(items.relabel_apps(task).values())
    assert labels == {f"App {n:03d}" for n in range(1, 101)}


def test_list_items_reports_stats_and_targets():
    task = _task(mode=EvalTask.Mode.SINGLE)
    app = _app(task)
    items.build_items(task)
    [payload] = items.list_items(task)
    assert payload["app_a"] == {"id": str(app.id), "label": app.label}
    assert payload["app_b"] is None
    assert payload["target"] == 3
    assert (payload["submitted"], payload["in_progress"], payload["skipped"]) == (0, 0, 0)
