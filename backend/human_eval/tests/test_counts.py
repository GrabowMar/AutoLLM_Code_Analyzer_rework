"""services/counts.py: TaskCounts, per-item and per-app stats (preview excluded), plus make_open_task."""

from __future__ import annotations

import pytest

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import Participant
from backend.human_eval.services import counts
from backend.human_eval.services import design as design_service
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task

pytestmark = pytest.mark.django_db

S = Assignment.Status


def _assign(task, item, participant, status, **extra):
    return AssignmentFactory(task=task, item=item, participant=participant, status=status, **extra)


@pytest.fixture
def populated():
    """An open single-mode task: 3 apps / items, 3 participants (+ 1 preview) with mixed assignments."""
    task = make_open_task(n_apps=3)
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    p1 = ParticipantFactory(task=task, number=1, status=Participant.Status.ACTIVE)
    p2 = ParticipantFactory(task=task, number=2, status=Participant.Status.COMPLETED)
    ParticipantFactory(task=task, number=3, status=Participant.Status.INVITED, kind=Participant.Kind.CODE)
    preview = ParticipantFactory(task=task, number=4, kind=Participant.Kind.PREVIEW)
    _assign(task, items[0], p1, S.SUBMITTED)
    _assign(task, items[1], p1, S.IN_PROGRESS)
    _assign(task, items[2], p1, S.SKIPPED, skip_reason="app_broken")
    _assign(task, items[0], p2, S.SUBMITTED)
    _assign(task, items[1], p2, S.ASSIGNED)
    _assign(task, items[2], p2, S.EXPIRED)
    # preview work never counts
    _assign(task, items[0], preview, S.SUBMITTED)
    _assign(task, items[1], preview, S.SKIPPED, skip_reason="platform_unavailable")
    return task, items


def test_task_counts_exclude_preview(populated):
    task, _ = populated
    assert counts.task_counts(task) == {
        "apps": 3,
        "active_apps": 3,
        "items": 3,
        "participants": 3,
        "participants_active": 1,
        "participants_completed": 1,
        "assignments_submitted": 2,
        "assignments_in_progress": 2,
        "assignments_skipped": 1,
        "target_ratings": 3 * 3,  # balanced default: 3 ratings per item
    }


def test_inactive_items_and_apps_are_not_targets(populated):
    task, items = populated
    EvalItem.objects.filter(pk=items[2].pk).update(is_active=False)
    task.apps.filter(pk=items[2].app_a_id).update(is_active=False)
    result = counts.task_counts(task)
    assert result["items"] == 2
    assert result["active_apps"] == 2
    assert result["apps"] == 3
    assert result["target_ratings"] == 2 * 3
    assert counts.item_stats(task)[str(items[2].id)]["target"] is None


def test_target_for_strategy_all_counts_active_and_completed_raters(populated):
    task, _ = populated
    task.settings = design_service.deep_merge(task.settings, {"allocation": {"strategy": "all"}})
    task.save(update_fields=["settings"])
    # active (P001) + completed (P002); invited P003 and the preview do not count
    assert counts.task_counts(task)["target_ratings"] == 3 * 2
    assert {stats["target"] for stats in counts.item_stats(task).values()} == {2}


def test_draft_task_without_items_has_no_target():
    task = EvalTaskFactory(status=EvalTask.Status.DRAFT)
    result = counts.task_counts(task)
    assert result["items"] == 0
    assert result["target_ratings"] is None
    assert counts.item_stats(task) == {}
    assert counts.app_stats(task) == {}


def test_item_stats(populated):
    task, items = populated
    stats = counts.item_stats(task)
    assert set(stats) == {str(item.id) for item in items}
    assert stats[str(items[0].id)] == {"submitted": 2, "in_progress": 0, "skipped": 0, "target": 3}
    assert stats[str(items[1].id)] == {"submitted": 0, "in_progress": 2, "skipped": 0, "target": 3}
    assert stats[str(items[2].id)] == {"submitted": 0, "in_progress": 0, "skipped": 1, "target": 3}


def test_app_stats_single(populated):
    task, items = populated
    stats = counts.app_stats(task)
    assert set(stats) == {str(app_id) for app_id in task.apps.values_list("id", flat=True)}
    assert stats[str(items[0].app_a_id)] == {"submitted": 2, "in_progress": 0, "skipped": 0, "unavailable_skips": 0}
    assert stats[str(items[2].app_a_id)] == {"submitted": 0, "in_progress": 0, "skipped": 1, "unavailable_skips": 1}
    # the preview's platform_unavailable skip on item 1 is not counted
    assert stats[str(items[1].app_a_id)]["unavailable_skips"] == 0


def test_app_stats_pairwise_counts_both_apps():
    task = make_open_task(n_apps=4, mode="pairwise", conditions=2)
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    assert items
    assert all(item.app_b_id is not None for item in items)
    participant = ParticipantFactory(task=task, number=1)
    item = items[0]
    _assign(task, item, participant, S.SKIPPED, skip_reason="platform_unavailable")
    stats = counts.app_stats(task)
    for app_id in (item.app_a_id, item.app_b_id):
        assert stats[str(app_id)] == {"submitted": 0, "in_progress": 0, "skipped": 1, "unavailable_skips": 1}
    untouched = set(task.apps.values_list("id", flat=True)) - {item.app_a_id, item.app_b_id}
    for app_id in untouched:
        assert stats[str(app_id)]["skipped"] == 0
    before = counts.app_stats(task)
    other = ParticipantFactory(task=task, number=2)
    _assign(task, items[1], other, S.SKIPPED, skip_reason="conflict_of_interest")
    after = counts.app_stats(task)
    for app_id in (items[1].app_a_id, items[1].app_b_id):
        assert after[str(app_id)]["skipped"] == before[str(app_id)]["skipped"] + 1
        assert after[str(app_id)]["unavailable_skips"] == before[str(app_id)]["unavailable_skips"]


# --- make_open_task (shared helper other test modules rely on) -------------------------------


def test_make_open_task_single():
    task = make_open_task(n_apps=3)
    assert task.status == EvalTask.Status.OPEN
    assert task.is_frozen
    assert task.opened_at is not None
    assert task.design_hash == design_service.design_hash(task.mode, task.design, task.settings)
    apps = list(task.apps.order_by("position"))
    assert [app.label for app in apps] == ["App 01", "App 02", "App 03"]
    assert len({app.group_key for app in apps}) == 1
    snapshot = apps[0].snapshot
    for key in (
        "model_id",
        "model_name",
        "provider",
        "condition_id",
        "condition_label",
        "experiment_id",
        "experiment_name",
        "app_slug",
        "app_name",
        "repeat_index",
        "mode",
        "functional",
        "evaluator_version",
        "tester_sha256",
        "created_at",
        "checks",
        "spec_content_hash",
        "static_findings",
    ):
        assert key in snapshot
    assert set(snapshot["functional"]) == {"status", "pass_rate", "deployed", "source", "by_category"}
    assert apps[0].group_key == f"{snapshot['app_slug']}:{snapshot['spec_content_hash'][:12]}"
    assert apps[0].job.metrics["functional"]["source"] == "conformance"
    assert apps[0].job.app_requirement is not None
    assert all(app.snapshot["condition_id"] is not None for app in apps)
    assert [app.job.repeat_index for app in apps] == [0, 0, 1]
    items = EvalItem.objects.filter(task=task)
    assert items.count() == 3
    assert sorted(items.values_list("position", flat=True)) == [0, 1, 2]
    assert all(item.app_b_id is None for item in items)


def test_make_open_task_pairwise_pairs_across_conditions_only():
    task = make_open_task(n_apps=4, mode="pairwise", conditions=2)
    pairs = list(EvalItem.objects.filter(task=task))
    # 4 apps in 2 conditions: 6 pairs minus 2 same-condition pairs
    assert len(pairs) == 4
    for item in pairs:
        assert str(item.app_a_id) < str(item.app_b_id)
        assert item.app_a.snapshot["condition_id"] != item.app_b.snapshot["condition_id"]


def test_make_open_task_options():
    settings = {"allocation": {"strategy": "all"}, "hosting": {"isolation": "exclusive", "reset": "per_assignment"}}
    task = make_open_task(n_apps=2, conditions=0, settings=settings)
    assert task.experiment is None
    assert task.settings["allocation"]["strategy"] == "all"
    assert task.settings["hosting"]["reset"] == "per_assignment"
    assert all(app.snapshot["condition_id"] is None for app in task.apps.all())
    with pytest.raises(ValueError, match="invalid design"):
        make_open_task(settings={"hosting": {"reset": "per_assignment"}})
