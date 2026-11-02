"""services/allocation.py: gates, balanced/all strategies, exclusive isolation, side balancing, expiry and done."""

from __future__ import annotations

import hashlib
import itertools
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import allocation
from backend.human_eval.services import design as design_service
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task

pytestmark = pytest.mark.django_db

PRE_ANSWERS = {"programming_years": 4, "web_dev_experience": 3, "security_testing_experience": 2}


def _ready(task: EvalTask, **kwargs) -> Participant:
    """A participant that passed consent and the pre-questionnaire."""
    kwargs.setdefault("consent_at", timezone.now())
    kwargs.setdefault("pre_answers", dict(PRE_ANSWERS))
    return ParticipantFactory(task=task, **kwargs)


def _settings(**allocation_settings) -> dict:
    return {"allocation": allocation_settings}


def _submit(assignment: Assignment) -> None:
    assignment.status = Assignment.Status.SUBMITTED
    assignment.submitted_at = timezone.now()
    assignment.save(update_fields=["status", "submitted_at"])


def _take(participant: Participant) -> Assignment:
    assignment, reason = allocation.next_assignment(participant)
    assert reason == "assigned", reason
    return assignment


# --- gates ----------------------------------------------------------------------


def test_gates_in_order():
    task = make_open_task(n_apps=2, settings=_settings(max_items_per_participant=1))
    fresh = ParticipantFactory(task=task)
    assert allocation.next_assignment(fresh) == (None, "consent_required")
    fresh.consent_at = timezone.now()
    fresh.save()
    assert allocation.next_assignment(fresh) == (None, "pre_questions_required")
    fresh.pre_answers = {"programming_years": 2, "web_dev_experience": 3, "security_testing_experience": ""}
    fresh.save()
    assert allocation.next_assignment(fresh) == (None, "pre_questions_required")
    fresh.pre_answers = dict(PRE_ANSWERS)
    fresh.save()
    first = _take(fresh)
    _submit(first)
    assert allocation.next_assignment(fresh) == (None, "limit_reached")

    completed = _ready(task, status=Participant.Status.COMPLETED)
    assert allocation.next_assignment(completed) == (None, "done")
    withdrawn = _ready(task, status=Participant.Status.WITHDRAWN)
    assert allocation.next_assignment(withdrawn) == (None, "not_accepting")

    task.status = EvalTask.Status.PAUSED
    task.save(update_fields=["status"])
    assert allocation.next_assignment(_ready(task)) == (None, "not_accepting")


def test_consent_and_pre_questions_not_required_when_the_design_has_none():
    design = design_service.default_design("single")
    design["consent"]["enabled"] = False
    design["pre_questions"] = []
    task = make_open_task(n_apps=1, design=design)
    participant = ParticipantFactory(task=task)
    assignment, reason = allocation.next_assignment(participant)
    assert reason == "assigned"
    assert assignment.position == 1


def test_open_assignment_is_resumed_before_any_gate():
    task = make_open_task(n_apps=2)
    participant = _ready(task)
    current = _take(participant)
    assert allocation.next_assignment(participant) == (current, "resumed")
    task.status = EvalTask.Status.PAUSED
    task.save(update_fields=["status"])
    assert allocation.next_assignment(participant) == (current, "resumed")


def test_new_assignments_are_assigned_with_increasing_positions_and_activate_invited():
    task = make_open_task(n_apps=3, settings=_settings(ratings_per_item=3))
    participant = _ready(task, kind=Participant.Kind.ACCOUNT, status=Participant.Status.INVITED, joined_at=None)
    first = _take(participant)
    participant.refresh_from_db()
    assert participant.status == Participant.Status.ACTIVE
    assert participant.joined_at is not None
    assert (first.status, first.position, first.swapped) == (Assignment.Status.ASSIGNED, 1, False)
    assert first.started_at is None
    _submit(first)
    second = _take(participant)
    assert second.position == 2
    assert second.item_id != first.item_id


# --- balanced -------------------------------------------------------------------


def test_balanced_spreads_items_to_the_target_then_stops():
    task = make_open_task(n_apps=3, settings=_settings(ratings_per_item=1))
    seen = []
    for _ in range(3):
        assignment = _take(_ready(task))
        _submit(assignment)
        seen.append(assignment.item_id)
    assert len(set(seen)) == 3  # min load first: every item got one rating before any got two
    assert allocation.next_assignment(_ready(task)) == (None, "no_items_available")


def test_balanced_overflow_hands_out_min_load_items():
    task = make_open_task(n_apps=2, settings=_settings(ratings_per_item=1, allow_overflow=True))
    first = _take(_ready(task))
    _submit(first)
    second = _take(_ready(task))
    _submit(second)
    third = _take(_ready(task))
    assert third.item_id in {first.item_id, second.item_id}
    fourth = _take(_ready(task))
    assert {third.item_id, fourth.item_id} == {first.item_id, second.item_id}


def test_load_ignores_preview_excluded_participants_and_excluded_assignments():
    task = make_open_task(n_apps=1, settings=_settings(ratings_per_item=1))
    item = EvalItem.objects.get(task=task)
    preview = _ready(task, kind=Participant.Kind.PREVIEW)
    excluded = _ready(task, excluded=True)
    other = _ready(task)
    AssignmentFactory(participant=preview, item=item, status=Assignment.Status.SUBMITTED)
    AssignmentFactory(participant=excluded, item=item, status=Assignment.Status.SUBMITTED)
    AssignmentFactory(participant=other, item=item, status=Assignment.Status.SUBMITTED, excluded=True)
    assignment = _take(_ready(task))
    assert assignment.item_id == item.id
    _submit(assignment)
    assert allocation.next_assignment(_ready(task)) == (None, "no_items_available")


def test_preview_participants_may_always_overflow():
    task = make_open_task(n_apps=1, settings=_settings(ratings_per_item=1))
    _submit(_take(_ready(task)))
    assert allocation.next_assignment(_ready(task)) == (None, "no_items_available")
    assignment, reason = allocation.next_assignment(_ready(task, kind=Participant.Kind.PREVIEW))
    assert reason == "assigned"
    assert assignment.item.task_id == task.id


def test_balanced_prefers_the_group_the_participant_rated_least():
    # 4 apps over 2 conditions: apps 0, 2 are condition 1; apps 1, 3 condition 2.
    task = make_open_task(n_apps=4, conditions=2, settings=_settings(ratings_per_item=5))
    for _ in range(4):
        participant = _ready(task)
        first = _take(participant)
        _submit(first)
        second = _take(participant)
        assert allocation.item_group(second.item) != allocation.item_group(first.item)
        _submit(second)


def test_balanced_tie_break_is_seeded_and_reproducible():
    task = make_open_task(n_apps=4, conditions=1, settings=_settings(ratings_per_item=5))
    participant = _ready(task)
    items = list(EvalItem.objects.filter(task=task))
    expected = min(
        items,
        key=lambda item: int(hashlib.sha256(f"{task.seed}:{participant.id}:{item.id}".encode()).hexdigest(), 16),
    )
    assert _take(participant).item_id == expected.id


# --- all ----------------------------------------------------------------------------


def test_strategy_all_hands_every_item_once_interleaved_by_group():
    task = make_open_task(n_apps=4, conditions=2, settings=_settings(strategy="all"))
    participant = _ready(task)
    groups = []
    items = []
    while True:
        assignment, reason = allocation.next_assignment(participant)
        if assignment is None:
            break
        assert reason == "assigned"
        items.append(assignment.item_id)
        groups.append(allocation.item_group(assignment.item))
        _submit(assignment)
    assert reason == "done"
    assert len(items) == len(set(items)) == 4
    # Round-robin across the two conditions: consecutive items never share a group.
    assert all(a != b for a, b in itertools.pairwise(groups))
    order = allocation._all_order(task, participant)
    assert sorted(order, key=order.get) == items
    assert allocation._all_order(task, participant) == order  # seeded, reproducible


def test_strategy_all_never_expires():
    task = make_open_task(n_apps=1, settings=_settings(strategy="all", assignment_timeout_minutes=5))
    old = timezone.now() - timedelta(hours=5)
    item = EvalItem.objects.get(task=task)
    assignment = AssignmentFactory(participant=_ready(task), item=item, last_activity_at=old)
    assert allocation.expire_stale(task) == 0
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS


# --- exclusive isolation -------------------------------------------------------------


def test_exclusive_isolation_keeps_an_app_in_one_open_assignment():
    task = make_open_task(
        n_apps=2,
        settings={"allocation": {"ratings_per_item": 3}, "hosting": {"isolation": "exclusive"}},
    )
    first = _take(_ready(task))
    second = _take(_ready(task))
    assert first.item_id != second.item_id
    assert allocation.next_assignment(_ready(task)) == (None, "no_items_available")
    # A preview is blocked too (it would disturb the participant's app state) ...
    assert allocation.next_assignment(_ready(task, kind=Participant.Kind.PREVIEW)) == (None, "no_items_available")
    _submit(first)
    # ... but its own open assignment never blocks real participants.
    preview_assignment = _take(_ready(task, kind=Participant.Kind.PREVIEW))
    assert preview_assignment.item_id == first.item_id
    assert _take(_ready(task)).item_id == first.item_id


def test_exclusive_isolation_blocks_every_pair_sharing_an_app():
    task = make_open_task(
        n_apps=3,
        conditions=3,
        mode="pairwise",
        settings={"allocation": {"ratings_per_item": 5}, "hosting": {"isolation": "exclusive"}},
    )
    assert EvalItem.objects.filter(task=task).count() == 3
    _take(_ready(task))
    assert allocation.next_assignment(_ready(task)) == (None, "no_items_available")


def test_shared_isolation_allows_parallel_use():
    task = make_open_task(n_apps=1, settings=_settings(ratings_per_item=3))
    first = _take(_ready(task))
    assert _take(_ready(task)).item_id == first.item_id


# --- side balancing -----------------------------------------------------------------------


def test_pairwise_sides_alternate_per_item_from_a_seeded_coin():
    task = make_open_task(n_apps=2, conditions=2, mode="pairwise", settings=_settings(ratings_per_item=10))
    item = EvalItem.objects.get(task=task)
    coin = bool(hashlib.sha256(f"{task.seed}:{item.id}".encode()).digest()[-1] & 1)
    sides = []
    for index in range(4):
        if index == 2:
            preview = _take(_ready(task, kind=Participant.Kind.PREVIEW))
            assert preview.swapped is coin  # previews never shift the parity: same side as the next participant
        sides.append(_take(_ready(task)).swapped)
    assert sides == [coin, not coin, coin, not coin]


def test_single_items_are_never_swapped():
    task = make_open_task(n_apps=1)
    assert _take(_ready(task)).swapped is False


# --- expiry and re-opening ---------------------------------------------------------------


def test_expire_stale_uses_last_activity_or_created_at():
    task = make_open_task(n_apps=3, settings=_settings(assignment_timeout_minutes=30))
    items = list(EvalItem.objects.filter(task=task))
    old = timezone.now() - timedelta(minutes=31)
    stale = AssignmentFactory(participant=_ready(task), item=items[0], last_activity_at=old)
    fresh = AssignmentFactory(participant=_ready(task), item=items[1], last_activity_at=timezone.now())
    never_active = AssignmentFactory(
        participant=_ready(task),
        item=items[2],
        status=Assignment.Status.ASSIGNED,
        last_activity_at=None,
    )
    Assignment.objects.filter(pk=never_active.pk).update(created_at=old)
    done = AssignmentFactory(participant=_ready(task), item=items[0], status=Assignment.Status.SUBMITTED)
    Assignment.objects.filter(pk=done.pk).update(last_activity_at=old)
    assert allocation.expire_stale(task) == 2
    statuses = dict(Assignment.objects.values_list("id", "status"))
    assert statuses[stale.id] == Assignment.Status.EXPIRED
    assert statuses[never_active.id] == Assignment.Status.EXPIRED
    assert statuses[fresh.id] == Assignment.Status.IN_PROGRESS
    assert statuses[done.id] == Assignment.Status.SUBMITTED


def test_own_expired_assignment_is_reopened_with_its_work():
    task = make_open_task(n_apps=2, settings=_settings(ratings_per_item=2, assignment_timeout_minutes=5))
    owner = _ready(task)
    mine = _take(owner)
    mine.status = Assignment.Status.IN_PROGRESS
    mine.started_at = timezone.now() - timedelta(minutes=30)
    mine.context = {"blocks": {}, "hosting": {}, "instructions_sha256": "x"}
    mine.last_activity_at = timezone.now() - timedelta(minutes=10)
    mine.save()
    Response.objects.create(assignment=mine, block_id="ratings", answers={"criteria": {"overall": 5}})

    _take(_ready(task))  # another participant's next() expires the stale assignment
    mine.refresh_from_db()
    assert mine.status == Assignment.Status.EXPIRED

    reopened, reason = allocation.next_assignment(owner)
    assert (reopened.id, reason) == (mine.id, "resumed")
    reopened.refresh_from_db()
    assert reopened.status == Assignment.Status.IN_PROGRESS
    assert reopened.context["instructions_sha256"] == "x"
    assert reopened.last_activity_at > timezone.now() - timedelta(minutes=1)
    assert reopened.responses.get(block_id="ratings").answers == {"criteria": {"overall": 5}}
    assert Assignment.objects.filter(participant=owner).count() == 1


def test_expired_assignment_that_was_never_opened_goes_back_to_assigned():
    task = make_open_task(n_apps=1, settings=_settings(ratings_per_item=2))
    owner = _ready(task)
    mine = _take(owner)
    Assignment.objects.filter(pk=mine.pk).update(status=Assignment.Status.EXPIRED)
    reopened, reason = allocation.next_assignment(owner)
    assert (reopened.id, reason, reopened.status) == (mine.id, "resumed", Assignment.Status.ASSIGNED)


def test_expired_assignment_is_not_reopened_once_the_item_reached_its_target():
    task = make_open_task(n_apps=1, settings=_settings(ratings_per_item=1, assignment_timeout_minutes=5))
    owner = _ready(task)
    mine = _take(owner)
    Assignment.objects.filter(pk=mine.pk).update(last_activity_at=timezone.now() - timedelta(minutes=6))
    other = _take(_ready(task))  # expired work never counts as load
    assert other.item_id == mine.item_id
    _submit(other)
    assert allocation.next_assignment(owner) == (None, "no_items_available")
    mine.refresh_from_db()
    assert mine.status == Assignment.Status.EXPIRED


# --- done -------------------------------------------------------------------------------


def test_done_means_every_active_item_submitted_or_skipped():
    task = make_open_task(n_apps=3, settings=_settings(ratings_per_item=1))
    participant = _ready(task)
    first = _take(participant)
    _submit(first)
    second = _take(participant)
    second.status = Assignment.Status.SKIPPED
    second.skip_reason = "app_broken"
    second.save()
    remaining = EvalItem.objects.filter(task=task).exclude(pk__in=[first.item_id, second.item_id]).get()
    remaining.is_active = False
    remaining.save()
    assert allocation.next_assignment(participant) == (None, "done")
    assert allocation.is_done(task, participant)
    # Skips are no load: the skipped item goes to the next participant ...
    other = _take(_ready(task))
    assert other.item_id == second.item_id
    _submit(other)
    # ... after which every active item is at its target: nothing available, but not done.
    late = _ready(task)
    assert allocation.next_assignment(late) == (None, "no_items_available")
    assert not allocation.is_done(task, late)


def test_expired_items_are_not_done():
    task = make_open_task(n_apps=1, settings=_settings(strategy="all"))
    participant = _ready(task)
    AssignmentFactory(participant=participant, item=EvalItem.objects.get(task=task), status=Assignment.Status.EXPIRED)
    assert not allocation.is_done(task, participant)


# --- helpers ------------------------------------------------------------------------------


def test_available_count_and_targets():
    task = make_open_task(n_apps=3, settings=_settings(ratings_per_item=1, max_items_per_participant=2))
    participant = _ready(task)
    assert allocation.available_count(participant) == 2  # capped by the limit
    _submit(_take(participant))
    assert allocation.available_count(participant) == 1
    assert allocation.available_count(_ready(task, status=Participant.Status.WITHDRAWN)) == 0
    assert allocation.participant_target(task) == 2

    unlimited = make_open_task(n_apps=3, settings=_settings(strategy="balanced"))
    assert allocation.participant_target(unlimited) is None
    every = make_open_task(n_apps=3, settings=_settings(strategy="all"))
    assert allocation.participant_target(every) == 3
    assert allocation.participant_target(every, n_items=0) is None


def test_missing_required_answers():
    questions = [
        {"id": "a", "kind": "text", "label": "A", "required": True},
        {"id": "b", "kind": "multi", "label": "B", "required": True, "options": []},
        {"id": "c", "kind": "number", "label": "C", "required": False},
        {"id": "d", "kind": "number", "label": "D", "required": True},
    ]
    errors = allocation.missing_required_answers(questions, {"a": "  ", "b": [], "d": 0})
    assert [error["path"] for error in errors] == ["a", "b"]
    assert allocation.missing_required_answers(questions, {"a": "x", "b": ["y"], "d": 0}) == []
    assert len(allocation.missing_required_answers(questions, None)) == 3


# --- regressions: expiry under "all" + exclusive (C16), pauses (C15), lock order (C41), release (C02) -----


def test_strategy_all_with_exclusive_apps_expires_an_abandoned_assignment_and_its_owner_resumes_it():
    task = make_open_task(
        n_apps=1,
        settings={
            "allocation": {"strategy": "all", "assignment_timeout_minutes": 5},
            "hosting": {"isolation": "exclusive"},
        },
    )
    owner = _ready(task)
    abandoned = _take(owner)
    Assignment.objects.filter(pk=abandoned.pk).update(
        status=Assignment.Status.IN_PROGRESS,
        last_activity_at=timezone.now() - timedelta(minutes=6),
    )
    other = _ready(task)
    # The abandoned assignment no longer holds the app: the next participant gets the item.
    taken = _take(other)
    assert taken.item_id == abandoned.item_id
    abandoned.refresh_from_db()
    assert abandoned.status == Assignment.Status.EXPIRED
    # Its owner waits while the app is in use, then re-opens the same assignment (work kept).
    assert allocation.next_assignment(owner) == (None, "no_items_available")
    _submit(taken)
    resumed, reason = allocation.next_assignment(owner)
    assert (resumed.id, reason) == (abandoned.id, "resumed")


def test_strategy_all_with_shared_apps_still_never_expires():
    task = make_open_task(
        n_apps=1,
        settings={
            "allocation": {"strategy": "all", "assignment_timeout_minutes": 5},
            "hosting": {"isolation": "shared"},
        },
    )
    assignment = AssignmentFactory(
        participant=_ready(task),
        item=EvalItem.objects.get(task=task),
        last_activity_at=timezone.now() - timedelta(hours=5),
    )
    assert allocation.expire_stale(task) == 0
    assignment.refresh_from_db()
    assert assignment.status == Assignment.Status.IN_PROGRESS


def test_nothing_expires_while_the_task_does_not_accept_work():
    task = make_open_task(n_apps=2, settings=_settings(assignment_timeout_minutes=60))
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    stale = AssignmentFactory(
        participant=_ready(task),
        item=items[0],
        last_activity_at=timezone.now() - timedelta(minutes=90),
    )
    EvalTask.objects.filter(pk=task.pk).update(status=EvalTask.Status.PAUSED)
    task.refresh_from_db()
    assert allocation.expire_stale(task) == 0
    # A researcher preview may work during the pause; its next() must not expire real work.
    _take(_ready(task, kind=Participant.Kind.PREVIEW))
    stale.refresh_from_db()
    assert stale.status == Assignment.Status.IN_PROGRESS
    # Past closes_at (status still open) counts as not accepting too.
    EvalTask.objects.filter(pk=task.pk).update(
        status=EvalTask.Status.OPEN,
        closes_at=timezone.now() - timedelta(minutes=1),
    )
    task.refresh_from_db()
    assert allocation.expire_stale(task) == 0


def test_resume_idle_clocks_keeps_a_pause_from_expiring_open_work():
    task = make_open_task(n_apps=2, settings=_settings(assignment_timeout_minutes=60))
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    paused_at = timezone.now() - timedelta(minutes=90)
    open_one = AssignmentFactory(participant=_ready(task), item=items[0], last_activity_at=paused_at)
    done = AssignmentFactory(
        participant=_ready(task),
        item=items[1],
        status=Assignment.Status.SUBMITTED,
        last_activity_at=paused_at,
    )
    # The task reopens after a 90 minute pause: the caller restarts the idle clocks ...
    assert allocation.resume_idle_clocks(task) == 1
    # ... so the first next() afterwards expires nothing.
    _take(_ready(task))
    open_one.refresh_from_db()
    assert open_one.status == Assignment.Status.IN_PROGRESS
    assert open_one.last_activity_at > timezone.now() - timedelta(minutes=1)
    done.refresh_from_db()
    assert done.last_activity_at == paused_at


@pytest.mark.django_db(transaction=True)
def test_expire_stale_skips_assignment_rows_another_transaction_holds():
    import threading

    from django.db import close_old_connections
    from django.db import connection
    from django.db import transaction

    task = make_open_task(n_apps=2, settings=_settings(assignment_timeout_minutes=5))
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    old = timezone.now() - timedelta(minutes=10)
    held = AssignmentFactory(participant=_ready(task), item=items[0], last_activity_at=old)
    free = AssignmentFactory(participant=_ready(task), item=items[1], last_activity_at=old)
    locked, release = threading.Event(), threading.Event()

    def hold() -> None:  # e.g. an autosave or heartbeat of this assignment in progress (row lock only)
        close_old_connections()
        try:
            with transaction.atomic():
                list(Assignment.objects.select_for_update(of=("self",)).filter(pk=held.pk))
                locked.set()
                release.wait(10)
        finally:
            connection.close()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert locked.wait(5)
        assert allocation.expire_stale(task) == 1  # returns at once instead of waiting on the lock
    finally:
        release.set()
        holder.join(10)
    statuses = dict(Assignment.objects.values_list("id", "status"))
    assert statuses == {held.id: Assignment.Status.IN_PROGRESS, free.id: Assignment.Status.EXPIRED}


def test_release_untouched_expires_only_open_assignments_without_saved_work():
    from backend.human_eval.tests.factories import FindingVerdictFactory
    from backend.human_eval.tests.factories import ObservationFactory
    from backend.human_eval.tests.factories import ResponseFactory

    task = make_open_task(n_apps=5)
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    participant = _ready(task)
    untouched = AssignmentFactory(participant=participant, item=items[0], status=Assignment.Status.ASSIGNED)
    opened = AssignmentFactory(participant=participant, item=items[1])
    answered = ResponseFactory(assignment=AssignmentFactory(participant=participant, item=items[2])).assignment
    reported = ObservationFactory(assignment=AssignmentFactory(participant=participant, item=items[3])).assignment
    reviewed = FindingVerdictFactory(assignment=AssignmentFactory(participant=participant, item=items[4])).assignment
    others = AssignmentFactory(participant=_ready(task), item=items[0])
    assert allocation.release_untouched(participant) == 2
    statuses = dict(Assignment.objects.values_list("id", "status"))
    assert statuses[untouched.id] == statuses[opened.id] == Assignment.Status.EXPIRED
    for kept in (answered, reported, reviewed, others):
        assert statuses[kept.id] == Assignment.Status.IN_PROGRESS


def test_app_groups_match_item_group_without_loading_snapshots():
    task = make_open_task(n_apps=4, conditions=2, mode="pairwise", settings=_settings(ratings_per_item=3))
    groups = allocation._app_groups(task.pk)
    items = list(EvalItem.objects.filter(task=task).select_related("app_a", "app_b"))
    assert items
    for item in items:
        assert allocation._item_group_of(item, groups) == allocation.item_group(item)
    model_only = make_open_task(n_apps=2, conditions=0)
    groups = allocation._app_groups(model_only.pk)
    for item in EvalItem.objects.filter(task=model_only).select_related("app_a"):
        assert allocation._item_group_of(item, groups) == allocation.item_group(item)
        assert allocation.item_group(item).startswith("m:")
