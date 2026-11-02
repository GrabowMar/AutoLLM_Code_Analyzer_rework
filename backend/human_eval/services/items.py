"""Evaluation items (owner A): pairing, ``build_items`` at first open, blinded labels and ``relabel_apps``.

Items are the units participants evaluate (spec §5.3):

- **single** mode: one item per active app;
- **pairwise** mode (``settings.pairing``): candidate apps are grouped by ``group_key``
  (same app spec version) and pairs are only formed within a group:

  - ``all_pairs`` — every unordered pair; ``cross_condition_only`` drops pairs whose apps
    share a condition (``snapshot.condition_id``, falling back to ``model_id``);
  - ``random_k`` — seeded (``random.Random(task.seed)``): shuffle the group and pair
    neighbours, repeatedly, until every app is in at least ``k`` pairs or its eligible
    pairs (the ``all_pairs`` set) are used up; never a duplicate pair;
  - ``vs_baseline`` — each non-baseline app against the baseline app(s) of its group
    (the same ``repeat_index`` when there is one, else every baseline app); the baseline
    is ``pairing.baseline_condition_id`` or the experiment's baseline condition.

  ``app_a`` / ``app_b`` are ordered by app id string (canonical orientation).

Item ``position`` is a seeded shuffle. Items are built once, at first open; apps
deactivated later have their items deactivated (:func:`sync_app_items`).

Labels: while the task is a draft every app has a provisional ``App NN`` label numbered
from the highest existing number + 1 (:func:`provisional_labels`). First open relabels
every app with a seeded shuffle, ``App 01`` .. ``App NN`` zero-padded to
``max(2, len(str(N)))``, in two phases (temporary ``tmp-<hex>`` labels first) because
the ``(task, label)`` unique constraint is checked row by row.
"""

from __future__ import annotations

import itertools
import random
import re
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Q

from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import counts
from backend.human_eval.services.design import merged_settings
from backend.human_eval.services.errors import conflict

if TYPE_CHECKING:
    from collections.abc import Iterable

LABEL_PATTERN = re.compile(r"^App (\d+)$")
#: Rounds of neighbour pairing without a new pair before random_k falls back to a greedy fill.
_RANDOM_K_MAX_STALL = 25
_RANDOM_K_MAX_ROUNDS = 500

Unit = tuple[EvalTaskApp, EvalTaskApp | None]


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def label_width(count: int) -> int:
    """Digits of a blinded label number: ``max(2, len(str(count)))``."""
    return max(2, len(str(max(count, 0))))


def format_label(number: int, width: int) -> str:
    """``App 07`` style label."""
    return f"App {number:0{width}d}"


def label_number(label: str) -> int | None:
    """The number of an ``App NN`` label, or None for any other label."""
    match = LABEL_PATTERN.match(label or "")
    return int(match.group(1)) if match else None


def provisional_labels(task: EvalTask, count: int) -> list[str]:
    """``count`` new provisional labels, numbered from the highest existing ``App NN`` number + 1."""
    existing = list(EvalTaskApp.objects.filter(task=task).values_list("label", flat=True))
    taken = set(existing)
    top = max((number for number in map(label_number, existing) if number is not None), default=0)
    width = label_width(top + count)
    labels: list[str] = []
    number = top
    while len(labels) < count:
        number += 1
        label = format_label(number, width)
        if label not in taken:
            labels.append(label)
    return labels


def relabel_apps(task: EvalTask) -> dict[str, str]:
    """Give every app of the task its final blinded label (first open); returns ``{app_id: label}``.

    Seeded shuffle of the apps (ordered by id) → ``App 01`` .. ``App NN``; ``position`` follows
    the new label order. Two phases inside one atomic block (temporary labels, then final).
    """
    with transaction.atomic():
        apps = sorted(EvalTaskApp.objects.select_for_update().filter(task=task), key=lambda app: str(app.id))
        order = list(apps)
        random.Random(f"{task.seed}:labels").shuffle(order)  # noqa: S311 - seeded study design, not a secret
        for app in apps:
            app.label = f"tmp-{uuid.uuid4().hex[:16]}"
        EvalTaskApp.objects.bulk_update(apps, ["label"])
        width = label_width(len(order))
        for index, app in enumerate(order):
            app.label = format_label(index + 1, width)
            app.position = index
        EvalTaskApp.objects.bulk_update(order, ["label", "position"])
    return {str(app.id): app.label for app in order}


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def condition_key(app: EvalTaskApp) -> Any:
    """What ``cross_condition_only`` compares: the snapshot's condition id, else its model id."""
    snapshot = app.snapshot or {}
    condition_id = snapshot.get("condition_id")
    if condition_id is not None:
        return ("condition", condition_id)
    return ("model", snapshot.get("model_id") or str(app.id))


def baseline_condition_id(task: EvalTask) -> int | None:
    """``pairing.baseline_condition_id``, else the task experiment's baseline condition, else None."""
    pairing = merged_settings(task.settings)["pairing"]
    if pairing.get("baseline_condition_id") is not None:
        return int(pairing["baseline_condition_id"])
    if task.experiment_id is not None and task.experiment is not None:
        return task.experiment.baseline_condition_id
    return None


def _ordered(first: EvalTaskApp, second: EvalTaskApp) -> tuple[EvalTaskApp, EvalTaskApp]:
    return (first, second) if str(first.id) < str(second.id) else (second, first)


def _groups(apps: Iterable[EvalTaskApp]) -> dict[str, list[EvalTaskApp]]:
    groups: dict[str, list[EvalTaskApp]] = defaultdict(list)
    for app in apps:
        groups[app.group_key or str(app.id)].append(app)
    return {key: sorted(members, key=lambda app: str(app.id)) for key, members in sorted(groups.items())}


def _eligible_pairs(members: list[EvalTaskApp], *, cross_only: bool) -> list[tuple[EvalTaskApp, EvalTaskApp]]:
    pairs = []
    for index, first in enumerate(members):
        for second in members[index + 1 :]:
            if cross_only and condition_key(first) == condition_key(second):
                continue
            pairs.append(_ordered(first, second))
    return pairs


def _random_k(
    members: list[EvalTaskApp],
    eligible: list[tuple[EvalTaskApp, EvalTaskApp]],
    k: int,
    rng: random.Random,
) -> list[tuple[EvalTaskApp, EvalTaskApp]]:
    by_id = {str(app.id): app for app in members}
    allowed = {(str(a.id), str(b.id)) for a, b in eligible}
    partners: dict[str, set[str]] = {app_id: set() for app_id in by_id}
    for a, b in allowed:
        partners[a].add(b)
        partners[b].add(a)
    chosen: set[tuple[str, str]] = set()
    degree = dict.fromkeys(by_id, 0)

    def key(first: str, second: str) -> tuple[str, str]:
        return (first, second) if first < second else (second, first)

    def needs(app_id: str) -> bool:
        return degree[app_id] < k and any(key(app_id, other) not in chosen for other in partners[app_id])

    def take(pair: tuple[str, str]) -> None:
        chosen.add(pair)
        degree[pair[0]] += 1
        degree[pair[1]] += 1

    ids = sorted(by_id)
    stall = 0
    rounds = 0
    while any(needs(app_id) for app_id in ids) and stall < _RANDOM_K_MAX_STALL and rounds < _RANDOM_K_MAX_ROUNDS:
        rounds += 1
        order = list(ids)
        rng.shuffle(order)
        added = False
        for left, right in itertools.pairwise(order):
            pair = key(left, right)
            if pair in allowed and pair not in chosen and (degree[left] < k or degree[right] < k):
                take(pair)
                added = True
        stall = 0 if added else stall + 1
    # Constraints (cross-condition, small groups) can starve the neighbour pairing: fill greedily.
    for app_id in ids:
        options = sorted(other for other in partners[app_id] if key(app_id, other) not in chosen)
        rng.shuffle(options)
        for other in options:
            if degree[app_id] >= k:
                break
            take(key(app_id, other))
    return [(by_id[a], by_id[b]) for a, b in sorted(chosen)]


def _vs_baseline(members: list[EvalTaskApp], baseline: int | None) -> list[tuple[EvalTaskApp, EvalTaskApp]]:
    if baseline is None:
        return []
    base = [app for app in members if (app.snapshot or {}).get("condition_id") == baseline]
    others = [app for app in members if (app.snapshot or {}).get("condition_id") != baseline]
    pairs = []
    for app in others:
        repeat = (app.snapshot or {}).get("repeat_index")
        same_repeat = [b for b in base if repeat is not None and (b.snapshot or {}).get("repeat_index") == repeat]
        for partner in same_repeat or base:
            pairs.append(_ordered(app, partner))
    return pairs


def plan_pairs(task: EvalTask, apps: Iterable[EvalTaskApp]) -> list[tuple[EvalTaskApp, EvalTaskApp]]:
    """The pairs ``settings.pairing`` forms from ``apps`` (deduplicated, canonical orientation)."""
    pairing = merged_settings(task.settings)["pairing"]
    strategy = pairing.get("strategy") or "all_pairs"
    cross_only = bool(pairing.get("cross_condition_only"))
    rng = random.Random(task.seed)  # noqa: S311 - seeded study design, not a secret
    baseline = baseline_condition_id(task) if strategy == "vs_baseline" else None
    pairs: list[tuple[EvalTaskApp, EvalTaskApp]] = []
    seen: set[tuple[str, str]] = set()
    for members in _groups(apps).values():
        if len(members) < 2:  # a pair needs two apps
            continue
        if strategy == "vs_baseline":
            group_pairs = _vs_baseline(members, baseline)
        else:
            eligible = _eligible_pairs(members, cross_only=cross_only)
            group_pairs = (
                _random_k(members, eligible, int(pairing.get("k") or 1), rng) if strategy == "random_k" else eligible
            )
        for first, second in group_pairs:
            pair_key = (str(first.id), str(second.id))
            if pair_key not in seen:
                seen.add(pair_key)
                pairs.append((first, second))
    return pairs


def plan_items(task: EvalTask, apps: Iterable[EvalTaskApp] | None = None) -> list[Unit]:
    """The items the task would get from its active apps: ``[(app_a, app_b | None)]`` in canonical order."""
    if apps is None:
        apps = EvalTaskApp.objects.filter(task=task, is_active=True)
    active = [app for app in apps if app.is_active]
    if task.mode == EvalTask.Mode.PAIRWISE:
        units: list[Unit] = list(plan_pairs(task, active))
    else:
        units = [(app, None) for app in active]
    return sorted(
        units,
        key=lambda unit: (unit[0].group_key or "", str(unit[0].id), str(unit[1].id) if unit[1] else ""),
    )


def build_items(task: EvalTask) -> list[EvalItem]:
    """Create the task's items from its active apps (first open; refuses when items exist)."""
    if EvalItem.objects.filter(task=task).exists():
        msg = "This task already has its evaluation items"
        raise conflict(msg, "items_exist")
    units = plan_items(task)
    positions = list(range(len(units)))
    random.Random(f"{task.seed}:items").shuffle(positions)  # noqa: S311 - seeded study design, not a secret
    items = [
        EvalItem(task=task, app_a=app_a, app_b=app_b, group_key=app_a.group_key, position=position)
        for (app_a, app_b), position in zip(units, positions, strict=True)
    ]
    return EvalItem.objects.bulk_create(items)


def sync_app_items(task_app: EvalTaskApp) -> int:
    """Follow the app's ``is_active`` on its items; returns how many items changed.

    Deactivating deactivates every item with the app; re-activating re-activates the items whose
    apps are all active again. Open assignments on deactivated items are left to finish.
    """
    items = EvalItem.objects.filter(Q(app_a=task_app) | Q(app_b=task_app))
    if not task_app.is_active:
        return items.filter(is_active=True).update(is_active=False)
    return (
        items.filter(is_active=False, app_a__is_active=True)
        .filter(Q(app_b__isnull=True) | Q(app_b__is_active=True))
        .update(is_active=True)
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _app_ref(app: EvalTaskApp | None) -> dict[str, str] | None:
    if app is None:
        return None
    return {"id": str(app.id), "label": app.label}


def serialize_item(item: EvalItem, stats: dict[str, Any]) -> dict[str, Any]:
    """TS ``Item``."""
    entry = stats.get(str(item.id)) or {"submitted": 0, "in_progress": 0, "skipped": 0, "target": None}
    return {
        "id": str(item.id),
        "app_a": _app_ref(item.app_a),
        "app_b": _app_ref(item.app_b),
        "group_key": item.group_key,
        "is_active": item.is_active,
        "submitted": entry["submitted"],
        "in_progress": entry["in_progress"],
        "skipped": entry["skipped"],
        "target": entry["target"],
    }


def list_items(task: EvalTask) -> list[dict[str, Any]]:
    """Every item of the task as TS ``Item``, by position (stats exclude preview participants)."""
    stats = counts.item_stats(task)
    items = EvalItem.objects.filter(task=task).select_related("app_a", "app_b").order_by("position", "id")
    return [serialize_item(item, stats) for item in items]
