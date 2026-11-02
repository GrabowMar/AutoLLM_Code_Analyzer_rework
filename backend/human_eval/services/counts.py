"""Descriptive progress counts of a task (TS ``TaskCounts``, ``Item`` and ``TaskApp.stats``).

Foundation-owned (spec R3.4). Researcher preview participants and their
assignments are excluded everywhere. Excluded participants / assignments are
still counted: these are descriptive progress numbers, not analysis inputs.

- ``in_progress`` counts open assignments (status ``assigned`` or ``in_progress``).
- ``TaskCounts.items`` counts *active* items (the ones that can still be handed out).
- ``target_ratings`` / per-item ``target``: balanced → active items x
  ``allocation.ratings_per_item``; all → active items x non-preview participants
  who are active or completed; ``None`` before items exist (draft tasks).
- ``unavailable_skips`` counts skips with reason ``app_broken`` or
  ``platform_unavailable`` (for a pair, both apps of the item).
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count
from django.db.models import Q

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import Participant
from backend.human_eval.services.design import merged_settings

UNAVAILABLE_SKIP_REASONS = frozenset({"app_broken", "platform_unavailable"})
_OPEN = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)
_PREVIEW = Participant.Kind.PREVIEW


def counted_assignments(task: EvalTask):
    """Assignments of the task that count for progress: every non-preview participant's."""
    return Assignment.objects.filter(task=task).exclude(participant__kind=_PREVIEW)


def counted_participants(task: EvalTask):
    """Participants of the task that count for progress (every non-preview participant)."""
    return Participant.objects.filter(task=task).exclude(kind=_PREVIEW)


def _raters_for_all(task: EvalTask) -> int:
    return (
        counted_participants(task).filter(status__in=(Participant.Status.ACTIVE, Participant.Status.COMPLETED)).count()
    )


def per_item_target(task: EvalTask, *, raters: int | None = None) -> int | None:
    """Ratings wanted per active item: ``ratings_per_item`` (balanced) or the number of raters (all)."""
    allocation = merged_settings(task.settings)["allocation"]
    if allocation.get("strategy") == "all":
        return _raters_for_all(task) if raters is None else raters
    return int(allocation.get("ratings_per_item") or 0)


def task_counts(task: EvalTask) -> dict[str, Any]:
    """TS ``TaskCounts`` for the task (preview excluded)."""
    apps = EvalTaskApp.objects.filter(task=task).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )
    items = EvalItem.objects.filter(task=task).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )
    participants = counted_participants(task).aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=Participant.Status.ACTIVE)),
        completed=Count("id", filter=Q(status=Participant.Status.COMPLETED)),
    )
    assignments = counted_assignments(task).aggregate(
        submitted=Count("id", filter=Q(status=Assignment.Status.SUBMITTED)),
        in_progress=Count("id", filter=Q(status__in=_OPEN)),
        skipped=Count("id", filter=Q(status=Assignment.Status.SKIPPED)),
    )
    target_ratings = None
    if items["total"]:
        raters = participants["active"] + participants["completed"]
        target_ratings = items["active"] * (per_item_target(task, raters=raters) or 0)
    return {
        "apps": apps["total"],
        "active_apps": apps["active"],
        "items": items["active"],
        "participants": participants["total"],
        "participants_active": participants["active"],
        "participants_completed": participants["completed"],
        "assignments_submitted": assignments["submitted"],
        "assignments_in_progress": assignments["in_progress"],
        "assignments_skipped": assignments["skipped"],
        "target_ratings": target_ratings,
    }


def _status_rows(task: EvalTask):
    return (
        counted_assignments(task)
        .order_by()
        .values("item_id", "item__app_a_id", "item__app_b_id", "status", "skip_reason")
        .annotate(n=Count("id"))
    )


def _empty_item_stats() -> dict[str, Any]:
    return {"submitted": 0, "in_progress": 0, "skipped": 0}


def _empty_app_stats() -> dict[str, Any]:
    return {**_empty_item_stats(), "unavailable_skips": 0}


def _add(stats: dict[str, Any], status: str, n: int) -> None:
    if status == Assignment.Status.SUBMITTED:
        stats["submitted"] += n
    elif status in _OPEN:
        stats["in_progress"] += n
    elif status == Assignment.Status.SKIPPED:
        stats["skipped"] += n


def item_stats(task: EvalTask) -> dict[str, dict[str, Any]]:
    """``{str(item_id): {submitted, in_progress, skipped, target}}`` for every item of the task.

    ``target`` is None for inactive items (they get no new assignments).
    """
    target = per_item_target(task)
    stats: dict[str, dict[str, Any]] = {}
    for item_id, is_active in EvalItem.objects.filter(task=task).values_list("id", "is_active"):
        stats[str(item_id)] = {**_empty_item_stats(), "target": target if is_active else None}
    for row in _status_rows(task):
        entry = stats.setdefault(str(row["item_id"]), {**_empty_item_stats(), "target": None})
        _add(entry, row["status"], row["n"])
    return stats


def app_stats(task: EvalTask) -> dict[str, dict[str, Any]]:
    """``{str(app_id): {submitted, in_progress, skipped, unavailable_skips}}`` for every app of the task.

    An assignment counts for every app of its item (both apps of a pair).
    """
    stats: dict[str, dict[str, Any]] = {
        str(app_id): _empty_app_stats()
        for app_id in EvalTaskApp.objects.filter(task=task).values_list("id", flat=True)
    }
    for row in _status_rows(task):
        for app_id in (row["item__app_a_id"], row["item__app_b_id"]):
            if app_id is None:
                continue
            entry = stats.setdefault(str(app_id), _empty_app_stats())
            _add(entry, row["status"], row["n"])
            if row["status"] == Assignment.Status.SKIPPED and row["skip_reason"] in UNAVAILABLE_SKIP_REASONS:
                entry["unavailable_skips"] += row["n"]
    return stats
