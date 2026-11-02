"""services/quality.py: speeding, straightlining, attention checks, app not viewed, possible duplicates."""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import Participant
from backend.human_eval.services import design as design_service
from backend.human_eval.services import quality
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import ResponseFactory
from backend.human_eval.tests.factories import make_open_task

pytestmark = pytest.mark.django_db

S = Assignment.Status
VIEWED = {"single": {"viewed_seconds": 120, "first_running_at": "2026-11-01T10:00:00+00:00"}}

ATTENTION = {
    "id": "attention_1",
    "kind": "attention",
    "label": "To show you read this, choose 'Agree'.",
    "required": True,
    "options": [{"value": "agree", "label": "Agree"}, {"value": "disagree", "label": "Disagree"}],
    "expected": "agree",
}

FOUR_CRITERIA_BLOCK = {
    "id": "ratings",
    "type": "rating",
    "title": "Ratings",
    "criteria": [
        {"id": cid, "label": cid.title(), "scale": {"min": 1, "max": 7}, "required": True, "allow_na": True}
        for cid in ("functionality", "usability", "design", "overall")
    ],
}


def _design(**extra):
    design = design_service.default_design("single")
    design["blocks"] = [copy.deepcopy(FOUR_CRITERIA_BLOCK)]
    design["analysis"]["primary"] = {"block_id": "ratings", "metric": "composite"}
    design.update(extra)
    return design


def _submitted(task, item, participant, *, active=600, wall=600, views=None, **extra):
    now = timezone.now()
    fields = {
        "status": S.SUBMITTED,
        "started_at": now - timedelta(minutes=10),
        "submitted_at": now,
        "active_seconds": active,
        "wall_seconds": wall,
        "app_views": VIEWED if views is None else views,
        **extra,
    }
    return AssignmentFactory(task=task, item=item, participant=participant, **fields)


@pytest.fixture
def task():
    return make_open_task(n_apps=2, design=_design(), settings={"quality": {"min_seconds_per_item": 120}})


def _items(task):
    return list(EvalItem.objects.filter(task=task).order_by("position"))


def test_speeding_needs_both_active_and_wall_time_below_threshold(task):
    item, other = _items(task)
    participant = ParticipantFactory(task=task, number=1)
    fast = _submitted(task, item, participant, active=30, wall=40)
    hidden_tab = _submitted(task, other, participant, active=30, wall=900)
    flags = quality.assignment_flags(task)
    assert flags[str(fast.pk)] == ["speeding"]
    assert flags[str(hidden_tab.pk)] == []


def test_speeding_falls_back_to_elapsed_time_without_heartbeat_wall_time(task):
    item, other = _items(task)
    participant = ParticipantFactory(task=task, number=1)
    slow = _submitted(task, item, participant, active=0, wall=0)  # 10 minutes elapsed
    now = timezone.now()
    quick = _submitted(task, other, participant, active=0, wall=0, started_at=now - timedelta(seconds=20))
    flags = quality.assignment_flags(task)
    assert flags[str(slow.pk)] == []
    assert flags[str(quick.pk)] == ["speeding"]


def test_speeding_is_off_with_zero_threshold():
    task = make_open_task(n_apps=1, design=_design(), settings={"quality": {"min_seconds_per_item": 0}})
    row = _submitted(task, _items(task)[0], ParticipantFactory(task=task, number=1), active=1, wall=1)
    assert quality.assignment_flags(task)[str(row.pk)] == []


def test_straightlining_needs_four_identical_numeric_answers(task):
    item, other = _items(task)
    participant = ParticipantFactory(task=task, number=1)
    flat = _submitted(task, item, participant)
    ResponseFactory(
        assignment=flat,
        block_id="ratings",
        answers={"criteria": {"functionality": 5, "usability": 5, "design": 5, "overall": 5}},
    )
    with_na = _submitted(task, other, participant)
    ResponseFactory(
        assignment=with_na,
        block_id="ratings",
        answers={"criteria": {"functionality": 5, "usability": 5, "design": "na", "overall": 5}},
    )
    flags = quality.assignment_flags(task)
    assert flags[str(flat.pk)] == ["straightlining"]
    assert flags[str(with_na.pk)] == []  # only three numeric answers


def test_attention_failed_on_pre_and_post_questions():
    task = make_open_task(n_apps=1, design=_design(post_questions=[copy.deepcopy(ATTENTION)]))
    failed = ParticipantFactory(task=task, number=1, post_answers={"attention_1": "disagree"})
    passed = ParticipantFactory(task=task, number=2, post_answers={"attention_1": "agree"})
    unanswered = ParticipantFactory(task=task, number=3, post_answers={})
    report = quality.quality_report(task)
    assert report.participants[str(failed.pk)] == ["attention_failed"]
    assert report.participants[str(passed.pk)] == []
    assert report.participants[str(unanswered.pk)] == []
    assert report.participant_attention_failures == {str(failed.pk): 1}


def test_attention_failed_inside_a_custom_questionnaire_block():
    design = _design()
    design["blocks"].append(
        {
            "id": "survey",
            "type": "questionnaire",
            "title": "Survey",
            "instrument": "custom",
            "questions": [copy.deepcopy(ATTENTION)],
        },
    )
    task = make_open_task(n_apps=1, design=design)
    participant = ParticipantFactory(task=task, number=1)
    row = _submitted(task, _items(task)[0], participant)
    ResponseFactory(assignment=row, block_id="survey", answers={"answers": {"attention_1": "disagree"}})
    report = quality.quality_report(task)
    assert report.assignments[str(row.pk)] == ["attention_failed"]
    assert report.assignment_attention_failures == {str(row.pk): 1}
    # the participant inherits the flags of their assignments
    assert report.participants[str(participant.pk)] == ["attention_failed"]
    assert quality.rule_matches(report, {"attention_failed": True}, row) == ["attention_failed"]
    assert quality.rule_matches(report, {"attention_failed": False}, row) == []


def test_app_not_viewed_per_slot_in_pairwise():
    task = make_open_task(n_apps=2, mode="pairwise")
    item = _items(task)[0]
    participant = ParticipantFactory(task=task, number=1)
    only_a = _submitted(task, item, participant, views={"A": {"viewed_seconds": 90}, "B": {"viewed_seconds": 0}})
    other = ParticipantFactory(task=task, number=2)
    running_b = _submitted(
        task,
        item,
        other,
        views={"A": {"viewed_seconds": 30}, "B": {"viewed_seconds": 0, "first_running_at": "2026-11-01T10:00:00Z"}},
    )
    flags = quality.assignment_flags(task)
    assert flags[str(only_a.pk)] == ["app_not_viewed"]
    assert flags[str(running_b.pk)] == []


def test_possible_duplicate_by_device_hash_ignores_preview_and_blank(task):
    first = ParticipantFactory(task=task, number=1, device_hash="d" * 64)
    second = ParticipantFactory(task=task, number=2, device_hash="d" * 64)
    preview = ParticipantFactory(task=task, number=3, device_hash="e" * 64, kind=Participant.Kind.PREVIEW)
    lone = ParticipantFactory(task=task, number=4, device_hash="e" * 64)
    blank_a = ParticipantFactory(task=task, number=5, device_hash="")
    blank_b = ParticipantFactory(task=task, number=6, device_hash="")
    flags = quality.participant_flags(task)
    assert flags[str(first.pk)] == ["possible_duplicate"]
    assert flags[str(second.pk)] == ["possible_duplicate"]
    assert flags[str(preview.pk)] == []
    assert flags[str(lone.pk)] == []
    assert flags[str(blank_a.pk)] == flags[str(blank_b.pk)] == []


def test_flags_cover_every_row_and_only_submitted_work_is_flagged(task):
    item, other = _items(task)
    participant = ParticipantFactory(task=task, number=1)
    open_row = AssignmentFactory(task=task, item=item, participant=participant, status=S.IN_PROGRESS)
    skipped = AssignmentFactory(task=task, item=other, participant=participant, status=S.SKIPPED)
    flags = quality.assignment_flags(task)
    assert flags == {str(open_row.pk): [], str(skipped.pk): []}
    assert quality.participant_flags(task) == {str(participant.pk): []}


def test_participant_flags_union_in_display_order(task):
    item, other = _items(task)
    participant = ParticipantFactory(task=task, number=1, device_hash="f" * 64)
    ParticipantFactory(task=task, number=2, device_hash="f" * 64)
    _submitted(task, item, participant, views={})
    _submitted(task, other, participant, active=1, wall=1)
    assert quality.participant_flags(task)[str(participant.pk)] == [
        "speeding",
        "app_not_viewed",
        "possible_duplicate",
    ]


def test_rule_matches_follows_enabled_rules_only(task):
    item, _ = _items(task)
    row = _submitted(task, item, ParticipantFactory(task=task, number=1), active=1, wall=1, views={})
    report = quality.quality_report(task)
    assert quality.rule_matches(report, {"speeding": True, "app_not_viewed": True}, row) == [
        "speeding",
        "app_not_viewed",
    ]
    assert quality.rule_matches(report, {"straightlining": True}, row) == []
    assert quality.rule_matches(report, {}, row) == []


def test_item_seconds_prefers_active_time(task):
    item, _ = _items(task)
    row = _submitted(task, item, ParticipantFactory(task=task, number=1), active=0, wall=300)
    assert quality.item_seconds(row) == 300
    row.active_seconds = 200
    assert quality.item_seconds(row) == 200


def test_rule_excluded_assignments_lists_what_the_plan_removes():
    design = _design()
    design["analysis"]["exclusion_rules"] = {"speeding": True}
    task = make_open_task(n_apps=2, design=design, settings={"quality": {"min_seconds_per_item": 120}})
    item, other = _items(task)
    speeder = ParticipantFactory(task=task, number=1)
    fast = _submitted(task, item, speeder, active=5, wall=5)
    _submitted(task, other, speeder)
    excluded = ParticipantFactory(task=task, number=2, excluded=True)
    _submitted(task, item, excluded, active=5, wall=5)  # already excluded by hand: not a rule exclusion
    preview = ParticipantFactory(task=task, number=3, kind=Participant.Kind.PREVIEW)
    _submitted(task, item, preview, active=5, wall=5)
    assert quality.rule_excluded_assignments(task) == {str(fast.pk): ["speeding"]}
    task.design["analysis"]["exclusion_rules"] = {}
    task.save(update_fields=["design"])
    assert quality.rule_excluded_assignments(task) == {}
