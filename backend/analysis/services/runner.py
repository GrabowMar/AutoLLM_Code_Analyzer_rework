"""Execute an :class:`AnalysisRun`: run each selected tool, persist findings."""

from __future__ import annotations

import fnmatch
import logging
import re
import shlex
import threading
import zlib
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from pathlib import PurePosixPath

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services import ai_runner
from backend.analysis.services import parsers
from backend.analysis.services import workspace_service
from backend.analysis.services.base import AnalyzerOutput
from backend.analysis.services.base import ParsedOutput
from backend.analysis.services.base import build_severity_counts
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


# Port generated apps listen on inside their container (see runtime.proxy).
APP_CONTAINER_PORT = 8000

# ToolResult.summary["skip_reason"] values. Only a not-applicable skip is a
# measurement (the app has nothing the tool reads, so zero findings is true);
# the others mean the tool said nothing about the app.
SKIP_NOT_APPLICABLE = "not_applicable"
SKIP_APP_NOT_RUNNING = "app_not_running"
SKIP_UNAVAILABLE = "unavailable"
# The run was cancelled before (or while) this tool ran; it measured nothing.
SKIP_CANCELLED = "cancelled"

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


def reap_stale_runs(stale_after: timedelta = STALE_AFTER) -> int:
    """Fail ``running`` runs whose executing thread is gone; return how many.

    A live run beats every ``HEARTBEAT_INTERVAL_S``, so only a run whose
    heartbeat (or, for rows from before heartbeats, start) is older than
    *stale_after* is reaped: a run genuinely in progress in any live
    process is never touched. Its still-``running`` tool results fail too.
    Queued (``pending``) runs whose queue stopped beating fail as well; a
    pending run that was never queued (no heartbeat) is left alone.
    """
    now = timezone.now()
    cutoff = now - stale_after
    stale = AnalysisRun.objects.filter(status=AnalysisRun.Status.RUNNING).filter(
        Q(heartbeat_at__lt=cutoff)
        | Q(heartbeat_at__isnull=True, started_at__lt=cutoff)
        | Q(heartbeat_at__isnull=True, started_at__isnull=True, updated_at__lt=cutoff),
    )
    queued = AnalysisRun.objects.filter(status=AnalysisRun.Status.PENDING, heartbeat_at__lt=cutoff)
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
        return _execute_locked(run)


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
    files = _materialize(code)
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

    app_url, app_url_error = "", ""
    if any(t.analysis_mode == "dynamic" for t in tools.values()):
        app_url, app_url_error = resolve_app_url(run)

    _set_phase(run, PHASE_TOOLS)
    statuses: list[str] = []
    cancelled = False
    for slug in run.tool_slugs:
        tool = tools.get(slug)
        cancelled = cancelled or is_cancelled(run)
        skip = _skip(tool, files, cancelled=cancelled, app_url=app_url, app_url_error=app_url_error)
        if skip is not None:
            reason, message = skip
            result = ToolResult.objects.create(
                run=run,
                tool_slug=slug,
                category=tool.category if tool else "",
                status=ToolResult.Status.SKIPPED,
                error_message=message,
                summary={"skip_reason": reason},
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

        if tool.kind == "ai":
            output = ai_runner.run(tool, files, tool_configs.get(slug) or {}, run.created_by)
        else:
            output = _run_container_tool(
                tool,
                container_name,
                config=tool_configs.get(slug),
                app_url=app_url,
                run_id=run.pk,
            )
        result.duration_seconds = round((timezone.now() - started_at).total_seconds(), 3)

        if is_cancelled(run):
            # Killed by cancel (or finished after it): not a measurement.
            cancelled = True
            result.status = ToolResult.Status.SKIPPED
            result.error_message = "Run cancelled while this tool was running"
            result.summary = {"skip_reason": SKIP_CANCELLED}
            result.save(update_fields=["status", "error_message", "summary", "duration_seconds"])
            _publish_result(result)
            statuses.append(result.status)
            continue

        if tool.kind != "ai" and tool_versions.get(slug):
            output.summary["tool_version"] = tool_versions[slug]
        # The options the findings were produced with (provenance for reports).
        output.summary["config"] = {**(tool.default_config or {}), **(tool_configs.get(slug) or {})}
        _persist_result(result, output)
        _publish_result(result)
        statuses.append(result.status)

    _finalize(run, statuses, truncated_inputs)
    return run


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
    if not applicable_files(tool, files):
        # Not "0 findings": the tool had nothing to read, so it measured nothing.
        return SKIP_NOT_APPLICABLE, f"Not applicable: no files matching {', '.join(tool.file_patterns)}"
    return None


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
    """Which parts of the analyzed code were cut off at generation time."""
    if not run.generation_job_id:
        return []
    data = getattr(run.generation_job, "result_data", None) or {}
    if not isinstance(data, dict):
        return []
    return [part for part in ("backend", "frontend") if data.get(f"{part}_truncated")]


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
    result = docker_manager.exec_in(
        container_name,
        marked,
        timeout_s=tool.run_timeout,
        workdir=workspace_service.WORK_DIR,
    )
    if result.get("error") and result.get("exit_code") == -1:
        return AnalyzerOutput(error=result["error"])
    raw = result.get("output", "")
    exit_code = result.get("exit_code")
    raw_output = {
        "command": cmd,
        "exit_code": exit_code,
        "stdout": raw[:_STDOUT_STORE_LIMIT],
        **({"target_url": app_url} if tool.analysis_mode == "dynamic" else {}),
    }
    error = _exit_error(tool, exit_code, raw)
    parsed = ParsedOutput()
    if error is None:
        parsed = parsers.parse(tool.parser_key, raw) if tool.parser_key else ParsedOutput()
        error = parsed.error
        if exit_code not in (0, None) and not parsed.findings and not parsed.metrics:
            # Linters exit non-zero *because* they found something; non-zero
            # with nothing parsed means the tool itself failed.
            error = (
                f"Exited with code {exit_code} without a parsable report: {_tail(raw)}"
                if error is None
                else f"Exited with code {exit_code}: {error}"
            )
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


def _exit_error(tool: AnalyzerTool, exit_code, raw: str) -> str | None:
    """A failure the exit code alone proves (``timeout -s KILL`` exits 137)."""
    if exit_code == 137:
        return f"Killed after {tool.run_timeout}s (timeout) or out of memory; the result would be incomplete."
    if exit_code in (126, 127):
        return f"Command not found or not executable (exit {exit_code}) — reinstall the tool: {_tail(raw)}"
    return None


def _persist_result(result: ToolResult, output: AnalyzerOutput) -> None:
    _store_findings(result, output)
    if output.has_error:
        result.status = ToolResult.Status.FAILED
        result.error_message = output.error or ""
        result.raw_output = output.raw_output
        result.summary = {k: v for k, v in output.summary.items() if k in ("tool_version", "config")}
        if output.findings:
            result.summary["finding_count"] = len(output.findings)
            result.summary["severity_counts"] = build_severity_counts(output.findings)
            result.summary["reported_severity_counts"] = _reported_counts(output.findings)
        if output.warnings:
            result.summary["warnings"] = output.warnings
        result.save(update_fields=["status", "error_message", "raw_output", "summary", "duration_seconds"])
        return

    result.status = ToolResult.Status.COMPLETED
    result.raw_output = output.raw_output
    result.metrics = output.metrics
    result.summary = {
        **output.summary,
        "severity_counts": build_severity_counts(output.findings),
        "reported_severity_counts": _reported_counts(output.findings),
        **({"warnings": output.warnings} if output.warnings else {}),
    }
    result.save(update_fields=["status", "raw_output", "metrics", "summary", "duration_seconds"])


def _reported_counts(findings) -> dict[str, int]:
    """Findings per level as the tool printed it (case-folded); unrated ones are not counted."""
    counts = Counter((f.tool_severity or "").strip().lower() for f in findings)
    counts.pop("", None)
    return dict(counts)


def _store_findings(result: ToolResult, output: AnalyzerOutput) -> None:
    Finding.objects.bulk_create(
        [
            Finding(
                result=result,
                severity=f.severity,
                category=f.category,
                confidence=f.confidence,
                title=f.title[:500],
                description=f.description,
                suggestion=f.suggestion,
                file_path=_normalize_path(f.file_path)[:500],
                line_number=f.line_number,
                column_number=f.column_number,
                code_snippet=f.code_snippet,
                rule_id=f.rule_id[:100],
                tool_specific_data=f.tool_specific_data,
                tool_severity=(f.tool_severity or "").strip()[:40],
            )
            for f in output.findings
        ],
    )


def _finalize(run: AnalysisRun, statuses: list[str], truncated_inputs: list[str] | None = None) -> None:
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
        "severity_counts": severity_counts,
        "reported_severity_counts": dict(reported),
        "unrated_findings": unrated,
        "metrics_by_tool": metrics_by_tool,
        "tools_run": len(statuses),
        "tools_completed": completed,
        "tools_failed": failed,
    }
    if truncated_inputs:
        # The generated code was cut off at max_tokens; findings describe an
        # incomplete app, so surface that next to them.
        summary["truncated_inputs"] = truncated_inputs
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


def _materialize(code: dict[str, str]) -> dict[str, str]:
    """Turn a semantic/file-keyed code map into ``{filename: content}``."""
    files: dict[str, str] = {}
    for key, content in code.items():
        if not content or not str(content).strip():
            continue
        if "." in Path(key).name:
            filename = key
        else:
            filename = f"{key}{_EXT_BY_KEY.get(key.lower(), '.py')}"
        files[filename.lstrip("/")] = content
    return files
