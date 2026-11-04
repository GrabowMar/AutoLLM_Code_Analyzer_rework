"""Hosting of task apps for participants (owner C): start/stop/queue/reset/reap app containers.

A task app (:class:`~backend.human_eval.models.EvalTaskApp`) is served by a
``runtime.ContainerInstance`` named ``llm-<hex>``. This module decides when to
build, start, stop or reset that container and what participants and the
researcher are told about it. Rules (spec §5.11, R1, R3.2, R3.8):

- **DB-only inside a request.** Container work goes through
  ``transaction.on_commit``: ``container_service.build_for_job (a GenerationRun) / start_instance /
  stop_instance / remove_instance`` (each runs its Docker work in its own daemon
  thread), and the per-assignment reset runs in a ``dispatch_in_thread`` worker.
  Info functions (``participant_hosting_info``, ``researcher_hosting_info``,
  ``environment_info``) never write.
- **Container resolution** (:func:`current_container`): the FK unless removed or
  failed, else the newest ``llm-*`` container of the run (running ones first);
  ``ensure_running`` adopts it (``started_by_task=False`` until the task starts it).
- **Capacity.** Slots are DISTINCT containers that are running, building, pending
  (fresh, or with an in-flight marker) or have a start in flight, plus apps with an
  in-flight marker but no container yet, among task-started apps. Limits: the
  server-wide ``HUMAN_EVAL_MAX_RUNNING_APPS``, the task's ``hosting.max_running_apps``
  and, for builds, ``HUMAN_EVAL_MAX_CONCURRENT_BUILDS``. Decisions are serialized by a
  transaction-level Postgres advisory lock (lock order: assignment row → advisory
  lock → task-app rows). When full, the least-recently-active task-started,
  unpinned, unshared app idle for 3+ minutes is evicted; otherwise the app joins
  the FIFO queue (``hosting_requested_at``) that :func:`advance_queue` drains.
- **In-flight marker** ``start_dispatched_at`` (set under the row lock): while it is
  younger than 10 minutes nothing is dispatched again; the on_commit callback (or
  the reset worker) clears it; the reaper clears stale ones.
- **Ownership** (``started_by_task``): set when the task starts (or resets) a container,
  cleared for every app on it whenever human-eval stops or removes it (and by the reaper
  for a stopped container nothing is starting), so a later start from elsewhere (Runtime
  page, DAST analysis, another task) is never stopped by this task. Non-force stops only
  ever stop the app's own FK container.
- **Per-assignment reset** (``hosting.reset == "per_assignment"``): the container's
  state "users" (assignment ids that were handed the running app, via ensure, heartbeat
  or the console) are kept in
  ``ContainerInstance.metadata["human_eval"] = {docker_id, reset_at, users}``. When
  an assignment needs the app and another assignment already used this container
  state (and this one was not handed it yet), :func:`reset_app` recreates the container
  from its EXISTING image (same name, port and network, so the app URL stays valid).
  Participants are shown ``idle`` (no URL) and the console refuses until that reset ran,
  so they never see an earlier rater's data. A reset never runs while another open
  non-preview assignment uses the container (preview use never blocks a reset).
- **Shared containers** (R3.8): reaper, eviction, :func:`stop_task_apps`, non-force
  :func:`stop_app`, reset and rebuild leave a container alone when another
  open/paused task's app references it and is pinned or active within its idle
  window (rebuild/reset → 409 ``shared_container``). :func:`touch` updates every
  task app on the same container.
- **Release** (:func:`release_apps`, before a task or a draft's app is deleted): every
  container the apps started is stopped, pinned or not, unless another app still owns or
  uses it; a build/start/reset in flight is stopped by a worker thread once it finished.
- **Idle reaper** (:func:`reap_idle_apps`): Celery beat every 120 s plus lazily
  (at most once a minute) from ensure / heartbeat / task hosting, always after the
  caller's own activity was recorded (it never stops the app it then reports running).
- **Liveness** (R3.11): heartbeats and ensure count as assignment activity only while the
  workbench is visible or was visible within ``assignment_timeout_minutes``, so an
  abandoned hidden tab lets the assignment expire; an honoured beat keeps every app of
  the assignment alive (pairwise: the app not in focus too).
- **Failed starts**: a STOPPED container whose newest start failed (less than 10 minutes
  ago) is ``failed``; participants do not re-dispatch it, researchers can.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db import transaction
from django.db.models import F
from django.db.models import Q
from django.utils import timezone

from backend.common.threading import dispatch_in_thread
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import Participant
from backend.human_eval.services import design as design_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict
from backend.runtime.api.schema import ContainerInstanceSchema
from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.services import container_service
from backend.runtime.services import docker_manager
from backend.runtime.services import traefik_router
from backend.runtime.services.container_service import _verify_running

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

APP_PREFIX = "llm-"
META_KEY = "human_eval"
MAX_ERROR = 4000
MAX_USERS = 2000

#: In-flight markers, open start/stop actions and queue requests older than this are stale.
MARKER_TTL = timedelta(minutes=10)
#: A pending/building container this old (and no marker) is a build whose thread died.
BUILD_STALE_AFTER = timedelta(minutes=60)
#: Eviction only takes apps idle at least this long.
EVICT_IDLE_AFTER = timedelta(minutes=3)
#: Queue requests nobody polled for this long are dropped (pinned apps stay queued).
QUEUE_ABANDON_AFTER = timedelta(minutes=10)
#: ``touch`` writes at most this often per app.
TOUCH_THROTTLE = timedelta(seconds=10)
#: Heartbeats: the longest gap counted, and the gap after which nothing is counted.
BEAT_CAP_SECONDS = 60
BEAT_GAP_LIMIT_SECONDS = 5 * 60
LAST_SEEN_THROTTLE = timedelta(seconds=60)

#: Release worker: how often it checks whether the build/start/reset in flight finished, and for how long.
RELEASE_POLL_SECONDS = 5.0
RELEASE_GIVE_UP_AFTER = BUILD_STALE_AFTER

LAZY_REAP_CACHE_KEY = "human_eval:hosting:lazy_reap"
LAZY_REAP_SECONDS = 60
#: pg_advisory_xact_lock key serializing hosting capacity decisions (arbitrary constant).
CAPACITY_LOCK_KEY = 4_871_120_355_017

_S = ContainerInstance.Status
_RUNNING = _S.RUNNING
_ACTIVE = (_S.RUNNING, _S.BUILDING, _S.PENDING)
_LIVE = (_S.RUNNING, _S.STOPPED, _S.BUILDING, _S.PENDING)
_A = ContainerAction.ActionType
_OPEN_ACTION = (ContainerAction.Status.PENDING, ContainerAction.Status.RUNNING)
#: Open actions that bring a container up (a release waits for them before stopping it).
_STARTING_ACTIONS = (_A.BUILD, _A.START, _A.RESTART)
_CLOSED_TASK = (EvalTask.Status.CLOSED, EvalTask.Status.ARCHIVED)
_LIVE_TASK = (EvalTask.Status.OPEN, EvalTask.Status.PAUSED)
_OPEN_ASSIGNMENT = (Assignment.Status.ASSIGNED, Assignment.Status.IN_PROGRESS)

MSG_UNREACHABLE = (
    "Apps cannot be opened from the evaluation portal in this environment: the server must serve apps on "
    "their own subdomains (subdomain or Traefik routing). Please tell the researcher."
)
MSG_NOT_STARTED = "This app has not been started yet — ask the researcher"
MSG_IDLE = "The app is not running yet — start it to begin"
MSG_FRESH_COPY = "Start the app to get a fresh copy of it"
MSG_BUILDING = "Building the app — this can take a few minutes"
MSG_STARTING = "Starting the app…"
MSG_RESETTING = "Preparing a fresh copy of the app…"
MSG_STOPPING = "The app is stopping"
MSG_NO_CODE = "This app has no generated code to run"
MSG_STUCK = "The previous build or start did not finish; rebuild the app"
PARTICIPANT_ERROR_APP = "The app could not be started."
PARTICIPANT_ERROR_PLATFORM = "The hosting platform is unavailable right now."


# ---------------------------------------------------------------------------
# Routing and settings
# ---------------------------------------------------------------------------


def apps_reachable() -> bool:
    """Whether participants can open apps: subdomain or Traefik routing.

    (``APPS_SUBDOMAIN_PROXY`` or ``TRAEFIK_DYNAMIC_DIR``; path mode is owner-only and direct ports are not public.)
    """
    return bool(getattr(settings, "TRAEFIK_DYNAMIC_DIR", "") or getattr(settings, "APPS_SUBDOMAIN_PROXY", False))


def routing_mode() -> str:
    """``"traefik" | "subdomain" | "path" | "direct"`` in the order ``resolve_app_url`` checks them."""
    if getattr(settings, "TRAEFIK_DYNAMIC_DIR", ""):
        return "traefik"
    if getattr(settings, "APPS_SUBDOMAIN_PROXY", False):
        return "subdomain"
    if getattr(settings, "APPS_PROXY_PATH", False):
        return "path"
    return "direct"


def _global_cap() -> int:
    return max(1, int(getattr(settings, "HUMAN_EVAL_MAX_RUNNING_APPS", 4)))


def _build_cap() -> int:
    return max(1, int(getattr(settings, "HUMAN_EVAL_MAX_CONCURRENT_BUILDS", 1)))


def _hosting(task: EvalTask) -> dict[str, Any]:
    return design_service.merged_settings(task.settings)["hosting"]


def _error_text(value: Any) -> str:
    return str(value or "")[:MAX_ERROR]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else None


def _is_participant(actor: Any) -> bool:
    return isinstance(actor, Participant)


def _container_user_id(actor: Any, task: EvalTask) -> int | None:
    """Whom container actions are attributed to: the researcher acting, else the task owner."""
    if actor is not None and not _is_participant(actor) and getattr(actor, "is_authenticated", False):
        return actor.pk
    return task.created_by_id


def _user(user_id: int | None) -> Any:
    if user_id is None:
        return None
    from backend.users.models import User

    return User.objects.filter(pk=user_id).first()


def _has_code(run: Any) -> bool:
    """Whether the run has a collected app tree a runtime build can use (``RunView.files("app")``)."""
    if run is None:
        return False
    from backend.generation.runs.view import run_view

    return bool(run_view(run).files("app"))


def _capacity_lock() -> None:
    """Serialize hosting capacity decisions until the current transaction ends (call inside ``atomic``)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CAPACITY_LOCK_KEY])


def _close_connection() -> None:
    """Close the worker thread's DB connection (never the caller's transaction, e.g. under tests)."""
    if not connection.in_atomic_block:
        connection.close()


# ---------------------------------------------------------------------------
# Container resolution and bookkeeping
# ---------------------------------------------------------------------------


def _job_containers(job_id: Any, exclude: Iterable[Any] = ()) -> Any:
    qs = ContainerInstance.objects.filter(generation_job_id=job_id, name__startswith=APP_PREFIX, status__in=_LIVE)
    excluded = [pk for pk in exclude if pk is not None]
    if excluded:
        qs = qs.exclude(pk__in=excluded)
    return qs


def current_container(task_app: EvalTaskApp, *, exclude: Iterable[Any] = ()) -> ContainerInstance | None:
    """The app's current ``ContainerInstance`` (DB-only).

    The FK unless it is removed or failed, else the newest ``llm-*`` container of the run in
    running/stopped/building/pending (running ones first). A RUNNING container of the run is
    preferred over a STOPPED FK, so a running copy is never started twice (a building/pending FK
    always wins: it is a rebuild in progress). ``exclude`` skips containers.
    """
    excluded = {pk for pk in exclude if pk is not None}
    fk = task_app.container if task_app.container_id else None
    if fk is not None and fk.pk not in excluded and fk.status not in (_S.REMOVED, _S.FAILED):
        if fk.status != _S.STOPPED or task_app.job_id is None:
            return fk
        running = (
            _job_containers(task_app.job_id, excluded | {fk.pk})
            .filter(status=_RUNNING)
            .order_by("-created_at")
            .first()
        )
        return running or fk
    if task_app.job_id is None:
        return None
    candidates = _job_containers(task_app.job_id, excluded).order_by("-created_at")
    return candidates.filter(status=_RUNNING).first() or candidates.first()


def _failed_container(task_app: EvalTaskApp) -> ContainerInstance | None:
    fk = task_app.container if task_app.container_id else None
    if fk is not None and fk.status == _S.FAILED:
        return fk
    return None


def _marker_on(task_app: EvalTaskApp, now: datetime) -> bool:
    marker = task_app.start_dispatched_at
    return marker is not None and marker >= now - MARKER_TTL


def _stuck(container: ContainerInstance, task_app: EvalTaskApp, now: datetime) -> bool:
    """A pending/building container whose build or reset thread died (old, and no in-flight marker)."""
    if container.status not in (_S.PENDING, _S.BUILDING) or _marker_on(task_app, now):
        return False
    return container.created_at is not None and container.created_at < now - BUILD_STALE_AFTER


def _open_actions(container: ContainerInstance | None, now: datetime) -> dict[str, ContainerAction]:
    """Recent pending/running actions of the container, newest per type."""
    if container is None:
        return {}
    actions: dict[str, ContainerAction] = {}
    for action in ContainerAction.objects.filter(
        container=container,
        status__in=_OPEN_ACTION,
        created_at__gte=now - MARKER_TTL,
    ).order_by("-created_at"):
        actions.setdefault(action.action_type, action)
    return actions


def _stopping_container_ids(now: datetime) -> set[Any]:
    return set(
        ContainerAction.objects.filter(
            action_type=_A.STOP,
            status__in=_OPEN_ACTION,
            created_at__gte=now - MARKER_TTL,
        ).values_list("container_id", flat=True),
    )


def _last_error(container: ContainerInstance | None) -> str:
    if container is None:
        return ""
    message = (
        container.actions.filter(status=ContainerAction.Status.FAILED)
        .order_by("-created_at")
        .values_list("error_message", flat=True)
        .first()
    )
    return _error_text(message or "The app container failed")


def _failed_start(container: ContainerInstance | None, now: datetime) -> str:
    """The error of a recent failed start of this STOPPED container, else "".

    The runtime leaves a container STOPPED when ``docker start`` fails (daemon down, container gone from
    Docker); only its newest START action, failed less than ``MARKER_TTL`` ago, tells. A newer start (open
    or completed) replaces it.
    """
    if container is None or container.status != _S.STOPPED:
        return ""
    last = (
        ContainerAction.objects.filter(container=container, action_type=_A.START)
        .order_by("-created_at")
        .only("status", "error_message", "created_at")
        .first()
    )
    if last is None or last.status != ContainerAction.Status.FAILED:
        return ""
    if last.created_at is None or last.created_at < now - MARKER_TTL:
        return ""
    return _error_text(last.error_message or "The app container could not be started")


def _app_url(container: ContainerInstance) -> str | None:
    return ContainerInstanceSchema.resolve_app_url(container)


def _usage(container: ContainerInstance) -> dict[str, Any]:
    """This container state's human-eval bookkeeping ``{docker_id, reset_at, users}``.

    Starts empty for a new container, and again whenever the Docker container behind the row changed
    (a fresh container has fresh state).
    """
    raw = (container.metadata or {}).get(META_KEY)
    docker_id = container.container_id or ""
    if not isinstance(raw, dict) or raw.get("docker_id") != docker_id:
        return {"docker_id": docker_id, "reset_at": None, "users": []}
    users = [str(user) for user in raw.get("users") or [] if user]
    return {"docker_id": docker_id, "reset_at": raw.get("reset_at"), "users": users}


def _record_use(container: ContainerInstance, assignment: Assignment) -> None:
    """Note that ``assignment`` was handed this container state (idempotent; row-locked)."""
    with transaction.atomic():
        locked = ContainerInstance.objects.select_for_update().filter(pk=container.pk).first()
        if locked is None:
            return
        usage = _usage(locked)
        key = str(assignment.pk)
        if key in usage["users"]:
            return
        usage["users"] = [*usage["users"], key][-MAX_USERS:]
        metadata = dict(locked.metadata or {})
        metadata[META_KEY] = usage
        ContainerInstance.objects.filter(pk=locked.pk).update(metadata=metadata)
        container.metadata = metadata


def hosting_provenance(task_app: EvalTaskApp, *, assignment: Assignment | None = None) -> dict[str, Any]:
    """TS ``HostingProvenance`` of the app's current container for ``assignment`` (DB-only).

    ``container_id`` is the ContainerInstance id; ``prior_users`` counts the other assignments that
    were handed this container state since its last reset or build; ``reset_at`` is the last
    per-assignment reset (None after a plain build). For ``context.record_hosting_provenance``.
    """
    container = current_container(task_app)
    if container is None:
        return {
            "container_id": None,
            "container_name": None,
            "image": "",
            "reset_at": None,
            "prior_users": 0,
            "first_running_at": None,
        }
    usage = _usage(container)
    own = str(assignment.pk) if assignment is not None else None
    running = container.status == _RUNNING
    return {
        "container_id": str(container.pk),
        "container_name": container.name,
        "image": container.image or "",
        "reset_at": usage["reset_at"],
        "prior_users": sum(1 for user in usage["users"] if user != own),
        "first_running_at": timezone.now().isoformat() if running else None,
    }


def _note_running(assignment: Assignment, slot: str, task_app: EvalTaskApp, container: ContainerInstance) -> None:
    """The app is running for this assignment: record container use and the hosting provenance."""
    from backend.human_eval.services import context

    _record_use(container, assignment)
    context.record_hosting_provenance(assignment, slot, task_app)


def _shared_active_elsewhere(container_id: Any, task_app: EvalTaskApp, now: datetime) -> bool:
    """R3.8: another open/paused task's app on this container is pinned or active within its idle window."""
    if container_id is None:
        return False
    others = (
        EvalTaskApp.objects.filter(container_id=container_id, task__status__in=_LIVE_TASK)
        .exclude(task_id=task_app.task_id)
        .select_related("task")
    )
    for other in others:
        if other.pinned:
            return True
        idle = timedelta(minutes=int(_hosting(other.task).get("idle_stop_minutes") or 20))
        if other.last_activity_at is not None and other.last_activity_at >= now - idle:
            return True
    return False


def _other_open_users(container: ContainerInstance, assignment: Assignment | None) -> bool:
    """Whether another OPEN non-preview assignment was handed this container state (a reset would wipe its work).

    Researcher previews are left out, as they are of exclusive isolation: an abandoned preview must never
    keep a real participant from getting a fresh copy.
    """
    own = str(assignment.pk) if assignment is not None else None
    others = [user for user in _usage(container)["users"] if user != own]
    if not others:
        return False
    return (
        Assignment.objects.filter(pk__in=others, status__in=_OPEN_ASSIGNMENT)
        .exclude(participant__kind=Participant.Kind.PREVIEW)
        .exists()
    )


def needs_reset(task_app: EvalTaskApp, *, assignment: Assignment | None = None) -> bool:
    """Whether the app must be reset before ``assignment`` (or, without one, any new assignment) uses it.

    True under ``hosting.reset == "per_assignment"`` when another assignment was already handed the
    current container state (running or stopped container) and ``assignment`` was not: a state this
    assignment already works in is never reset under it, whoever else was handed it later. DB-only.
    """
    if _hosting(task_app.task).get("reset") != "per_assignment":
        return False
    container = current_container(task_app)
    if container is None or container.status not in (_S.RUNNING, _S.STOPPED):
        return False
    own = str(assignment.pk) if assignment is not None else None
    users = _usage(container)["users"]
    if own is not None and own in users:
        return False
    return any(user != own for user in users)


def _reset_pending(task_app: EvalTaskApp, container: ContainerInstance, assignment: Assignment) -> bool:
    """A reset is due for this assignment and may run now (no other open user, not shared elsewhere)."""
    return (
        needs_reset(task_app, assignment=assignment)
        and not _other_open_users(container, assignment)
        and not _shared_active_elsewhere(container.pk, task_app, timezone.now())
    )


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


def _occupancy(now: datetime, *, task_id: Any = None) -> tuple[set[str], set[str]]:
    """``(slot keys holding an app slot, slot keys holding a build slot)`` among task-started apps.

    Keys are container ids (distinct containers), or ``job:<id>`` for an in-flight start that
    has no container yet. A container with a stop in flight frees its slot.
    """
    cutoff = now - MARKER_TTL
    recent = ContainerAction.objects.filter(status__in=_OPEN_ACTION, created_at__gte=cutoff)
    starting = set(recent.filter(action_type=_A.START).values_list("container_id", flat=True))
    stopping = set(recent.filter(action_type=_A.STOP).values_list("container_id", flat=True))
    qs = EvalTaskApp.objects.filter(Q(started_by_task=True) | Q(start_dispatched_at__gte=cutoff)).filter(
        Q(container__status__in=_ACTIVE) | Q(start_dispatched_at__gte=cutoff) | Q(container_id__in=starting),
    )
    if task_id is not None:
        qs = qs.filter(task_id=task_id)
    running: set[str] = set()
    builds: set[str] = set()
    rows = qs.values_list(
        "container_id",
        "job_id",
        "pk",
        "start_dispatched_at",
        "container__status",
        "container__created_at",
    )
    for container_id, job_id, app_id, marker, status, created_at in rows:
        marker_on = marker is not None and marker >= cutoff
        if container_id is None:
            if marker_on:
                key = f"job:{job_id or app_id}"
                running.add(key)
                builds.add(key)
            continue
        if container_id in stopping and not marker_on:
            continue
        key = str(container_id)
        if status == _RUNNING:
            running.add(key)
        elif status in (_S.PENDING, _S.BUILDING):
            fresh = created_at is not None and created_at >= now - BUILD_STALE_AFTER
            if fresh or marker_on:
                running.add(key)
            if fresh:
                builds.add(key)
        elif marker_on or container_id in starting:
            running.add(key)
    return running, builds


def global_running_count() -> int:
    """App slots in use server-wide (the number checked against ``HUMAN_EVAL_MAX_RUNNING_APPS``)."""
    return len(_occupancy(timezone.now())[0])


def _blocked(task: EvalTask, *, needs_build: bool, now: datetime, freed: set[str]) -> tuple[str, int, int] | None:
    """Which cap a new start would exceed: ``(reason, used, cap)`` or None when it fits."""
    running, builds = _occupancy(now)
    running -= freed
    if len(running) >= _global_cap():
        return "global", len(running), _global_cap()
    task_running = _occupancy(now, task_id=task.pk)[0] - freed
    task_cap = int(_hosting(task).get("max_running_apps") or 1)
    if len(task_running) >= task_cap:
        return "task", len(task_running), task_cap
    if needs_build and len(builds) >= _build_cap():
        return "build", len(builds), _build_cap()
    return None


def _evict_one(task: EvalTask | None, exclude_app: EvalTaskApp, now: datetime, skip: set[str]) -> str | None:
    """Stop the least-recently-active evictable app (of ``task`` when given); returns its slot key."""
    qs = (
        EvalTaskApp.objects.filter(started_by_task=True, pinned=False, container__status=_RUNNING)
        .filter(Q(last_activity_at__isnull=True) | Q(last_activity_at__lt=now - EVICT_IDLE_AFTER))
        .exclude(pk=exclude_app.pk)
        .select_related("container", "task")
        .order_by(F("last_activity_at").asc(nulls_first=True), "container__updated_at")
    )
    if task is not None:
        qs = qs.filter(task=task)
    stopping = _stopping_container_ids(now)
    for candidate in qs[:50]:
        key = str(candidate.container_id)
        if key in skip or candidate.container_id in stopping or candidate.container_id == exclude_app.container_id:
            continue
        busy_sharer = (
            EvalTaskApp.objects.filter(container_id=candidate.container_id)
            .exclude(pk=candidate.pk)
            .filter(Q(pinned=True) | Q(last_activity_at__gte=now - EVICT_IDLE_AFTER))
            .exists()
        )
        if busy_sharer or _shared_active_elsewhere(candidate.container_id, candidate, now):
            continue
        logger.info("human_eval: evicting idle app %s (container %s)", candidate.pk, candidate.container.name)
        _dispatch_stop(candidate.container, None)
        return key
    return None


# ---------------------------------------------------------------------------
# on_commit dispatch
# ---------------------------------------------------------------------------


def _build_callback(app_id: Any, run_id: Any, user_id: int | None) -> None:
    from backend.generation.models import GenerationRun

    if not EvalTaskApp.objects.filter(pk=app_id).exists():
        return  # deleted (task deleted, app removed) before its build was dispatched
    try:
        run = GenerationRun.objects.get(pk=run_id)
        container = container_service.build_for_job(run, _user(user_id))
    except Exception as exc:
        logger.exception("human_eval: build of app %s could not be dispatched", app_id)
        EvalTaskApp.objects.filter(pk=app_id).update(hosting_error=_error_text(exc), start_dispatched_at=None)
        return
    if not EvalTaskApp.objects.filter(pk=app_id).update(container=container, start_dispatched_at=None):
        # The app was deleted while its build was being dispatched: nothing tracks this container any more.
        _spawn_release_worker([(container.pk, user_id)])


def _start_callback(app_id: Any, container_id: Any, user_id: int | None) -> None:
    if not EvalTaskApp.objects.filter(pk=app_id).exists():
        return  # deleted before its start was dispatched
    try:
        container = ContainerInstance.objects.get(pk=container_id)
        container_service.start_instance(container, _user(user_id))
    except Exception as exc:
        logger.exception("human_eval: start of app %s could not be dispatched", app_id)
        EvalTaskApp.objects.filter(pk=app_id).update(hosting_error=_error_text(exc), start_dispatched_at=None)
        return
    if not EvalTaskApp.objects.filter(pk=app_id).update(start_dispatched_at=None):
        _spawn_release_worker([(container_id, user_id)])  # deleted while the start was being dispatched


def _stop_callback(container_id: Any, user_id: int | None) -> None:
    try:
        container = ContainerInstance.objects.get(pk=container_id)
        if container.status == _RUNNING:
            container_service.stop_instance(container, _user(user_id))
    except Exception:
        logger.exception("human_eval: stop of container %s could not be dispatched", container_id)


def _remove_callback(container_id: Any, user_id: int | None) -> None:
    try:
        container = ContainerInstance.objects.get(pk=container_id)
        if container.status != _S.REMOVED:
            container_service.remove_instance(container, _user(user_id))
    except Exception:
        logger.exception("human_eval: removal of container %s could not be dispatched", container_id)


def _dispatch_build(task_app: EvalTaskApp, user_id: int | None) -> None:
    transaction.on_commit(partial(_build_callback, task_app.pk, task_app.job_id, user_id))


def _dispatch_start(task_app: EvalTaskApp, container: ContainerInstance, user_id: int | None) -> None:
    transaction.on_commit(partial(_start_callback, task_app.pk, container.pk, user_id))


def _disown(container_id: Any) -> None:
    """The container's current run ends: no task owns it any more (only a new task start claims it again).

    Without this a task would keep "owning" the container after it stopped it, and its reaper (closed task:
    at once; open task: after its stale idle window) would stop the next run someone else starts.
    """
    EvalTaskApp.objects.filter(container_id=container_id, started_by_task=True).update(started_by_task=False)


def _dispatch_stop(container: ContainerInstance, user_id: int | None) -> None:
    _disown(container.pk)
    transaction.on_commit(partial(_stop_callback, container.pk, user_id))


def _dispatch_remove(container: ContainerInstance, user_id: int | None) -> None:
    _disown(container.pk)
    transaction.on_commit(partial(_remove_callback, container.pk, user_id))


def _dispatch_reset(task_app: EvalTaskApp, container: ContainerInstance, assignment: Assignment | None) -> None:
    args = (task_app.pk, container.pk, str(assignment.pk) if assignment is not None else "")
    transaction.on_commit(
        partial(dispatch_in_thread, _reset_worker, *args, name=f"human-eval-reset-{container.name}"),
    )


# ---------------------------------------------------------------------------
# Reset (per-assignment app state isolation)
# ---------------------------------------------------------------------------


def _publish(container: ContainerInstance) -> None:
    try:
        container_service.publish_container_status(container)
    except Exception:
        logger.exception("human_eval: could not publish the status of %s", container.name)


def _reset_container(container_id: Any, assignment_id: str) -> str:
    """Recreate the container from its existing image; returns "" or an error message (worker thread)."""
    container = ContainerInstance.objects.get(pk=container_id)
    image = container.image or f"backend/{container.name}:latest"
    if not docker_manager.image_exists(image):
        container.status = _S.FAILED
        container.save(update_fields=["status"])
        _publish(container)
        return "The app's image is missing; rebuild the app"
    container.status = _S.PENDING
    container.save(update_fields=["status"])
    _publish(container)
    removed = docker_manager.remove(container.name, force=True)
    error = removed.get("error", "")
    if error and not any(marker in error for marker in ("404", "No such container", "Not Found")):
        container.status = _S.FAILED
        container.save(update_fields=["status"])
        _publish(container)
        return f"Could not remove the old container: {error}"
    network = getattr(settings, "DOCKER_APPS_NETWORK", "")
    ports = {"8000/tcp": container.app_port} if container.app_port and not network else {}
    docker_id = docker_manager.run_container(image, container.name, ports, {}, str(container.pk), network=network)
    container.container_id = docker_id
    container.save(update_fields=["container_id"])
    ok, detail = _verify_running(container)
    if not ok:
        container.status = _S.FAILED
        container.save(update_fields=["status"])
        traefik_router.delete_route(container)
        _publish(container)
        return _error_text(detail[-2000:] or "The app exited right after the reset")
    metadata = dict(container.metadata or {})
    metadata[META_KEY] = {
        "docker_id": docker_id,
        "reset_at": timezone.now().isoformat(),
        "users": [assignment_id] if assignment_id else [],
    }
    container.metadata = metadata
    container.status = _RUNNING
    container.save(update_fields=["status", "metadata"])
    traefik_router.write_route(container)
    _publish(container)
    return ""


def _reset_worker(app_id: Any, container_id: Any, assignment_id: str) -> None:
    """Thread body of a reset: recreate the container, then clear the in-flight marker (and record errors)."""
    try:
        try:
            error = _reset_container(container_id, assignment_id)
        except Exception as exc:
            logger.exception("human_eval: reset of container %s failed", container_id)
            error = _error_text(exc) or "The reset failed"
            ContainerInstance.objects.filter(pk=container_id).exclude(status=_S.REMOVED).update(status=_S.FAILED)
        updates: dict[str, Any] = {"start_dispatched_at": None}
        if error:
            updates["hosting_error"] = _error_text(f"Reset failed: {error}")
        if not EvalTaskApp.objects.filter(pk=app_id).update(**updates):
            # The app was deleted during the reset: the recreated container belongs to nobody.
            _release_settled(container_id, None)
    except Exception:
        logger.exception("human_eval: reset bookkeeping of app %s failed", app_id)
    finally:
        _close_connection()


def reset_app(task_app: EvalTaskApp, *, assignment: Assignment | None = None) -> None:
    """Recreate the app's container from its existing image (same name, port and network; no rebuild).

    Dispatched on commit to a worker thread; the app shows ``starting`` until it is running again.
    Only a RUNNING container is reset here (it already holds an app slot); a stopped one is reset by
    :func:`ensure_running` under the capacity rules. 409 ``not_running`` without a running container,
    409 ``shared_container`` when another open/paused task's app on the container is pinned or active,
    409 ``hosting_busy`` while a start/stop/reset is in flight. ``assignment`` becomes the first user
    of the new state.
    """
    now = timezone.now()
    container = current_container(task_app)
    if container is None or container.status != _RUNNING:
        msg = "The app is not running; start it first"
        raise conflict(msg, "not_running")
    if _shared_active_elsewhere(container.pk, task_app, now):
        msg = "Another open task is using this app container"
        raise conflict(msg, "shared_container")
    if _marker_on(task_app, now) or _open_actions(container, now):
        msg = "The app is busy starting or stopping; try again shortly"
        raise conflict(msg, "hosting_busy")
    with transaction.atomic():
        locked = _lock_app(task_app.pk)
        _begin_reset(locked, container, assignment, now)


def _begin_reset(task_app: EvalTaskApp, container: ContainerInstance, assignment: Assignment | None, now) -> None:
    if task_app.container_id != container.pk:
        task_app.container = container
    task_app.started_by_task = True
    task_app.start_dispatched_at = now
    task_app.last_activity_at = now
    task_app.hosting_requested_at = None
    task_app.hosting_error = ""
    task_app.save(
        update_fields=[
            "container",
            "started_by_task",
            "start_dispatched_at",
            "last_activity_at",
            "hosting_requested_at",
            "hosting_error",
        ],
    )
    _dispatch_reset(task_app, container, assignment)


# ---------------------------------------------------------------------------
# Hosting info (DB-only)
# ---------------------------------------------------------------------------


class _InfoContext:
    """Per-call memo of the global numbers shared by several apps' info (occupancy, queue order)."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self._running: int | None = None
        self._queue: dict[Any, int] | None = None

    def running(self) -> int:
        if self._running is None:
            self._running = len(_occupancy(self.now)[0])
        return self._running

    def queue_position(self, task_app: EvalTaskApp) -> int:
        if self._queue is None:
            ordered = EvalTaskApp.objects.filter(hosting_requested_at__isnull=False).order_by(
                "hosting_requested_at",
                "pk",
            )
            self._queue = {pk: index + 1 for index, pk in enumerate(ordered.values_list("pk", flat=True))}
        return self._queue.get(task_app.pk, len(self._queue) + 1)


def _queue_message(task_app: EvalTaskApp, ctx: _InfoContext) -> str:
    position = ctx.queue_position(task_app)
    return f"Waiting for a free app slot ({ctx.running()} of {_global_cap()} in use) — number {position} in the queue"


def _state(task_app: EvalTaskApp, ctx: _InfoContext) -> dict[str, Any]:
    """The real hosting state: ``{state, progress, message, error, container}`` (``container`` may be a failed one)."""
    now = ctx.now
    # Marker without FK: this app's build is being dispatched (e.g. a rebuild); older containers of the
    # job are not its own.
    in_flight_build = task_app.container_id is None and _marker_on(task_app, now)
    container = None if in_flight_build else current_container(task_app)
    failed = _failed_container(task_app)
    stuck = False
    if container is not None and _stuck(container, task_app, now):
        failed, container, stuck = container, None, True
    # The in-flight marker is this app's own start/reset unless the container is another app's (adopted later).
    marker = _marker_on(task_app, now) and (container is None or task_app.container_id == container.pk)
    actions = _open_actions(container, now)
    result: dict[str, Any] = {"state": "idle", "progress": None, "message": "", "error": "", "container": container}
    if container is not None and container.status == _RUNNING:
        if _A.STOP in actions:
            result.update(state="stopping", message=MSG_STOPPING)
        elif marker:
            result.update(state="starting", message=MSG_RESETTING)
        else:
            result.update(state="running")
    elif container is not None and container.status in (_S.BUILDING, _S.PENDING):
        build = actions.get(_A.BUILD)
        if container.status == _S.BUILDING or build is not None:
            if build is None:
                build = container.actions.filter(action_type=_A.BUILD).order_by("-created_at").first()
            result.update(state="building", message=MSG_BUILDING, progress=build.progress_percent if build else 0)
        else:
            result.update(state="starting", message=MSG_RESETTING if marker else MSG_STARTING)
    elif (container is not None and (_A.START in actions or marker)) or marker:
        result.update(state="starting", message=MSG_STARTING)
    elif task_app.hosting_requested_at is not None:
        result.update(state="queued", message=_queue_message(task_app, ctx))
    elif task_app.hosting_error or failed is not None:
        error = task_app.hosting_error or (MSG_STUCK if stuck else _last_error(failed))
        result.update(state="failed", message="The app could not be started", error=_error_text(error))
        result["container"] = container or failed
    elif start_error := _failed_start(container, now):
        result.update(state="failed", message="The app could not be started", error=start_error)
    return result


#: Error texts that mean the hosting platform (not the app) failed: daemon down, container gone from Docker.
_PLATFORM_ERRORS = ("daemon", "no such container")


def _participant_error(error: str) -> str:
    """Participants never see raw build/Docker output (it can name containers or code)."""
    lowered = error.lower()
    if any(marker in lowered for marker in _PLATFORM_ERRORS):
        return PARTICIPANT_ERROR_PLATFORM
    return PARTICIPANT_ERROR_APP


def participant_hosting_info(task_app: EvalTaskApp, *, assignment: Assignment | None = None) -> dict[str, Any]:
    """TS ``HostingInfo`` for participants (DB-only; no container ids/names except the app URL host).

    ``unavailable`` when apps are not reachable from the portal. With ``assignment``, an app whose
    container state must first be reset for it is reported ``idle`` without a URL, so the
    participant's ensure call performs the reset before they see the app.
    """
    if not apps_reachable():
        return {"state": "unavailable", "url": None, "progress": None, "message": MSG_UNREACHABLE, "error": ""}
    raw = _state(task_app, _InfoContext(timezone.now()))
    state, message, url = raw["state"], raw["message"], None
    container = raw["container"]
    if state == "running":
        if assignment is not None and _reset_pending(task_app, container, assignment):
            state, message = "idle", MSG_FRESH_COPY
        else:
            url = _app_url(container)
    if state == "idle" and not message:
        hosting = _hosting(task_app.task)
        message = MSG_IDLE if hosting.get("on_demand") or task_app.pinned else MSG_NOT_STARTED
    return {
        "state": state,
        "url": url,
        "progress": raw["progress"],
        "message": message,
        "error": _participant_error(raw["error"]) if state == "failed" else "",
    }


def researcher_hosting_info(task_app: EvalTaskApp, *, ctx: _InfoContext | None = None) -> dict[str, Any]:
    """TS ``ResearcherHostingInfo`` (DB-only): the real container state even when participants cannot reach apps."""
    raw = _state(task_app, ctx or _InfoContext(timezone.now()))
    container = raw["container"]
    message = raw["message"]
    if raw["state"] == "idle":
        if _hosting(task_app.task).get("on_demand"):
            message = "Not running — starts when a participant opens it"
        else:
            message = "Not running — on-demand starts are off; start it here"
    return {
        "state": raw["state"],
        "url": _app_url(container) if raw["state"] == "running" and container is not None else None,
        "progress": raw["progress"],
        "message": message,
        "error": raw["error"],
        "container_id": str(container.pk) if container is not None else None,
        "container_name": container.name if container is not None else None,
        "container_status": container.status if container is not None else None,
        "started_by_task": bool(task_app.started_by_task),
        "pinned": bool(task_app.pinned),
        "last_activity_at": _iso(task_app.last_activity_at),
    }


def environment_info() -> dict[str, Any]:
    """TS ``HumanEvalEnvironment``: routing, caps, warnings, design defaults, instruments and checklist."""
    from backend.human_eval.services.access import shared_client_ip
    from backend.human_eval.services.checklists import default_security_checklist
    from backend.human_eval.services.instruments import environment_instruments

    reachable = apps_reachable()
    warnings: list[str] = []
    if not reachable:
        warnings.append("apps_not_reachable")
    if shared_client_ip():
        warnings.append("shared_client_ip")
    return {
        "apps_reachable": reachable,
        "warnings": warnings,
        "routing_mode": routing_mode(),
        "global_max_running_apps": _global_cap(),
        "global_running_apps": global_running_count(),
        "max_concurrent_builds": _build_cap(),
        "default_settings": design_service.default_settings(),
        "default_design": design_service.default_design("single"),
        "security_checklist_default": default_security_checklist(),
        "instruments": environment_instruments(),
    }


# ---------------------------------------------------------------------------
# Starting apps
# ---------------------------------------------------------------------------


def _lock_app(pk: Any) -> EvalTaskApp:
    return EvalTaskApp.objects.select_for_update(of=("self",)).select_related("task", "job", "container").get(pk=pk)


def _save(task_app: EvalTaskApp, *fields: str) -> None:
    task_app.save(update_fields=list(dict.fromkeys(fields)))


def _queue(task_app: EvalTaskApp, now: datetime) -> str:
    """Join (or stay in) the FIFO queue; the poll keeps the request alive (``last_activity_at``)."""
    if task_app.hosting_requested_at is None:
        task_app.hosting_requested_at = now
    task_app.last_activity_at = now
    _save(task_app, "hosting_requested_at", "last_activity_at")
    return "queued"


def _ahead_in_queue(task_app: EvalTaskApp) -> Any:
    qs = EvalTaskApp.objects.filter(hosting_requested_at__isnull=False).exclude(pk=task_app.pk)
    mine = task_app.hosting_requested_at
    if mine is not None:
        qs = qs.filter(Q(hosting_requested_at__lt=mine) | Q(hosting_requested_at=mine, pk__lt=task_app.pk))
    return qs


def _ensure_locked(  # noqa: PLR0911
    task_app: EvalTaskApp,
    *,
    actor: Any = None,
    pin: bool = False,
    assignment: Assignment | None = None,
    force_build: bool = False,
    exclude: Iterable[Any] = (),
    allow_evict: bool = True,
    from_queue: bool = False,
) -> str:
    """Start, reset or queue the (row-locked) app if needed. Returns an outcome slug (for callers and tests).

    Outcomes: ``running``, ``reset``, ``dispatched``, ``in_flight``, ``queued``, ``unreachable``,
    ``on_demand_off``, ``no_code``, ``failed``, ``closed``.
    """
    now = timezone.now()
    task = task_app.task
    participant = _is_participant(actor)
    if pin and not task_app.pinned:
        task_app.pinned = True
        _save(task_app, "pinned")
    if task.status in _CLOSED_TASK:
        return "closed"
    if participant and not apps_reachable():
        return "unreachable"
    container = None if force_build else current_container(task_app, exclude=exclude)
    if container is not None and _stuck(container, task_app, now):
        container = None
    if container is not None and task_app.container_id != container.pk:
        # Adopt the run's existing container; it only counts as task-started once this task starts it. A marker
        # left while waiting for another task's build of this run is done with.
        task_app.container = container
        task_app.started_by_task = False
        task_app.start_dispatched_at = None
        _save(task_app, "container", "started_by_task", "start_dispatched_at")
    actions = _open_actions(container, now)
    if container is not None and container.status == _RUNNING:
        if _A.STOP in actions or _marker_on(task_app, now):
            return "in_flight"
        if assignment is not None and _reset_pending(task_app, container, assignment):
            _begin_reset(task_app, container, assignment, now)
            return "reset"
        touch([task_app])
        if task_app.hosting_requested_at is not None:
            task_app.hosting_requested_at = None
            _save(task_app, "hosting_requested_at")
        return "running"
    if _marker_on(task_app, now) or (container is not None and (container.status != _S.STOPPED or actions)):
        return "in_flight"
    hosting = _hosting(task)
    if participant and not hosting.get("on_demand") and not task_app.pinned:
        return "on_demand_off"
    if task_app.job_id is None or not _has_code(task_app.job):
        if task_app.hosting_error != MSG_NO_CODE:
            task_app.hosting_error = MSG_NO_CODE
            _save(task_app, "hosting_error")
        return "no_code"
    if participant and container is None and (task_app.hosting_error or _failed_container(task_app) is not None):
        # A failed app is not rebuilt on every participant click; the researcher restarts or rebuilds it.
        return "failed"
    if participant and _failed_start(container, now):
        # Nor is a container whose start just failed restarted on every poll (MARKER_TTL later it may be).
        return "failed"
    needs_build = container is None
    if needs_build and not force_build:
        building_elsewhere = (
            EvalTaskApp.objects.filter(
                job_id=task_app.job_id,
                container__isnull=True,
                start_dispatched_at__gte=now - MARKER_TTL,
            )
            .exclude(pk=task_app.pk)
            .exists()
        )
        if building_elsewhere:
            # Another task is already building this run: wait for its container (adopted on the next call).
            task_app.start_dispatched_at = now
            _save(task_app, "start_dispatched_at")
            return "in_flight"
    if not from_queue and _ahead_in_queue(task_app).exists():
        _advance_queue_locked(now, before=task_app)
    freed: set[str] = set()
    blocked = _blocked(task, needs_build=needs_build, now=now, freed=freed)
    while blocked is not None and blocked[0] in ("global", "task") and allow_evict:
        victim = _evict_one(task if blocked[0] == "task" else None, task_app, now, freed)
        if victim is None:
            break
        freed.add(victim)
        blocked = _blocked(task, needs_build=needs_build, now=now, freed=freed)
    if blocked is not None:
        return _queue(task_app, now)
    reset = container is not None and assignment is not None and _reset_pending(task_app, container, assignment)
    task_app.started_by_task = True
    task_app.hosting_requested_at = None
    task_app.hosting_error = ""
    task_app.start_dispatched_at = now
    task_app.last_activity_at = now
    started_fields = ("started_by_task", "hosting_requested_at", "hosting_error", "start_dispatched_at")
    _save(task_app, *started_fields, "last_activity_at")
    user_id = _container_user_id(actor, task)
    if reset:
        _dispatch_reset(task_app, container, assignment)
        return "reset"
    if needs_build:
        _dispatch_build(task_app, user_id)
    else:
        _dispatch_start(task_app, container, user_id)
    return "dispatched"


def ensure_running(
    task_app: EvalTaskApp,
    *,
    actor: Any = None,
    pin: bool = False,
    assignment: Assignment | None = None,
) -> dict[str, Any]:
    """Start (or queue, or reset for ``assignment``) the app if needed and return its hosting info.

    ``actor`` is the requesting Participant (participant rules: unreachable routing → nothing
    started; ``hosting.on_demand`` false and not pinned → idle "ask the researcher"; failed apps are
    not retried) or the researcher User (same capacity rules). Returns TS ``HostingInfo`` for
    participants, ``ResearcherHostingInfo`` otherwise. Container work is dispatched on commit.

    The lazy reaper runs after the app was handled: its stops only happen on commit, so running it first
    could stop the very app this call then reports running (the app is touched, so it is not idle now).
    """
    with transaction.atomic():
        _capacity_lock()
        locked = _lock_app(task_app.pk)
        _ensure_locked(locked, actor=actor, pin=pin, assignment=assignment)
        lazy_reap()
    if _is_participant(actor):
        return participant_hosting_info(locked, assignment=assignment)
    return researcher_hosting_info(locked)


def _advance_queue_locked(now: datetime, *, before: EvalTaskApp | None = None, limit: int = 200) -> int:
    """Start queued apps in FIFO order while capacity allows (caller holds the capacity lock).

    Rows locked by other transactions are skipped. ``before`` restricts the pass to the apps queued
    ahead of that app. Stops at the first app blocked by the server-wide cap.
    """
    qs = EvalTaskApp.objects.filter(hosting_requested_at__isnull=False)
    if before is not None:
        qs = _ahead_in_queue(before)
    started = 0
    for pk in list(qs.order_by("hosting_requested_at", "pk").values_list("pk", flat=True)[:limit]):
        locked = (
            EvalTaskApp.objects.select_for_update(of=("self",), skip_locked=True)
            .select_related("task", "job", "container")
            .filter(pk=pk, hosting_requested_at__isnull=False)
            .first()
        )
        if locked is None:
            continue
        if locked.task.status in _CLOSED_TASK:
            locked.hosting_requested_at = None
            _save(locked, "hosting_requested_at")
            continue
        outcome = _ensure_locked(locked, allow_evict=False, from_queue=True)
        if outcome in ("dispatched", "reset"):
            started += 1
        elif outcome == "queued" and len(_occupancy(now)[0]) >= _global_cap():
            break
        elif outcome not in ("queued", "in_flight"):
            locked.hosting_requested_at = None
            _save(locked, "hosting_requested_at")
    return started


def advance_queue() -> int:
    """Start queued apps (FIFO) while capacity allows; returns how many were dispatched."""
    with transaction.atomic():
        _capacity_lock()
        return _advance_queue_locked(timezone.now())


# ---------------------------------------------------------------------------
# Stopping, touching, reaping
# ---------------------------------------------------------------------------


def _stop_locked(task_app: EvalTaskApp, *, user_id: int | None, force: bool, now: datetime) -> bool:
    """Dispatch a stop of the app's running container; returns whether one was dispatched.

    Without ``force`` only the app's own FK container is stopped, and only while the task owns its run:
    another running ``llm-*`` container of the run that :func:`current_container` prefers was never
    started by this task.
    """
    if task_app.hosting_requested_at is not None:
        task_app.hosting_requested_at = None
        _save(task_app, "hosting_requested_at")
    container = current_container(task_app)
    if container is None or container.status != _RUNNING:
        return False
    if not force and (
        not task_app.started_by_task
        or container.pk != task_app.container_id
        or _shared_active_elsewhere(container.pk, task_app, now)
    ):
        return False
    if _A.STOP in _open_actions(container, now):
        return False
    _dispatch_stop(container, user_id)
    if task_app.container_id == container.pk:
        task_app.started_by_task = False  # _dispatch_stop disowned it in the DB
    return True


def stop_app(task_app: EvalTaskApp, *, actor: Any = None, force: bool = False) -> None:
    """Stop the app's container (on_commit) and drop its queue request.

    Without ``force`` only a task-started container that no other open/paused task is using
    (pinned or active) is stopped; ``force`` (the researcher asked) stops it regardless.
    """
    with transaction.atomic():
        locked = _lock_app(task_app.pk)
        _stop_locked(locked, user_id=_container_user_id(actor, locked.task), force=force, now=timezone.now())


def stop_task_apps(task: EvalTask) -> int:
    """Stop every task-started, unpinned app of the task (on_commit; shared containers are left alone).

    Also drops the task's queue requests. Returns how many containers were stopped. Called on close
    and before deletion (the callbacks only need container ids).
    """
    now = timezone.now()
    stopped: set[Any] = set()
    with transaction.atomic():
        EvalTaskApp.objects.filter(task=task, hosting_requested_at__isnull=False).update(hosting_requested_at=None)
        apps = EvalTaskApp.objects.filter(task=task, started_by_task=True, pinned=False).select_related(
            "task",
            "job",
            "container",
        )
        for task_app in apps:
            if task_app.container_id in stopped:
                continue
            if _stop_locked(task_app, user_id=task.created_by_id, force=False, now=now):
                stopped.add(task_app.container_id)
    return len(stopped)


def _claimed_elsewhere(container_id: Any, exclude: Iterable[Any], now: datetime) -> bool:
    """Whether an app other than ``exclude`` still owns or uses the container (a release then leaves it).

    It owns it when it started it (``started_by_task``) or has a start/reset in flight (its task's reaper
    keeps managing it); an open/paused task's app uses it when pinned or active within its idle window
    (R3.8).
    """
    others = (
        EvalTaskApp.objects.filter(container_id=container_id)
        .exclude(pk__in=[pk for pk in exclude if pk is not None])
        .select_related("task")
    )
    for other in others:
        if other.started_by_task or _marker_on(other, now):
            return True
        if other.task.status not in _LIVE_TASK:
            continue
        if other.pinned:
            return True
        idle = timedelta(minutes=int(_hosting(other.task).get("idle_stop_minutes") or 20))
        if other.last_activity_at is not None and other.last_activity_at >= now - idle:
            return True
    return False


def _in_flight(container: ContainerInstance, task_app: EvalTaskApp, now: datetime) -> bool:
    """Whether a build, start or reset of the app's container has not finished yet (it will come up)."""
    if _marker_on(task_app, now):
        return True
    if container.status in (_S.PENDING, _S.BUILDING):
        return not _stuck(container, task_app, now)
    actions = _open_actions(container, now)
    return any(kind in actions for kind in _STARTING_ACTIONS)


def _release_settled(container_id: Any, user_id: int | None) -> bool:
    """One release check of a container: stop it once nothing brings it up any more; True when done.

    Done when the container is gone, failed or stopped, or was stopped now. A container another app
    still owns or uses is left to that app (:func:`_claimed_elsewhere`).
    """
    now = timezone.now()
    container = ContainerInstance.objects.filter(pk=container_id).first()
    if container is None:
        return True
    actions = _open_actions(container, now)
    if container.status in (_S.PENDING, _S.BUILDING) or any(kind in actions for kind in _STARTING_ACTIONS):
        return False
    if container.status == _RUNNING and _A.STOP not in actions and not _claimed_elsewhere(container.pk, (), now):
        logger.info("human_eval: stopping released container %s", container.name)
        container_service.stop_instance(container, _user(user_id))
    return True


def _release_worker(targets: list[tuple[Any, int | None]]) -> None:
    """Thread body: stop released containers once the build/start/reset in flight on them finished."""
    try:
        pending = dict(targets)
        deadline = time.monotonic() + RELEASE_GIVE_UP_AFTER.total_seconds()
        while pending and time.monotonic() < deadline:
            time.sleep(RELEASE_POLL_SECONDS)
            for container_id, user_id in list(pending.items()):
                try:
                    done = _release_settled(container_id, user_id)
                except Exception:
                    logger.exception("human_eval: release check of container %s failed", container_id)
                    done = False
                if done:
                    del pending[container_id]
        if pending:
            logger.warning("human_eval: released containers still in flight after the deadline: %s", list(pending))
    finally:
        _close_connection()


def _spawn_release_worker(targets: list[tuple[Any, int | None]]) -> None:
    """Start a release worker now (callers are outside the request transaction: callbacks, workers)."""
    try:
        dispatch_in_thread(_release_worker, targets, name="human-eval-release")
    except Exception:
        logger.exception("human_eval: release worker could not be started for %s", targets)


def release_apps(task_apps: Iterable[EvalTaskApp]) -> int:
    """Release the hosting of apps that are about to be deleted (task deletion, a draft's app removal).

    Every container one of the apps started (``started_by_task`` or an in-flight marker), pinned or not, is
    stopped on commit unless another app still owns or uses it (:func:`_claimed_elsewhere`; R3.8). One
    whose build, start or reset is still in flight is stopped by a worker thread once it is up (a
    stop now would be lost: the runtime only stops running containers). Containers the apps merely
    adopted are left alone. Queue requests, pins, ownership and in-flight markers are cleared (row-locked).
    Returns how many containers are being stopped.
    """
    pks = [app.pk for app in task_apps if app is not None and app.pk is not None]
    if not pks:
        return 0
    now = timezone.now()
    seen: set[Any] = set()
    stopped = 0
    deferred: list[tuple[Any, int | None]] = []
    with transaction.atomic():
        apps = list(
            EvalTaskApp.objects.select_for_update(of=("self",))
            .select_related("task", "container")
            .filter(pk__in=pks)
            .order_by("pk"),
        )
        for task_app in apps:
            container = task_app.container if task_app.container_id else None
            # FK-less marker: a build being dispatched (its callback finds the app gone) or a wait for
            # another task's build of the run (not this task's container).
            if container is None or container.pk in seen:
                continue
            if not (task_app.started_by_task or _marker_on(task_app, now)):
                continue
            seen.add(container.pk)
            if container.status in (_S.REMOVED, _S.FAILED) or _claimed_elsewhere(container.pk, pks, now):
                continue
            user_id = task_app.task.created_by_id
            if _in_flight(container, task_app, now):
                deferred.append((container.pk, user_id))
            elif container.status == _RUNNING and _A.STOP not in _open_actions(container, now):
                _dispatch_stop(container, user_id)
                stopped += 1
        EvalTaskApp.objects.filter(pk__in=pks).update(
            started_by_task=False,
            pinned=False,
            start_dispatched_at=None,
            hosting_requested_at=None,
        )
        if deferred:
            transaction.on_commit(partial(_spawn_release_worker, deferred))
    return stopped + len(deferred)


def touch(task_apps: Any) -> None:
    """Set ``last_activity_at = now`` on the apps and every EvalTaskApp sharing their containers.

    Accepts one EvalTaskApp or an iterable of them; writes at most every ``TOUCH_THROTTLE`` per row.
    """
    apps = [task_apps] if isinstance(task_apps, EvalTaskApp) else [app for app in task_apps or [] if app is not None]
    if not apps:
        return
    now = timezone.now()
    condition = Q(pk__in={app.pk for app in apps})
    containers = {app.container_id for app in apps if app.container_id}
    if containers:
        condition |= Q(container_id__in=containers)
    EvalTaskApp.objects.filter(condition).filter(
        Q(last_activity_at__isnull=True) | Q(last_activity_at__lt=now - TOUCH_THROTTLE),
    ).update(last_activity_at=now)
    for app in apps:
        app.last_activity_at = now


def reap_idle_apps() -> int:
    """Stop idle task-started unpinned apps (and apps of closed tasks), clear stale markers, advance the queue.

    - stale in-flight markers (> 10 min) are cleared;
    - queue requests of closed/archived tasks, and unpinned ones nobody polled for 10 min, are dropped;
    - ownership of runs that ended outside human-eval is dropped: ``started_by_task`` is cleared on apps
      whose container is stopped/removed/failed with no start of it in flight (stops by human-eval
      clear it at once), so a later start from elsewhere is never this task's to stop;
    - a task-started RUNNING container is stopped when its task is closed/archived, or when the app is
      unpinned and its ``last_activity_at`` (else the container's ``updated_at``) is older than the
      task's ``hosting.idle_stop_minutes`` — unless another open/paused task's app on it is pinned or
      active (R3.8). Adopted containers (``started_by_task`` false) are never stopped.

    Returns how many containers were stopped.
    """
    now = timezone.now()
    stopped: set[Any] = set()
    with transaction.atomic():
        _capacity_lock()
        EvalTaskApp.objects.filter(start_dispatched_at__lt=now - MARKER_TTL).update(start_dispatched_at=None)
        starting = ContainerAction.objects.filter(
            action_type__in=_STARTING_ACTIONS,
            status__in=_OPEN_ACTION,
            created_at__gte=now - MARKER_TTL,
        ).values("container_id")
        EvalTaskApp.objects.filter(
            started_by_task=True,
            container__status__in=(_S.STOPPED, _S.REMOVED, _S.FAILED),
        ).filter(Q(start_dispatched_at__isnull=True) | Q(start_dispatched_at__lt=now - MARKER_TTL)).exclude(
            container_id__in=starting,
        ).update(started_by_task=False)
        abandoned = Q(pinned=False) & (
            Q(last_activity_at__lt=now - QUEUE_ABANDON_AFTER)
            | Q(last_activity_at__isnull=True, hosting_requested_at__lt=now - QUEUE_ABANDON_AFTER)
        )
        EvalTaskApp.objects.filter(hosting_requested_at__isnull=False).filter(
            Q(task__status__in=_CLOSED_TASK) | abandoned,
        ).update(hosting_requested_at=None)
        stopping = _stopping_container_ids(now)
        candidates = EvalTaskApp.objects.filter(started_by_task=True, container__status=_RUNNING).select_related(
            "task",
            "container",
        )
        for task_app in candidates:
            container_id = task_app.container_id
            if container_id in stopped or container_id in stopping:
                continue
            if task_app.task.status not in _CLOSED_TASK:
                if task_app.pinned:
                    continue
                idle = timedelta(minutes=int(_hosting(task_app.task).get("idle_stop_minutes") or 20))
                last = task_app.last_activity_at or task_app.container.updated_at
                if last is not None and last >= now - idle:
                    continue
            if _shared_active_elsewhere(container_id, task_app, now):
                continue
            logger.info("human_eval: stopping idle app %s (container %s)", task_app.pk, task_app.container.name)
            _dispatch_stop(task_app.container, None)
            stopped.add(container_id)
    advance_queue()
    return len(stopped)


def lazy_reap() -> None:
    """Run :func:`reap_idle_apps` at most once per ``LAZY_REAP_SECONDS`` (cache throttle); never raises."""
    try:
        if not cache.add(LAZY_REAP_CACHE_KEY, 1, timeout=LAZY_REAP_SECONDS):
            return
    except Exception:
        logger.exception("human_eval: lazy reaper throttle unavailable")
        return
    try:
        with transaction.atomic():
            reap_idle_apps()
    except Exception:
        logger.exception("human_eval: lazy idle-app reaper failed")


# ---------------------------------------------------------------------------
# Researcher views
# ---------------------------------------------------------------------------


def task_hosting(task: EvalTask) -> dict[str, Any]:
    """TS ``TaskHosting`` for the researcher's hosting panel (runs the lazy reaper first)."""
    lazy_reap()
    now = timezone.now()
    ctx = _InfoContext(now)
    apps = list(
        EvalTaskApp.objects.filter(task=task).select_related("task", "job", "container").order_by("position", "label"),
    )
    rows = [{**researcher_hosting_info(app, ctx=ctx), "app_id": str(app.pk), "label": app.label} for app in apps]
    return {
        "apps": rows,
        "task_max_running": int(_hosting(task).get("max_running_apps") or 0),
        "global_max_running": _global_cap(),
        "global_running": ctx.running(),
        "running": sum(1 for row in rows if row["state"] == "running"),
        "queued": sum(1 for app in apps if app.hosting_requested_at is not None),
        "apps_reachable": apps_reachable(),
    }


def _refuse_closed(task: EvalTask) -> None:
    if task.status in _CLOSED_TASK:
        msg = "The task is closed; reopen it to start apps"
        raise conflict(msg, "task_closed")


def task_hosting_action(task: EvalTask, action: str, *, user: Any) -> dict[str, Any]:
    """``start_all`` (pin + ensure every active app in position order; the rest stay queued) or ``stop_all``
    (unpin + stop every task-started app, shared containers excepted). Returns TS ``TaskHosting``."""
    if action not in ("start_all", "stop_all"):
        msg = "Unknown hosting action"
        raise HumanEvalServiceError(400, msg, "invalid", [{"path": "action", "message": msg}])
    if action == "start_all":
        _refuse_closed(task)
    now = timezone.now()
    with transaction.atomic():
        _capacity_lock()
        apps = EvalTaskApp.objects.filter(task=task).order_by("position", "label")
        if action == "start_all":
            apps = apps.filter(is_active=True)
        for pk in list(apps.values_list("pk", flat=True)):
            locked = _lock_app(pk)
            if action == "start_all":
                _ensure_locked(locked, actor=user, pin=True)
                continue
            if locked.pinned:
                locked.pinned = False
                _save(locked, "pinned")
            if locked.started_by_task:
                _stop_locked(locked, user_id=_container_user_id(user, task), force=False, now=now)
            elif locked.hosting_requested_at is not None:
                locked.hosting_requested_at = None
                _save(locked, "hosting_requested_at")
    # task_hosting runs the lazy reaper, after the pins: a reap before them could stop (on commit) an idle
    # app this call then pins and reports running.
    return task_hosting(task)


def app_hosting_action(task_app: EvalTaskApp, action: str, *, user: Any) -> dict[str, Any]:
    """One researcher action on one app: ``start | stop | pin | unpin | rebuild``. Returns ``ResearcherHostingInfo``.

    - start: ensure_running (same capacity rules; on-demand setting ignored);
    - stop: unpin and stop even if not task-started (force);
    - rebuild: remove the current container (on_commit) and build a new one; 409 ``shared_container``
      when another open task is using it, 409 ``hosting_busy`` while a build/start/stop/reset is in
      flight, 409 ``no_code`` when the run has no code.
    """
    task = task_app.task
    if action == "start":
        _refuse_closed(task)
        return ensure_running(task_app, actor=user)
    if action in ("pin", "unpin"):
        with transaction.atomic():
            locked = _lock_app(task_app.pk)
            locked.pinned = action == "pin"
            _save(locked, "pinned")
        return researcher_hosting_info(locked)
    if action == "stop":
        with transaction.atomic():
            locked = _lock_app(task_app.pk)
            if locked.pinned:
                locked.pinned = False
                _save(locked, "pinned")
            _stop_locked(locked, user_id=_container_user_id(user, task), force=True, now=timezone.now())
        return researcher_hosting_info(locked)
    if action == "rebuild":
        return _rebuild(task_app, user)
    msg = "Unknown hosting action"
    raise HumanEvalServiceError(400, msg, "invalid", [{"path": "action", "message": msg}])


def _rebuild(task_app: EvalTaskApp, user: Any) -> dict[str, Any]:
    task = task_app.task
    _refuse_closed(task)
    now = timezone.now()
    if task_app.job_id is None or not _has_code(task_app.job):
        raise conflict(MSG_NO_CODE, "no_code")
    container = current_container(task_app)
    if container is not None:
        stuck = _stuck(container, task_app, now)
        if not stuck and (_marker_on(task_app, now) or container.status in (_S.PENDING, _S.BUILDING)):
            msg = "The app is being built or started; wait until it finishes"
            raise conflict(msg, "hosting_busy")
        if _open_actions(container, now):
            msg = "The app is busy starting or stopping; try again shortly"
            raise conflict(msg, "hosting_busy")
        if _shared_active_elsewhere(container.pk, task_app, now):
            msg = "Another open task is using this app container"
            raise conflict(msg, "shared_container")
    elif _marker_on(task_app, now):
        msg = "The app is being built or started; wait until it finishes"
        raise conflict(msg, "hosting_busy")
    user_id = _container_user_id(user, task)
    failed = _failed_container(task_app)
    with transaction.atomic():
        _capacity_lock()
        locked = _lock_app(task_app.pk)
        if container is not None:
            _dispatch_remove(container, user_id)
        if failed is not None and failed != container:
            # Clean up the failed container too (its image and host port).
            _dispatch_remove(failed, user_id)
        locked.container = None
        locked.started_by_task = False
        locked.hosting_error = ""
        _save(locked, "container", "started_by_task", "hosting_error")
        _ensure_locked(locked, actor=user, force_build=True, exclude=[container.pk] if container else [])
    return researcher_hosting_info(locked)


# ---------------------------------------------------------------------------
# Portal ensure and heartbeat
# ---------------------------------------------------------------------------


def _slot_app(assignment: Assignment, slot: str) -> EvalTaskApp:
    from backend.human_eval.services import context
    from backend.human_eval.services.errors import not_found

    for name, app in context.slot_apps(assignment):
        if name == slot and app is not None:
            return app
    msg = "App not in this assignment"
    raise not_found(msg)


def _liveness_honoured(assignment: Assignment, now: datetime, *, visible: bool = False) -> bool:
    """R3.11: whether activity of this assignment counts as liveness now.

    Always while the workbench is visible; while hidden only within ``assignment_timeout_minutes`` of
    the last visible beat (``app_views["_last_visible_at"]``, else the start), so an abandoned hidden
    tab lets the assignment expire however it keeps polling.
    """
    if visible:
        return True
    views = assignment.app_views if isinstance(assignment.app_views, dict) else {}
    last_visible = _parse_dt(views.get("_last_visible_at")) or assignment.started_at or assignment.created_at
    if last_visible is None:
        return False
    allocation = design_service.merged_settings(assignment.task.settings)["allocation"]
    timeout = timedelta(minutes=int(allocation.get("assignment_timeout_minutes") or 60))
    return now - last_visible <= timeout


def ensure_for_assignment(assignment: Assignment, slot: str) -> dict[str, Any]:
    """Portal ``ensure``: start (or reset) the slot's app for this assignment; TS ``HostingInfo``.

    Call with the assignment row locked. 409 ``read_only`` on a read-only assignment. Counts as
    assignment activity only within the window heartbeats honour (:func:`_liveness_honoured`): the
    workbench polls ensure while an app is not running, and a hidden, abandoned tab must not keep the
    assignment alive that way. When the app is running for the participant, the container use and the
    hosting provenance are recorded.
    """
    from backend.human_eval.services import access

    task_app = _slot_app(assignment, slot)
    access.ensure_writable(assignment)
    info = ensure_running(task_app, actor=assignment.participant, assignment=assignment)
    now = timezone.now()
    if _liveness_honoured(assignment, now):
        Assignment.objects.filter(pk=assignment.pk).update(last_activity_at=now)
        assignment.last_activity_at = now
    if info["state"] == "running":
        container = current_container(task_app)
        if container is not None:
            _note_running(assignment, slot, task_app, container)
    return info


def _shown_running(task_app: EvalTaskApp, container: ContainerInstance | None, now: datetime) -> bool:
    """Whether participants are shown this app as running (reachable, running, no own reset/stop in flight)."""
    if container is None or container.status != _RUNNING or not apps_reachable():
        return False
    if _marker_on(task_app, now) and task_app.container_id == container.pk:
        return False
    return _A.STOP not in _open_actions(container, now)


def serving_container(task_app: EvalTaskApp, assignment: Assignment) -> ContainerInstance | None:
    """The container ``assignment``'s participant is served for this app, else None (DB-only).

    Only while they are shown it running (see :func:`participant_hosting_info`): running and reachable,
    no own start/reset or stop in flight, and no per-assignment reset due for this assignment. The HTTP
    console uses it, so it never reaches a previous rater's state or a container being torn down.
    """
    container = current_container(task_app)
    if not _shown_running(task_app, container, timezone.now()):
        return None
    if _reset_pending(task_app, container, assignment):
        return None
    return container


def note_served(assignment: Assignment, slot: str, task_app: EvalTaskApp, container: ContainerInstance) -> None:
    """``assignment`` used the running app outside ensure/heartbeat (the console): record the container use
    (so the next assignment gets a fresh copy under per-assignment reset) and the hosting provenance.

    Takes the container row lock: call after any assignment row lock of the request (lock order).
    """
    _note_running(assignment, slot, task_app, container)


def _clamp_seconds(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if math.isnan(number):
        return 0
    return int(max(0, min(number, BEAT_CAP_SECONDS)))


def _heartbeat_result(assignment: Assignment, slots: list[tuple[str, EvalTaskApp]]) -> dict[str, Any]:
    return {
        "status": assignment.status,
        "active_seconds": int(assignment.active_seconds or 0),
        "apps": [
            {"slot": slot, "hosting": participant_hosting_info(app, assignment=assignment)}
            for slot, app in slots
            if app is not None
        ],
    }


def heartbeat(assignment: Assignment, *, active_seconds: Any, visible: bool, viewing: Iterable[str]) -> dict[str, Any]:
    """Portal heartbeat (every 30 s while the workbench is open); TS ``HeartbeatResult``.

    Call with the assignment row locked. Read-only assignments only get status and hosting back.
    Otherwise (R1, R3.11):

    - a beat is *honoured* when visible, or hidden within ``assignment_timeout_minutes`` of the last
      visible beat (``app_views["_last_visible_at"]``); unhonoured beats change nothing, so an
      abandoned hidden tab lets the assignment expire;
    - honoured beats set ``last_activity_at`` and add ``min(gap, 60)`` to ``wall_seconds`` (gap since
      the previous beat, ``app_views["_last_beat_at"]``; gaps over 5 minutes add nothing);
    - visible beats add ``clamp(active_seconds, 0, 60)`` (never more than the elapsed gap) to
      ``active_seconds``;
    - every app of the assignment is kept from idling out (:func:`touch`), viewed or not: a pairwise
      participant comparing in one app must not lose the other to eviction or the reaper;
    - each viewed slot adds the wall increment to ``app_views[slot].viewed_seconds`` and, once its app is
      running, sets ``first_running_at`` and records the hosting provenance (view time stays per slot);
    - ``assigned`` → ``in_progress`` (``started_at``; context built if empty);
    - the lazy reaper runs after the touch (it never stops an app this beat keeps alive);
    - the participant's ``last_seen_at`` is refreshed (at most once a minute).
    """
    from backend.human_eval.services import access
    from backend.human_eval.services import context

    slots = context.slot_apps(assignment)
    if access.assignment_read_only(assignment):
        return _heartbeat_result(assignment, slots)
    now = timezone.now()
    fields: list[str] = []
    if assignment.status == Assignment.Status.ASSIGNED:
        assignment.status = Assignment.Status.IN_PROGRESS
        assignment.started_at = assignment.started_at or now
        fields += ["status", "started_at"]
        if not assignment.context:
            assignment.context = context.build_context(assignment)
            fields.append("context")
    views = dict(assignment.app_views or {})
    last_beat = _parse_dt(views.get("_last_beat_at")) or assignment.started_at
    honoured = _liveness_honoured(assignment, now, visible=bool(visible))
    to_note: list[tuple[str, EvalTaskApp, ContainerInstance]] = []
    if honoured:
        gap = max(0.0, (now - last_beat).total_seconds()) if last_beat is not None else None
        counted = gap is not None and gap <= BEAT_GAP_LIMIT_SECONDS
        wall_inc = min(round(gap), BEAT_CAP_SECONDS) if counted else 0
        active_inc = 0
        if visible:
            requested = _clamp_seconds(active_seconds)
            active_inc = min(requested, math.ceil(gap)) if counted else requested
        assignment.wall_seconds = int(assignment.wall_seconds or 0) + wall_inc
        assignment.active_seconds = int(assignment.active_seconds or 0) + active_inc
        assignment.last_activity_at = now
        views["_last_beat_at"] = now.isoformat()
        if visible:
            views["_last_visible_at"] = now.isoformat()
        apps_by_slot = {slot: app for slot, app in slots if app is not None}
        for slot in dict.fromkeys(str(name) for name in viewing or []):
            task_app = apps_by_slot.get(slot)
            if task_app is None:
                continue
            entry = dict(views.get(slot) or {})
            entry["viewed_seconds"] = int(entry.get("viewed_seconds") or 0) + wall_inc
            entry.setdefault("first_running_at", None)
            container = current_container(task_app)
            if _shown_running(task_app, container, now) and not _reset_pending(task_app, container, assignment):
                if not entry.get("first_running_at"):
                    entry["first_running_at"] = now.isoformat()
                to_note.append((slot, task_app, container))
            views[slot] = entry
        assignment.app_views = views
        fields += ["wall_seconds", "active_seconds", "last_activity_at", "app_views"]
    if fields:
        assignment.save(update_fields=list(dict.fromkeys(fields)))
    if honoured:
        touch([app for _, app in slots if app is not None])
    lazy_reap()
    for slot, task_app, container in to_note:
        _note_running(assignment, slot, task_app, container)
    Participant.objects.filter(pk=assignment.participant_id).filter(
        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=now - LAST_SEEN_THROTTLE),
    ).update(last_seen_at=now)
    return _heartbeat_result(assignment, slots)
