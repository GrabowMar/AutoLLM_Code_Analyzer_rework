"""Allocation engine (owner B1): which item a participant evaluates next (spec §5.6, R1, R3.3, R3.7).

:func:`next_assignment` runs under ``select_for_update()`` on the task row, so
concurrent requests of one task are serialized. Its steps:

1. :func:`expire_stale` — open assignments idle longer than
   ``allocation.assignment_timeout_minutes`` become ``expired`` (expired work is
   kept). Never under strategy ``all`` unless apps are held exclusively
   (``hosting.isolation == "exclusive"``: an abandoned assignment would block its
   apps for everyone forever); never while the task does not accept work (paused,
   outside its window), and :func:`resume_idle_clocks` restarts every open
   assignment's idle clock when it accepts work again, so paused time never counts.
2. The participant's open assignment, if any, is returned (``resumed``).
3. Gates: the task accepts the participant's work (``not_accepting``), the
   participant has not finished (``done``), consent (``consent_required``),
   required pre-questions answered (``pre_questions_required``),
   ``max_items_per_participant`` counting submitted + skipped (``limit_reached``).
4. Candidates are the active items without an assignment row of this participant,
   plus the items of its own *expired* assignments. ``exclusive`` isolation drops
   items with an app in another (non-preview) participant's open assignment.
   ``balanced`` keeps items whose load is below ``ratings_per_item`` (when none is
   left and ``allow_overflow`` is on — or for researcher previews — all of them).
5. The participant's own expired assignment on a still-eligible item is re-opened
   first (``resumed``: context, responses and timings are kept).
6. Otherwise a new assignment is created (``assigned``):

   - ``all``: the first candidate in the participant's seeded order
     (``random.Random(f"{seed}:{participant.id}")`` over the item ids sorted, then
     interleaved round-robin across groups);
   - ``balanced``: the minimum load, then the group (condition) the participant has
     rated least, then ``sha256(f"{seed}:{participant.id}:{item.id}")``.

   Pairwise sides are balanced per item: ``swapped`` = (number of earlier
   non-preview assignments of the item is odd) XOR a seeded per-item coin
   (low bit of ``sha256(f"{seed}:{item.id}")``).
7. No candidate: ``done`` when every active item has a submitted or skipped
   assignment of the participant, else ``no_items_available``.

*Load* counts submitted + open assignments that are not excluded, of participants
that are neither researcher previews nor excluded. Preview participants never add
load, never hold apps under exclusive isolation and always may overflow (preview
data never reaches results).

**Lock order**: the task row before assignment rows, everywhere (``next_assignment``,
``responses.submit`` / ``skip``, the portal ``finish``, task deletion), and
:func:`expire_stale` skips assignment rows another transaction holds (a row being
written is not idle), so it never waits on one.

:func:`release_untouched` (portal ``finish``) expires the participant's open
assignments that hold no saved work, so stopping early never needs a fake skip.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Count
from django.db.models import Exists
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Q
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services.access import is_accepting
from backend.human_eval.services.design import merged_settings

if TYPE_CHECKING:
    import datetime as dt
    import uuid

OPEN_STATUSES = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)
DONE_STATUSES = (Assignment.Status.SUBMITTED, Assignment.Status.SKIPPED)
LOAD_STATUSES = (Assignment.Status.SUBMITTED, *OPEN_STATUSES)
_PREVIEW = Participant.Kind.PREVIEW

REASON_ASSIGNED = "assigned"
REASON_RESUMED = "resumed"
REASON_DONE = "done"
REASON_LIMIT = "limit_reached"
REASON_NONE = "no_items_available"
REASON_NOT_ACCEPTING = "not_accepting"
REASON_CONSENT = "consent_required"
REASON_PRE_QUESTIONS = "pre_questions_required"


# ---------------------------------------------------------------------------
# Small public helpers (also used by the portal session views)
# ---------------------------------------------------------------------------


def answer_missing(value: Any) -> bool:
    """Whether a stored question answer counts as unanswered (None, blank text or an empty list)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def missing_required_answers(questions: list[dict[str, Any]] | None, answers: Any) -> list[dict[str, str]]:
    """``[{path: <question id>, message}]`` for every required question without an answer in ``answers``.

    Stored pre/post answers are validated when they are saved; this only checks completeness.
    """
    stored = answers if isinstance(answers, dict) else {}
    errors: list[dict[str, str]] = []
    for question in questions or []:
        question_id = str(question.get("id") or "")
        if question.get("required", True) and answer_missing(stored.get(question_id)):
            errors.append({"path": question_id, "message": "This question is required"})
    return errors


def participant_target(task: EvalTask, *, n_items: int | None = None) -> int | None:
    """How many items one participant is expected to evaluate (JoinInfo.estimated_items, participation target).

    ``max_items_per_participant`` caps it; strategy ``all`` means every active item; ``balanced`` without a
    limit has no fixed number (None). None too while the task has no items (draft).
    """
    if n_items is None:
        n_items = EvalItem.objects.filter(task=task, is_active=True).count()
    if not n_items:
        return None
    allocation = merged_settings(task.settings)["allocation"]
    limit = allocation.get("max_items_per_participant")
    if limit:
        return min(int(limit), n_items)
    if allocation.get("strategy") == "all":
        return n_items
    return None


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def expire_stale(task: EvalTask) -> int:
    """Expire open assignments idle longer than ``allocation.assignment_timeout_minutes``; returns the count.

    Idle time runs from ``last_activity_at`` (or ``created_at`` when the assignment never saw activity).
    Expired assignments keep their context, responses and timings; their owner may re-open them later.
    Nothing expires

    - under strategy ``all`` (every participant rates every item), unless ``hosting.isolation`` is
      ``exclusive``: there an abandoned assignment would hold its apps (and every item sharing them)
      for good, while expiring it costs nothing — its owner re-opens it once the apps are free;
    - while the task does not accept participants' work (paused, before ``opens_at``, after
      ``closes_at``): nobody can show activity then, so a preview's ``next`` during a pause must not
      expire everyone's work (see also :func:`resume_idle_clocks`).

    Rows another transaction holds (being autosaved, heartbeated or reported on right now) are not idle
    and are skipped (``SKIP LOCKED``), so this never waits on an assignment lock while it holds the
    task row.
    """
    settings = merged_settings(task.settings)
    allocation = settings["allocation"]
    if allocation.get("strategy") == "all" and settings["hosting"].get("isolation") != "exclusive":
        return 0
    if not is_accepting(task):
        return 0
    minutes = int(allocation.get("assignment_timeout_minutes") or 60)
    cutoff = timezone.now() - timedelta(minutes=minutes)
    stale = Q(last_activity_at__lt=cutoff) | Q(last_activity_at__isnull=True, created_at__lt=cutoff)
    with transaction.atomic():
        # of=self and no ordering: the default ordering joins the participant and task rows, which a
        # bare FOR UPDATE would lock (and SKIP LOCKED would then skip every row while the task is locked).
        ids = list(
            Assignment.objects.filter(task_id=task.pk, status__in=OPEN_STATUSES)
            .filter(stale)
            .order_by()
            .select_for_update(skip_locked=True, of=("self",))
            .values_list("pk", flat=True),
        )
        if not ids:
            return 0
        return Assignment.objects.filter(pk__in=ids, status__in=OPEN_STATUSES).update(
            status=Assignment.Status.EXPIRED,
        )


def resume_idle_clocks(task: EvalTask, now: dt.datetime | None = None) -> int:
    """Restart the idle clock of every open assignment of the task (``last_activity_at = now``).

    Call it when the task starts accepting work again (paused or closed → open, or a window change
    that makes an open task accept work): heartbeats and autosaves are refused while it does not, so
    without this the first ``next`` after a pause longer than the timeout would expire every
    assignment that was open before it. Returns the number of assignments touched.
    """
    return Assignment.objects.filter(task_id=task.pk, status__in=OPEN_STATUSES).update(
        last_activity_at=now or timezone.now(),
    )


def release_untouched(participant: Participant) -> int:
    """Expire the participant's open assignments that hold no saved work; returns the count.

    *Saved work* is any ``Response``, ``Observation`` or ``FindingVerdict`` row. Used by the portal
    ``finish``: the app handed out automatically after a submit must not keep a participant who
    wants to stop from finishing (expired assignments add no load and hold no app).
    """
    has_work = (
        Exists(Response.objects.filter(assignment_id=OuterRef("pk")))
        | Exists(Observation.objects.filter(assignment_id=OuterRef("pk")))
        | Exists(FindingVerdict.objects.filter(assignment_id=OuterRef("pk")))
    )
    return (
        Assignment.objects.filter(participant_id=participant.pk, status__in=OPEN_STATUSES)
        .exclude(has_work)
        .update(status=Assignment.Status.EXPIRED)
    )


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    item: EvalItem
    load: int
    group: str
    reopen: Assignment | None  # the participant's own expired assignment on this item


def _app_group(app: EvalTaskApp | None) -> str:
    """The condition of an app (``snapshot.condition_id``), falling back to the model, then the app itself."""
    if app is None:
        return ""
    snapshot = app.snapshot if isinstance(app.snapshot, dict) else {}
    if snapshot.get("condition_id") is not None:
        return f"c:{snapshot['condition_id']}"
    if snapshot.get("model_id"):
        return f"m:{snapshot['model_id']}"
    return f"a:{app.pk}"


def item_group(item: EvalItem) -> str:
    """The group (condition) key of an item: its app's condition, or both conditions of a pair (sorted)."""
    keys = [_app_group(item.app_a)]
    if item.app_b_id is not None:
        keys.append(_app_group(item.app_b))
    return "|".join(sorted(keys))


def _app_groups(task_id: Any) -> dict[uuid.UUID, str]:
    """:func:`_app_group` of every app of the task, reading only the two snapshot keys it needs.

    Snapshots also carry the frozen spec and review findings (large), so allocation never loads them.
    """
    rows = EvalTaskApp.objects.filter(task_id=task_id).values_list(
        "pk",
        "snapshot__condition_id",
        "snapshot__model_id",
    )
    groups: dict[uuid.UUID, str] = {}
    for pk, condition_id, model_id in rows:
        if condition_id is not None:
            groups[pk] = f"c:{condition_id}"
        elif model_id:
            groups[pk] = f"m:{model_id}"
        else:
            groups[pk] = f"a:{pk}"
    return groups


def _item_group_of(item: EvalItem, groups: dict[uuid.UUID, str]) -> str:
    """:func:`item_group` from an :func:`_app_groups` map (no app rows loaded)."""
    keys = [groups.get(item.app_a_id, f"a:{item.app_a_id}")]
    if item.app_b_id is not None:
        keys.append(groups.get(item.app_b_id, f"a:{item.app_b_id}"))
    return "|".join(sorted(keys))


def _digest_int(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)


def _tie_break(task: EvalTask, participant: Participant, item: EvalItem) -> int:
    return _digest_int(f"{task.seed}:{participant.pk}:{item.pk}")


def _item_loads(task: EvalTask) -> dict[uuid.UUID, int]:
    """Per item: submitted + open assignments of non-preview, non-excluded participants, not excluded."""
    rows = (
        Assignment.objects.filter(task_id=task.pk, status__in=LOAD_STATUSES, excluded=False)
        .exclude(participant__kind=_PREVIEW)
        .exclude(participant__excluded=True)
        .order_by()
        .values("item_id")
        .annotate(n=Count("id"))
    )
    return {row["item_id"]: row["n"] for row in rows}


def _busy_apps(task: EvalTask, participant: Participant) -> set[uuid.UUID]:
    """Apps in an open assignment of another non-preview participant (exclusive isolation)."""
    rows = (
        Assignment.objects.filter(task_id=task.pk, status__in=OPEN_STATUSES)
        .exclude(participant_id=participant.pk)
        .exclude(participant__kind=_PREVIEW)
        .values_list("item__app_a_id", "item__app_b_id")
    )
    busy: set[uuid.UUID] = set()
    for app_a_id, app_b_id in rows:
        busy.add(app_a_id)
        if app_b_id is not None:
            busy.add(app_b_id)
    return busy


def _candidates(task: EvalTask, participant: Participant, settings: dict[str, Any]) -> list[_Candidate]:
    """Eligible items for the participant right now (fresh ones and its own expired ones), see the module doc."""
    allocation = settings["allocation"]
    items = list(EvalItem.objects.filter(task_id=task.pk, is_active=True).order_by("position"))
    if not items:
        return []
    own = {assignment.item_id: assignment for assignment in Assignment.objects.filter(participant_id=participant.pk)}
    pool: list[tuple[EvalItem, Assignment | None]] = []
    for item in items:
        mine = own.get(item.pk)
        if mine is None:
            pool.append((item, None))
        elif mine.status == Assignment.Status.EXPIRED:
            pool.append((item, mine))
    if not pool:
        return []
    if settings["hosting"].get("isolation") == "exclusive":
        busy = _busy_apps(task, participant)
        pool = [
            (item, mine)
            for item, mine in pool
            if item.app_a_id not in busy and (item.app_b_id is None or item.app_b_id not in busy)
        ]
    loads = _item_loads(task)
    groups = _app_groups(task.pk)
    candidates = [
        _Candidate(item=item, load=loads.get(item.pk, 0), group=_item_group_of(item, groups), reopen=mine)
        for item, mine in pool
    ]
    if allocation.get("strategy") == "all":
        return candidates
    target = int(allocation.get("ratings_per_item") or 1)
    under = [candidate for candidate in candidates if candidate.load < target]
    if under:
        return under
    if allocation.get("allow_overflow") or participant.kind == _PREVIEW:
        return candidates
    return []


def _all_order(task: EvalTask, participant: Participant) -> dict[uuid.UUID, int]:
    """Strategy ``all``: item id → rank in the participant's seeded order, interleaved round-robin by group.

    The permutation covers every item of the task (active or not) so deactivating one never reshuffles the rest.
    """
    items = sorted(EvalItem.objects.filter(task_id=task.pk), key=lambda i: str(i.pk))
    rng = random.Random(f"{task.seed}:{participant.pk}")  # noqa: S311 - reproducible study order, not a secret
    rng.shuffle(items)
    groups = _app_groups(task.pk)
    queues: dict[str, list[EvalItem]] = {}
    for item in items:
        queues.setdefault(_item_group_of(item, groups), []).append(item)
    order: dict[uuid.UUID, int] = {}
    depth = max((len(queue) for queue in queues.values()), default=0)
    for index in range(depth):
        for queue in queues.values():
            if index < len(queue):
                order[queue[index].pk] = len(order)
    return order


def _rated_groups(participant: Participant) -> dict[str, int]:
    """How many items of each group the participant has rated (submitted) or is rating (open)."""
    counts: dict[str, int] = {}
    groups = _app_groups(participant.task_id)
    rows = Assignment.objects.filter(participant_id=participant.pk, status__in=LOAD_STATUSES).select_related("item")
    for assignment in rows:
        group = _item_group_of(assignment.item, groups)
        counts[group] = counts.get(group, 0) + 1
    return counts


def _pick(
    task: EvalTask,
    participant: Participant,
    settings: dict[str, Any],
    candidates: list[_Candidate],
) -> _Candidate | None:
    """The candidate to hand out next (see the module doc), or None."""
    if not candidates:
        return None
    reopenable = [candidate for candidate in candidates if candidate.reopen is not None]
    if reopenable:
        return min(reopenable, key=lambda c: (c.load, _tie_break(task, participant, c.item)))
    if settings["allocation"].get("strategy") == "all":
        order = _all_order(task, participant)
        return min(candidates, key=lambda c: (order.get(c.item.pk, len(order)), str(c.item.pk)))
    rated = _rated_groups(participant)
    return min(candidates, key=lambda c: (c.load, rated.get(c.group, 0), _tie_break(task, participant, c.item)))


def swapped_for(task: EvalTask, item: EvalItem) -> bool:
    """Pairwise side balancing for the next assignment of ``item`` (False for single items).

    (earlier non-preview assignments of the item is odd) XOR the low bit of ``sha256(f"{seed}:{item.id}")``.
    """
    if item.app_b_id is None:
        return False
    earlier = Assignment.objects.filter(item_id=item.pk).exclude(participant__kind=_PREVIEW).count()
    coin = hashlib.sha256(f"{task.seed}:{item.pk}".encode()).digest()[-1] & 1
    return bool((earlier % 2 == 1) ^ bool(coin))


def is_done(task: EvalTask, participant: Participant) -> bool:
    """Every active item has a submitted or skipped assignment of the participant (and there is at least one)."""
    active = set(EvalItem.objects.filter(task_id=task.pk, is_active=True).values_list("id", flat=True))
    if not active:
        return False
    finished = set(
        Assignment.objects.filter(participant_id=participant.pk, status__in=DONE_STATUSES, item_id__in=active)
        .values_list("item_id", flat=True)
        .distinct(),
    )
    return active <= finished


def available_count(participant: Participant) -> int:
    """How many items the participant could start right now (read-only; PortalTask.progress.remaining_available).

    0 for withdrawn or completed participants; capped by what is left of ``max_items_per_participant``.
    """
    if participant.status in (Participant.Status.WITHDRAWN, Participant.Status.COMPLETED):
        return 0
    task = participant.task
    settings = merged_settings(task.settings)
    count = len(_candidates(task, participant, settings))
    limit = settings["allocation"].get("max_items_per_participant")
    if limit:
        done = Assignment.objects.filter(participant_id=participant.pk, status__in=DONE_STATUSES).count()
        count = min(count, max(0, int(limit) - done))
    return count


# ---------------------------------------------------------------------------
# next_assignment
# ---------------------------------------------------------------------------


def _activate(participant: Participant) -> None:
    """An invited participant (account / code) that receives work becomes active."""
    if participant.status != Participant.Status.INVITED:
        return
    participant.status = Participant.Status.ACTIVE
    participant.joined_at = participant.joined_at or timezone.now()
    participant.save(update_fields=["status", "joined_at"])


def _gate(task: EvalTask, participant: Participant, settings: dict[str, Any]) -> str | None:
    """The NextReason that stops new work before candidates are considered, or None."""
    if not is_accepting(task, participant=participant):
        return REASON_NOT_ACCEPTING
    if participant.status == Participant.Status.COMPLETED:
        return REASON_DONE
    design = task.design if isinstance(task.design, dict) else {}
    if (design.get("consent") or {}).get("enabled") and participant.consent_at is None:
        return REASON_CONSENT
    if missing_required_answers(design.get("pre_questions"), participant.pre_answers):
        return REASON_PRE_QUESTIONS
    limit = settings["allocation"].get("max_items_per_participant")
    if limit:
        done = Assignment.objects.filter(participant_id=participant.pk, status__in=DONE_STATUSES).count()
        if done >= int(limit):
            return REASON_LIMIT
    return None


def next_assignment(participant: Participant) -> tuple[Assignment | None, str]:
    """The participant's next assignment and a TS ``NextReason`` (see the module doc for the rules).

    ``assigned`` (new) and ``resumed`` (open, or own expired re-opened) return the assignment; every other
    reason returns None. Runs in its own savepoint under a row lock on the task.
    """
    with transaction.atomic():
        task = EvalTask.objects.select_for_update().get(pk=participant.task_id)
        participant = Participant.objects.get(pk=participant.pk)
        participant.task = task
        expire_stale(task)

        current = (
            Assignment.objects.filter(participant_id=participant.pk, status__in=OPEN_STATUSES)
            .order_by("position")
            .first()
        )
        if current is not None:
            return current, REASON_RESUMED

        settings = merged_settings(task.settings)
        reason = _gate(task, participant, settings)
        if reason is not None:
            return None, reason

        chosen = _pick(task, participant, settings, _candidates(task, participant, settings))
        if chosen is None:
            return None, REASON_DONE if is_done(task, participant) else REASON_NONE

        _activate(participant)
        now = timezone.now()
        if chosen.reopen is not None:
            assignment = chosen.reopen
            # Back to where it was: in progress once it had been opened (context built), else assigned.
            started = assignment.started_at is not None or bool(assignment.context)
            assignment.status = Assignment.Status.IN_PROGRESS if started else Assignment.Status.ASSIGNED
            assignment.last_activity_at = now
            assignment.save(update_fields=["status", "last_activity_at"])
            return assignment, REASON_RESUMED

        top = Assignment.objects.filter(participant_id=participant.pk).aggregate(top=Max("position"))["top"]
        assignment = Assignment.objects.create(
            task=task,
            participant=participant,
            item=chosen.item,
            status=Assignment.Status.ASSIGNED,
            position=(top or 0) + 1,
            swapped=swapped_for(task, chosen.item),
        )
        return assignment, REASON_ASSIGNED
