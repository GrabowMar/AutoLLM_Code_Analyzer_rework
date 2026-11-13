"""Execute an :class:`AnalysisRun`: run each selected tool, persist findings."""

from __future__ import annotations

import fnmatch
import functools
import inspect
import json
import logging
import re
import shlex
import threading
import time
import zlib
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from pathlib import PurePosixPath

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services import ai_runner
from backend.analysis.services import dast_seed
from backend.analysis.services import parsers
from backend.analysis.services import workspace_service
from backend.analysis.services.base import ADVISORY_TOOLS
from backend.analysis.services.base import AnalyzerOutput
from backend.analysis.services.base import ParsedOutput
from backend.analysis.services.base import build_severity_counts
from backend.analysis.services.base import fingerprint
from backend.analysis.services.base import is_generated_bundle
from backend.analysis.services.base import is_non_source_path
from backend.analysis.services.base import non_source_paths
from backend.analysis.services.serialize import serialize_result
from backend.common.threading import dispatch_in_thread
from backend.realtime import events as realtime
from backend.runtime.models import ContainerInstance
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

# Stored stdout is a debugging artifact (parsing happens on the full output
# first); keep enough to be useful without bloating rows.
_STDOUT_STORE_LIMIT = 30_000

_EXT_BY_KEY = {
    "backend": ".py",
    "backend_code": ".py",
    "python": ".py",
    "content": ".py",
    "frontend": ".js",
    "frontend_code": ".jsx",
    "javascript": ".js",
    "typescript": ".ts",
}

# File names that have no extension but are files, not semantic code blobs.
_EXTENSIONLESS_FILES = frozenset(
    {"Dockerfile", "Containerfile", "Makefile", "Procfile", "Gemfile", "Pipfile", "Jenkinsfile", "Vagrantfile"},
)

# Port generated apps listen on inside their container (see runtime.proxy).
APP_CONTAINER_PORT = 8000

# ToolResult.summary["skip_reason"] values. None of them is a measurement: a
# not-applicable tool (nothing in the app's tree it reads) is ``not_measured``,
# never "0 findings" (spec v3 section 8.8), and the others mean the tool said
# nothing about the app.
SKIP_NOT_APPLICABLE = "not_applicable"
SKIP_APP_NOT_RUNNING = "app_not_running"
SKIP_UNAVAILABLE = "unavailable"
# The run was cancelled before (or while) this tool ran; it measured nothing.
SKIP_CANCELLED = "cancelled"

# ToolResult.summary keys of the v3 applicability record (spec section 8.8):
# ``tool_applicable`` is True/False per (run, tool) when it was decided (False:
# no in-scope file the tool reads) and ``measurement`` is ``measured`` |
# ``not_measured``. The run's summary repeats them as ``tool_applicable`` {slug: bool | None}.
MEASURED = "measured"
NOT_MEASURED = "not_measured"
# The file set of a generation run handed to the tools: always the whole app tree.
ANALYSIS_SCOPE = "app"

# A running run's executing thread refreshes ``heartbeat_at`` this often; a run
# whose heartbeat is older than ``STALE_AFTER`` has no live thread (runs execute
# on daemon threads of the web process, which a reload kills).
HEARTBEAT_INTERVAL_S = 30
STALE_AFTER = timedelta(minutes=5)
INTERRUPTED_MESSAGE = (
    "Interrupted: the backend process executing this run stopped (restart or reload). Re-run the analysis."
)
QUEUE_INTERRUPTED_MESSAGE = (
    "Interrupted before it started: the backend process holding the queue stopped (restart or reload). "
    "Re-run the analysis."
)

# What a running run is doing between its tools (``summary["phase"]`` while
# running, and ``progress`` events): waiting for another run of the same user
# to release the workspace, copying the code in, then running tools.
PHASE_WAITING = "waiting_for_workspace"
PHASE_PREPARING = "preparing"
PHASE_TOOLS = "running_tools"

# Prefixed to every tool command (``: <marker>; <cmd>``) so cancel can find
# and kill that run's in-flight processes in the shared workspace container.
_CMD_MARKER = "llmlab-analysis-run"

_CONFIG_PLACEHOLDER_RE = re.compile(r"\{config:([A-Za-z0-9_]+)\}")

# Seconds the workspace waits for the app to answer the liveness probe that
# brackets every dynamic tool (any HTTP status counts as alive).
APP_PROBE_TIMEOUT_S = 10

# Postgres text and jsonb reject NUL; binary tool output (a scanned SQLite file,
# a DAST response body) must not make a result unsavable.
_NUL = "\x00"


def _scrub(value):
    """*value* with every NUL character (in nested dicts/lists too) replaced by U+FFFD."""
    if isinstance(value, str):
        return value.replace(_NUL, "\ufffd") if _NUL in value else value
    if isinstance(value, dict):
        return {_scrub(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def command_fingerprint(command: str, url: str = "") -> str:
    """Provenance hash of a rendered tool command (``summary["command_sha"]``).

    The app's URL (different for every app) is normalized to ``{url}``, so one
    command over many apps hashes alike. For results stored before the hash
    existed: ``command_fingerprint(raw_output["command"], raw_output.get("target_url", ""))``.
    """
    return fingerprint(command.replace(url, "{url}") if url else command)


def _source(fn) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return getattr(fn, "__qualname__", "")


@functools.cache
def parser_fingerprint(parser_key: str) -> str:
    """Provenance hash of the parser behind *parser_key* (``summary["parser_sha"]``).

    A parser edit changes what a tool reports under the same build and
    command, so the parser's source and the analysis helpers it calls by name
    (one level deep) are part of the instrument. "" when no parser is registered.
    """
    fn = parsers._PARSERS.get(parser_key)  # noqa: SLF001
    if fn is None:
        return ""
    module = inspect.getmodule(fn)
    parts = [_source(fn)]
    for name in sorted(set(fn.__code__.co_names)):
        helper = getattr(module, name, None)
        if (
            inspect.isfunction(helper)
            and helper is not fn
            and helper.__module__.startswith("backend.analysis.services")
        ):
            parts.append(_source(helper))
    return fingerprint("\n".join(parts))


def patterns_fingerprint(tool: AnalyzerTool) -> str:
    """Provenance hash of the file patterns that decide whether *tool* applies (``summary["patterns_sha"]``).

    A catalog edit to ``file_patterns`` moves apps between "not applicable" (a
    true zero) and "measured" under the same build, command and parser. ""
    for dynamic tools, which scan the running app whatever its files.
    """
    if tool.analysis_mode == "dynamic":
        return ""
    return fingerprint(json.dumps(sorted(tool.file_patterns or [])))


def render_command(tool: AnalyzerTool, *, target: str, url: str = "", config: dict | None = None) -> str:
    """Fill a tool's ``run_cmd`` template.

    ``{target}`` is the code directory, ``{url}`` the running app's base URL
    (dynamic tools) and ``{config:<name>}`` the user's option value (falling
    back to the tool's default), shell-quoted because it is user input.
    """
    values = {**(tool.default_config or {}), **(config or {})}

    def _option(match: re.Match) -> str:
        value = values.get(match.group(1), "")
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, list):
            value = ",".join(str(v) for v in value)
        return shlex.quote(str(value))

    cmd = tool.run_cmd.replace("{target}", target).replace("{url}", url)
    return _CONFIG_PLACEHOLDER_RE.sub(_option, cmd)


def resolve_app_url(run: AnalysisRun) -> tuple[str, str]:
    """``(base_url, "")`` of the run's app as seen from the workspace, or ``("", reason)``.

    Only a running container of the run's own generation job, started by or
    belonging to the run's owner, is ever a target: dynamic tools attack what
    they scan, so they must never be pointed at anything else.
    """
    if not run.generation_job_id:
        return "", "Dynamic tools scan a running app; analyze a generated app (not pasted code) to use them."
    container = running_app_containers(run.created_by, [run.generation_job_id]).get(str(run.generation_job_id))
    if container is None:
        return "", "The app is not running. Start its container, then re-run the dynamic tools."
    if getattr(settings, "DOCKER_APPS_NETWORK", ""):
        # Workspace and app share the apps network: reach the app by name.
        return f"http://{container.name}:{APP_CONTAINER_PORT}", ""
    # Default bridge (both containers live on the same daemon): use the app's IP.
    info = docker_manager.inspect(container.name)
    if info.get("State", {}).get("Running") is not True:
        return "", "The app's container is not running. Start it, then re-run the dynamic tools."
    for net in (info.get("NetworkSettings", {}).get("Networks") or {}).values():
        ip = (net or {}).get("IPAddress")
        if ip:
            return f"http://{ip}:{APP_CONTAINER_PORT}", ""
    return "", "Could not determine the app container's address."


def running_app_containers(user, job_ids) -> dict[str, ContainerInstance]:
    """``{job id: newest RUNNING container}`` for the jobs that have one, as recorded in the database.

    Only a container of the job started by, or belonging to, *user* counts:
    dynamic tools attack what they scan, so nothing else may be a target.
    """
    containers = ContainerInstance.objects.filter(
        Q(created_by=user) | Q(generation_job__created_by=user),
        generation_job_id__in=list(job_ids),
        status=ContainerInstance.Status.RUNNING,
    ).order_by("generation_job_id", "-updated_at")
    found: dict[str, ContainerInstance] = {}
    for container in containers:
        found.setdefault(str(container.generation_job_id), container)
    return found


def dispatch(run_id: str) -> None:
    """Run an analysis on a background daemon thread."""
    dispatch_many([run_id])


def dispatch_many(run_ids) -> None:
    """Run analyses one after another on a single background daemon thread.

    Runs of one user share a workspace and execute serially anyway; one
    thread per batch keeps a 50-app batch from parking 50 threads (and
    database connections) on the workspace lock. While queued, the runs'
    ``heartbeat_at`` is refreshed so :func:`reap_stale_runs` can tell a live
    queue from one that died with its process.
    """
    ids = [str(i) for i in run_ids]
    if not ids:
        return
    AnalysisRun.objects.filter(pk__in=ids, status=AnalysisRun.Status.PENDING).update(heartbeat_at=timezone.now())
    # Requests are atomic: a thread started before the commit may not see the runs yet.
    transaction.on_commit(lambda: dispatch_in_thread(_execute_queue, ids, name=f"analysis-queue-{ids[0]}"))


def _execute_queue(run_ids: list[str]) -> None:
    try:
        with _Heartbeat(run_ids, statuses=(AnalysisRun.Status.PENDING, AnalysisRun.Status.RUNNING)):
            for run_id in run_ids:
                _execute(run_id)
    finally:
        connection.close()


def _channel(run_id) -> str:
    return f"analysis:{run_id}"


def _publish(run: AnalysisRun) -> None:
    realtime.publish(
        _channel(run.id),
        {
            "type": "status",
            "status": run.status,
            "summary": run.summary,
            "error_message": run.error_message,
            "updated_at": timezone.now().isoformat(),
        },
    )


def _publish_result(result: ToolResult) -> None:
    """A tool started or finished (``result`` event; the payload is the API's tool result)."""
    realtime.publish(_channel(result.run_id), {"type": "result", "result": serialize_result(result)})


def _set_phase(run: AnalysisRun, phase: str) -> None:
    """Record what the running *run* is doing between tools (``progress`` event)."""
    summary = {"phase": phase}
    AnalysisRun.objects.filter(pk=run.pk, status=AnalysisRun.Status.RUNNING).update(
        summary=summary,
        updated_at=timezone.now(),
    )
    run.summary = summary
    realtime.publish(_channel(run.id), {"type": "progress", "phase": phase})


def _execute(run_id: str) -> None:
    try:
        run = AnalysisRun.objects.select_related("created_by", "workspace").get(id=run_id)
    except AnalysisRun.DoesNotExist:
        logger.warning("AnalysisRun %s vanished before execution", run_id)
        return
    try:
        execute(run)
    except Exception:
        logger.exception("AnalysisRun %s failed unexpectedly", run_id)
        _fail(run, "Internal error during analysis")
    finally:
        connection.close()


def is_cancelled(run: AnalysisRun) -> bool:
    """Whether *run* was cancelled (read from the database: cancel happens in another request)."""
    return AnalysisRun.objects.filter(pk=run.pk, status=AnalysisRun.Status.CANCELLED).exists()


def _save_unless_cancelled(run: AnalysisRun, **fields) -> bool:
    """Write *fields* to *run*, keeping ``status``/``error_message`` if it was cancelled.

    One conditional UPDATE, so a cancel that lands while the runner is
    finishing is never overwritten. Returns False when the run was cancelled.
    """
    now = timezone.now()
    updated = (
        AnalysisRun.objects.filter(pk=run.pk)
        .exclude(status=AnalysisRun.Status.CANCELLED)
        .update(updated_at=now, **fields)
    )
    if not updated:
        fields = {k: v for k, v in fields.items() if k not in ("status", "error_message")}
        if fields:
            AnalysisRun.objects.filter(pk=run.pk).update(updated_at=now, **fields)
        fields["status"] = AnalysisRun.Status.CANCELLED
    for key, value in fields.items():
        setattr(run, key, value)
    return bool(updated)


def _fail(run: AnalysisRun, message: str) -> None:
    completed_at = timezone.now()
    _save_unless_cancelled(
        run,
        status=AnalysisRun.Status.FAILED,
        error_message=message,
        summary={k: v for k, v in (run.summary or {}).items() if k != "phase"},
        completed_at=completed_at,
        duration_seconds=(completed_at - run.started_at).total_seconds() if run.started_at else None,
    )
    _publish(run)


def cancel(run: AnalysisRun) -> bool:
    """Cancel a pending or running *run*; kill its in-flight tool in the workspace.

    The executing thread notices the cancellation between tools (and after
    the killed tool returns) and stops; its final save keeps ``cancelled``.
    An in-flight AI review (an HTTP call) cannot be interrupted; its result
    is discarded when it returns. Returns False when the run had already finished.
    """
    now = timezone.now()
    updated = AnalysisRun.objects.filter(
        pk=run.pk,
        status__in=[AnalysisRun.Status.PENDING, AnalysisRun.Status.RUNNING],
    ).update(status=AnalysisRun.Status.CANCELLED, completed_at=now, updated_at=now)
    run.refresh_from_db()
    if not updated:
        return False

    def _after_commit() -> None:
        _kill_in_flight(run)
        _publish(run)

    # Kill only once "cancelled" is committed (requests are atomic): the runner
    # reads the status when the killed tool returns and must see the cancel,
    # or it records the kill as a tool failure and starts the next tool.
    transaction.on_commit(_after_commit)
    return True


def _kill_script(run_id) -> str:
    """Shell that SIGKILLs the process group of every process whose command line
    carries this run's marker. Each tool runs under ``timeout``, which leads its
    own process group, so the group holds the tool and all its children. The
    analyzer image has no procps, so this reads /proc. The marker is assembled
    at run time so this script's own command line never matches it."""
    head, tail = _CMD_MARKER.split("-", 1)
    return (
        f"m='{head}'; m=\"$m-{tail}:{run_id}\"; "
        "me=$(sed 's/.*) //' /proc/$$/stat | cut -d' ' -f3); n=0; "
        "for d in /proc/[0-9]*; do "
        "c=$( { tr '\\0' ' ' < \"$d/cmdline\"; } 2>/dev/null) || continue; "
        'case "$c" in *"$m"*) ;; *) continue ;; esac; '
        "g=$( { sed 's/.*) //' \"$d/stat\" | cut -d' ' -f3; } 2>/dev/null); "
        'case "$g" in \'\'|0|1|"$me") continue ;; esac; '
        'kill -KILL -"$g" 2>/dev/null && n=$((n+1)); '
        'done; echo "killed $n"'
    )


def _kill_in_flight(run: AnalysisRun) -> None:
    workspace = run.workspace
    container = workspace.container if workspace else None
    if container is None:
        return
    result = docker_manager.exec_in(container.name, _kill_script(run.id), timeout_s=15)
    if result.get("exit_code") != 0:
        logger.warning("Could not kill in-flight tools of AnalysisRun %s: %s", run.id, result)


class _Heartbeat:
    """Refreshes ``heartbeat_at`` of runs on a side thread while they execute or
    wait in a queue (a single tool can run for many minutes, so beats between
    tools are not enough). Only runs still in one of *statuses* are touched."""

    def __init__(self, run_ids, statuses=(AnalysisRun.Status.RUNNING,)) -> None:
        self._run_ids = list(run_ids) if isinstance(run_ids, (list, tuple, set)) else [run_ids]
        self._statuses = list(statuses)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._beat,
            daemon=True,
            name=f"analysis-heartbeat-{self._run_ids[0] if self._run_ids else ''}",
        )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()

    def _beat(self) -> None:
        try:
            while not self._stop.wait(HEARTBEAT_INTERVAL_S):
                try:
                    now = timezone.now()
                    # updated_at too: the System "clear stuck" action ages runs by it.
                    AnalysisRun.objects.filter(pk__in=self._run_ids, status__in=self._statuses).update(
                        heartbeat_at=now,
                        updated_at=now,
                    )
                except Exception:  # noqa: BLE001 - a missed beat must not end the loop
                    logger.warning("Heartbeat of AnalysisRun(s) %s failed", self._run_ids, exc_info=True)
        finally:
            connection.close()


def reap_stale_runs(stale_after: timedelta = STALE_AFTER, run_ids=None) -> int:
    """Fail ``running`` runs whose executing thread is gone; return how many.

    A live run beats every ``HEARTBEAT_INTERVAL_S``, so only a run whose
    heartbeat (or, for rows from before heartbeats, start) is older than
    *stale_after* is reaped: a run genuinely in progress in any live
    process is never touched. Its still-``running`` tool results fail too.
    Queued (``pending``) runs whose queue stopped beating fail as well; a
    pending run that was never queued (no heartbeat) is left alone.
    *run_ids* limits the reaping to those runs.
    """
    now = timezone.now()
    cutoff = now - stale_after
    scope = AnalysisRun.objects.all() if run_ids is None else AnalysisRun.objects.filter(id__in=list(run_ids))
    stale = scope.filter(status=AnalysisRun.Status.RUNNING).filter(
        Q(heartbeat_at__lt=cutoff)
        | Q(heartbeat_at__isnull=True, started_at__lt=cutoff)
        | Q(heartbeat_at__isnull=True, started_at__isnull=True, updated_at__lt=cutoff),
    )
    queued = scope.filter(status=AnalysisRun.Status.PENDING, heartbeat_at__lt=cutoff)
    queued_count = queued.update(
        status=AnalysisRun.Status.FAILED,
        error_message=QUEUE_INTERRUPTED_MESSAGE,
        completed_at=now,
        updated_at=now,
    )
    ids = list(stale.values_list("id", flat=True))
    if not ids:
        return queued_count
    count = AnalysisRun.objects.filter(id__in=ids, status=AnalysisRun.Status.RUNNING).update(
        status=AnalysisRun.Status.FAILED,
        error_message=INTERRUPTED_MESSAGE,
        completed_at=now,
        updated_at=now,
    )
    ToolResult.objects.filter(run_id__in=ids, status=ToolResult.Status.RUNNING).update(
        status=ToolResult.Status.FAILED,
        error_message=INTERRUPTED_MESSAGE,
    )
    logger.warning("Reaped %s interrupted analysis run(s): %s", count, ids)
    return count + queued_count


def execute(run: AnalysisRun) -> AnalysisRun:
    """Synchronously execute *run* (also used directly by tests/automation)."""
    now = timezone.now()
    if not _save_unless_cancelled(run, status=AnalysisRun.Status.RUNNING, started_at=now, heartbeat_at=now):
        return run  # cancelled before it started
    _publish(run)

    # One workspace container, one /work and fixed /tmp report paths: two
    # runs of the same user must not interleave, or each scans the other's code.
    with _Heartbeat(run.pk), _workspace_lock(run.workspace_id, on_wait=lambda: _set_phase(run, PHASE_WAITING)):
        try:
            return _execute_locked(run)
        except Exception:
            # The caller fails the run (``_execute``, the study post-run);
            # no tool result may be left looking like it is still in progress.
            _fail_unfinished_results(run, "Internal error during analysis")
            raise


def _fail_unfinished_results(run: AnalysisRun, message: str) -> None:
    ToolResult.objects.filter(run=run, status__in=[ToolResult.Status.PENDING, ToolResult.Status.RUNNING]).update(
        status=ToolResult.Status.FAILED,
        error_message=message,
    )


@contextmanager
def _workspace_lock(workspace_id, on_wait=None):
    """Serialize runs on one workspace (a session-level Postgres advisory lock).

    ``on_wait`` is called once when another run holds the lock, before blocking.
    """
    if not workspace_id or connection.vendor != "postgresql":
        yield
        return
    key = zlib.crc32(f"analysis-workspace:{workspace_id}".encode())
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        if not cursor.fetchone()[0]:
            if on_wait is not None:
                on_wait()
            cursor.execute("SELECT pg_advisory_lock(%s)", [key])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def _execute_locked(run: AnalysisRun) -> AnalysisRun:
    if is_cancelled(run):  # cancelled while waiting for the workspace
        _finalize(run, [], [])
        return run
    _set_phase(run, PHASE_PREPARING)
    code = run.get_code_for_analysis()
    excluded_paths = non_source_paths(code)
    files = analysis_files(run, code)
    origins = _record_code_scope(run, files)
    truncated_inputs = _truncated_inputs(run)
    tools = _resolve_tools(run.tool_slugs)
    tool_configs = _resolve_tool_configs(run)
    tool_versions = _resolve_tool_versions(run)

    container_name = ""
    needs_container = any(t.kind == "container" for t in tools.values())
    if needs_container and run.workspace_id:
        try:
            container_name = workspace_service.require_ready_container(run.workspace)
            # /work is reused across runs in the long-lived workspace container.
            # Clear it first so files (and tool caches) from a previous run don't
            # leak into this one's findings.
            cleared = docker_manager.exec_in(
                container_name,
                f"find {workspace_service.WORK_DIR} -mindepth 1 -delete",
                timeout_s=30,
            )
            if cleared.get("exit_code") != 0:
                # Stale files would be analyzed as part of this app.
                msg = f"Could not clear the workspace: {(cleared.get('output') or cleared.get('error') or '')[:300]}"
                raise RuntimeError(msg)
            if files:
                copied = docker_manager.copy_files_in(
                    container_name,
                    files,
                    dest=workspace_service.WORK_DIR,
                    owner=workspace_service.WORKSPACE_UID,
                )
                if "error" in copied:
                    msg = f"Could not copy the code into the workspace: {copied['error']}"
                    raise RuntimeError(msg)
        except RuntimeError as exc:
            _fail(run, str(exc))
            return run

    _set_phase(run, PHASE_TOOLS)
    statuses: list[str] = []
    cancelled = False
    scan_scope: dict | None = None
    for slug in run.tool_slugs:
        tool = tools.get(slug)
        cancelled = cancelled or is_cancelled(run)
        app_url, app_url_error = "", ""
        if not cancelled and tool is not None and tool.is_enabled and tool.analysis_mode == "dynamic":
            # Resolved per tool, right before it: the app may have died (or
            # moved to another address) while the previous tools ran.
            app_url, app_url_error = _live_app_url(run, container_name)
            if app_url and container_name and scan_scope is None:
                # Once per run, before its first dynamic tool: sign in and describe the API to the scanners.
                scan_scope = _seed_dynamic_scan(run, container_name, app_url)
        skip = _skip(tool, files, cancelled=cancelled, app_url=app_url, app_url_error=app_url_error)
        if skip is not None:
            reason, message = skip
            summary = {"skip_reason": reason, "measurement": NOT_MEASURED}
            if reason == SKIP_NOT_APPLICABLE:
                # Not measured (never a zero): nothing in the app's tree is something this tool reads.
                summary["tool_applicable"] = False
                if patterns_sha := patterns_fingerprint(tool):
                    summary["patterns_sha"] = patterns_sha
            result = ToolResult.objects.create(
                run=run,
                tool_slug=slug,
                category=tool.category if tool else "",
                status=ToolResult.Status.SKIPPED,
                error_message=message,
                summary=summary,
            )
            _publish_result(result)
            statuses.append(result.status)
            continue

        started_at = timezone.now()
        result = ToolResult.objects.create(
            run=run,
            tool_slug=slug,
            category=tool.category,
            status=ToolResult.Status.RUNNING,
            started_at=started_at,
        )
        _publish_result(result)
        try:
            cancelled = _run_tool(
                run,
                tool,
                result,
                files=files,
                container_name=container_name,
                config=tool_configs.get(slug) or {},
                tool_version=tool_versions.get(slug, ""),
                app_url=app_url,
                origins=origins,
                scan_scope=scan_scope if tool.analysis_mode == "dynamic" else None,
            )
        except Exception as exc:
            # One tool's crash (a provider's odd response, an unsavable value)
            # must not abort the run: the other tools still measure the app.
            logger.exception("Tool %s of AnalysisRun %s failed unexpectedly", slug, run.pk)
            result.status = ToolResult.Status.FAILED
            result.error_message = _scrub(f"Internal error: {type(exc).__name__}: {exc}")[:500]
            result.duration_seconds = round((timezone.now() - started_at).total_seconds(), 3)
            result.summary, result.raw_output, result.metrics = {"measurement": NOT_MEASURED}, {}, {}
            result.save(
                update_fields=["status", "error_message", "duration_seconds", "summary", "raw_output", "metrics"],
            )
        _publish_result(result)
        statuses.append(result.status)

    _finalize(run, statuses, truncated_inputs, excluded_paths)
    return run


def _run_tool(
    run: AnalysisRun,
    tool: AnalyzerTool,
    result: ToolResult,
    *,
    files: dict[str, str],
    container_name: str,
    config: dict,
    tool_version: str,
    app_url: str,
    origins: dict[str, str] | None = None,
    scan_scope: dict | None = None,
) -> bool:
    """Execute *tool* and store its outcome on *result*; returns whether the run was cancelled meanwhile."""
    started_at = result.started_at or timezone.now()
    if tool.kind == "ai":
        output = ai_runner.run(tool, files, config, run.created_by)
    else:
        output = _run_container_tool(tool, container_name, config=config, app_url=app_url, run_id=run.pk)
    result.duration_seconds = round((timezone.now() - started_at).total_seconds(), 3)

    if is_cancelled(run):
        # Killed by cancel (or finished after it): not a measurement.
        result.status = ToolResult.Status.SKIPPED
        result.error_message = "Run cancelled while this tool was running"
        result.summary = {"skip_reason": SKIP_CANCELLED, "measurement": NOT_MEASURED}
        result.save(update_fields=["status", "error_message", "summary", "duration_seconds"])
        return True

    if tool.analysis_mode == "dynamic" and app_url and container_name:
        # Twice: an app still busy with the scan's last requests may answer late.
        alive, detail = _app_answers(container_name, app_url, attempts=2)
        if not alive:
            # The app died during the scan (crashed under the attack, OOM,
            # stopped): what the scanner saw is not a measurement of the app.
            message = (
                f"The app stopped answering at {app_url} during the scan ({detail}); "
                "the scan is incomplete. Restart the app, then re-run the dynamic tools."
            )
            if not output.findings:
                # A scanner aimed at a dead port prints nothing: never a clean zero.
                result.status = ToolResult.Status.SKIPPED
                result.error_message = message
                result.summary = {"skip_reason": SKIP_APP_NOT_RUNNING, "measurement": NOT_MEASURED}
                result.raw_output = _scrub(output.raw_output)
                result.save(update_fields=["status", "error_message", "summary", "raw_output", "duration_seconds"])
                return False
            output.error = f"{message} {output.error}" if output.error else message

    if tool.kind != "ai" and tool_version:
        output.summary["tool_version"] = tool_version
    # The options the findings were produced with (provenance for reports).
    output.summary["config"] = {**(tool.default_config or {}), **config}
    # What turned the code into findings besides the build and the options:
    # the rendered command (or the reviewer's prompt) and the parser's code.
    if tool.kind == "ai":
        output.summary["command_sha"] = ai_runner.prompt_fingerprint()
    elif output.raw_output.get("command"):
        output.summary["command_sha"] = command_fingerprint(output.raw_output["command"], app_url)
    if tool.kind != "ai" and tool.parser_key and (parser_sha := parser_fingerprint(tool.parser_key)):
        output.summary["parser_sha"] = parser_sha
    if patterns_sha := patterns_fingerprint(tool):
        output.summary["patterns_sha"] = patterns_sha
    if scan_scope is not None:
        # What the scanner was given besides the start page (dast_seed): API description, signed-in identity.
        output.summary["scan_scope"] = scan_scope
    # It ran: the app has files it reads (dynamic tools: a running app).
    output.summary["tool_applicable"] = True
    _persist_result(result, output, origins or {})
    return False


def _seed_dynamic_scan(run: AnalysisRun, container_name: str, app_url: str) -> dict:
    """``dast_seed.prepare`` that never fails the run: an unseeded scan is still a (shallower) scan."""
    try:
        return dast_seed.prepare(run, container_name, app_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Seeding the dynamic scan of AnalysisRun %s failed", run.pk, exc_info=True)
        try:
            dast_seed.clear(container_name)
        except Exception:  # noqa: BLE001
            logger.warning("Could not clear the seed files of AnalysisRun %s", run.pk, exc_info=True)
        return {"seeded": False, "reason": f"seeding failed: {type(exc).__name__}: {exc}"[:300]}


def _live_app_url(run: AnalysisRun, container_name: str) -> tuple[str, str]:
    """``(url, "")`` of the run's app when it answers HTTP from the workspace now, else ``("", reason)``."""
    try:
        url, reason = resolve_app_url(run)
    except Exception as exc:  # noqa: BLE001 - an unreachable daemon means "cannot reach the app"
        logger.warning("Resolving the app of AnalysisRun %s failed", run.pk, exc_info=True)
        return "", f"Could not locate the app's container: {type(exc).__name__}: {exc}"[:300]
    if not url or not container_name:
        return url, reason
    alive, detail = _app_answers(container_name, url)
    if not alive:
        return "", f"The app did not answer at {url} ({detail}). Start it, then re-run the dynamic tools."
    return url, ""


def _app_answers(container_name: str, url: str, *, attempts: int = 1) -> tuple[bool, str]:
    """Whether the app at *url* answers HTTP from the workspace (any status counts), and why not."""
    detail = ""
    for _ in range(attempts):
        probe = docker_manager.exec_in(
            container_name,
            f"curl -sS -o /dev/null --max-time {APP_PROBE_TIMEOUT_S} {shlex.quote(url.rstrip('/') + '/')}",
            timeout_s=APP_PROBE_TIMEOUT_S + 5,
        )
        code = probe.get("exit_code")
        if code == 0:
            return True, ""
        if code == 127 or (code == -1 and probe.get("error")):
            # No curl, or the workspace itself is unreachable: the app's state is
            # unknown, and the tool run will report a workspace problem itself.
            logger.warning("Could not probe %s from %s: %s", url, container_name, probe)
            return True, ""
        detail = _scrub((probe.get("output") or "").strip())[:200] or f"curl exit {code}"
    return False, detail


def _skip(
    tool: AnalyzerTool | None,
    files: dict[str, str],
    *,
    cancelled: bool,
    app_url: str,
    app_url_error: str,
) -> tuple[str, str] | None:
    """``(skip_reason, message)`` when *tool* must not run in this run, else None."""
    if cancelled:
        return SKIP_CANCELLED, "Run cancelled"
    if tool is None or not tool.is_enabled:
        return SKIP_UNAVAILABLE, "Tool unavailable"
    if tool.analysis_mode == "dynamic" and not app_url:
        return SKIP_APP_NOT_RUNNING, app_url_error
    reason = not_applicable_reason(tool, files)
    if reason:
        # Not measured, never "0 findings": the app has nothing this tool reads.
        return SKIP_NOT_APPLICABLE, reason
    return None


def not_applicable_reason(tool: AnalyzerTool, files: dict[str, str]) -> str:
    """Why *tool* has nothing to read in *files* ("" when it applies)."""
    if tool.kind == "ai" and not ai_runner.reviewable_files(files):
        return "Not applicable: no reviewable source files (lockfiles and minified files are not reviewed)"
    if not applicable_files(tool, nonempty_files(files)):
        return f"Not applicable: no files matching {', '.join(tool.file_patterns)}"
    return ""


def nonempty_files(files: dict[str, str]) -> dict[str, str]:
    """*files* without empty ones: an empty file is copied in (an empty ``__init__.py`` shapes the
    package layout) but gives a tool nothing to read, so it never makes the tool applicable."""
    return {path: content for path, content in files.items() if isinstance(content, str) and content.strip()}


def applicable_files(tool: AnalyzerTool, files: dict[str, str]) -> list[str]:
    """The paths in *files* that *tool* reads (all of them when it declares no patterns)."""
    patterns = tool.file_patterns or []
    if not patterns or tool.analysis_mode == "dynamic":
        return list(files) or ["<app>"]
    matched = []
    for path in files:
        name = PurePosixPath(path).name
        if any(fnmatch.fnmatchcase(path if "/" in pat else name, pat) for pat in patterns):
            matched.append(path)
    return matched


def _resolve_tools(slugs: list[str]) -> dict[str, AnalyzerTool]:
    qs = AnalyzerTool.objects.filter(slug__in=list(slugs))
    return {t.slug: t for t in qs}


def _resolve_tool_versions(run: AnalysisRun) -> dict[str, str]:
    """Installed version per slug, recorded on each result so findings stay attributable
    to the exact tool build after the workspace is updated."""
    if not run.workspace_id:
        return {}
    return {
        it.tool.slug: it.installed_version or it.catalog_version
        for it in run.workspace.installed_tools.select_related("tool")
    }


def _resolve_tool_configs(run: AnalysisRun) -> dict[str, dict]:
    """Per-user tool config, keyed by slug (from the workspace's installed tools)."""
    if not run.workspace_id:
        return {}
    return {it.tool.slug: it.config or {} for it in run.workspace.installed_tools.select_related("tool")}


def _truncated_inputs(run: AnalysisRun) -> list[str]:
    """Why the analyzed app may be incomplete: the generation run was truncated (``validity``).

    ``[termination]`` (``budget``, ``wall_clock``, ``idle``, ``max_turns``) of a truncated run, else ``[]``.
    """
    if not run.generation_job_id:
        return []
    validity = getattr(run.generation_job, "validity", None)
    if not isinstance(validity, dict) or validity.get("truncated") is not True:
        return []
    return [str(validity.get("termination") or "truncated").lower()]


def _record_code_scope(run: AnalysisRun, files: dict[str, str]) -> dict[str, str]:
    """Record which file set of a generation run the tools receive; return ``{path: RunFile origin}``.

    Generation runs are always analyzed on the ``app`` scope (``code_scope``), and
    ``code_scope_paths_sha`` is ``H(sorted analyzed paths)``. The origins (``starter``,
    ``authored``, ``modified``, ``slot``, or ``parent`` for a slot variant's overlaid parent files)
    tag each finding, so model/slot attribution happens at stats time, never by filtering the input.
    Inline-source runs keep a blank scope and get no origins.
    """
    if not run.generation_job_id:
        return {}
    from backend.generation.identity import H
    from backend.generation.runs.view import run_view

    origins = {entry.path: entry.origin for entry in run_view(run.generation_job).files(ANALYSIS_SCOPE)}
    run.code_scope = ANALYSIS_SCOPE
    run.code_scope_paths_sha = H(sorted(files))
    AnalysisRun.objects.filter(pk=run.pk).update(
        code_scope=run.code_scope,
        code_scope_paths_sha=run.code_scope_paths_sha,
    )
    return origins


def _normalize_path(path: str) -> str:
    """One file, one identity: container tools report /work-prefixed paths,
    jscpd relative ones — strip the workspace prefix so findings group."""
    path = path.strip()
    # djlint drops the leading slash ("work/app.html").
    path = path.removeprefix(workspace_service.WORK_DIR + "/").removeprefix(workspace_service.WORK_DIR[1:] + "/")
    return path.removeprefix("./")


def _run_container_tool(
    tool: AnalyzerTool,
    container_name: str,
    *,
    config: dict | None = None,
    app_url: str = "",
    run_id=None,
) -> AnalyzerOutput:
    if not container_name:
        return AnalyzerOutput(error="Analyzer workspace unavailable")
    if not tool.run_cmd:
        return AnalyzerOutput(error="Tool has no run command")
    cmd = render_command(tool, target=workspace_service.WORK_DIR, url=app_url, config=config)
    # The marker lets cancel() find this run's processes (see _kill_script).
    marked = f": {_CMD_MARKER}:{run_id}; {cmd}" if run_id else cmd
    started = time.monotonic()
    result = docker_manager.exec_in(
        container_name,
        marked,
        timeout_s=tool.run_timeout,
        workdir=workspace_service.WORK_DIR,
    )
    if result.get("error") and result.get("exit_code") == -1:
        return AnalyzerOutput(error=_scrub(result["error"]))
    # Binary output (and the parsers' "\u0000" escapes) must not reach the database.
    raw = _scrub(result.get("output", ""))
    exit_code = result.get("exit_code")
    raw_output = {
        "command": cmd,
        "exit_code": exit_code,
        "stdout": raw[:_STDOUT_STORE_LIMIT],
        # The stored stdout is cut: re-parsing it offline would see an incomplete report.
        "stdout_truncated": len(raw) > _STDOUT_STORE_LIMIT,
        **({"target_url": app_url} if tool.analysis_mode == "dynamic" else {}),
    }
    error = _exit_error(tool, exit_code, raw, elapsed=time.monotonic() - started)
    parsed = ParsedOutput()
    if error is None:
        parsed = parsers.parse(tool.parser_key, raw) if tool.parser_key else ParsedOutput()
        error = parsed.error
        if exit_code not in (0, None) and not parsed.findings and not parsed.metrics and not parsed.recognized:
            # Linters exit non-zero *because* they found something; non-zero
            # with nothing parsed means the tool itself failed.
            error = (
                f"Exited with code {exit_code} without a parsable report: {_tail(raw)}"
                if error is None
                else f"Exited with code {exit_code}: {error}"
            )
            if exit_code in (126, 127):
                error += " (127/126 also mean the command was not found or not executable: Verify the tool's install.)"
    if error is not None:
        # Keep what the parser did find (nonexistent-package/-version findings
        # of a manifest whose audit could not run): stored on the failed
        # result, which still does not count as a measurement (study tables).
        return AnalyzerOutput(
            error=error,
            raw_output=raw_output,
            findings=parsed.findings,
            warnings=parsed.warnings,
        )
    return AnalyzerOutput(
        findings=parsed.findings,
        metrics=parsed.metrics,
        raw_output=raw_output,
        warnings=parsed.warnings,
        summary={"finding_count": len(parsed.findings)},
    )


def _tail(raw: str, limit: int = 300) -> str:
    text = (raw or "").strip()
    return ("…" + text[-limit:]) if len(text) > limit else (text or "(no output)")


# What a shell prints when the command itself is missing or not executable
# (dash, bash, busybox). Exit 127 alone proves nothing: tools use it too
# (osv-scanner: "general error").
_NOT_FOUND_RE = re.compile(r"not found|No such file or directory|Permission denied|cannot execute", re.IGNORECASE)


def _exit_error(tool: AnalyzerTool, exit_code, raw: str, *, elapsed: float | None = None) -> str | None:
    """A failure the exit code alone proves (``timeout -s KILL`` exits 137)."""
    if exit_code == 137:
        if elapsed is not None and elapsed < tool.run_timeout * 0.9:
            return (
                f"Killed (SIGKILL) after {elapsed:.1f}s, before its {tool.run_timeout}s timeout: out of memory or "
                "killed from outside; the result would be incomplete."
            )
        return f"Killed after {tool.run_timeout}s (timeout) or out of memory; the result would be incomplete."
    if exit_code in (126, 127) and _NOT_FOUND_RE.search(raw or ""):
        return f"Command not found or not executable (exit {exit_code}) — reinstall the tool: {_tail(raw)}"
    return None


# Summary keys a failed result keeps: what it was produced with (provenance),
# and which files the AI reviewer could not fit into its prompt.
_PROVENANCE_KEYS = ("tool_version", "config", "command_sha", "parser_sha", "patterns_sha", "model")
_FAILED_KEPT_KEYS = (*_PROVENANCE_KEYS, "files_not_reviewed", "tool_applicable")


def _persist_result(result: ToolResult, output: AnalyzerOutput, origins: dict[str, str] | None = None) -> None:
    # Findings and the result's status land together: a failed save must not
    # leave findings attached to a result the caller then marks failed.
    with transaction.atomic():
        _store_findings(result, output, origins or {})
        if output.has_error:
            result.status = ToolResult.Status.FAILED
            result.error_message = _scrub(output.error or "")
            result.raw_output = _scrub(output.raw_output)
            result.summary = {k: v for k, v in output.summary.items() if k in _FAILED_KEPT_KEYS}
            result.summary["measurement"] = NOT_MEASURED
            if output.findings:
                result.summary["finding_count"] = len(output.findings)
                result.summary["severity_counts"] = build_severity_counts(output.findings)
                result.summary["reported_severity_counts"] = _reported_counts(output.findings)
            if output.warnings:
                result.summary["warnings"] = output.warnings
            result.summary = _scrub(result.summary)
            result.save(update_fields=["status", "error_message", "raw_output", "summary", "duration_seconds"])
            return

        result.status = ToolResult.Status.COMPLETED
        result.raw_output = _scrub(output.raw_output)
        result.metrics = _scrub(output.metrics)
        result.summary = _scrub(
            {
                **output.summary,
                "severity_counts": build_severity_counts(output.findings),
                "reported_severity_counts": _reported_counts(output.findings),
                **({"warnings": output.warnings} if output.warnings else {}),
                "measurement": MEASURED,
            },
        )
        result.save(update_fields=["status", "raw_output", "metrics", "summary", "duration_seconds"])


def _reported_counts(findings) -> dict[str, int]:
    """Findings per level as the tool printed it (case-folded); unrated ones are not counted."""
    counts = Counter(_scrub((f.tool_severity or "").strip().lower()) for f in findings)
    counts.pop("", None)
    return dict(counts)


# ``Finding.tool_specific_data`` key holding the RunFile origin of the finding's file (spec section 8.8):
# ``starter`` | ``authored`` | ``modified`` | ``slot`` | ``parent`` (a slot variant's overlaid parent file).
# Absent for inline-source runs and for findings whose path is not a file of the analyzed tree (a URL of a
# dynamic tool, a package-only advisory): attribution treats those as unattributed, never as model code.
FILE_ORIGIN_KEY = "file_origin"


def finding_origin(origins: dict[str, str], path: str) -> str:
    """The origin of *path* (a finding's normalized file path) in the analyzed tree, "" when it is not one."""
    if not path or not origins:
        return ""
    return origins.get(path) or origins.get(path.lstrip("/")) or ""


def _tagged(extra, origin: str) -> dict:
    data = dict(extra) if isinstance(extra, dict) else {}
    if origin:
        data[FILE_ORIGIN_KEY] = origin
    return data


def _store_findings(result: ToolResult, output: AnalyzerOutput, origins: dict[str, str] | None = None) -> None:
    origins = origins or {}
    rows = []
    for f in output.findings:
        path = _scrub(_normalize_path(f.file_path))[:500]
        rows.append(
            Finding(
                result=result,
                severity=f.severity,
                category=_scrub(f.category),
                confidence=_scrub(f.confidence),
                title=_scrub(f.title)[:500],
                description=_scrub(f.description),
                suggestion=_scrub(f.suggestion),
                file_path=path,
                line_number=f.line_number,
                column_number=f.column_number,
                code_snippet=_scrub(f.code_snippet),
                rule_id=_scrub(f.rule_id)[:100],
                tool_specific_data=_scrub(_tagged(f.tool_specific_data, finding_origin(origins, path))),
                tool_severity=_scrub((f.tool_severity or "").strip())[:40],
            ),
        )
    Finding.objects.bulk_create(rows)


def _finalize(
    run: AnalysisRun,
    statuses: list[str],
    truncated_inputs: list[str] | None = None,
    excluded_paths: list[str] | None = None,
) -> None:
    findings = Finding.objects.filter(result__run=run)
    severity_counts: dict[str, int] = {}
    for sev in findings.values_list("severity", flat=True):
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    completed = sum(1 for s in statuses if s == ToolResult.Status.COMPLETED)
    failed = sum(1 for s in statuses if s == ToolResult.Status.FAILED)

    if statuses and completed == 0 and failed:
        status = AnalysisRun.Status.FAILED
    elif failed:
        status = AnalysisRun.Status.PARTIAL
    else:
        status = AnalysisRun.Status.COMPLETED

    metrics_by_tool = {slug: metrics for slug, metrics in run.results.values_list("tool_slug", "metrics") if metrics}
    # Levels exactly as the tools printed them (case-folded); unrated findings
    # come from tools that rate nothing. ``severity_counts`` is the normalized
    # (partly heuristic) level kept for aggregate statistics and thresholds.
    reported = Counter((label or "").strip().lower() for label in findings.values_list("tool_severity", flat=True))
    unrated = reported.pop("", 0)

    summary = {
        "total_findings": findings.count(),
        # Code findings are what the model wrote; dependency advisories and
        # what dynamic tools saw at runtime are reported apart (study rules).
        # A scope no tool measured is None, not 0 (a not-applicable tool
        # measures nothing); skipped tools are split into not applicable and
        # otherwise unmeasured.
        **run_scope_counts([run.pk])[run.pk],
        "severity_counts": severity_counts,
        "reported_severity_counts": dict(reported),
        "unrated_findings": unrated,
        "metrics_by_tool": metrics_by_tool,
        "tools_run": len(statuses),
        "tools_completed": completed,
        "tools_failed": failed,
        # Per (run, tool): True (ran on files it reads), False (nothing in the tree it reads:
        # not measured), None (not decided: cancelled, unavailable, app not running).
        "tool_applicable": tool_applicability(run),
    }
    if truncated_inputs:
        # The generated code was cut off at max_tokens; findings describe an
        # incomplete app, so surface that next to them.
        summary["truncated_inputs"] = truncated_inputs
    if excluded_paths:
        # Stored files inside dependency, cache or scratch directories (not the app's source).
        summary["excluded_paths"] = {"count": len(excluded_paths), "sample": excluded_paths[:20]}
    completed_at = timezone.now()
    duration = (completed_at - run.started_at).total_seconds() if run.started_at else None
    # A cancelled run keeps its status (cancel is sticky); it still gets the
    # summary of what ran before the cancel.
    _save_unless_cancelled(
        run,
        status=status,
        summary=summary,
        completed_at=completed_at,
        duration_seconds=duration,
    )
    _publish(run)


# Summary key of each finding scope's counter.
SCOPE_KEYS = {"code": "code_findings", "dependencies": "dependency_findings", "runtime": "runtime_findings"}


def tool_scope(slug: str, dynamic: set[str]) -> str:
    """The finding scope a tool measures: ``runtime`` (dynamic tools), ``dependencies``
    (known-vulnerability scanners) or ``code`` (every other tool, the AI reviewer included)."""
    if slug in dynamic:
        return "runtime"
    if slug in ADVISORY_TOOLS:
        return "dependencies"
    return "code"


def _is_measurement(status: str, skip_reason) -> bool:
    """Only a completed result measures the code.

    A tool skipped as not applicable (no in-scope file it reads) is ``not_measured``, never a zero
    (spec v3 section 8.8): a scope covered only by such tools is unmeasured (None), not 0.
    """
    return status == ToolResult.Status.COMPLETED


def tool_applicability(run: AnalysisRun) -> dict[str, bool | None]:
    """``{tool slug: tool_applicable}`` of *run*'s results (None where it was not decided)."""
    out: dict[str, bool | None] = {}
    for slug, status, summary in run.results.values_list("tool_slug", "status", "summary"):
        data = summary if isinstance(summary, dict) else {}
        value = data.get("tool_applicable")
        if value is None and status == ToolResult.Status.SKIPPED and data.get("skip_reason") == SKIP_NOT_APPLICABLE:
            value = False
        out[slug] = value if isinstance(value, bool) else None
    return out


def measured_scopes(results, dynamic: set[str] | None = None) -> set[str]:
    """The scopes that at least one measurement among *results* covers.

    *results* is an iterable of ``(tool_slug, status, skip_reason)``.
    """
    dynamic = AnalyzerTool.dynamic_slugs() if dynamic is None else dynamic
    return {tool_scope(slug, dynamic) for slug, status, reason in results if _is_measurement(status, reason)}


def finding_scope_counts(findings, measured: set[str] | None = None) -> dict[str, int | None]:
    """``code_findings`` / ``dependency_findings`` / ``runtime_findings`` of a Finding queryset.

    Dependency advisories are the known-vulnerability scanners' package
    findings (``base.is_dependency_advisory``); runtime findings come from
    dynamic tools; everything else is a code finding. With *measured* (see
    :func:`measured_scopes`), a scope that no tool measured and that has no
    findings is None: unmeasured is not zero.
    """
    dynamic = AnalyzerTool.dynamic_slugs()
    counts = findings.aggregate(
        total=Count("id"),
        dependencies=Count("id", filter=finding_scope_q("dependencies", dynamic)),
        runtime=Count("id", filter=finding_scope_q("runtime", dynamic)),
    )
    return _scope_counters(counts, measured)


def _scope_counters(counts: dict[str, int], measured: set[str] | None) -> dict[str, int | None]:
    values = {
        "code": counts["total"] - counts["dependencies"] - counts["runtime"],
        "dependencies": counts["dependencies"],
        "runtime": counts["runtime"],
    }
    return {
        SCOPE_KEYS[scope]: n if measured is None or n or scope in measured else None for scope, n in values.items()
    }


def finding_scope_q(scope: str, dynamic: set[str] | None = None) -> Q:
    """Filter on Finding for *scope*: ``code`` | ``dependencies`` | ``runtime``."""
    advisory = Q(result__tool_slug__in=ADVISORY_TOOLS, category="dependency")
    dynamic = AnalyzerTool.dynamic_slugs() if dynamic is None else dynamic
    runtime = Q(result__tool_slug__in=sorted(dynamic)) & ~advisory
    if scope == "dependencies":
        return advisory
    if scope == "runtime":
        return runtime
    if scope == "code":
        return ~advisory & ~runtime
    msg = f"Unknown finding scope {scope!r}"
    raise ValueError(msg)


def run_scope_counts(run_ids) -> dict:
    """Per run id: the scope counters (:func:`finding_scope_counts`, None when unmeasured) plus
    ``tools_not_applicable`` (skipped, nothing in the tree to read: not measured, never a zero) and
    ``tools_unmeasured`` (skipped as app not running, tool unavailable, cancelled: they said nothing).

    Computed from the rows in two grouped queries, so finished runs of any age
    get the current rules (the list and detail views fill them in at read time).
    """
    ids = list(run_ids)
    dynamic = AnalyzerTool.dynamic_slugs()
    counts = {
        str(row["result__run_id"]): row
        for row in Finding.objects.filter(result__run_id__in=ids)
        .values("result__run_id")
        .annotate(
            total=Count("id"),
            dependencies=Count("id", filter=finding_scope_q("dependencies", dynamic)),
            runtime=Count("id", filter=finding_scope_q("runtime", dynamic)),
        )
    }
    results: dict[str, list] = {}
    for run_id, slug, status, reason in ToolResult.objects.filter(run_id__in=ids).values_list(
        "run_id",
        "tool_slug",
        "status",
        "summary__skip_reason",
    ):
        results.setdefault(str(run_id), []).append((slug, status, reason))
    out = {}
    for run_id in ids:
        rows = results.get(str(run_id), [])
        skipped = [reason for _, status, reason in rows if status == ToolResult.Status.SKIPPED]
        not_applicable = sum(1 for reason in skipped if reason == SKIP_NOT_APPLICABLE)
        out[run_id] = {
            **_scope_counters(
                counts.get(str(run_id)) or {"total": 0, "dependencies": 0, "runtime": 0},
                measured_scopes(rows, dynamic),
            ),
            "tools_not_applicable": not_applicable,
            "tools_unmeasured": len(skipped) - not_applicable,
        }
    return out


def analysis_files(run: AnalysisRun, code: dict[str, str] | None = None) -> dict[str, str]:
    """The ``{path: content}`` the tools receive: *code* (default ``run.get_code_for_analysis()``)
    without files in non-source directories and generated bundles.

    A generation run's app tree already has real relative paths and is kept verbatim; pasted code
    may use bare semantic keys (``backend``) and goes through :func:`_materialize`.
    """
    code = run.get_code_for_analysis() if code is None else code
    kept = {k: v for k, v in code.items() if not is_non_source_path(k) and not is_generated_bundle(k, v)}
    if run.generation_job_id and not run.source_code:
        return {path.lstrip("/"): text for path, text in kept.items() if isinstance(text, str)}
    return _materialize(kept)


def _materialize(code: dict[str, str]) -> dict[str, str]:
    """Turn a semantic/file-keyed code map into ``{filename: content}``.

    Real files keep empty content (an empty ``__init__.py`` makes a package:
    the tools must see the tree the app runs with); empty semantic blobs
    (``backend_code`` …) are dropped.
    """
    files: dict[str, str] = {}
    for key, content in code.items():
        if not isinstance(content, str):
            continue
        name = Path(key).name
        # A file's real path (extension, directory, or a well-known extension-
        # less name such as Dockerfile) is kept as is; a bare key is a blob.
        is_path = "." in name or "/" in key.strip("/") or name in _EXTENSIONLESS_FILES
        if not content.strip() and not is_path:
            continue
        filename = key if is_path else f"{key}{_EXT_BY_KEY.get(key.lower(), '.py')}"
        files[filename.lstrip("/")] = content
    return files
