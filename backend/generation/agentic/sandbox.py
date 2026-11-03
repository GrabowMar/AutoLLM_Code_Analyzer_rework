"""The per-run sandbox: network, gateway sidecar, agent container and its supervisor (spec section 6.5).

Topology per attempt (nested containers of the apps daemon, never the host daemon)::

    [agent: harness] --internal network--> [llm-gateway]  --egress--> OpenRouter / executor LLM proxy
                                                          \\--CONNECT--> package registries (policy "packages")

* The agent container is attached **only** to a per-attempt ``internal`` network: its one route is the
  gateway. It runs with the study's resolved ``sandbox.memory``/``nano_cpus``, ``cap_drop ALL``,
  ``no-new-privileges`` and ``pids_limit`` (512). No writable shared volume is mounted; dependency caches are
  reached over the network and Maven gets a throwaway ``~/.m2``.
* The gateway holds the real credentials (``OPENROUTER_API_KEY`` or the local proxy token) and the attempt's
  ``GATEWAY_CONFIG``; the agent only ever sees a dummy key.
* The **entry script** (:func:`entry_script`; ``harnesses.base.entry_script`` overrides it when WP-07 ships
  one) commits the seed as ``base``, writes anchored excludes only, runs the harness under
  ``timeout -s TERM -k 15 <wall>``, writes ``changes.patch`` and an anchored ``workspace.tar``. Nested
  directories (``backend/data``, ``src/build``, ``app/dist``) are never excluded.
* The **supervisor** (:class:`Supervisor`, one per attempt, in the executor) heartbeats, polls the gateway's
  ``state.json`` (live spend), runs **idle detection** (:class:`IdleDetector`: the idle clock advances only while
  no call is in flight **and** the agent's CPU is below 5% of one CPU; it resets on every ``last_call_end``
  change and on every workspace change), handles cancellation, tracks install time and tails the harness
  stdout for live ``trajectory_step`` events. After exit the backend reads ``State.OOMKilled``.

:class:`DockerSandbox` is the production backend. Tests drive the same :class:`Supervisor` with fakes
(``generation/testing/fake_agentic.py``) and run the real :func:`entry_script` against a local directory.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shlex
import shutil
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Protocol

from django.conf import settings

from backend.generation.agentic.workspace import SeedFile
from backend.generation.agentic.workspace import make_tar
from backend.generation.agentic.workspace import read_tar
from backend.generation.contracts.harness import ExitInfo
from backend.generation.contracts.outcome import TimeSplit

if TYPE_CHECKING:
    from backend.generation.agentic.images import ImageSet
    from backend.generation.contracts.harness import ImageSpec
    from backend.generation.contracts.harness import RenderedHarness
    from backend.generation.contracts.resources import GatewayConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ENTRY_EXCLUDES",
    "GATEWAY_ALIAS",
    "GATEWAY_PORT",
    "LABEL",
    "PACKAGE_MANAGERS",
    "TAR_EXCLUDES",
    "DockerSandbox",
    "GatewayConfigInvalid",
    "IdleDetector",
    "SandboxBackend",
    "SandboxError",
    "SandboxHooks",
    "SandboxPlan",
    "SandboxResult",
    "SupervisedProcess",
    "Supervisor",
    "entry_script",
    "get_backend",
    "proxy_env",
    "reap_orphans",
    "workspace_loss",
]

LABEL: Final = "llmlab.agent-run"
GATEWAY_ALIAS: Final = "llm-gateway"
GATEWAY_PORT: Final = 8080
WORKSPACE: Final = "/workspace"
LOGS: Final = "/logs"
KILL_AFTER_S: Final = 15
# Anchored patterns only (section 6.5 step 2): nested directories are never excluded.
ENTRY_EXCLUDES: Final = ("/node_modules/", "/.venv/", "/venv/", "/data/*.db", "/.tmp/")
TAR_EXCLUDES: Final = ("./node_modules", "./.venv", "./venv", "./.git")
# Process names counted as dependency installation for the time split (section 6.5).
PACKAGE_MANAGERS: Final = frozenset(
    {"npm", "npx", "pnpm", "yarn", "bun", "pip", "pip3", "uv", "poetry", "pipenv", "go", "mvn", "gradle", "cargo"},
)
PACKAGE_REGISTRY_HOSTS: Final = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org")
DUMMY_API_KEY: Final = "sk-llmlab-dummy"  # the agent's key; the gateway holds the real one
_GATEWAY_STARTUP_S: Final = 30
_ARCHIVE_PREFIX_LOGS: Final = "logs"


class SandboxError(RuntimeError):
    """The sandbox could not be prepared or run (infrastructure, never the agent's fault)."""


class GatewayConfigInvalid(SandboxError):  # noqa: N818 - the gateway's own name for the condition
    """The gateway refused its ``GATEWAY_CONFIG`` (exit status 78): ``infra_error:gateway_config_invalid``."""


GATEWAY_CONFIG_INVALID_EXIT: Final = 78


# =====================================================================================
# Entry script (section 6.5 steps 1-5)
# =====================================================================================

_ENTRY_SCRIPT: Final = r"""
set -u
ROOT="${LLMLAB_ROOT:-}"
WS="$ROOT/workspace"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
cd "$WS" || exit 70
git init -q -b main . >/dev/null 2>&1
git config core.excludesFile /dev/null
printf '%s\n' __EXCLUDES__ > .git/info/exclude
git add -A >/dev/null 2>&1
git -c user.name=llm-lab -c user.email=agent@llm-lab.local commit -q --allow-empty -m base >/dev/null 2>&1
git tag -f base >/dev/null 2>&1
touch "$LOGS/.idle_mark"
STDIN="${LLMLAB_STDIN:-/dev/null}"
start=$(date +%s.%N)
timeout -s TERM -k __KILL__ "$LLMLAB_WALL" bash -c "$LLMLAB_COMMAND" \
    <"$STDIN" >"$LOGS/harness.out" 2>"$LOGS/harness.err" &
pid=$!
echo "$pid" >"$LOGS/harness.pid"
wait "$pid"
code=$?
end=$(date +%s.%N)
rm -f "$LOGS/harness.pid"
cd "$WS" || exit 70
git add -A >/dev/null 2>&1
git diff --cached --binary base >"$LOGS/changes.patch" 2>/dev/null
git diff --cached --name-status base >"$LOGS/name_status.txt" 2>/dev/null
tar --anchored __TAR_EXCLUDES__ -C "$WS" -cf "$LOGS/workspace.tar" . 2>>"$LOGS/harness.err"
too_large=false
size=$(wc -c <"$LOGS/workspace.tar")
limit="${LLMLAB_MAX_WORKSPACE_BYTES:-0}"
if [ "$limit" -gt 0 ] && [ "$size" -gt "$limit" ]; then
    tar -tf "$LOGS/workspace.tar" >"$LOGS/workspace.manifest"
    rm -f "$LOGS/workspace.tar"
    too_large=true
fi
printf '{"exit_code": %s, "started": %s, "ended": %s, "workspace_bytes": %s, "workspace_too_large": %s}\n' \
    "$code" "$start" "$end" "$size" "$too_large" >"$LOGS/exit.json"
exit 0
"""
STDOUT_FILE: Final = "harness.out"
EXIT_FILE: Final = "exit.json"


def entry_script(*, kill_after_s: int = KILL_AFTER_S) -> str:
    """A root-relocatable copy of the common entry script (section 6.5 steps 1-5), for local sandboxes.

    Containers run WP-07's ``harnesses.base.entry_script`` through ``sandbox_payload`` (absolute paths). This
    copy produces the same ``/logs`` layout (``harness.out``, ``harness.err``, ``exit.json``,
    ``changes.patch``, ``name_status.txt``, ``workspace.tar`` or ``workspace.manifest``, ``.idle_mark``) below
    ``$LLMLAB_ROOT`` and reads ``LLMLAB_COMMAND``, ``LLMLAB_WALL``, ``LLMLAB_STDIN`` and
    ``LLMLAB_MAX_WORKSPACE_BYTES``. It also records the ``timeout`` pid in ``harness.pid``.
    """
    return (
        _ENTRY_SCRIPT.replace("__EXCLUDES__", " ".join(shlex.quote(p) for p in ENTRY_EXCLUDES))
        .replace("__TAR_EXCLUDES__", " ".join(f"--exclude={shlex.quote(p)}" for p in TAR_EXCLUDES))
        .replace("__KILL__", str(int(kill_after_s)))
    )


@dataclass(frozen=True)
class ExitRecord:
    """``/logs/exit.json`` of the entry script."""

    exit_code: int | None
    elapsed_s: float | None
    workspace_too_large: bool


def read_exit(logs_dir: Path) -> ExitRecord | None:
    """The entry script's exit record, or ``None`` when the script never got that far."""
    try:
        data = json.loads((logs_dir / EXIT_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    code = data.get("exit_code")
    started, ended = data.get("started"), data.get("ended")
    elapsed = None
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)) and ended >= started:
        elapsed = float(ended) - float(started)
    return ExitRecord(
        exit_code=int(code) if isinstance(code, int) and not isinstance(code, bool) else None,
        elapsed_s=elapsed,
        workspace_too_large=bool(data.get("workspace_too_large")),
    )


def workspace_loss(logs_dir: Path, record: ExitRecord | None) -> str | None:
    """Why an attempt's workspace evidence is missing, or ``None`` when it is there.

    A missing ``exit.json`` (the backstop kill, the entry script dying before its tar step, ``get_archive``
    failing) or a missing ``workspace.tar`` without the truncation manifest is a platform failure: the
    backends raise ``SandboxError`` for it so the attempt is retried as infra, never analysed as a model
    outcome (no silent clean results, appendix A #5).
    """
    if record is None:
        return "the entry script left no exit record (killed before it finished or /logs could not be copied)"
    if (logs_dir / "workspace.tar").exists():
        return None
    if record.workspace_too_large or (logs_dir / "workspace.manifest").exists():
        return None
    return "the entry script left neither workspace.tar nor workspace.manifest"


def proxy_env(network_policy: str) -> dict[str, str]:
    """Egress through the gateway's CONNECT allowlist (policy ``packages`` only; HTTPS only, loopback direct)."""
    if network_policy != "packages":
        return {}
    proxy = f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}"
    no_proxy = f"{GATEWAY_ALIAS},localhost,127.0.0.1,::1,0.0.0.0"
    return {
        "HTTPS_PROXY": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        "npm_config_https_proxy": proxy,
    }


# =====================================================================================
# Plan, result, hooks
# =====================================================================================


@dataclass
class SandboxPlan:
    """Everything one attempt's sandbox needs (built by ``agentic/runner.py``)."""

    run_id: str
    attempt: int
    rendered: RenderedHarness
    seed: Mapping[str, SeedFile]
    wall_clock_s: int
    gateway_config: GatewayConfig
    idle_timeout_s: int | None = None
    max_workspace_bytes: int | None = None
    memory: str = "2g"
    nano_cpus: int = 1_000_000_000
    pids_limit: int = 512
    network_policy: str = "none"
    images: ImageSet | None = None
    gateway_env: Mapping[str, str] = field(default_factory=dict)  # real credentials; gateway only
    gateway_network: str = "bridge"  # egress network of the gateway
    local_gateway: bool = False

    @property
    def short(self) -> str:
        return f"{self.run_id.replace('-', '')[:12]}-a{int(self.attempt)}"


@dataclass
class SandboxResult:
    """What one attempt's sandbox produced. ``logs_dir`` holds the agent's ``/logs`` plus the gateway logs."""

    exit_info: ExitInfo
    logs_dir: Path
    workspace_tar: Path | None
    workspace_truncated: bool
    artifacts: dict[str, bytes] = field(default_factory=dict)
    time_split: TimeSplit = field(default_factory=TimeSplit)
    container_log_tail: str = ""
    cleanup_dirs: list[Path] = field(default_factory=list)  # temporary directories the caller removes

    def cleanup(self) -> None:
        for directory in self.cleanup_dirs:
            shutil.rmtree(directory, ignore_errors=True)

    def outputs(self) -> dict[str, bytes]:
        """Every file in ``logs_dir`` except the workspace tar, plus the harness artifacts."""
        out = {
            path.relative_to(self.logs_dir).as_posix(): path.read_bytes()
            for path in sorted(self.logs_dir.rglob("*"))
            if path.is_file() and path.name != "workspace.tar"
        }
        out.update(self.artifacts)
        return out

    def calls(self) -> list[dict[str, Any]]:
        from backend.generation.agentic.evidence import read_jsonl

        path = self.logs_dir / "calls.jsonl"
        return read_jsonl(path.read_bytes()) if path.exists() else []

    def events(self) -> list[dict[str, Any]]:
        from backend.generation.agentic.evidence import read_jsonl

        path = self.logs_dir / "gateway_events.jsonl"
        return read_jsonl(path.read_bytes()) if path.exists() else []

    def state(self) -> dict[str, Any] | None:
        path = self.logs_dir / "state.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return value if isinstance(value, dict) else None


@dataclass
class SandboxHooks:
    """Callbacks the supervisor runs while the agent works (all optional, none may raise into the loop)."""

    heartbeat: Callable[[], bool] | None = None  # False: the run is no longer ours (cancel/requeue) -> stop
    on_state: Callable[[dict[str, Any]], None] | None = None  # every state.json poll (live spend)
    on_stdout_line: Callable[[str], None] | None = None  # harness stdout, line by line
    heartbeat_every_s: float = 20.0
    state_every_s: float = 10.0


class SandboxBackend(Protocol):
    """What the runner needs from a sandbox implementation (docker in production, fakes in tests)."""

    def ensure_images(
        self,
        manifest: Mapping[str, Any],
        spec: ImageSpec,
        *,
        local_gateway: bool,
        run_id: str,
    ) -> ImageSet: ...

    def run(self, plan: SandboxPlan, hooks: SandboxHooks) -> SandboxResult: ...


# =====================================================================================
# Supervisor
# =====================================================================================


class IdleDetector:
    """The idle clock (section 6.5): it advances only while nothing is in flight and the CPU is quiet.

    ``observe`` is called once per supervisor tick with that tick's facts. Unknown facts (no ``state.json``
    yet, no CPU sample) never count as idle: a run is only ever stopped for idleness on evidence.
    """

    def __init__(self, timeout_s: float | None, *, cpu_threshold: float = 0.05) -> None:
        self.timeout_s = timeout_s
        self.cpu_threshold = cpu_threshold
        self.idle_s = 0.0  # the current idle stretch
        self.total_idle_s = 0.0  # every idle second of the attempt (time split)
        self.fired = False
        self.last_call_end: Any = None

    def observe(
        self,
        dt: float,
        *,
        inflight: int | None,
        last_call_end: Any,
        cpu_fraction: float | None,
        workspace_changed: bool,
    ) -> bool:
        """Advance by ``dt`` seconds; returns True on the tick the timeout is reached."""
        if last_call_end != self.last_call_end:
            self.last_call_end = last_call_end
            self.idle_s = 0.0
            return False
        if workspace_changed:
            self.idle_s = 0.0
            return False
        busy = inflight is None or inflight > 0 or cpu_fraction is None or cpu_fraction >= self.cpu_threshold
        if busy:
            self.idle_s = 0.0
            return False
        self.idle_s += dt
        self.total_idle_s += dt
        if self.timeout_s and not self.fired and self.idle_s >= self.timeout_s:
            self.fired = True
            return True
        return False


class SupervisedProcess(Protocol):
    """The view of a running agent the supervisor needs."""

    def exited(self) -> bool: ...
    def read_state(self) -> dict[str, Any] | None: ...
    def cpu_fraction(self) -> float | None: ...  # of one CPU, over the recent window
    def workspace_changed(self) -> bool: ...
    def processes(self) -> list[str]: ...  # command names
    def terminate(self) -> None: ...  # SIGTERM
    def kill(self) -> None: ...  # SIGKILL


@dataclass
class SupervisorReport:
    idle_fired: bool = False
    cancelled: bool = False
    backstop_killed: bool = False
    install_s: float = 0.0
    idle_s: float = 0.0
    last_state: dict[str, Any] | None = None
    elapsed_s: float = 0.0


class Supervisor:
    """Watches one running agent until it exits (section 6.5). Thread-safe ``stop()`` for teardown."""

    def __init__(
        self,
        process: SupervisedProcess,
        hooks: SandboxHooks,
        *,
        wall_clock_s: int,
        idle_timeout_s: int | None,
        tick_s: float | None = None,
        cpu_threshold: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.process = process
        self.hooks = hooks
        self.wall_clock_s = wall_clock_s
        self.tick_s = tick_s if tick_s is not None else float(getattr(settings, "AGENT_SUPERVISOR_TICK_S", 2.0))
        threshold = cpu_threshold if cpu_threshold is not None else getattr(settings, "AGENT_IDLE_CPU_FRACTION", 0.05)
        self.idle = IdleDetector(idle_timeout_s, cpu_threshold=float(threshold))
        self.clock = clock
        self.sleep = sleep
        self.report = SupervisorReport()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _call(self, hook: Callable[..., Any] | None, *args: Any) -> Any:
        if hook is None:
            return None
        try:
            return hook(*args)
        except Exception:
            logger.exception("sandbox supervisor hook %s failed", getattr(hook, "__name__", hook))
            return None

    def run(self) -> SupervisorReport:
        start = last = self.clock()
        last_beat = last_state = start - 1e9
        term_at: float | None = None
        backstop = self.wall_clock_s + KILL_AFTER_S + float(getattr(settings, "AGENT_SANDBOX_GRACE_S", 60))
        while not self._stop.is_set():
            if self.process.exited():
                break
            now = self.clock()
            dt, last = now - last, now
            if now - last_beat >= self.hooks.heartbeat_every_s:
                last_beat = now
                if self._call(self.hooks.heartbeat) is False:
                    self.report.cancelled = True
                    self.process.kill()
                    break
            state = self.process.read_state()
            if state is not None:
                self.report.last_state = state
                if now - last_state >= self.hooks.state_every_s:
                    last_state = now
                    self._call(self.hooks.on_state, state)
            names = self.process.processes()
            if any(PurePosixPath(name).name in PACKAGE_MANAGERS for name in names):
                self.report.install_s += dt
            fired = self.idle.observe(
                dt,
                inflight=state.get("inflight") if state is not None else None,
                last_call_end=state.get("last_call_end") if state is not None else self.idle.last_call_end,
                cpu_fraction=self.process.cpu_fraction(),
                workspace_changed=self.process.workspace_changed(),
            )
            if fired:
                logger.info("agent idle for %.0f s: stopping it", self.idle.idle_s)
                self.report.idle_fired = True
                term_at = now
                self.process.terminate()
            if term_at is not None and now - term_at >= KILL_AFTER_S:
                self.process.kill()
                term_at = None
            if now - start > backstop:
                # The in-container timeout should have fired long ago: this is the backstop.
                self.report.backstop_killed = True
                self.process.kill()
                break
            self.sleep(self.tick_s)
        self.report.idle_s = self.idle.total_idle_s
        self.report.elapsed_s = self.clock() - start
        return self.report


def time_split(calls: list[dict[str, Any]], *, elapsed_s: float, install_s: float, idle_s: float) -> TimeSplit:
    """``{install_s, llm_s, tool_s, idle_s}``: LLM time from the gateway call durations, tool time the rest."""
    llm_s = 0.0
    for call in calls:
        value = call.get("duration_s")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            llm_s += float(value)
    llm_s = min(llm_s, elapsed_s) if elapsed_s > 0 else llm_s
    tool_s = max(0.0, elapsed_s - llm_s - install_s - idle_s)
    return TimeSplit(
        install_s=round(install_s, 3),
        llm_s=round(llm_s, 3),
        tool_s=round(tool_s, 3),
        idle_s=round(idle_s, 3),
    )


def exit_info_from(
    *,
    exit_code: int | None,
    elapsed_s: float,
    wall_clock_s: int,
    report: SupervisorReport,
    oom_killed: bool,
    calls: list[dict[str, Any]],
    state: Mapping[str, Any] | None,
) -> ExitInfo:
    """The adapter's ``ExitInfo`` from the facts of the sandbox and the gateway."""
    from backend.generation.agentic.validity import call_ok

    exhausted = (state or {}).get("budget_exhausted") or None
    return ExitInfo(
        exit_code=exit_code,
        elapsed_s=round(elapsed_s, 3),
        wall_clock_s=int(wall_clock_s),
        idle_fired=report.idle_fired,
        oom_killed=oom_killed,
        budget_exhausted=str(exhausted) if exhausted else None,
        cancelled=report.cancelled,
        gateway_calls_ok=sum(1 for call in calls if call_ok(call)),
        gateway_calls_failed_transport=sum(1 for call in calls if call.get("transport_error")),
    )


# =====================================================================================
# Docker backend
# =====================================================================================


def _absolute_tar(files: Mapping[str, bytes]) -> bytes:
    """A tar of absolute in-container paths (``RenderedHarness.files``), to ``put_archive`` at ``/``."""
    rel: dict[str, SeedFile] = {}
    for path, data in files.items():
        pure = PurePosixPath(path)
        if not pure.is_absolute() or ".." in pure.parts:
            msg = f"rendered file paths must be absolute without '..': {path!r}"
            raise SandboxError(msg)
        rel[pure.relative_to("/").as_posix()] = SeedFile(bytes(data))
    return make_tar(rel)


class _DockerProcess:
    """``SupervisedProcess`` over a running agent container and its gateway sidecar."""

    def __init__(self, agent: Any, gateway: Any, *, cpu_window_s: float) -> None:
        self.agent = agent
        self.gateway = gateway
        self._samples: deque[tuple[float, int]] = deque()
        self._window = cpu_window_s
        self._exited = False

    def exited(self) -> bool:
        if self._exited:
            return True
        try:
            self.agent.reload()
        except Exception:  # noqa: BLE001 - a vanished container has exited
            self._exited = True
            return True
        self._exited = self.agent.status in {"exited", "dead"}
        return self._exited

    def read_state(self) -> dict[str, Any] | None:
        try:
            code, output = self.gateway.exec_run(["cat", f"{LOGS}/state.json"])
        except Exception:  # noqa: BLE001
            return None
        if code != 0:
            return None
        try:
            value = json.loads(output)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def cpu_fraction(self) -> float | None:
        try:
            stats = self.agent.stats(stream=False)
            usage = int(stats["cpu_stats"]["cpu_usage"]["total_usage"])
        except Exception:  # noqa: BLE001
            return None
        now = time.monotonic()
        self._samples.append((now, usage))
        while len(self._samples) > 2 and now - self._samples[0][0] > self._window:
            self._samples.popleft()
        if len(self._samples) < 2:
            return None
        (t0, u0), (t1, u1) = self._samples[0], self._samples[-1]
        if t1 <= t0:
            return None
        return max(0.0, (u1 - u0) / ((t1 - t0) * 1e9))

    def workspace_changed(self) -> bool:
        script = (
            f"find {WORKSPACE} -path {WORKSPACE}/node_modules -prune -o -path {WORKSPACE}/.git -prune "
            f"-o -newer {LOGS}/.idle_mark -print -quit; touch {LOGS}/.idle_mark"
        )
        try:
            code, output = self.agent.exec_run(["sh", "-c", script])
        except Exception:  # noqa: BLE001
            return False
        return code == 0 and bool(output.strip())

    def processes(self) -> list[str]:
        try:
            top = self.agent.top(ps_args="-eo comm")
        except Exception:  # noqa: BLE001
            return []
        return [str(row[0]) for row in top.get("Processes") or [] if row]

    def _signal_harness(self, signal: str) -> bool:
        # ``timeout`` forwards the signal to the harness and keeps its -k grace; the entry script then
        # still writes changes.patch and workspace.tar (killing PID 1 would lose the workspace).
        script = (
            f"if test -s {LOGS}/harness.pid; then kill -{signal} $(cat {LOGS}/harness.pid); exit $?; fi; "
            "found=1; for p in /proc/[0-9]*; do "
            '[ "$(cat $p/comm 2>/dev/null)" = timeout ] && kill -' + signal + ' "${p#/proc/}" && found=0; '
            "done; exit $found"
        )
        try:
            code, _ = self.agent.exec_run(["sh", "-c", script])
        except Exception:  # noqa: BLE001
            return False
        return code == 0

    def terminate(self) -> None:
        if not self._signal_harness("TERM"):
            with contextlib.suppress(Exception):
                self.agent.kill(signal="SIGTERM")

    def kill(self) -> None:
        if not self._signal_harness("KILL"):
            with contextlib.suppress(Exception):
                self.agent.kill()


def _archive(container: Any, path: str, dest: Path) -> dict[str, bytes]:
    """Copy ``path`` out of ``container`` into ``dest`` (regular files only); returns the small files read."""
    try:
        stream, _ = container.get_archive(path)
    except Exception as exc:  # noqa: BLE001 - a missing directory means nothing to collect
        logger.warning("get_archive(%s) failed: %s", path, exc)
        return {}
    with tempfile.TemporaryFile() as spool:
        for chunk in stream:
            spool.write(chunk)
        spool.seek(0)
        files = read_tar(spool, strip_prefix=PurePosixPath(path).name)
    for rel, entry in files.items():
        target = dest.joinpath(*PurePosixPath(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(entry.data)
    return {rel: entry.data for rel, entry in files.items()}


class DockerSandbox:
    """The production backend on the apps daemon (``runtime.services.docker_manager.new_client``)."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from backend.runtime.services import docker_manager

            self._client = docker_manager.new_client()
        return self._client

    def ensure_images(
        self,
        manifest: Mapping[str, Any],
        spec: ImageSpec,
        *,
        local_gateway: bool,
        run_id: str,
    ) -> ImageSet:
        from backend.generation.agentic.images import ensure_images

        return ensure_images(manifest, spec, client=self.client, local_gateway=local_gateway, run_id=run_id)

    def frontend_static_check(self, rows: list[Any]) -> dict[str, Any]:
        from backend.generation.agentic.static_checks import frontend_static_check

        return frontend_static_check(rows, client=self.client)

    def run(self, plan: SandboxPlan, hooks: SandboxHooks) -> SandboxResult:
        containers: list[Any] = []
        networks: list[Any] = []
        try:
            return self._run(plan, hooks, containers, networks)
        finally:
            for container in reversed(containers):
                with contextlib.suppress(Exception):
                    container.remove(force=True)
            for network in networks:
                with contextlib.suppress(Exception):
                    network.remove()

    def _run(
        self,
        plan: SandboxPlan,
        hooks: SandboxHooks,
        resources: list[Any],
        networks: list[Any],
    ) -> SandboxResult:
        client = self.client
        if plan.images is None:
            msg = "sandbox plan has no resolved images"
            raise SandboxError(msg)
        labels = {LABEL: plan.run_id, "llmlab.attempt": str(plan.attempt), "llmlab.started": str(int(time.time()))}
        if plan.local_gateway:
            from backend.inference.gateway_wiring import ensure_egress_network

            ensure_egress_network(client)
        try:
            network = client.networks.create(
                f"llmlab-agent-{plan.short}",
                driver="bridge",
                internal=True,
                labels=labels,
            )
        except Exception as exc:
            msg = f"creating the sandbox network failed: {exc}"
            raise SandboxError(msg) from exc
        networks.append(network)
        gateway = self._start_gateway(client, plan, labels, network)
        resources.append(gateway)
        payload_files, payload_env, entrypoint = container_payload(plan)
        # Sandbox env first; the rendered env goes last because it owns HOME / XDG (WP-07).
        env = {
            **proxy_env(plan.network_policy),
            "MAVEN_OPTS": "-Dmaven.repo.local=/tmp/m2",
            **payload_env,
        }
        try:
            agent = client.containers.create(
                plan.images.harness_tag,
                command=list(entrypoint),
                name=f"llmlab-agent-{plan.short}",
                environment=env,
                network=network.name,
                labels=labels,
                working_dir=WORKSPACE,
                mem_limit=plan.memory,
                memswap_limit=plan.memory,
                nano_cpus=int(plan.nano_cpus),
                pids_limit=int(plan.pids_limit),
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
        except Exception as exc:
            msg = f"creating the agent container failed: {exc}"
            raise SandboxError(msg) from exc
        resources.append(agent)
        try:
            agent.put_archive(WORKSPACE, make_tar(dict(plan.seed)))
            if payload_files:
                agent.put_archive("/", _absolute_tar(payload_files))
        except Exception as exc:
            msg = f"seeding the agent container failed: {exc}"
            raise SandboxError(msg) from exc
        process = _DockerProcess(agent, gateway, cpu_window_s=float(getattr(settings, "AGENT_IDLE_CPU_WINDOW_S", 10)))
        supervisor = Supervisor(process, hooks, wall_clock_s=plan.wall_clock_s, idle_timeout_s=plan.idle_timeout_s)
        started = time.monotonic()
        agent.start()
        tail_stop = threading.Event()
        tail = threading.Thread(
            target=self._tail,
            args=(agent, hooks, tail_stop),
            name=f"tail-{plan.short}",
            daemon=True,
        )
        tail.start()
        try:
            report = supervisor.run()
        finally:
            tail_stop.set()
        elapsed = time.monotonic() - started
        with contextlib.suppress(Exception):
            agent.wait(timeout=KILL_AFTER_S + 30)
        agent.reload()
        state = agent.attrs.get("State", {}) if isinstance(agent.attrs, dict) else {}
        oom_killed = bool(state.get("OOMKilled"))
        logs_dir = Path(tempfile.mkdtemp(prefix=f"llmlab-sbx-{plan.short}-"))
        _archive(agent, LOGS, logs_dir)
        record = read_exit(logs_dir)
        lost = None if report.cancelled else workspace_loss(logs_dir, record)
        if lost is not None:
            shutil.rmtree(logs_dir, ignore_errors=True)
            msg = f"sandbox run of {plan.run_id} attempt {plan.attempt} lost its workspace: {lost}"
            raise SandboxError(msg)
        exit_code = record.exit_code if record is not None else _container_exit_code(state)
        if record is not None and record.elapsed_s is not None:
            elapsed = record.elapsed_s
        artifacts = self._artifacts(agent, plan.rendered.artifacts)
        log_tail = ""
        with contextlib.suppress(Exception):
            log_tail = agent.logs(tail=200).decode("utf-8", errors="replace")
        with contextlib.suppress(Exception):
            gateway.stop(timeout=5)
        gateway_logs = Path(tempfile.mkdtemp(prefix=f"llmlab-gw-{plan.short}-"))
        _archive(gateway, LOGS, gateway_logs)
        for name in ("calls.jsonl", "gateway_events.jsonl", "state.json"):
            source = gateway_logs / name
            if source.exists():
                (logs_dir / name).write_bytes(source.read_bytes())
        tar_path = logs_dir / "workspace.tar"
        truncated = bool(record and record.workspace_too_large) or (
            (logs_dir / "workspace.manifest").exists() and not tar_path.exists()
        )
        calls = read_logs_calls(logs_dir)
        final_state = report.last_state
        with contextlib.suppress(FileNotFoundError, ValueError):
            final_state = json.loads((logs_dir / "state.json").read_text(encoding="utf-8"))
        exit_info = exit_info_from(
            exit_code=exit_code,
            elapsed_s=elapsed,
            wall_clock_s=plan.wall_clock_s,
            report=report,
            oom_killed=oom_killed,
            calls=calls,
            state=final_state,
        )
        return SandboxResult(
            exit_info=exit_info,
            logs_dir=logs_dir,
            workspace_tar=tar_path if tar_path.exists() else None,
            workspace_truncated=truncated,
            artifacts=artifacts,
            time_split=time_split(calls, elapsed_s=elapsed, install_s=report.install_s, idle_s=report.idle_s),
            container_log_tail=log_tail,
            cleanup_dirs=[logs_dir, gateway_logs],
        )

    @staticmethod
    def _tail(agent: Any, hooks: SandboxHooks, stop: threading.Event) -> None:
        """Follow ``/logs/harness.out`` inside the container (the entry script redirects stdout there)."""
        if hooks.on_stdout_line is None:
            return
        offset = 0
        buffer = b""
        while not stop.wait(1.0):
            script = f"tail -c +{offset + 1} {LOGS}/{STDOUT_FILE} 2>/dev/null | head -c 262144"
            try:
                code, chunk = agent.exec_run(["sh", "-c", script])
            except Exception:  # noqa: BLE001 - the live stream is best effort
                logger.debug("log tail of %s ended", getattr(agent, "name", "?"), exc_info=True)
                return
            if code != 0 or not chunk:
                continue
            offset += len(chunk)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                hooks.on_stdout_line(line.decode("utf-8", errors="replace"))

    @staticmethod
    def _artifacts(agent: Any, paths: tuple[str, ...]) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        for path in paths:
            try:
                stream, _ = agent.get_archive(path)
            except Exception:  # noqa: BLE001 - an artifact the harness did not write
                logger.debug("no artifact %s", path)
                continue
            for rel, entry in read_tar(stream).items():
                name = (
                    PurePosixPath(rel).name if rel == PurePosixPath(path).name else f"{PurePosixPath(path).name}/{rel}"
                )
                out[name] = entry.data
        return out

    @staticmethod
    def _start_gateway(client: Any, plan: SandboxPlan, labels: Mapping[str, str], network: Any) -> Any:
        if plan.images is None:  # pragma: no cover - checked by the caller
            msg = "no gateway image"
            raise SandboxError(msg)
        config = plan.gateway_config.model_dump(mode="json", by_alias=True)
        environment = {"GATEWAY_CONFIG": json.dumps(config, sort_keys=True), **plan.gateway_env}
        if plan.network_policy == "packages":
            # CONNECT egress for package installs only (GatewayConfig carries no egress section).
            environment["GATEWAY_CONNECT_ALLOWLIST"] = ",".join(PACKAGE_REGISTRY_HOSTS)
        try:
            gateway = client.containers.create(
                plan.images.gateway_tag,
                name=f"llmlab-gateway-{plan.short}",
                environment=environment,
                network=plan.gateway_network,
                labels=dict(labels),
                mem_limit=str(getattr(settings, "AGENT_GATEWAY_MEM_LIMIT", "256m")),
                nano_cpus=500_000_000,
                pids_limit=256,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            network.connect(gateway, aliases=[GATEWAY_ALIAS])
            gateway.start()
        except Exception as exc:
            msg = f"starting the LLM gateway failed: {exc}"
            raise SandboxError(msg) from exc
        deadline = time.monotonic() + _GATEWAY_STARTUP_S
        probe = f"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{GATEWAY_PORT}/healthz')"
        while time.monotonic() < deadline:
            gateway.reload()
            if gateway.status != "running":
                logs = gateway.logs(tail=50).decode(errors="replace")
                state = (gateway.attrs or {}).get("State", {}) if isinstance(gateway.attrs, dict) else {}
                if state.get("ExitCode") == GATEWAY_CONFIG_INVALID_EXIT:
                    msg = f"LLM gateway refused its configuration: {logs[-1000:]}"
                    raise GatewayConfigInvalid(msg)
                msg = f"LLM gateway exited during startup: {logs[-1000:]}"
                raise SandboxError(msg)
            code, _ = gateway.exec_run(["python", "-c", probe])
            if code == 0:
                return gateway
            time.sleep(0.5)
        msg = f"LLM gateway did not become healthy within {_GATEWAY_STARTUP_S}s"
        raise SandboxError(msg)


def read_logs_calls(logs_dir: Path) -> list[dict[str, Any]]:
    """The gateway ``calls.jsonl`` copied into a sandbox logs directory."""
    from backend.generation.agentic.evidence import read_jsonl

    path = logs_dir / "calls.jsonl"
    return read_jsonl(path.read_bytes()) if path.exists() else []


def _container_exit_code(state: Mapping[str, Any]) -> int | None:
    code = state.get("ExitCode")
    return int(code) if isinstance(code, int) and not state.get("Running") else None


def container_payload(plan: SandboxPlan) -> tuple[dict[str, bytes], dict[str, str], tuple[str, ...]]:
    """``(files, env, entrypoint)`` of the agent container.

    WP-07's ``harnesses.base.sandbox_payload`` (the common entry script at ``/run/entry.sh`` and the
    harness's ``command.sh``) when available; otherwise the relocatable :func:`entry_script` with the command
    passed through the environment.
    """
    try:
        from backend.generation.harnesses import base

        payload = base.sandbox_payload(
            plan.rendered,
            wall_clock_s=plan.wall_clock_s,
            max_workspace_bytes=plan.max_workspace_bytes,
        )
    except (ImportError, AttributeError, NotImplementedError):
        env = {
            **plan.rendered.env,
            "LLMLAB_COMMAND": plan.rendered.command,
            "LLMLAB_WALL": str(int(plan.wall_clock_s)),
            "LLMLAB_MAX_WORKSPACE_BYTES": str(int(plan.max_workspace_bytes or 0)),
        }
        if plan.rendered.stdin_path:
            env["LLMLAB_STDIN"] = plan.rendered.stdin_path
        return dict(plan.rendered.files), env, ("bash", "-c", entry_script())
    return dict(payload.files), dict(payload.env), tuple(payload.entrypoint)


def reap_orphans(older_than_seconds: int = 0, *, client: Any = None) -> int:
    """Remove agent-run containers and networks left by a crashed executor; returns the containers removed."""
    if client is None:
        from backend.runtime.services import docker_manager

        client = docker_manager.new_client()
    removed = 0
    now = time.time()
    for container in client.containers.list(all=True, filters={"label": LABEL}):
        labels = getattr(container, "labels", {}) or {}
        try:
            started = float(labels.get("llmlab.started") or 0)
        except ValueError:
            started = 0.0
        if now - started >= older_than_seconds:
            with contextlib.suppress(Exception):
                container.remove(force=True)
                removed += 1
    for network in client.networks.list(filters={"label": LABEL}):
        try:
            network.remove()
        except Exception as exc:  # noqa: BLE001 - still in use by a live run
            logger.debug("Keeping agent network %s: %s", getattr(network, "name", "?"), exc)
    return removed


def get_backend() -> SandboxBackend:
    """The sandbox backend (``settings.GENERATION_SANDBOX_BACKEND``: ``docker``; tests patch this function)."""
    name = str(getattr(settings, "GENERATION_SANDBOX_BACKEND", "docker"))
    if name != "docker":
        msg = f"unknown sandbox backend {name!r}"
        raise SandboxError(msg)
    return DockerSandbox()
