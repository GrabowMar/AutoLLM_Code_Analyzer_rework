"""``FakeSandbox``: the sandbox entry script against a temporary directory, without docker (spec 6.5).

The container's filesystem is mapped under one root directory: ``/workspace`` -> ``<root>/workspace``,
``/logs`` -> ``<root>/logs``, ``/run`` -> ``<root>/run``, and so on for every absolute path in
``RenderedHarness.files``. The command sees ``LLMLAB_ROOT=<root>`` (the fake harness prefixes its paths with
it) and ``PYTHON`` (this interpreter). Gateway URLs (``http://gateway:8080``) in the rendered env and files are
rewritten to a running ``FakeGateway``.

The entry script follows section 6.5:

1. ``git init`` and commit the seed as ``base``;
2. ``core.excludesFile=/dev/null`` and only the anchored patterns in ``.git/info/exclude``;
3. ``timeout -s TERM -k 15 <wall> <command>`` (stdin from ``stdin_path`` when given);
4. ``git add -A && git diff --binary base > /logs/changes.patch``;
5. an anchored ``tar`` of the workspace without ``node_modules``, ``.venv``, ``venv`` and ``.git`` into
   ``/logs/workspace.tar``. A tar larger than ``max_workspace_bytes`` is replaced by a path manifest and
   ``workspace_truncated`` is set.

The supervisor parts that need docker are reduced to what tests use: elapsed time, cancellation
(``cancel`` event: the process group is killed), and the gateway facts of ``ExitInfo`` (calls ok, transport
failures, ``budget_exhausted``) read from the gateway or from ``/logs``. Idle detection and OOM are not
simulated (``idle_fired`` / ``oom_killed`` are always False).
"""

from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Self

from backend.generation.contracts.harness import ExitInfo
from backend.generation.contracts.harness import RenderedHarness

if TYPE_CHECKING:
    from backend.generation.testing.fake_gateway import FakeGateway

__all__ = ["ENTRY_EXCLUDES", "FakeSandbox", "SandboxRun", "tar_bytes"]

GATEWAY_ORIGIN: Final = "http://gateway:8080"
# Anchored patterns only: nested directories are never excluded (section 6.5).
ENTRY_EXCLUDES: Final = ("/node_modules/", "/.venv/", "/venv/", "/data/*.db", "/.tmp/")
TAR_EXCLUDES: Final = ("./node_modules", "./.venv", "./venv", "./.git")
_KILL_AFTER_S: Final = 15

_ENTRY_SCRIPT: Final = r"""
set -u
ROOT="$LLMLAB_ROOT"
WS="$ROOT/workspace"
LOGS="$ROOT/logs"
cd "$WS"
git init -q
git config core.excludesFile /dev/null
git config user.name llmlab
git config user.email llmlab@sandbox.invalid
printf '%s\n' __EXCLUDES__ >> .git/info/exclude
git add -A
git commit -q --allow-empty -m base
git tag base
set +e
STDIN="${LLMLAB_STDIN:-/dev/null}"
timeout -s TERM -k __KILL__ "$LLMLAB_WALL" bash -c "$LLMLAB_COMMAND" \
    <"$STDIN" >"$LOGS/agent.stdout" 2>"$LOGS/agent.stderr"
code=$?
set -e
echo "$code" >"$LOGS/exit_code"
cd "$WS"
git add -A && git diff --binary base >"$LOGS/changes.patch"
tar --anchored __TAR_EXCLUDES__ -C "$WS" -cf "$LOGS/workspace.tar" .
exit 0
"""


def _entry_script() -> str:
    return (
        _ENTRY_SCRIPT.replace("__EXCLUDES__", " ".join(shlex.quote(p) for p in ENTRY_EXCLUDES))
        .replace("__TAR_EXCLUDES__", " ".join(f"--exclude={shlex.quote(p)}" for p in TAR_EXCLUDES))
        .replace("__KILL__", str(_KILL_AFTER_S))
    )


@dataclass
class SandboxRun:
    """The result of one ``FakeSandbox.run``: the directories, the ``ExitInfo`` and the logs."""

    root: Path
    exit_info: ExitInfo
    workspace_truncated: bool = False
    artifacts: dict[str, bytes] = field(default_factory=dict)

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def stdout(self) -> str:
        return _read_text(self.logs / "agent.stdout")

    @property
    def stderr(self) -> str:
        return _read_text(self.logs / "agent.stderr")

    @property
    def changes_patch(self) -> bytes:
        path = self.logs / "changes.patch"
        return path.read_bytes() if path.exists() else b""

    def outputs(self) -> dict[str, bytes]:
        """Every file under ``/logs`` (by relative path) plus the collected harness artifacts."""
        out = {
            path.relative_to(self.logs).as_posix(): path.read_bytes()
            for path in sorted(self.logs.rglob("*"))
            if path.is_file()
        }
        out.update(self.artifacts)
        return out

    def calls(self) -> list[dict[str, Any]]:
        path = self.logs / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def state(self) -> dict[str, Any] | None:
        path = self.logs / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def workspace_files(self) -> dict[str, bytes]:
        """The files in ``/logs/workspace.tar`` (what the collector reads), by relative POSIX path."""
        tar_path = self.logs / "workspace.tar"
        if self.workspace_truncated or not tar_path.exists():
            return {}
        out: dict[str, bytes] = {}
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    handle = tar.extractfile(member)
                    if handle is not None:
                        out[PurePosixPath(member.name).relative_to(".").as_posix()] = handle.read()
        return out


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _container_path(root: Path, path: str) -> Path:
    pure = PurePosixPath(path)
    if not pure.is_absolute() or ".." in pure.parts:
        msg = f"rendered file paths must be absolute in-container paths without '..': {path!r}"
        raise ValueError(msg)
    return root.joinpath(*pure.parts[1:])


class FakeSandbox:
    """Run a ``RenderedHarness`` like the sandbox container does, in a directory on this machine.

    ``root`` is the directory that stands in for the container's ``/`` (a new temporary directory per run
    when omitted; it is kept for inspection and removed by ``cleanup()``).
    """

    def __init__(
        self,
        *,
        gateway: FakeGateway | None = None,
        root: Path | str | None = None,
        wall_clock_s: int = 120,
        max_workspace_bytes: int | None = None,
    ) -> None:
        self.gateway = gateway
        self._root = Path(root) if root is not None else None
        self._owned: list[Path] = []
        self.wall_clock_s = wall_clock_s
        self.max_workspace_bytes = max_workspace_bytes

    def cleanup(self) -> None:
        for path in self._owned:
            shutil.rmtree(path, ignore_errors=True)
        self._owned.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()

    # -- one run -----------------------------------------------------------------------------------

    def _rewrite(self, value: str) -> str:
        if self.gateway is None:
            return value
        return value.replace(GATEWAY_ORIGIN, self.gateway.origin)

    def _prepare(self, rendered: RenderedHarness, seed: Mapping[str, bytes | str] | None) -> Path:
        if self._root is not None:
            root = self._root
            root.mkdir(parents=True, exist_ok=True)
        else:
            root = Path(tempfile.mkdtemp(prefix="fake-sandbox-"))
            self._owned.append(root)
        for name in ("workspace", "logs", "run", "tmp"):
            (root / name).mkdir(exist_ok=True)
        for rel, data in (seed or {}).items():
            target = _container_path(root, f"/workspace/{rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data.encode("utf-8") if isinstance(data, str) else bytes(data))
        for path, raw in rendered.files.items():
            target = _container_path(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.gateway is not None:
                raw = raw.replace(GATEWAY_ORIGIN.encode(), self.gateway.origin.encode())  # noqa: PLW2901
            target.write_bytes(raw)
        return root

    def run(
        self,
        rendered: RenderedHarness,
        *,
        seed: Mapping[str, bytes | str] | None = None,
        wall_clock_s: int | None = None,
        cancel: threading.Event | None = None,
    ) -> SandboxRun:
        """Seed the workspace, run the entry script and return the run's logs and ``ExitInfo``."""
        wall = wall_clock_s or self.wall_clock_s
        root = self._prepare(rendered, seed)
        command = self._rewrite(rendered.command)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root / "tmp"),
            "TMPDIR": str(root / "tmp"),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            **{key: self._rewrite(value) for key, value in rendered.env.items()},
            "LLMLAB_ROOT": str(root),
            "LLMLAB_WALL": str(wall),
            "LLMLAB_COMMAND": command,
            "PYTHON": sys.executable,
        }
        if rendered.stdin_path:
            env["LLMLAB_STDIN"] = str(_container_path(root, rendered.stdin_path))
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - a fixed script, run by tests only
            ["bash", "-c", _entry_script()],  # noqa: S607 - bash from PATH, as in the sandbox image
            env=env,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        cancelled = False
        deadline = started + wall + _KILL_AFTER_S + 30
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                cancelled = True
                os.killpg(process.pid, signal.SIGKILL)
                break
            if time.monotonic() > deadline:
                os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.05)
        _, script_err = process.communicate()
        elapsed = time.monotonic() - started
        logs = root / "logs"
        exit_path = logs / "exit_code"
        if exit_path.exists():
            exit_code: int | None = int(exit_path.read_text().strip())
        elif cancelled:
            exit_code = None
        else:
            msg = f"the sandbox entry script failed before the command ran: {script_err.decode(errors='replace')}"
            raise RuntimeError(msg)
        truncated = self._enforce_workspace_limit(logs)
        artifacts = self._collect_artifacts(root, rendered)
        self._copy_gateway_logs(logs)
        calls = [json.loads(line) for line in _read_text(logs / "calls.jsonl").splitlines() if line.strip()]
        state = json.loads(_read_text(logs / "state.json") or "null")
        exit_info = ExitInfo(
            exit_code=exit_code,
            elapsed_s=round(elapsed, 3),
            wall_clock_s=wall,
            idle_fired=False,
            oom_killed=False,
            budget_exhausted=(state or {}).get("budget_exhausted"),
            cancelled=cancelled,
            gateway_calls_ok=sum(1 for c in calls if (c.get("status") or 0) < 400 and c.get("usage") is not None),
            gateway_calls_failed_transport=sum(1 for c in calls if c.get("transport_error")),
        )
        return SandboxRun(root=root, exit_info=exit_info, workspace_truncated=truncated, artifacts=artifacts)

    def _enforce_workspace_limit(self, logs: Path) -> bool:
        tar_path = logs / "workspace.tar"
        if self.max_workspace_bytes is None or not tar_path.exists():
            return False
        if tar_path.stat().st_size <= self.max_workspace_bytes:
            return False
        with tarfile.open(tar_path) as tar:
            names = sorted(m.name for m in tar.getmembers() if m.isfile())
        tar_path.unlink()
        (logs / "workspace.manifest").write_text("\n".join(names) + "\n", encoding="utf-8")
        return True

    @staticmethod
    def _collect_artifacts(root: Path, rendered: RenderedHarness) -> dict[str, bytes]:
        out = {}
        for path in rendered.artifacts:
            local = _container_path(root, path)
            if local.is_file():
                out[PurePosixPath(path).name] = local.read_bytes()
        return out

    def _copy_gateway_logs(self, logs: Path) -> None:
        """With a gateway, its calls, events and state are the authoritative logs (as in the runner)."""
        if self.gateway is None:
            return
        for path in (self.gateway.calls_path, self.gateway.events_path, self.gateway.state_path):
            if path.exists():
                shutil.copyfile(path, logs / path.name)


def tar_bytes(files: Mapping[str, bytes]) -> bytes:
    """A tar of ``files`` (relative paths), the shape of a seed tar or ``workspace.tar``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo(f"./{rel}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
