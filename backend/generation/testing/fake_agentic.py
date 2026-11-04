"""Fakes for the agentic run pipeline (WP-08): a local sandbox backend and fake image resolution.

``LocalSandboxBackend`` implements ``agentic.sandbox.SandboxBackend`` without docker:

* the container's ``/`` is a temporary directory (``/workspace``, ``/logs``, ``/run`` ... below it), and the
  **real** ``agentic.sandbox.entry_script()`` runs there with ``LLMLAB_ROOT`` set (so the anchored excludes,
  the ``changes.patch``, ``workspace.tar`` and the workspace size limit are the production ones);
* a ``FakeGateway`` (``testing/fake_gateway.py``) serves the attempt's ``GatewayConfig`` with scripted replies;
  ``http://llm-gateway:8080`` in the rendered env, files and command is rewritten to it;
* the production ``agentic.sandbox.Supervisor`` watches the run through :class:`LocalProcess`: in-flight calls
  and ``last_call_end`` come from the gateway's ``state.json``, the CPU fraction from a scripted callable, and
  workspace changes from file mtimes. Idle termination signals the ``timeout`` process (``harness.pid``), as
  the docker backend does, so the workspace is still archived;
* ``oom_killed`` can be scripted (docker's ``State.OOMKilled`` has no local equivalent).

``ensure_images`` returns the manifest's own digests (or raises a scripted ``ImageDigestMismatch``), and
``gateway_wiring`` needs no credentials.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Final

from backend.generation.agentic import sandbox as sandbox_module
from backend.generation.agentic.images import ImageDigestMismatch
from backend.generation.agentic.images import ImageSet
from backend.generation.agentic.sandbox import SandboxHooks
from backend.generation.agentic.sandbox import SandboxPlan
from backend.generation.agentic.sandbox import SandboxResult
from backend.generation.agentic.sandbox import Supervisor
from backend.generation.agentic.sandbox import exit_info_from
from backend.generation.agentic.sandbox import time_split
from backend.generation.testing.fake_gateway import FakeGateway
from backend.generation.testing.fake_gateway import ScriptedReply

__all__ = ["LocalProcess", "LocalSandboxBackend", "fake_image_set"]

SANDBOX_ORIGIN: Final = f"http://{sandbox_module.GATEWAY_ALIAS}:{sandbox_module.GATEWAY_PORT}"


def fake_image_set(manifest: Mapping[str, Any]) -> ImageSet:
    """The images a manifest pins, as if they were built exactly as recorded."""
    harness = manifest.get("harness") or {}
    gateway = manifest.get("gateway") or {}
    return ImageSet(
        harness_tag=f"llmlab/{harness.get('image', 'agent-fake')}:test",
        harness_digest=str(harness.get("image_digest") or ""),
        gateway_tag="llmlab/llm-gateway:test",
        gateway_digest=str(gateway.get("image_digest") or ""),
        starter_base={
            str(b.get("ref")): str(b.get("digest")) for b in (manifest.get("starter") or {}).get("base_images") or []
        },
    )


def _container_path(root: Path, path: str) -> Path:
    pure = PurePosixPath(path)
    if not pure.is_absolute() or ".." in pure.parts:
        msg = f"rendered file paths must be absolute in-container paths without '..': {path!r}"
        raise ValueError(msg)
    return root.joinpath(*pure.parts[1:])


class LocalProcess:
    """``SupervisedProcess`` over a local entry-script process and a ``FakeGateway``."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        root: Path,
        gateway: FakeGateway | None,
        *,
        cpu: Callable[[], float | None],
    ) -> None:
        self.process = process
        self.root = root
        self.gateway = gateway
        self._cpu = cpu
        self._mark = time.time()
        self.signals: list[str] = []

    def exited(self) -> bool:
        return self.process.poll() is not None

    def read_state(self) -> dict[str, Any] | None:
        if self.gateway is None:
            return None
        try:
            value = json.loads(self.gateway.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def cpu_fraction(self) -> float | None:
        return self._cpu()

    def workspace_changed(self) -> bool:
        workspace = self.root / "workspace"
        newest = 0.0
        for path in workspace.rglob("*"):
            if ".git" in path.relative_to(workspace).parts or "node_modules" in path.parts:
                continue
            with contextlib.suppress(FileNotFoundError):
                newest = max(newest, path.stat().st_mtime)
        changed = newest > self._mark
        self._mark = max(self._mark, newest, time.time())
        return changed

    def processes(self) -> list[str]:
        return []

    def _harness_pid(self) -> int | None:
        try:
            return int((self.root / "logs" / "harness.pid").read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def _signal(self, sig: signal.Signals) -> None:
        self.signals.append(sig.name)
        pid = self._harness_pid()
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if pid is not None:
                os.kill(pid, sig)
            else:
                os.killpg(self.process.pid, sig)

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)


@dataclass
class LocalSandboxBackend:
    """A ``SandboxBackend`` that runs the real entry script locally against a ``FakeGateway``.

    ``replies`` script the gateway's upstream (consumed in order, ``default_reply`` afterwards). ``cpu`` is the
    agent's CPU fraction as the supervisor sees it (default: quiet, 0.0). ``tick_s`` is the supervisor tick.
    """

    replies: Any = ()
    default_reply: ScriptedReply | None = None
    cpu: Callable[[], float | None] = lambda: 0.0
    oom_killed: bool = False
    mismatch_role: str | None = None
    # A /logs file removed after the entry script ends ("exit.json", "workspace.tar"): simulates the backstop
    # kill or a failed get_archive (the backend then raises SandboxError, as the docker backend does).
    drop_logs: str | None = None
    tick_s: float = 0.05
    heartbeat_every_s: float = 0.2
    state_every_s: float = 0.1
    keep_roots: bool = False
    roots: list[Path] = field(default_factory=list)
    plans: list[SandboxPlan] = field(default_factory=list)
    processes: list[LocalProcess] = field(default_factory=list)
    gateways: list[FakeGateway] = field(default_factory=list)

    # -- SandboxBackend ---------------------------------------------------------------------------

    def gateway_wiring(self, run: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {"env": {}, "network": "bridge", "local": False}

    def ensure_images(self, manifest: Mapping[str, Any], spec: Any, *, local_gateway: bool, run_id: str) -> ImageSet:
        images = fake_image_set(manifest)
        if self.mismatch_role is not None:
            raise ImageDigestMismatch(self.mismatch_role, "sha256:" + "0" * 64, "sha256:" + "1" * 64, "llmlab/x:y")
        return images

    def run(self, plan: SandboxPlan, hooks: SandboxHooks) -> SandboxResult:
        self.plans.append(plan)
        root = Path(tempfile.mkdtemp(prefix="wp08-sandbox-"))
        self.roots.append(root)
        for name in ("workspace", "logs", "run", "tmp", "gw"):
            (root / name).mkdir(exist_ok=True)
        gateway = FakeGateway(
            plan.gateway_config,
            replies=self.replies,
            default_reply=self.default_reply,
            logs_dir=root / "gw",
        )
        self.gateways.append(gateway)
        with gateway:
            return self._run(plan, hooks, root, gateway)

    # -- one run ------------------------------------------------------------------------------------

    def _rewrite(self, value: str, gateway: FakeGateway) -> str:
        return value.replace(SANDBOX_ORIGIN, gateway.origin)

    def _run(self, plan: SandboxPlan, hooks: SandboxHooks, root: Path, gateway: FakeGateway) -> SandboxResult:
        for rel, entry in plan.seed.items():
            target = _container_path(root, f"/workspace/{rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.data)
            target.chmod(entry.mode)
        for path, raw in plan.rendered.files.items():
            target = _container_path(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw.replace(SANDBOX_ORIGIN.encode(), gateway.origin.encode()))
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root / "tmp"),
            "TMPDIR": str(root / "tmp"),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            **{key: self._rewrite(value, gateway) for key, value in plan.rendered.env.items()},
            "LLMLAB_ROOT": str(root),
            "LLMLAB_WALL": str(int(plan.wall_clock_s)),
            "LLMLAB_COMMAND": self._rewrite(plan.rendered.command, gateway),
            "LLMLAB_MAX_WORKSPACE_BYTES": str(int(plan.max_workspace_bytes or 0)),
            "PYTHON": sys.executable,
        }
        if plan.rendered.stdin_path:
            env["LLMLAB_STDIN"] = str(_container_path(root, plan.rendered.stdin_path))
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - the production entry script, run by tests only
            ["bash", "-c", sandbox_module.entry_script()],  # noqa: S607
            env=env,
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        local = LocalProcess(process, root, gateway, cpu=self.cpu)
        self.processes.append(local)
        hooks = SandboxHooks(
            heartbeat=hooks.heartbeat,
            on_state=hooks.on_state,
            on_stdout_line=hooks.on_stdout_line,
            heartbeat_every_s=self.heartbeat_every_s,
            state_every_s=self.state_every_s,
        )
        supervisor = Supervisor(
            local,
            hooks,
            wall_clock_s=plan.wall_clock_s,
            idle_timeout_s=plan.idle_timeout_s,
            tick_s=self.tick_s,
        )
        # The supervisor runs on the calling thread (as in the docker backend), so its heartbeat sees the
        # caller's database connection; a helper thread drains the process.
        drained: dict[str, bytes] = {}

        def drain() -> None:
            try:
                _, drained["stderr"] = process.communicate(timeout=plan.wall_clock_s + 60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                _, drained["stderr"] = process.communicate()

        waiter = threading.Thread(target=drain, name="wp08-sandbox-wait", daemon=True)
        waiter.start()
        supervisor.run()
        waiter.join(timeout=plan.wall_clock_s + 90)
        script_err = drained.get("stderr", b"")
        elapsed = time.monotonic() - started
        logs = root / "logs"
        if hooks.on_stdout_line is not None and (logs / sandbox_module.STDOUT_FILE).exists():
            for line in (logs / sandbox_module.STDOUT_FILE).read_text(encoding="utf-8", errors="replace").splitlines():
                hooks.on_stdout_line(line)
        for source in (gateway.calls_path, gateway.events_path, gateway.state_path):
            if source.exists():
                shutil.copyfile(source, logs / source.name)
        record = sandbox_module.read_exit(logs)
        if self.drop_logs is not None:
            (logs / self.drop_logs).unlink(missing_ok=True)
            record = sandbox_module.read_exit(logs)
        lost = None if supervisor.report.cancelled else sandbox_module.workspace_loss(logs, record)
        if lost is not None:
            msg = f"sandbox run lost its workspace: {lost}: {script_err.decode(errors='replace')}"
            raise sandbox_module.SandboxError(msg)
        exit_code = record.exit_code if record is not None else None
        calls = sandbox_module.read_logs_calls(logs)
        state = None
        with contextlib.suppress(FileNotFoundError, ValueError):
            state = json.loads((logs / "state.json").read_text(encoding="utf-8"))
        report = supervisor.report
        artifacts: dict[str, bytes] = {}
        for path in plan.rendered.artifacts:
            local_path = _container_path(root, path)
            if local_path.is_file():
                artifacts[PurePosixPath(path).name] = local_path.read_bytes()
        tar_path = logs / "workspace.tar"
        return SandboxResult(
            exit_info=exit_info_from(
                exit_code=exit_code,
                elapsed_s=elapsed,
                wall_clock_s=plan.wall_clock_s,
                report=report,
                oom_killed=self.oom_killed,
                calls=calls,
                state=state,
            ),
            logs_dir=logs,
            workspace_tar=tar_path if tar_path.exists() else None,
            workspace_truncated=bool(record and record.workspace_too_large),
            artifacts=artifacts,
            time_split=time_split(calls, elapsed_s=elapsed, install_s=report.install_s, idle_s=report.idle_s),
            cleanup_dirs=[] if self.keep_roots else [root],
        )
