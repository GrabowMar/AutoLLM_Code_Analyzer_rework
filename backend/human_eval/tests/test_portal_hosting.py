"""Portal hosting endpoints (api/views/portal_hosting.py): heartbeat accounting, ensure, console.

Requests go through the real portal router and ``PortalAuth`` (participant cookie or
session user). Container work is recorded, never executed.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Participant
from backend.human_eval.services import access
from backend.human_eval.services import console
from backend.human_eval.services import context
from backend.human_eval.services import hosting
from backend.human_eval.services.presets import PRESETS
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import HostingContainerFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task
from backend.runtime.services import container_service

pytestmark = pytest.mark.django_db

PORTAL = "/api/human-eval/portal"
#: The real lazy reaper (the autouse fixture replaces it; ordering tests put it back).
REAL_LAZY_REAP = hosting.lazy_reap


@pytest.fixture(autouse=True)
def _environment(settings, monkeypatch):
    settings.APPS_SUBDOMAIN_PROXY = True
    settings.APPS_DOMAIN = "apps.test"
    settings.TRAEFIK_DYNAMIC_DIR = ""
    settings.APPS_PROXY_PATH = False
    settings.DOCKER_APPS_NETWORK = ""
    settings.APPS_UPSTREAM_HOST = "apps-dind"
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 4
    settings.HUMAN_EVAL_MAX_CONCURRENT_BUILDS = 1
    cache.clear()
    monkeypatch.setattr(hosting, "lazy_reap", lambda: None)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    """The hosting service's clock, frozen so heartbeat gaps are exact however slow the request is."""
    now = timezone.now()
    monkeypatch.setattr(hosting, "timezone", SimpleNamespace(now=lambda: now, is_aware=timezone.is_aware))
    return now


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    calls = SimpleNamespace(build=[], start=[], stop=[], provenance=[])

    def build(job, user):
        container = HostingContainerFactory(generation_job=job, status="pending")
        calls.build.append(job)
        return container

    monkeypatch.setattr(container_service, "build_for_job", build)
    monkeypatch.setattr(container_service, "start_instance", lambda c, u: calls.start.append(c.pk))
    monkeypatch.setattr(container_service, "stop_instance", lambda c, u: calls.stop.append(c.pk))
    monkeypatch.setattr(context, "record_hosting_provenance", lambda *args: calls.provenance.append(args))
    return calls


def _setup(*, mode="single", settings=None, design=None, status="in_progress", started_minutes=10, **fields):
    task = make_open_task(n_apps=2, mode=mode, settings=settings, design=design)
    participant = ParticipantFactory(task=task)
    now = timezone.now()
    fields.setdefault("started_at", now - timedelta(minutes=started_minutes))
    fields.setdefault("last_activity_at", now - timedelta(minutes=1))
    assignment = AssignmentFactory(
        participant=participant,
        task=task,
        item=task.items.order_by("position").first(),
        status=status,
        **fields,
    )
    return task, participant, assignment


def _client(participant: Participant) -> Client:
    client = Client()
    client.cookies[access.cookie_name()] = access.issue_session(participant)
    return client


def _beat(client, assignment, **payload):
    body = {"active_seconds": 30, "visible": True, "viewing": [], **payload}
    return client.post(f"{PORTAL}/assignments/{assignment.id}/heartbeat/", body, content_type="application/json")


def _set_views(assignment, **views):
    now = hosting.timezone.now()
    data = {key: (now - timedelta(seconds=value)).isoformat() for key, value in views.items()}
    Assignment.objects.filter(pk=assignment.pk).update(app_views=data)


def _running(app: EvalTaskApp):
    container = HostingContainerFactory(generation_job=app.job, status="running")
    EvalTaskApp.objects.filter(pk=app.pk).update(
        container=container,
        started_by_task=True,
        last_activity_at=timezone.now() - timedelta(minutes=30),
    )
    return container


# ---------------------------------------------------------------------------
# Heartbeat accounting
# ---------------------------------------------------------------------------


def test_visible_beat_adds_active_and_wall_time():
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=30)
    response = _beat(_client(participant), assignment, active_seconds=30)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["active_seconds"] == 30
    assignment.refresh_from_db()
    assert assignment.active_seconds == 30
    assert assignment.wall_seconds == 30
    assert assignment.last_activity_at > timezone.now() - timedelta(seconds=5)
    assert "_last_visible_at" in assignment.app_views


def test_increments_are_capped_at_60_seconds():
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=120)
    _beat(_client(participant), assignment, active_seconds=500)
    assignment.refresh_from_db()
    assert assignment.active_seconds == 60
    assert assignment.wall_seconds == 60


def test_active_time_never_exceeds_the_elapsed_time():
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=5)
    _beat(_client(participant), assignment, active_seconds=60)
    assignment.refresh_from_db()
    assert assignment.active_seconds == 5
    assert assignment.wall_seconds == 5


@pytest.mark.parametrize("value", [-30, "nan", None])
def test_bad_active_seconds_add_nothing(value):
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=30)
    response = _beat(_client(participant), assignment, active_seconds=value)
    if response.status_code == 200:
        assignment.refresh_from_db()
        assert assignment.active_seconds == 0
    else:
        assert response.status_code == 422


def test_hidden_beat_adds_wall_time_and_liveness_but_no_active_time():
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=30, _last_visible_at=60)
    _beat(_client(participant), assignment, visible=False, active_seconds=30)
    assignment.refresh_from_db()
    assert assignment.active_seconds == 0
    assert assignment.wall_seconds == 30
    assert assignment.last_activity_at > timezone.now() - timedelta(seconds=5)


def test_gaps_over_five_minutes_add_no_wall_time():
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=6 * 60)
    _beat(_client(participant), assignment, active_seconds=30)
    assignment.refresh_from_db()
    assert assignment.wall_seconds == 0
    assert assignment.active_seconds == 30
    assert assignment.last_activity_at > timezone.now() - timedelta(seconds=5)


def test_hidden_liveness_is_honoured_only_within_the_assignment_timeout():
    _, participant, assignment = _setup(settings={"allocation": {"assignment_timeout_minutes": 5}})
    client = _client(participant)
    stale = timezone.now() - timedelta(minutes=10)
    Assignment.objects.filter(pk=assignment.pk).update(last_activity_at=stale)
    _set_views(assignment, _last_beat_at=30, _last_visible_at=10 * 60)
    _beat(client, assignment, visible=False, active_seconds=0)
    assignment.refresh_from_db()
    assert assignment.last_activity_at == stale  # the hidden tab no longer keeps it alive
    assert assignment.wall_seconds == 0
    _set_views(assignment, _last_beat_at=30, _last_visible_at=2 * 60)
    _beat(client, assignment, visible=False, active_seconds=0)
    assignment.refresh_from_db()
    assert assignment.last_activity_at > stale
    assert assignment.wall_seconds == 30


def test_viewed_slots_count_view_time_touch_the_app_and_record_provenance(runtime):
    _, participant, assignment = _setup()
    app = assignment.item.app_a
    container = _running(app)
    _set_views(assignment, _last_beat_at=30)
    response = _beat(_client(participant), assignment, viewing=["single", "A"])
    assert response.status_code == 200
    assert response.json()["apps"] == [
        {
            "slot": "single",
            "hosting": {
                "state": "running",
                "url": f"https://{container.name}.apps.test",
                "progress": None,
                "message": "",
                "error": "",
            },
        },
    ]
    assignment.refresh_from_db()
    view = assignment.app_views["single"]
    assert view["viewed_seconds"] == 30
    assert view["first_running_at"]
    assert "A" not in assignment.app_views
    app.refresh_from_db()
    assert app.last_activity_at > timezone.now() - timedelta(seconds=5)
    assert [(args[0].pk, args[1], args[2].pk) for args in runtime.provenance] == [(assignment.pk, "single", app.pk)]
    container.refresh_from_db()
    assert container.metadata["human_eval"]["users"] == [str(assignment.pk)]


def test_viewing_an_app_that_is_not_running_sets_no_first_running_at(runtime):
    _, participant, assignment = _setup()
    _set_views(assignment, _last_beat_at=30)
    response = _beat(_client(participant), assignment, viewing=["single"])
    assert response.json()["apps"][0]["hosting"]["state"] == "idle"
    assignment.refresh_from_db()
    assert assignment.app_views["single"] == {"viewed_seconds": 30, "first_running_at": None}
    assert runtime.provenance == []


def test_first_beat_starts_an_assigned_assignment(monkeypatch):
    _, participant, assignment = _setup(status="assigned", started_at=None)
    built = {"blocks": {}, "hosting": {}, "instructions_sha256": "abc"}
    monkeypatch.setattr(context, "build_context", lambda a: built)
    Assignment.objects.filter(pk=assignment.pk).update(started_at=None, context={})
    response = _beat(_client(participant), assignment)
    assert response.json()["status"] == "in_progress"
    assignment.refresh_from_db()
    assert assignment.status == "in_progress"
    assert assignment.started_at is not None
    assert assignment.context == built


def test_honoured_beats_keep_every_app_of_the_assignment_alive(runtime):
    _, participant, assignment = _setup(mode="pairwise")
    item = assignment.item
    _running(item.app_a)
    _running(item.app_b)
    _set_views(assignment, _last_beat_at=30)
    # Desktop default: only the app in focus is framed and reported as viewed.
    assert _beat(_client(participant), assignment, viewing=["A"]).status_code == 200
    recent = timezone.now() - timedelta(seconds=30)
    for app in (item.app_a, item.app_b):
        app.refresh_from_db()
        assert app.last_activity_at > recent  # the app not in focus is not evicted or reaped either
    assignment.refresh_from_db()
    assert assignment.app_views["A"]["viewed_seconds"] == 30  # view time stays per viewed slot
    assert "B" not in assignment.app_views


def test_unhonoured_beats_keep_no_app_alive(runtime):
    _, participant, assignment = _setup(settings={"allocation": {"assignment_timeout_minutes": 5}})
    app = assignment.item.app_a
    _running(app)
    stale = EvalTaskApp.objects.get(pk=app.pk).last_activity_at
    _set_views(assignment, _last_beat_at=30, _last_visible_at=10 * 60)
    _beat(_client(participant), assignment, visible=False, viewing=["single"])
    app.refresh_from_db()
    assert app.last_activity_at == stale


def test_heartbeat_reaper_never_stops_an_app_the_beat_keeps_alive(
    monkeypatch,
    runtime,
    django_capture_on_commit_callbacks,
):
    monkeypatch.setattr(hosting, "lazy_reap", REAL_LAZY_REAP)
    cache.clear()
    _, participant, assignment = _setup(settings={"hosting": {"idle_stop_minutes": 20}})
    container = _running(assignment.item.app_a)  # task-started, unpinned, idle for 30 minutes
    with django_capture_on_commit_callbacks(execute=True):
        body = _beat(_client(participant), assignment, viewing=[]).json()
    assert body["apps"][0]["hosting"]["state"] == "running"
    assert runtime.stop == []
    container.refresh_from_db()
    assert container.status == "running"


def test_pairwise_beat_reports_both_slots():
    _, participant, assignment = _setup(mode="pairwise", swapped=True)
    item = assignment.item
    _running(item.app_b)
    body = _beat(_client(participant), assignment).json()
    assert [(row["slot"], row["hosting"]["state"]) for row in body["apps"]] == [("A", "running"), ("B", "idle")]


def test_read_only_assignment_only_gets_status_back():
    _, participant, assignment = _setup(status="submitted")
    _set_views(assignment, _last_beat_at=30)
    response = _beat(_client(participant), assignment, active_seconds=30)
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    assignment.refresh_from_db()
    assert assignment.active_seconds == 0
    assert assignment.wall_seconds == 0


def test_heartbeat_refreshes_the_participant_last_seen():
    _, participant, assignment = _setup()
    Participant.objects.filter(pk=participant.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
    client = _client(participant)
    Participant.objects.filter(pk=participant.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
    _beat(client, assignment)
    participant.refresh_from_db()
    assert participant.last_seen_at > timezone.now() - timedelta(seconds=5)


def test_heartbeat_of_another_participant_is_404():
    _, _, assignment = _setup()
    _, stranger, _ = _setup()
    assert _beat(_client(stranger), assignment).status_code == 404


def test_heartbeat_needs_a_portal_identity():
    _, _, assignment = _setup()
    assert _beat(Client(), assignment).status_code == 401


def test_heartbeat_requires_csrf():
    _, participant, assignment = _setup()
    client = Client(enforce_csrf_checks=True)
    client.cookies[access.cookie_name()] = access.issue_session(participant)
    assert _beat(client, assignment).status_code == 403
    token = "a" * 32
    client.cookies[django_settings.CSRF_COOKIE_NAME] = token
    response = client.post(
        f"{PORTAL}/assignments/{assignment.id}/heartbeat/",
        {"active_seconds": 0, "visible": True, "viewing": []},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Ensure
# ---------------------------------------------------------------------------


def test_ensure_starts_the_app_for_the_participant(runtime, django_capture_on_commit_callbacks):
    _, participant, assignment = _setup()
    with django_capture_on_commit_callbacks(execute=True):
        response = _client(participant).post(f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"state", "url", "progress", "message", "error"}  # no container ids for participants
    assert body["state"] == "starting"
    assert runtime.build == [assignment.item.app_a.job]
    assignment.refresh_from_db()
    assert assignment.last_activity_at > timezone.now() - timedelta(seconds=5)


def test_ensure_from_an_abandoned_hidden_tab_does_not_keep_the_assignment_alive(runtime):
    _, participant, assignment = _setup(settings={"allocation": {"assignment_timeout_minutes": 5}})
    client = _client(participant)
    url = f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/"
    stale = timezone.now() - timedelta(minutes=10)
    Assignment.objects.filter(pk=assignment.pk).update(last_activity_at=stale)
    _set_views(assignment, _last_visible_at=10 * 60)  # hidden for longer than the assignment timeout
    assert client.post(url).status_code == 200
    assignment.refresh_from_db()
    assert assignment.last_activity_at == stale  # the workbench's ensure poll is no liveness: it can expire
    _set_views(assignment, _last_visible_at=60)  # within the window heartbeats honour
    client.post(url)
    assignment.refresh_from_db()
    assert assignment.last_activity_at > stale


def test_ensure_running_app_returns_url_and_records_provenance(runtime):
    _, participant, assignment = _setup()
    container = _running(assignment.item.app_a)
    response = _client(participant).post(f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/")
    assert response.json()["url"] == f"https://{container.name}.apps.test"
    assert [(args[0].pk, args[1]) for args in runtime.provenance] == [(assignment.pk, "single")]


def test_ensure_on_a_read_only_assignment_is_409(runtime):
    _, participant, assignment = _setup(status="expired")
    response = _client(participant).post(f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/")
    assert response.status_code == 409
    assert response.json()["code"] == "read_only"
    assert runtime.build == []


def test_ensure_unknown_slot_is_404():
    _, participant, assignment = _setup()
    response = _client(participant).post(f"{PORTAL}/assignments/{assignment.id}/apps/B/ensure/")
    assert response.status_code == 404


def test_ensure_of_another_participants_assignment_is_404(runtime):
    _, _, assignment = _setup()
    _, stranger, _ = _setup()
    response = _client(stranger).post(f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/")
    assert response.status_code == 404
    assert runtime.build == []


def test_ensure_with_on_demand_off_asks_the_researcher(runtime):
    _, participant, assignment = _setup(settings={"hosting": {"on_demand": False}})
    response = _client(participant).post(f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/")
    assert response.json()["state"] == "idle"
    assert response.json()["message"] == hosting.MSG_NOT_STARTED
    assert runtime.build == []


def test_researcher_preview_session_can_ensure(runtime, client, django_capture_on_commit_callbacks):
    task, _, _ = _setup()
    preview = ParticipantFactory(task=task, kind="preview", user=task.created_by)
    assignment = AssignmentFactory(participant=preview, task=task, item=task.items.order_by("position").first())
    client.force_login(task.created_by)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(f"{PORTAL}/assignments/{assignment.id}/apps/single/ensure/")
    assert response.status_code == 200
    assert response.json()["state"] == "starting"


# ---------------------------------------------------------------------------
# Console endpoint
# ---------------------------------------------------------------------------


def _security_setup():
    preset = next(p for p in PRESETS if p["slug"] == "security-assessment")
    task, participant, assignment = _setup(design=preset["design"], settings=preset["settings"])
    container = _running(assignment.item.app_a)
    EvalTaskApp.objects.filter(pk=assignment.item.app_a_id).update(last_activity_at=timezone.now())
    container.app_port = 9011
    container.save(update_fields=["app_port"])
    return task, participant, assignment


class _Response:
    status_code = 200
    reason = "OK"
    headers = {"Content-Type": "text/plain"}

    def iter_content(self, chunk_size=1):
        yield b"pong"

    def close(self):
        pass


def test_console_endpoint_round_trip(monkeypatch):
    _, participant, assignment = _security_setup()
    sent = []

    class Session:
        trust_env = True

        def mount(self, prefix, adapter):
            pass

        def request(self, method, url, **kwargs):
            sent.append((method, url, self.trust_env))
            return _Response()

        def close(self):
            pass

    monkeypatch.setattr(console.requests, "Session", Session)
    client = _client(participant)
    url = f"{PORTAL}/assignments/{assignment.id}/console/"
    payload = {"slot": "single", "method": "GET", "path": "/ping", "headers": {}, "body": ""}
    response = client.post(url, payload, content_type="application/json")
    assert response.status_code == 200
    body = response.json()
    assert body["status_code"] == 200
    assert body["body"] == "pong"
    assert body["id"] == str(HttpExchange.objects.get().id)
    assert sent == [("GET", "http://apps-dind:9011/ping", False)]
    response = client.post(url, {**payload, "path": "ping"}, content_type="application/json")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid"
    assert response.json()["errors"][0]["path"] == "path"
    _, stranger, _ = _security_setup()
    assert _client(stranger).post(url, payload, content_type="application/json").status_code == 404
    assert HttpExchange.objects.count() == 1
