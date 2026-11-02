"""Hosting service (services/hosting.py) and the researcher hosting endpoints (api/views/hosting.py).

Docker is never touched: ``container_service`` entry points are replaced by recorders,
the reset worker's ``docker_manager`` calls and ``_verify_running`` are mocked, and
``dispatch_in_thread`` is captured. on_commit callbacks run through
``django_capture_on_commit_callbacks(execute=True)``.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import hosting
from backend.human_eval.services import pool as pool_service
from backend.human_eval.services import tasks as tasks_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import HostingContainerFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_draft_task
from backend.human_eval.tests.factories import make_open_task
from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.services import container_service
from backend.runtime.services import docker_manager
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/human-eval"
#: The real lazy reaper (the autouse fixture replaces it; ordering tests put it back).
REAL_LAZY_REAP = hosting.lazy_reap


@pytest.fixture(autouse=True)
def _environment(settings, monkeypatch):
    settings.APPS_SUBDOMAIN_PROXY = True
    settings.APPS_DOMAIN = "apps.test"
    settings.TRAEFIK_DYNAMIC_DIR = ""
    settings.APPS_PROXY_PATH = False
    settings.DOCKER_APPS_NETWORK = ""
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 4
    settings.HUMAN_EVAL_MAX_CONCURRENT_BUILDS = 1
    settings.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8"]
    cache.clear()
    # The lazy reaper is tested on its own; elsewhere it must not change what a test set up.
    monkeypatch.setattr(hosting, "lazy_reap", lambda: None)


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    """Recorders for every container_service entry point (no Docker, no threads)."""
    calls = SimpleNamespace(build=[], start=[], stop=[], remove=[], threads=[])

    def build(job, user):
        container = HostingContainerFactory(generation_job=job, status="pending", created_by=user)
        calls.build.append(SimpleNamespace(job=job, user=user, container=container))
        return container

    monkeypatch.setattr(container_service, "build_for_job", build)
    monkeypatch.setattr(container_service, "start_instance", lambda c, u: calls.start.append((c.pk, u)))
    monkeypatch.setattr(container_service, "stop_instance", lambda c, u: calls.stop.append((c.pk, u)))
    monkeypatch.setattr(container_service, "remove_instance", lambda c, u: calls.remove.append((c.pk, u)))
    monkeypatch.setattr(container_service, "publish_container_status", lambda c: None)
    monkeypatch.setattr(
        hosting,
        "dispatch_in_thread",
        lambda target, *args, name=None, **kwargs: calls.threads.append((target, args)),
    )
    return calls


def _apps(task: EvalTask) -> list[EvalTaskApp]:
    return list(task.apps.select_related("task", "job", "container").order_by("position"))


def _container(app: EvalTaskApp, *, status="running", started=True, idle_minutes=0.0, pinned=False, **fields):
    container = fields.pop("container", None) or HostingContainerFactory(generation_job=app.job, status=status)
    app.container = container
    app.started_by_task = started
    app.pinned = pinned
    app.last_activity_at = timezone.now() - timedelta(minutes=idle_minutes)
    for key, value in fields.items():
        setattr(app, key, value)
    app.save()
    return container


def _reload(app: EvalTaskApp) -> EvalTaskApp:
    return EvalTaskApp.objects.select_related("task", "job", "container").get(pk=app.pk)


def _task(owner=None, n_apps=3, **hosting_settings):
    return make_open_task(
        owner=owner,
        n_apps=n_apps,
        settings={"hosting": hosting_settings} if hosting_settings else None,
    )


# ---------------------------------------------------------------------------
# Starting apps
# ---------------------------------------------------------------------------


def test_ensure_builds_on_commit_and_saves_the_container(runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=task.created_by)
    assert info["state"] == "starting"
    assert len(runtime.build) == 1
    assert runtime.build[0].job == app.job
    assert runtime.build[0].user == task.created_by
    app = _reload(app)
    assert app.container == runtime.build[0].container
    assert app.started_by_task
    assert app.start_dispatched_at is None
    assert hosting.researcher_hosting_info(app)["state"] == "starting"


def test_build_is_dispatched_only_after_commit(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        hosting.ensure_running(app, actor=app.task.created_by)
    assert runtime.build == []
    assert len(callbacks) == 1
    assert _reload(app).start_dispatched_at is not None


def test_ensure_starts_a_stopped_container(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    container = _container(app, status="stopped", started=False)
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=app.task.created_by)
    assert runtime.start == [(container.pk, app.task.created_by)]
    assert runtime.build == []
    app = _reload(app)
    assert app.started_by_task
    assert app.start_dispatched_at is None


def test_running_app_is_touched_and_returns_its_url(runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    container = _container(app, idle_minutes=30)
    other = _apps(_task())[0]
    _container(other, container=container, started=False, idle_minutes=30)
    participant = ParticipantFactory(task=task)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=participant)
    assert info == {
        "state": "running",
        "url": f"https://{container.name}.apps.test",
        "progress": None,
        "message": "",
        "error": "",
    }
    assert runtime.start == []
    assert runtime.build == []
    recent = timezone.now() - timedelta(seconds=30)
    assert _reload(app).last_activity_at > recent
    assert _reload(other).last_activity_at > recent  # every task app on the same container


def test_existing_running_container_of_the_job_is_adopted_not_task_started(django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    researcher_started = HostingContainerFactory(generation_job=app.job, status="running")
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=app.task.created_by)
    assert info["state"] == "running"
    app = _reload(app)
    assert app.container == researcher_started
    assert not app.started_by_task


def test_recent_marker_without_container_returns_starting_without_dispatching(
    runtime,
    django_capture_on_commit_callbacks,
):
    app = _apps(_task())[0]
    app.start_dispatched_at = timezone.now() - timedelta(minutes=2)
    app.started_by_task = True
    app.save()
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=app.task.created_by)
    assert info["state"] == "starting"
    assert runtime.build == []


def test_stale_marker_dispatches_again(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    app.start_dispatched_at = timezone.now() - timedelta(minutes=11)
    app.save()
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=app.task.created_by)
    assert len(runtime.build) == 1


def test_build_failure_to_dispatch_sets_hosting_error_and_clears_marker(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    app = _apps(_task())[0]

    def broken(job, user):
        msg = "Docker daemon unavailable"
        raise ConnectionError(msg)

    monkeypatch.setattr(container_service, "build_for_job", broken)
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=app.task.created_by)
    app = _reload(app)
    assert app.start_dispatched_at is None
    assert "Docker daemon unavailable" in app.hosting_error
    assert hosting.researcher_hosting_info(app)["state"] == "failed"
    participant_info = hosting.participant_hosting_info(app)
    assert participant_info["state"] == "failed"
    assert participant_info["error"] == hosting.PARTICIPANT_ERROR_PLATFORM


# ---------------------------------------------------------------------------
# Capacity, queue, eviction
# ---------------------------------------------------------------------------


def test_global_cap_counts_distinct_containers_and_in_flight_markers(settings, runtime):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 3
    task = _task(n_apps=4, max_running_apps=10)
    first, marked, stopped_starting, newcomer = _apps(task)
    shared = _container(first)
    other = _apps(_task(n_apps=1, max_running_apps=10))[0]
    _container(other, container=shared)  # same container, another task: one slot
    marked.start_dispatched_at = timezone.now()
    marked.started_by_task = True
    marked.save()
    starting = _container(stopped_starting, status="stopped")
    ContainerAction.objects.create(
        action_id="act_start000001",
        container=starting,
        action_type=ContainerAction.ActionType.START,
        status=ContainerAction.Status.RUNNING,
    )
    assert hosting.global_running_count() == 3
    info = hosting.ensure_running(newcomer, actor=task.created_by)
    assert info["state"] == "queued"
    assert "3 of 3 in use" in info["message"]
    assert "number 1 in the queue" in info["message"]
    assert _reload(newcomer).hosting_requested_at is not None
    assert runtime.build == []


def test_task_cap_queues_beyond_max_running_apps(runtime):
    task = _task(max_running_apps=1)
    first, second, _ = _apps(task)
    _container(first)
    info = hosting.ensure_running(second, actor=task.created_by)
    assert info["state"] == "queued"
    assert runtime.build == []
    assert runtime.start == []


def test_build_cap_queues_builds_but_not_starts(runtime, django_capture_on_commit_callbacks):
    task = _task(max_running_apps=10)
    building, needs_build, stopped = _apps(task)
    _container(building, status="building")
    container = _container(stopped, status="stopped", started=False)
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.ensure_running(needs_build, actor=task.created_by)["state"] == "queued"
        assert hosting.ensure_running(stopped, actor=task.created_by)["state"] == "starting"
    assert runtime.build == []
    assert runtime.start == [(container.pk, task.created_by)]


def test_stuck_build_does_not_hold_a_slot(settings, runtime, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 1
    task = _task(max_running_apps=10)
    stuck_app, newcomer, _ = _apps(task)
    stuck = _container(stuck_app, status="building")
    ContainerInstance.objects.filter(pk=stuck.pk).update(created_at=timezone.now() - timedelta(hours=2))
    assert hosting.global_running_count() == 0
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(newcomer, actor=task.created_by)
    assert len(runtime.build) == 1
    info = hosting.researcher_hosting_info(_reload(stuck_app))
    assert info["state"] == "failed"
    assert info["error"] == hosting.MSG_STUCK


def test_start_all_pins_and_queues_beyond_capacity(settings, runtime, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_CONCURRENT_BUILDS = 5
    task = _task(max_running_apps=2)
    with django_capture_on_commit_callbacks(execute=True):
        result = hosting.task_hosting_action(task, "start_all", user=task.created_by)
    assert [row["state"] for row in result["apps"]] == ["starting", "starting", "queued"]
    assert result["queued"] == 1
    assert result["task_max_running"] == 2
    assert len(runtime.build) == 2  # start_all counted its own earlier dispatches
    apps = _apps(task)
    assert all(app.pinned for app in apps)
    assert apps[2].hosting_requested_at is not None


def test_advance_queue_is_fifo_and_stops_at_the_global_cap(settings, runtime, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 1
    settings.HUMAN_EVAL_MAX_CONCURRENT_BUILDS = 5
    task = _task(max_running_apps=10)
    blocker, early, late = _apps(task)
    blocker_container = _container(blocker)
    now = timezone.now()
    for app, minutes in ((early, 2), (late, 1)):
        app.hosting_requested_at = now - timedelta(minutes=minutes)
        app.last_activity_at = now
        app.save()
    assert hosting.advance_queue() == 0  # still full
    ContainerInstance.objects.filter(pk=blocker_container.pk).update(status="stopped")
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.advance_queue() == 1
    assert [call.job for call in runtime.build] == [early.job]
    assert _reload(early).hosting_requested_at is None
    assert _reload(late).hosting_requested_at is not None


def test_a_new_request_does_not_jump_the_queue(settings, runtime, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 1
    task = _task(max_running_apps=10)
    queued, newcomer, _ = _apps(task)
    queued.hosting_requested_at = timezone.now() - timedelta(minutes=1)
    queued.last_activity_at = timezone.now()
    queued.save()
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(newcomer, actor=task.created_by)
    assert info["state"] == "queued"
    assert [call.job for call in runtime.build] == [queued.job]


def test_eviction_stops_the_least_recently_active_idle_app(settings, runtime, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 2
    others = _apps(_task(n_apps=2, max_running_apps=10))
    oldest = _container(others[0], idle_minutes=20)
    _container(others[1], idle_minutes=10)
    task = _task(max_running_apps=10)
    newcomer = _apps(task)[0]
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(newcomer, actor=task.created_by)
    assert info["state"] == "starting"
    assert runtime.stop == [(oldest.pk, None)]
    assert len(runtime.build) == 1


def test_eviction_skips_pinned_recently_active_and_shared_apps(settings, runtime, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 3
    pinned_app, recent_app, shared_app = _apps(_task(n_apps=3, max_running_apps=10))
    _container(pinned_app, idle_minutes=30, pinned=True)
    _container(recent_app, idle_minutes=1)
    shared = _container(shared_app, idle_minutes=30)
    # Another open task uses the same container within its 20-minute idle window (R3.8).
    sharer = _apps(_task(n_apps=1, idle_stop_minutes=20))[0]
    _container(sharer, container=shared, started=False, idle_minutes=10)
    task = _task(max_running_apps=10)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(_apps(task)[0], actor=task.created_by)
    assert info["state"] == "queued"
    assert runtime.stop == []
    assert runtime.build == []


# ---------------------------------------------------------------------------
# Idle reaper
# ---------------------------------------------------------------------------


def test_reaper_stops_only_idle_unpinned_task_started_apps(runtime, django_capture_on_commit_callbacks):
    idle, recent, pinned, adopted = _apps(_task(n_apps=4, idle_stop_minutes=20))
    idle_container = _container(idle, idle_minutes=30)
    _container(recent, idle_minutes=5)
    _container(pinned, idle_minutes=30, pinned=True)
    _container(adopted, idle_minutes=30, started=False)  # researcher-started: never stopped
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 1
    assert runtime.stop == [(idle_container.pk, None)]


def test_reaper_uses_the_container_update_time_without_activity(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task(idle_stop_minutes=20))[0]
    container = _container(app)
    EvalTaskApp.objects.filter(pk=app.pk).update(last_activity_at=None)
    ContainerInstance.objects.filter(pk=container.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 1


def test_reaper_stops_apps_of_closed_tasks_even_when_pinned_or_active(runtime, django_capture_on_commit_callbacks):
    task = _task()
    task.status = EvalTask.Status.CLOSED
    task.save(update_fields=["status"])
    app = _apps(task)[0]
    container = _container(app, pinned=True)
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 1
    assert runtime.stop == [(container.pk, None)]


def test_reaper_leaves_containers_shared_with_an_active_open_task(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task(idle_stop_minutes=20))[0]
    container = _container(app, idle_minutes=30)
    sharer = _apps(_task(n_apps=1, idle_stop_minutes=20))[0]
    _container(sharer, container=container, started=False, idle_minutes=5)
    EvalTaskApp.objects.filter(pk=app.pk).update(last_activity_at=timezone.now() - timedelta(minutes=30))
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 0
    assert runtime.stop == []


def test_reaper_clears_stale_markers_and_abandoned_queue_requests(runtime, django_capture_on_commit_callbacks):
    task = _task(n_apps=3)
    marked, abandoned, pinned_waiting = _apps(task)
    long_ago = timezone.now() - timedelta(minutes=20)
    EvalTaskApp.objects.filter(pk=marked.pk).update(start_dispatched_at=timezone.now() - timedelta(minutes=15))
    EvalTaskApp.objects.filter(pk=abandoned.pk).update(hosting_requested_at=long_ago, last_activity_at=long_ago)
    EvalTaskApp.objects.filter(pk=pinned_waiting.pk).update(
        hosting_requested_at=long_ago,
        last_activity_at=long_ago,
        pinned=True,
    )
    closed_task = _task(n_apps=1)
    closed_task.status = EvalTask.Status.ARCHIVED
    closed_task.save(update_fields=["status"])
    closed_app = _apps(closed_task)[0]
    EvalTaskApp.objects.filter(pk=closed_app.pk).update(hosting_requested_at=timezone.now())
    with django_capture_on_commit_callbacks(execute=True):
        hosting.reap_idle_apps()
    assert _reload(marked).start_dispatched_at is None
    assert _reload(abandoned).hosting_requested_at is None
    assert _reload(closed_app).hosting_requested_at is None
    # The pinned request survived and was started by the queue advance.
    assert [call.job for call in runtime.build] == [pinned_waiting.job]
    assert _reload(pinned_waiting).hosting_requested_at is None


def test_a_stopped_run_is_no_longer_the_tasks_to_stop(runtime, django_capture_on_commit_callbacks):
    task = _task(idle_stop_minutes=20)
    app = _apps(task)[0]
    container = _container(app, idle_minutes=30)
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 1
    assert not _reload(app).started_by_task
    # Someone starts the container again outside human-eval (Runtime page, a DAST analysis run) ...
    ContainerInstance.objects.filter(pk=container.pk).update(status="running")
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 0  # ... the open task's stale idle time does not stop it
    task.status = EvalTask.Status.ARCHIVED
    task.save(update_fields=["status"])
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.reap_idle_apps() == 0  # nor does the task being archived
        assert hosting.stop_task_apps(task) == 0
    assert runtime.stop == [(container.pk, None)]


@pytest.mark.parametrize("path", ["eviction", "stop_app", "stop_all"])
def test_every_human_eval_stop_ends_the_ownership(settings, runtime, django_capture_on_commit_callbacks, path):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 1
    task = _task(max_running_apps=10)
    app, newcomer, _ = _apps(task)
    container = _container(app, idle_minutes=10)
    sharer = _apps(_task(n_apps=1))[0]
    _container(sharer, container=container, idle_minutes=30)  # another task started it too
    with django_capture_on_commit_callbacks(execute=True):
        if path == "eviction":
            hosting.ensure_running(newcomer, actor=task.created_by)
        elif path == "stop_app":
            hosting.stop_app(app)
        else:
            hosting.task_hosting_action(task, "stop_all", user=task.created_by)
    assert [pk for pk, _ in runtime.stop] == [container.pk]
    assert not _reload(app).started_by_task
    assert not _reload(sharer).started_by_task


def test_stop_paths_never_stop_another_running_container_of_the_job(runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    _container(app, status="stopped")  # the task's own (stale) run
    elsewhere = HostingContainerFactory(generation_job=app.job, status="running")  # started outside human-eval
    assert hosting.current_container(_reload(app)) == elsewhere
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.stop_task_apps(task) == 0
        hosting.stop_app(_reload(app))
        hosting.task_hosting_action(task, "stop_all", user=task.created_by)
    assert runtime.stop == []
    # The researcher's explicit stop still stops what the panel shows.
    with django_capture_on_commit_callbacks(execute=True):
        hosting.app_hosting_action(_reload(app), "stop", user=task.created_by)
    assert runtime.stop == [(elsewhere.pk, task.created_by)]


def test_reaper_drops_ownership_of_runs_that_ended_elsewhere(runtime, django_capture_on_commit_callbacks):
    stopped_elsewhere, starting, marked = _apps(_task())
    _container(stopped_elsewhere, status="stopped")
    start_pending = _container(starting, status="stopped")
    ContainerAction.objects.create(
        action_id="act_start000002",
        container=start_pending,
        action_type=ContainerAction.ActionType.START,
        status=ContainerAction.Status.PENDING,
    )
    _container(marked, status="stopped", start_dispatched_at=timezone.now())
    with django_capture_on_commit_callbacks(execute=True):
        hosting.reap_idle_apps()
    assert not _reload(stopped_elsewhere).started_by_task
    assert _reload(starting).started_by_task  # its start is in flight
    assert _reload(marked).started_by_task  # its start is being dispatched


def test_ensure_never_reports_running_an_app_the_lazy_reaper_then_stops(
    monkeypatch,
    runtime,
    django_capture_on_commit_callbacks,
):
    monkeypatch.setattr(hosting, "lazy_reap", REAL_LAZY_REAP)
    task = _task(idle_stop_minutes=20)
    app, other, _ = _apps(task)
    container = _container(app, idle_minutes=30)  # task-started, unpinned, idle past idle_stop_minutes
    other_container = _container(other, idle_minutes=30)
    assignment = _assignment(task, app)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_for_assignment(assignment, "single")
    assert info["state"] == "running"
    assert info["url"] == f"https://{container.name}.apps.test"
    assert runtime.stop == [(other_container.pk, None)]  # the reaper ran, and spared the app it handed out


def test_start_all_never_pins_an_app_the_lazy_reaper_then_stops(
    monkeypatch,
    runtime,
    django_capture_on_commit_callbacks,
):
    monkeypatch.setattr(hosting, "lazy_reap", REAL_LAZY_REAP)
    task = _task(idle_stop_minutes=20)
    app = _apps(task)[0]
    _container(app, idle_minutes=30)
    with django_capture_on_commit_callbacks(execute=True):
        result = hosting.task_hosting_action(task, "start_all", user=task.created_by)
    assert result["apps"][0]["state"] == "running"
    assert result["apps"][0]["pinned"] is True
    assert runtime.stop == []


def test_lazy_reaper_runs_at_most_once_a_minute(monkeypatch):
    monkeypatch.undo()  # restore the real lazy_reap (and the rest; this test needs no other patch)
    cache.clear()
    calls = []
    monkeypatch.setattr(hosting, "reap_idle_apps", lambda: calls.append(1) or 0)
    hosting.lazy_reap()
    hosting.lazy_reap()
    assert calls == [1]
    cache.delete(hosting.LAZY_REAP_CACHE_KEY)
    hosting.lazy_reap()
    assert calls == [1, 1]


def test_lazy_reaper_failures_never_break_the_caller(monkeypatch):
    monkeypatch.undo()
    cache.clear()

    def broken():
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(hosting, "reap_idle_apps", broken)
    hosting.lazy_reap()  # logged, not raised
    assert EvalTask.objects.count() >= 0  # the transaction is still usable


# ---------------------------------------------------------------------------
# Participant rules
# ---------------------------------------------------------------------------


def test_on_demand_off_tells_participants_to_ask_the_researcher(runtime, django_capture_on_commit_callbacks):
    task = _task(on_demand=False)
    app = _apps(task)[0]
    participant = ParticipantFactory(task=task)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=participant)
    assert info["state"] == "idle"
    assert info["message"] == hosting.MSG_NOT_STARTED
    assert runtime.build == []
    # A pinned (pre-started) app may be brought back up by a participant.
    EvalTaskApp.objects.filter(pk=app.pk).update(pinned=True)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=participant)
    assert info["state"] == "starting"
    assert len(runtime.build) == 1


def test_unreachable_routing_is_unavailable_for_participants_only(
    settings,
    runtime,
    django_capture_on_commit_callbacks,
):
    settings.APPS_SUBDOMAIN_PROXY = False
    task = _task()
    running_app, idle_app, _ = _apps(task)
    container = _container(running_app)
    participant = ParticipantFactory(task=task)
    info = hosting.participant_hosting_info(running_app)
    assert info["state"] == "unavailable"
    assert info["url"] is None
    assert "subdomain" in info["message"]
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.ensure_running(idle_app, actor=participant)["state"] == "unavailable"
    assert runtime.build == []
    researcher = hosting.researcher_hosting_info(running_app)
    assert researcher["state"] == "running"
    assert researcher["container_name"] == container.name
    assert researcher["container_id"] == str(container.pk)


def test_failed_apps_are_not_rebuilt_by_participant_clicks(runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    failed = _container(app, status="failed")
    ContainerAction.objects.create(
        action_id="act_failed00001",
        container=failed,
        action_type=ContainerAction.ActionType.BUILD,
        status=ContainerAction.Status.FAILED,
        error_message=f"pip failed in {failed.name}",
    )
    participant = ParticipantFactory(task=task)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=participant)
    assert info["state"] == "failed"
    assert info["error"] == hosting.PARTICIPANT_ERROR_APP
    assert "llm-" not in str(info)
    assert runtime.build == []
    researcher = hosting.researcher_hosting_info(_reload(app))
    assert failed.name in researcher["error"]
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=task.created_by)
    assert len(runtime.build) == 1


def _failed_start(container, error, *, minutes_ago=1.0):
    action = ContainerAction.objects.create(
        action_id=f"act_{container.pk.hex[:12]}",
        container=container,
        action_type=ContainerAction.ActionType.START,
        status=ContainerAction.Status.FAILED,
        error_message=error,
    )
    ContainerAction.objects.filter(pk=action.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    return action


@pytest.mark.parametrize(
    ("error", "participant_error"),
    [
        ("Docker daemon unavailable", hosting.PARTICIPANT_ERROR_PLATFORM),
        ('404 Client Error: Not Found ("No such container: llm-0001")', hosting.PARTICIPANT_ERROR_PLATFORM),
        ("driver failed programming external connectivity: port is already allocated", hosting.PARTICIPANT_ERROR_APP),
    ],
)
def test_a_failed_start_is_reported_and_not_retried_on_every_poll(
    runtime,
    django_capture_on_commit_callbacks,
    error,
    participant_error,
):
    task = _task()
    app = _apps(task)[0]
    container = _container(app, status="stopped")
    _failed_start(container, error)  # docker start failed: the runtime leaves the container STOPPED
    participant = ParticipantFactory(task=task)
    info = hosting.participant_hosting_info(_reload(app))
    assert (info["state"], info["error"]) == ("failed", participant_error)
    assert "llm-" not in str(info)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=participant)
    assert info["state"] == "failed"
    assert runtime.start == []  # the 3-second poll no longer re-dispatches a doomed start
    researcher = hosting.researcher_hosting_info(_reload(app))
    assert researcher["state"] == "failed"
    assert error in researcher["error"]
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=task.created_by)  # the researcher can retry at once
    assert runtime.start == [(container.pk, task.created_by)]


def test_an_old_failed_start_is_retried_and_a_newer_start_wins(runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    container = _container(app, status="stopped")
    _failed_start(container, "Docker daemon unavailable", minutes_ago=11)
    assert hosting.participant_hosting_info(_reload(app))["state"] == "idle"
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=ParticipantFactory(task=task))
    assert runtime.start == [(container.pk, task.created_by)]
    ContainerAction.objects.filter(container=container).delete()
    _failed_start(container, "Docker daemon unavailable", minutes_ago=2)
    ContainerAction.objects.create(
        action_id="act_startok0001",
        container=container,
        action_type=ContainerAction.ActionType.START,
        status=ContainerAction.Status.COMPLETED,
    )
    assert hosting.participant_hosting_info(_reload(app))["state"] == "idle"


def test_jobs_without_code_are_never_built(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    app.job.result_data = {"content": ""}
    app.job.save(update_fields=["result_data"])
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_running(app, actor=app.task.created_by)
    assert info["state"] == "failed"
    assert info["error"] == hosting.MSG_NO_CODE
    assert runtime.build == []


# ---------------------------------------------------------------------------
# Stop, touch, rebuild, reset
# ---------------------------------------------------------------------------


def test_stop_task_apps_stops_task_started_unpinned_unshared_apps(runtime, django_capture_on_commit_callbacks):
    task = _task(n_apps=4)
    mine, pinned, adopted, queued = _apps(task)
    mine_container = _container(mine)
    _container(pinned, pinned=True)
    _container(adopted, started=False)
    EvalTaskApp.objects.filter(pk=queued.pk).update(hosting_requested_at=timezone.now())
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.stop_task_apps(task) == 1
    assert runtime.stop == [(mine_container.pk, task.created_by)]
    assert _reload(queued).hosting_requested_at is None


def test_deleting_a_task_releases_pinned_and_in_flight_apps(monkeypatch, runtime, django_capture_on_commit_callbacks):
    task = _task(n_apps=4)
    pinned, building, adopted, stopped = _apps(task)
    pinned_container = _container(pinned, pinned=True)  # "Start all" pinned it
    build_container = _container(building, status="building")  # its build is still running
    _container(adopted, started=False, pinned=True)  # pinned, but a run the task did not start
    _container(stopped, status="stopped")
    with django_capture_on_commit_callbacks(execute=True):
        tasks_service.delete_task(task)
    assert not EvalTaskApp.objects.filter(task_id=task.pk).exists()
    assert runtime.stop == [(pinned_container.pk, task.created_by)]
    [(target, args)] = runtime.threads
    assert target is hosting._release_worker
    assert args == ([(build_container.pk, task.created_by_id)],)
    # The build finishes after the task is gone: the worker stops the container then.
    ContainerInstance.objects.filter(pk=build_container.pk).update(status="running")
    monkeypatch.setattr(hosting, "RELEASE_POLL_SECONDS", 0)
    target(*args)
    assert runtime.stop == [(pinned_container.pk, task.created_by), (build_container.pk, task.created_by)]


def test_removing_a_drafts_app_stops_the_container_it_started(runtime, django_capture_on_commit_callbacks):
    task = make_draft_task(n_apps=2)
    app, kept = _apps(task)
    container = _container(app, pinned=True)  # the researcher started (and pinned) it on the draft
    with django_capture_on_commit_callbacks(execute=True):
        pool_service.delete_task_app(task, app)
    assert not EvalTaskApp.objects.filter(pk=app.pk).exists()
    assert EvalTaskApp.objects.filter(pk=kept.pk).exists()
    assert runtime.stop == [(container.pk, task.created_by)]


def test_release_leaves_containers_another_app_owns_or_uses(runtime, django_capture_on_commit_callbacks):
    app, owned_too, _ = _apps(_task())
    used = _container(app, pinned=True)
    sharer = _apps(_task(n_apps=1))[0]
    _container(sharer, container=used, started=False, idle_minutes=5)  # an open task is using it (R3.8)
    shared_run = _container(owned_too)
    closed = _task(n_apps=1)
    closed.status = EvalTask.Status.CLOSED
    closed.save(update_fields=["status"])
    _container(_apps(closed)[0], container=shared_run)  # another task started it too: its reaper owns it
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.release_apps([app, owned_too]) == 0
    assert runtime.stop == []
    released = _reload(app)
    assert (released.pinned, released.started_by_task) == (False, False)


def test_release_worker_waits_for_the_build_then_stops(monkeypatch, runtime):
    job = _apps(_task())[0].job
    container = HostingContainerFactory(generation_job=job, status="building")
    assert hosting._release_settled(container.pk, None) is False
    assert runtime.stop == []
    ContainerInstance.objects.filter(pk=container.pk).update(status="running")
    monkeypatch.setattr(hosting, "RELEASE_POLL_SECONDS", 0)
    hosting._release_worker([(container.pk, None)])
    assert runtime.stop == [(container.pk, None)]
    failed = HostingContainerFactory(generation_job=job, status="failed")
    assert hosting._release_settled(failed.pk, None) is True  # nothing to stop


def test_build_of_an_app_deleted_before_its_dispatch_never_runs(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        hosting.ensure_running(app, actor=app.task.created_by)
    EvalTaskApp.objects.filter(pk=app.pk).delete()
    for callback in callbacks:
        callback()
    assert runtime.build == []


def test_build_of_an_app_deleted_during_its_dispatch_is_released(
    monkeypatch,
    runtime,
    django_capture_on_commit_callbacks,
):
    app = _apps(_task())[0]
    built = []

    def build_and_vanish(job, user):
        EvalTaskApp.objects.filter(pk=app.pk).delete()  # the task is deleted meanwhile
        built.append(HostingContainerFactory(generation_job=job, status="pending"))
        return built[-1]

    monkeypatch.setattr(container_service, "build_for_job", build_and_vanish)
    with django_capture_on_commit_callbacks(execute=True):
        hosting.ensure_running(app, actor=app.task.created_by)
    assert runtime.threads == [(hosting._release_worker, ([(built[0].pk, app.task.created_by_id)],))]


def test_stop_app_without_force_spares_containers_the_task_did_not_start(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    container = _container(app, started=False)
    with django_capture_on_commit_callbacks(execute=True):
        hosting.stop_app(app)
    assert runtime.stop == []
    with django_capture_on_commit_callbacks(execute=True):
        hosting.stop_app(app, actor=app.task.created_by, force=True)
    assert runtime.stop == [(container.pk, app.task.created_by)]


def test_touch_updates_every_app_on_the_same_container():
    app = _apps(_task())[0]
    container = _container(app, idle_minutes=30)
    sharer = _apps(_task(n_apps=1))[0]
    _container(sharer, container=container, started=False, idle_minutes=30)
    hosting.touch(app)
    recent = timezone.now() - timedelta(seconds=30)
    assert _reload(app).last_activity_at > recent
    assert _reload(sharer).last_activity_at > recent


def test_rebuild_removes_the_container_and_builds_a_new_one(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    old = _container(app)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.app_hosting_action(app, "rebuild", user=app.task.created_by)
    assert info["state"] == "starting"
    assert runtime.remove == [(old.pk, app.task.created_by)]
    assert len(runtime.build) == 1
    assert _reload(app).container == runtime.build[0].container


def test_rebuild_of_a_shared_container_is_refused_without_changes(runtime, django_capture_on_commit_callbacks):
    app = _apps(_task())[0]
    shared = _container(app)
    sharer = _apps(_task(n_apps=1))[0]
    _container(sharer, container=shared, started=False)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(HumanEvalServiceError) as raised:
        hosting.app_hosting_action(app, "rebuild", user=app.task.created_by)
    assert raised.value.status == 409
    assert raised.value.code == "shared_container"
    assert _reload(app).container == shared
    assert runtime.remove == []
    assert runtime.build == []


def test_rebuild_while_building_is_refused():
    app = _apps(_task())[0]
    _container(app, status="building")
    with pytest.raises(HumanEvalServiceError) as raised:
        hosting.app_hosting_action(app, "rebuild", user=app.task.created_by)
    assert raised.value.code == "hosting_busy"


def test_reset_of_a_shared_container_is_refused():
    app = _apps(_task())[0]
    shared = _container(app)
    sharer = _apps(_task(n_apps=1))[0]
    _container(sharer, container=shared, started=False, pinned=True)
    with pytest.raises(HumanEvalServiceError) as raised:
        hosting.reset_app(app)
    assert raised.value.status == 409
    assert raised.value.code == "shared_container"
    assert _reload(app).start_dispatched_at is None


def _reset_task():
    return _task(isolation="exclusive", reset="per_assignment")


def _assignment(task, app, status="in_progress"):
    item = task.items.get(app_a=app)
    return AssignmentFactory(participant=ParticipantFactory(task=task), task=task, item=item, status=status)


def test_reset_worker_recreates_the_container_from_its_image(monkeypatch, runtime, django_capture_on_commit_callbacks):
    task = _reset_task()
    app = _apps(task)[0]
    container = _container(app)
    earlier = _assignment(task, app, status="submitted")
    hosting._record_use(container, earlier)
    current = _assignment(task, app)
    run_calls = []
    monkeypatch.setattr(docker_manager, "image_exists", lambda tag: tag == container.image)
    monkeypatch.setattr(docker_manager, "remove", lambda name, force=True: {"status": "removed"})
    monkeypatch.setattr(
        docker_manager,
        "run_container",
        lambda *args, **kwargs: run_calls.append((args, kwargs)) or "newdockerid",
    )
    monkeypatch.setattr(hosting, "_verify_running", lambda c: (True, ""))
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_for_assignment(current, "single")
    assert info["state"] == "starting"
    assert info["message"] == hosting.MSG_RESETTING
    [(target, args)] = runtime.threads
    assert target is hosting._reset_worker
    assert args == (app.pk, container.pk, str(current.pk))
    target(*args)
    assert run_calls == [
        ((container.image, container.name, {"8000/tcp": container.app_port}, {}, str(container.pk)), {"network": ""}),
    ]
    container.refresh_from_db()
    assert container.status == "running"
    assert container.container_id == "newdockerid"
    usage = container.metadata["human_eval"]
    assert usage["users"] == [str(current.pk)]
    assert usage["reset_at"]
    app = _reload(app)
    assert app.start_dispatched_at is None
    assert app.hosting_error == ""
    provenance = hosting.hosting_provenance(app, assignment=current)
    assert provenance["prior_users"] == 0
    assert provenance["reset_at"] == usage["reset_at"]
    assert hosting.participant_hosting_info(app, assignment=current)["state"] == "running"


def test_reset_worker_failure_marks_the_container_failed(monkeypatch):
    task = _reset_task()
    app = _apps(task)[0]
    container = _container(app, start_dispatched_at=timezone.now())
    monkeypatch.setattr(docker_manager, "image_exists", lambda tag: True)
    monkeypatch.setattr(docker_manager, "remove", lambda name, force=True: {"error": "404 No such container"})
    monkeypatch.setattr(docker_manager, "run_container", lambda *args, **kwargs: "newid")
    monkeypatch.setattr(hosting, "_verify_running", lambda c: (False, "Container exited (code 1)"))
    hosting._reset_worker(app.pk, container.pk, "")
    container.refresh_from_db()
    assert container.status == "failed"
    app = _reload(app)
    assert app.start_dispatched_at is None
    assert app.hosting_error.startswith("Reset failed: Container exited")


def test_per_assignment_reset_hides_the_previous_raters_app(django_capture_on_commit_callbacks):
    task = _reset_task()
    app = _apps(task)[0]
    container = _container(app)
    earlier = _assignment(task, app, status="submitted")
    hosting._record_use(container, earlier)
    current = _assignment(task, app)
    assert hosting.needs_reset(app, assignment=current)
    assert not hosting.needs_reset(app, assignment=earlier)
    info = hosting.participant_hosting_info(app, assignment=current)
    assert info["state"] == "idle"
    assert info["url"] is None
    assert info["message"] == hosting.MSG_FRESH_COPY
    assert hosting.participant_hosting_info(app)["state"] == "running"


def test_no_reset_while_another_open_participant_uses_the_container(
    monkeypatch,
    runtime,
    django_capture_on_commit_callbacks,
):
    task = _reset_task()
    app = _apps(task)[0]
    container = _container(app)
    open_elsewhere = _assignment(task, app)  # a real participant still working in this state
    hosting._record_use(container, open_elsewhere)
    current = _assignment(task, app)
    recorded = []
    from backend.human_eval.services import context

    monkeypatch.setattr(context, "record_hosting_provenance", lambda *args: recorded.append(args))
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_for_assignment(current, "single")
    assert info["state"] == "running"
    assert runtime.threads == []
    assert recorded == [(current, "single", app)]
    assert hosting.hosting_provenance(app, assignment=current)["prior_users"] == 1


def test_an_open_preview_never_blocks_the_reset(runtime, django_capture_on_commit_callbacks):
    task = _reset_task()
    app = _apps(task)[0]
    container = _container(app)
    preview = ParticipantFactory(task=task, kind="preview")
    abandoned_preview = AssignmentFactory(participant=preview, task=task, item=task.items.get(app_a=app))
    hosting._record_use(container, abandoned_preview)  # the researcher tried the app and closed the tab
    current = _assignment(task, app)
    info = hosting.participant_hosting_info(app, assignment=current)
    assert (info["state"], info["url"], info["message"]) == ("idle", None, hosting.MSG_FRESH_COPY)
    with django_capture_on_commit_callbacks(execute=True):
        info = hosting.ensure_for_assignment(current, "single")
    assert info["state"] == "starting"
    assert info["message"] == hosting.MSG_RESETTING
    [(target, args)] = runtime.threads
    assert target is hosting._reset_worker
    assert args == (app.pk, container.pk, str(current.pk))


def test_a_state_the_assignment_was_handed_is_never_reset_under_it(runtime, django_capture_on_commit_callbacks):
    task = _reset_task()
    app = _apps(task)[0]
    container = _container(app)
    current = _assignment(task, app)
    hosting._record_use(container, current)
    # The researcher previews the same app later (previews ignore exclusive isolation), then finishes.
    preview = ParticipantFactory(task=task, kind="preview")
    later_preview = AssignmentFactory(participant=preview, task=task, item=task.items.get(app_a=app))
    hosting._record_use(container, later_preview)
    Assignment.objects.filter(pk=later_preview.pk).update(status=Assignment.Status.SUBMITTED)
    assert not hosting.needs_reset(app, assignment=current)
    assert hosting.participant_hosting_info(app, assignment=current)["state"] == "running"
    with django_capture_on_commit_callbacks(execute=True):
        assert hosting.ensure_for_assignment(current, "single")["state"] == "running"
    assert runtime.threads == []  # no mid-assignment wipe of the participant's work
    assert hosting.needs_reset(app, assignment=_assignment(task, app))  # the next rater still gets a fresh copy


def test_no_reset_when_the_task_keeps_shared_state(django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    container = _container(app)
    hosting._record_use(container, _assignment(task, app, status="submitted"))
    assert not hosting.needs_reset(app, assignment=_assignment(task, app))


def test_ensure_for_assignment_rejects_unknown_slots():
    task = _task()
    app = _apps(task)[0]
    with pytest.raises(HumanEvalServiceError) as raised:
        hosting.ensure_for_assignment(_assignment(task, app), "A")
    assert raised.value.status == 404


def test_ensure_on_a_read_only_assignment_is_409(runtime):
    task = _task()
    app = _apps(task)[0]
    with pytest.raises(HumanEvalServiceError) as raised:
        hosting.ensure_for_assignment(_assignment(task, app, status="submitted"), "single")
    assert raised.value.status == 409
    assert raised.value.code == "read_only"
    assert runtime.build == []


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def test_environment_info_reports_routing_warnings_and_running_apps(settings):
    settings.APPS_SUBDOMAIN_PROXY = False
    settings.TRUSTED_PROXY_CIDRS = []
    _container(_apps(_task())[0])
    info = hosting.environment_info()
    assert info["apps_reachable"] is False
    assert info["routing_mode"] == "direct"
    assert info["warnings"] == ["apps_not_reachable", "shared_client_ip"]
    assert info["global_running_apps"] == 1
    assert info["global_max_running_apps"] == 4
    assert info["max_concurrent_builds"] == 1
    assert set(info["instruments"]) == {"sus", "umux_lite", "nasa_tlx"}
    assert info["security_checklist_default"]


def test_task_hosting_summarises_the_task(settings):
    settings.HUMAN_EVAL_MAX_RUNNING_APPS = 6
    task = _task()
    running, queued, idle = _apps(task)
    _container(running)
    EvalTaskApp.objects.filter(pk=queued.pk).update(hosting_requested_at=timezone.now())
    summary = hosting.task_hosting(task)
    assert [row["label"] for row in summary["apps"]] == [running.label, queued.label, idle.label]
    assert [row["state"] for row in summary["apps"]] == ["running", "queued", "idle"]
    assert summary["running"] == 1
    assert summary["queued"] == 1
    assert summary["global_running"] == 1
    assert summary["global_max_running"] == 6
    assert summary["task_max_running"] == 3
    assert summary["apps_reachable"] is True
    assert summary["apps"][0]["url"].startswith("https://llm-")


# ---------------------------------------------------------------------------
# Researcher API
# ---------------------------------------------------------------------------


def test_environment_endpoint(client):
    client.force_login(UserFactory())
    response = client.get(f"{API}/environment/")
    assert response.status_code == 200
    body = response.json()
    assert body["routing_mode"] == "subdomain"
    assert body["apps_reachable"] is True
    assert body["default_settings"]["hosting"]["max_running_apps"] == 3


def test_environment_endpoint_needs_a_login(client):
    assert client.get(f"{API}/environment/").status_code == 401


def test_task_hosting_endpoint_is_owner_scoped(client):
    task = _task()
    client.force_login(UserFactory())
    assert client.get(f"{API}/tasks/{task.id}/hosting/").status_code == 404
    assert client.get(f"{API}/tasks/not-a-uuid/hosting/").status_code == 404
    client.force_login(task.created_by)
    response = client.get(f"{API}/tasks/{task.id}/hosting/")
    assert response.status_code == 200
    assert len(response.json()["apps"]) == 3
    client.force_login(UserFactory(is_staff=True))
    assert client.get(f"{API}/tasks/{task.id}/hosting/").status_code == 200


def test_task_hosting_actions_endpoint(client, runtime, settings, django_capture_on_commit_callbacks):
    settings.HUMAN_EVAL_MAX_CONCURRENT_BUILDS = 5
    task = _task(max_running_apps=2)
    client.force_login(task.created_by)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"{API}/tasks/{task.id}/hosting/",
            {"action": "start_all"},
            content_type="application/json",
        )
    assert response.status_code == 200
    assert response.json()["queued"] == 1
    assert len(runtime.build) == 2
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"{API}/tasks/{task.id}/hosting/",
            {"action": "stop_all"},
            content_type="application/json",
        )
    assert response.status_code == 200
    assert not any(app.pinned for app in _apps(task))
    assert all(app.hosting_requested_at is None for app in _apps(task))
    response = client.post(f"{API}/tasks/{task.id}/hosting/", {"action": "explode"}, content_type="application/json")
    assert response.status_code == 422


def test_start_all_on_a_closed_task_is_409(client):
    task = _task()
    task.status = EvalTask.Status.CLOSED
    task.save(update_fields=["status"])
    client.force_login(task.created_by)
    response = client.post(f"{API}/tasks/{task.id}/hosting/", {"action": "start_all"}, content_type="application/json")
    assert response.status_code == 409
    assert response.json() == {"detail": response.json()["detail"], "code": "task_closed", "errors": []}


def test_app_hosting_endpoint_actions_and_scoping(client, runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    foreign_app = _apps(_task())[0]
    client.force_login(task.created_by)
    url = f"{API}/tasks/{task.id}/apps/{app.id}/hosting/"
    assert (
        client.post(
            f"{API}/tasks/{task.id}/apps/{foreign_app.id}/hosting/",
            {"action": "pin"},
            content_type="application/json",
        ).status_code
        == 404
    )
    response = client.post(url, {"action": "pin"}, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["pinned"] is True
    container = _container(_reload(app), pinned=True)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url, {"action": "stop"}, content_type="application/json")
    assert response.status_code == 200
    assert response.json()["pinned"] is False
    assert runtime.stop == [(container.pk, task.created_by)]
    response = client.post(url, {"action": "unpin"}, content_type="application/json")
    assert response.json()["container_name"] == container.name
    client.force_login(UserFactory())
    assert client.post(url, {"action": "pin"}, content_type="application/json").status_code == 404


def test_app_hosting_start_endpoint(client, runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    client.force_login(task.created_by)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"{API}/tasks/{task.id}/apps/{app.id}/hosting/",
            {"action": "start"},
            content_type="application/json",
        )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "starting"
    assert body["started_by_task"] is True
    assert set(body) == {
        "state",
        "url",
        "progress",
        "message",
        "error",
        "container_id",
        "container_name",
        "container_status",
        "started_by_task",
        "pinned",
        "last_activity_at",
    }
    assert len(runtime.build) == 1


def test_rebuild_endpoint_409_leaves_no_changes(client, runtime, django_capture_on_commit_callbacks):
    task = _task()
    app = _apps(task)[0]
    shared = _container(app)
    sharer = _apps(_task(n_apps=1))[0]
    _container(sharer, container=shared, started=False)
    client.force_login(task.created_by)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"{API}/tasks/{task.id}/apps/{app.id}/hosting/",
            {"action": "rebuild"},
            content_type="application/json",
        )
    assert response.status_code == 409
    assert response.json()["code"] == "shared_container"
    assert _reload(app).container == shared
    assert runtime.remove == []
    assert runtime.build == []
    assert Assignment.objects.count() == 0
