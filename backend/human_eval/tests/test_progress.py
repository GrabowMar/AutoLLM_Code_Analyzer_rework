"""services/progress.py task progress (TS TaskProgress) and the ``tasks/{id}/progress/`` endpoint."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import Participant
from backend.human_eval.services import progress
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = Assignment.Status
DAY1 = dt.datetime(2026, 10, 30, 23, 30, tzinfo=dt.UTC)
DAY3 = dt.datetime(2026, 11, 1, 0, 15, tzinfo=dt.UTC)


def _assign(task, item, participant, status, **extra):
    return AssignmentFactory(task=task, item=item, participant=participant, status=status, **extra)


@pytest.fixture
def study():
    """Open task, 4 items (one inactive), 3 participants + a preview with every assignment status."""
    task = make_open_task(n_apps=4)
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    EvalItem.objects.filter(pk=items[3].pk).update(is_active=False)
    p1 = ParticipantFactory(task=task, number=1, status=Participant.Status.ACTIVE)
    p2 = ParticipantFactory(task=task, number=2, status=Participant.Status.COMPLETED, excluded=True)
    ParticipantFactory(task=task, number=3, status=Participant.Status.WITHDRAWN, kind=Participant.Kind.CODE)
    preview = ParticipantFactory(task=task, number=4, kind=Participant.Kind.PREVIEW)
    started = DAY1 - dt.timedelta(minutes=30)
    # item 0: 2 submissions (one excluded: still counted, progress is descriptive)
    _assign(task, items[0], p1, S.SUBMITTED, submitted_at=DAY1, started_at=started, active_seconds=600)
    _assign(task, items[0], p2, S.SUBMITTED, submitted_at=DAY3, started_at=started, active_seconds=0, excluded=True)
    # item 1: 1 submission (no heartbeat time: submitted - started = 20 min)
    _assign(
        task,
        items[1],
        p1,
        S.SUBMITTED,
        submitted_at=DAY3,
        started_at=DAY3 - dt.timedelta(minutes=20),
        active_seconds=0,
    )
    _assign(task, items[1], p2, S.IN_PROGRESS)
    _assign(task, items[2], p1, S.ASSIGNED)
    _assign(task, items[2], p2, S.SKIPPED, skip_reason="app_broken")
    _assign(task, items[3], p1, S.EXPIRED)
    # preview work never counts
    _assign(task, items[2], preview, S.SUBMITTED, submitted_at=DAY1, active_seconds=5)
    return task, items


def test_task_progress_counts_and_histograms(study):
    task, _ = study
    result = progress.task_progress(task)
    assert result["counts"]["assignments_submitted"] == 3
    assert result["counts"]["participants"] == 3
    assert result["assignments_by_status"] == {
        "assigned": 1,
        "in_progress": 1,
        "submitted": 3,
        "skipped": 1,
        "expired": 1,
    }
    assert result["participants_by_status"] == {"invited": 0, "active": 1, "completed": 1, "withdrawn": 1}


def test_coverage_histogram_is_dense_over_active_items(study):
    task, _ = study
    # active items: item0 = 2, item1 = 1, item2 = 0 (the preview submission does not count); item3 inactive
    assert progress.coverage_histogram(task) == [
        {"ratings": 0, "items": 1},
        {"ratings": 1, "items": 1},
        {"ratings": 2, "items": 1},
    ]


def test_timeline_groups_by_utc_day_and_fills_gaps(study):
    task, _ = study
    assert progress.timeline(task) == [
        {"date": "2026-10-30", "submitted": 1},
        {"date": "2026-10-31", "submitted": 0},
        {"date": "2026-11-01", "submitted": 2},
    ]


def test_median_minutes_uses_active_seconds_then_elapsed_time(study):
    task, _ = study
    # 600 s (heartbeats) = 10 min, 20 min elapsed, DAY3 - started (about 25 h elapsed) → median 20 min
    assert progress.median_minutes_per_item(task) == 20.0


def test_assignment_seconds_rules():
    now = dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
    assert progress.assignment_seconds(90, now, now) == 90.0
    assert progress.assignment_seconds(0, now - dt.timedelta(seconds=30), now) == 30.0
    assert progress.assignment_seconds(0, None, now) is None
    assert progress.assignment_seconds(0, now, now - dt.timedelta(seconds=1)) is None


def test_draft_task_progress_is_empty():
    task = EvalTaskFactory()
    result = progress.task_progress(task)
    assert result["coverage_histogram"] == []
    assert result["timeline"] == []
    assert result["median_minutes_per_item"] is None
    assert result["counts"]["target_ratings"] is None
    assert set(result["assignments_by_status"]) == set(S.values)
    assert set(result["participants_by_status"]) == set(Participant.Status.values)


def test_sparse_timeline_when_span_is_long():
    task = make_open_task(n_apps=1)
    item = EvalItem.objects.get(task=task)
    first = DAY1 - dt.timedelta(days=progress.MAX_DENSE_TIMELINE_DAYS + 5)
    for number, when in enumerate((first, DAY1), start=1):
        participant = ParticipantFactory(task=task, number=number)
        _assign(task, item, participant, S.SUBMITTED, submitted_at=when)
    assert progress.timeline(task) == [
        {"date": first.date().isoformat(), "submitted": 1},
        {"date": "2026-10-30", "submitted": 1},
    ]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _url(task) -> str:
    return f"/api/human-eval/tasks/{task.pk}/progress/"


def test_progress_endpoint_owner(client, study):
    task, _ = study
    client.force_login(task.created_by)
    response = client.get(_url(task))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "counts",
        "assignments_by_status",
        "participants_by_status",
        "coverage_histogram",
        "timeline",
        "median_minutes_per_item",
    }
    assert body["counts"]["assignments_submitted"] == 3
    assert body["timeline"][0] == {"date": "2026-10-30", "submitted": 1}
    assert body["median_minutes_per_item"] == 20.0


def test_progress_endpoint_scoping(client, study):
    task, _ = study
    assert client.get(_url(task)).status_code == 401
    client.force_login(UserFactory())
    response = client.get(_url(task))
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert client.get("/api/human-eval/tasks/not-a-uuid/progress/").status_code == 404
    client.force_login(UserFactory(is_staff=True))
    assert client.get(_url(task)).status_code == 200
