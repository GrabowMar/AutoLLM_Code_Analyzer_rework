"""Where an evaluation runs: build, start, restart, tester, adherence, data read-back (spec 6.10, 6.11).

``suite.evaluate`` talks to an ``EvalRuntime``:

* ``DockerEvalRuntime`` (production): ``sample_builds.build_sample`` on the apps daemon, an *internal* bridge
  network per evaluation (no egress), the app as alias ``app`` (memory/CPU caps, all capabilities dropped),
  the eval-tester image on the same network as alias ``mock.internal`` (the ``ssrf_mock`` server), and the
  adherence-checker image. The tester and checker images must match the digests pinned in the suite
  (``EvalSuiteContent.tester.image_digest`` / ``adherence_checker.image_digest``): a mismatch raises
  ``ImageDigestMismatchError`` (``infra_error``, reason ``image_digest_mismatch``), never an evaluation on an unknown
  image.
* ``LocalProcessRuntime``: runs a bundled standard-library app (``llmlab-local.json`` at the tree root; the
  conformance kit's probe mutants and the WP-09 tests) as a local process, and the tester and checker
  in-process from the same files the images ship. It refuses any other tree, so it can never execute a
  model's code outside a sandbox.

``get_runtime()`` picks by ``settings.EVAL_RUNTIME`` (``docker`` by default), read at call time.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping

    from backend.generation.models import GenerationRun

logger = logging.getLogger(__name__)

__all__ = [
    "AppHandle",
    "BuildOutcome",
    "DockerEvalRuntime",
    "EvalRuntime",
    "ImageDigestMismatchError",
    "LocalProcessRuntime",
    "RuntimeInfraError",
    "TesterCrashError",
    "get_runtime",
    "split_pg_dump",
]

LABEL = "llmlab.evaluation"


def _name_prefix() -> str:
    """Container/network name prefix (``settings.EVAL_NAME_PREFIX``, default ``llmlab-eval``)."""
    return str(getattr(settings, "EVAL_NAME_PREFIX", "") or "llmlab-eval")


LOCAL_MARKER = "llmlab-local.json"


class RuntimeInfraError(RuntimeError):
    """The platform could not run the step (docker, network, disk): ``infra_error``, never the sample's fault."""

    reason = "infra"


class ImageDigestMismatchError(RuntimeInfraError):
    reason = "image_digest_mismatch"


class TesterCrashError(RuntimeInfraError):
    """The tester produced no result (crash, timeout without output): retried inside the eval stage."""

    reason = "tester_crash"


@dataclass
class BuildOutcome:
    ok: bool
    image: str = ""
    build_key: str = ""
    infra_suspected: bool = False
    timed_out: bool = False
    detail: dict[str, Any] = field(default_factory=dict)
    log_tail: str = ""


@dataclass
class AppHandle:
    image: str
    env: dict[str, str]
    ref: Any = None  # container / process
    workdir: str = ""
    port: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class EvalRuntime(Protocol):
    name: str

    def build(
        self,
        run: GenerationRun,
        *,
        force_rebuild: bool,
        cancelled: Callable[[], bool] | None,
    ) -> BuildOutcome: ...

    def start(
        self,
        image: str,
        env: Mapping[str, str],
        *,
        overlay_files: Mapping[str, bytes] | None = None,
    ) -> AppHandle: ...

    def wait_healthy(self, handle: AppHandle, path: str, timeout_s: float) -> bool: ...

    def start_database(self, kind: str) -> dict[str, str]: ...

    def dump_database(self) -> dict[str, bytes]: ...

    def restart(self, handle: AppHandle) -> None: ...

    def run_tester(
        self,
        handle: AppHandle,
        plan: dict[str, Any],
        env: Mapping[str, str],
        timeout_s: float,
    ) -> dict[str, Any]: ...

    def run_adherence(
        self,
        tree: Mapping[str, bytes],
        expect: dict[str, Any],
    ) -> tuple[dict[str, Any], bytes | None]: ...

    def read_data(self, handle: AppHandle, data_dir: str) -> dict[str, bytes]: ...

    def logs(self, handle: AppHandle) -> str: ...

    def stop(self, handle: AppHandle) -> None: ...

    def close(self) -> None: ...


def get_runtime(suite_content: Mapping[str, Any] | None = None) -> EvalRuntime:
    """The configured runtime (``settings.EVAL_RUNTIME``: ``docker`` | ``local``)."""
    kind = getattr(settings, "EVAL_RUNTIME", "docker") or "docker"
    if kind == "local":
        return LocalProcessRuntime()
    return DockerEvalRuntime(suite_content or {})


# =====================================================================================
# Local: bundled stdlib apps only
# =====================================================================================


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_tree(root: Path, files: Mapping[str, bytes]) -> None:
    for rel, data in files.items():
        target = root.joinpath(*Path(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class LocalProcessRuntime:
    """Runs bundled standard-library apps as local processes; tester and checker in-process."""

    name = "local"

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="llmlab-eval-local-")
        self.root = Path(self._tmp.name)
        self._handles: list[AppHandle] = []

    def build(
        self,
        run: GenerationRun,
        *,
        force_rebuild: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> BuildOutcome:
        from backend.generation.build.context import BuildContextReport
        from backend.generation.build.context import prepare_build_dir

        report = BuildContextReport()
        dest = Path(tempfile.mkdtemp(prefix="build-", dir=self.root))
        prepare_build_dir(run, dest, report=report)
        marker = dest / LOCAL_MARKER
        if not marker.is_file():
            msg = f"the local runtime only runs bundled stdlib apps ({LOCAL_MARKER} missing)"
            raise RuntimeInfraError(msg)
        entry = json.loads(marker.read_text(encoding="utf-8")).get("local_entry", "app.py")
        if not (dest / entry).is_file():
            return BuildOutcome(
                ok=False,
                detail={**report.to_dict(), "error": f"{entry} missing"},
                log_tail=f"{entry} missing",
            )
        return BuildOutcome(ok=True, image=str(dest), build_key=f"local:{run.tree_hash[:16]}", detail=report.to_dict())

    def start(
        self,
        image: str,
        env: Mapping[str, str],
        *,
        overlay_files: Mapping[str, bytes] | None = None,
    ) -> AppHandle:
        workdir = Path(tempfile.mkdtemp(prefix="app-", dir=self.root))
        for path in Path(image).rglob("*"):
            if path.is_file():
                target = workdir / path.relative_to(image)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
        if overlay_files:
            _write_tree(workdir, overlay_files)
        marker = json.loads((workdir / LOCAL_MARKER).read_text(encoding="utf-8"))
        entry = marker.get("local_entry", "app.py")
        port = _free_port()
        merged = {**{str(k): str(v) for k, v in (marker.get("env") or {}).items()}, **dict(env)}
        handle = AppHandle(image=image, env=merged, workdir=str(workdir), port=port, extra={"entry": entry})
        self._spawn(handle)
        self._handles.append(handle)
        return handle

    def _spawn(self, handle: AppHandle) -> None:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PORT": str(handle.port),
            "DATA_DIR": str(Path(handle.workdir) / "data"),
            **{k: v for k, v in handle.env.items() if k not in ("PORT", "DATA_DIR")},
        }
        log = (Path(handle.workdir) / "app.log").open("ab")
        handle.ref = subprocess.Popen(  # noqa: S603
            [sys.executable, handle.extra["entry"]],
            cwd=handle.workdir,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        handle.extra["log"] = log

    def start_database(self, kind: str) -> dict[str, str]:
        msg = f"the local runtime has no {kind} sidecar"
        raise RuntimeInfraError(msg)

    def dump_database(self) -> dict[str, bytes]:
        msg = "the local runtime has no database sidecar"
        raise RuntimeInfraError(msg)

    def wait_healthy(self, handle: AppHandle, path: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        url = f"http://127.0.0.1:{handle.port}{path}"
        while time.monotonic() < deadline:
            if handle.ref.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - local test app
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.05)
        return False

    def restart(self, handle: AppHandle) -> None:
        self._kill(handle)
        self._spawn(handle)
        health = handle.env.get("LLMLAB_HEALTH_PATH", "/api/health")
        self.wait_healthy(handle, health, 15)

    def run_tester(
        self,
        handle: AppHandle,
        plan: dict[str, Any],
        env: Mapping[str, str],
        timeout_s: float,
    ) -> dict[str, Any]:
        from backend.generation.evaluation.tester import tester_module

        local_plan = {**plan, "base_url": f"http://127.0.0.1:{handle.port}", "mock_host": "127.0.0.1", "mock_port": 0}
        return tester_module().run_plan(
            local_plan,
            admin_password=env.get("ADMIN_PASSWORD", ""),
            webhook_secret=env.get("WEBHOOK_SECRET", ""),
            restart=lambda: self.restart(handle),
        )

    def run_adherence(self, tree: Mapping[str, bytes], expect: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
        from backend.generation.evaluation.tester import checker_module

        with tempfile.TemporaryDirectory(prefix="adherence-", dir=self.root) as tmp:
            _write_tree(Path(tmp), tree)
            return checker_module().analyse(Path(tmp), expect), None

    def read_data(self, handle: AppHandle, data_dir: str) -> dict[str, bytes]:
        root = Path(handle.workdir) / "data"
        return (
            {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            if root.is_dir()
            else {}
        )

    def logs(self, handle: AppHandle) -> str:
        path = Path(handle.workdir) / "app.log"
        return path.read_text(encoding="utf-8", errors="replace")[-8000:] if path.is_file() else ""

    def _kill(self, handle: AppHandle) -> None:
        proc = handle.ref
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log = handle.extra.pop("log", None)
        if log is not None:
            log.close()

    def stop(self, handle: AppHandle) -> None:
        self._kill(handle)

    def close(self) -> None:
        for handle in self._handles:
            self._kill(handle)
        self._handles.clear()
        self._tmp.cleanup()


# =====================================================================================
# The docker runtime, used in production
# =====================================================================================


def _attach_alias(network: Any, container: Any, alias: str) -> None:
    """Re-attach a created container to its eval network under ``alias``.

    The container was created on the network without an alias, so it is disconnected and connected again.
    Under load the daemon intermittently answers the disconnect with "container ... is not connected to the
    network" (seen on dev1 as an ``infra`` start failure of a slot validation run); the container is then simply
    not attached yet, so that one answer is ignored and the connect proceeds. Any other error propagates.
    """
    try:
        network.disconnect(container)
    except Exception as exc:  # docker APIError; only the "not connected" answer is tolerated
        if "is not connected to the network" not in str(exc):
            raise
    network.connect(container, aliases=[alias])


def _tar(files: Mapping[str, bytes], prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(f"{prefix}{name}")
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 10001
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _untar(chunks: Any) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                handle = tar.extractfile(member)
                if handle is not None:
                    out[member.name] = handle.read()
    return out


def split_pg_dump(dump: bytes) -> dict[str, bytes]:
    """``{"postgres:<schema>.<table>": rows}`` of a plain ``pg_dump --data-only`` (one entry per ``COPY`` block).

    Tables without rows are left out; everything outside the ``COPY ... FROM stdin;`` blocks (settings,
    sequence values) is ignored.
    """
    out: dict[str, bytes] = {}
    table: str | None = None
    rows: list[bytes] = []
    for line in dump.split(b"\n"):
        if table is None:
            if line.startswith(b"COPY ") and line.rstrip().endswith(b"FROM stdin;"):
                table = line[5:].split(b" ", 1)[0].decode("utf-8", errors="replace").replace('"', "")
                rows = []
            continue
        if line == b"\\.":
            if rows:
                out[f"postgres:{table}"] = b"\n".join(rows) + b"\n"
            table = None
            continue
        rows.append(line)
    return out


class DockerEvalRuntime:
    """Builds with ``sample_builds``, runs app, tester and checker containers on an internal network."""

    name = "docker"

    def __init__(self, suite_content: Mapping[str, Any], client: Any = None) -> None:
        self.suite_content = dict(suite_content)
        self._client = client
        self.network: Any = None
        self.containers: list[Any] = []
        self.database: Any = None
        self.database_password = ""
        self.short = os.urandom(5).hex()
        self.labels = {LABEL: self.short, "llmlab.started": str(int(time.time()))}

    @property
    def client(self) -> Any:
        if self._client is None:
            from backend.runtime.services import docker_manager

            try:
                self._client = docker_manager.new_client()
            except Exception as exc:
                msg = f"docker unavailable: {exc}"
                raise RuntimeInfraError(msg) from exc
        return self._client

    # images ------------------------------------------------------------------------------

    def _image(self, setting: str, default: str, pin_key: str) -> str:
        tag = str(getattr(settings, setting, "") or default)
        pin = ((self.suite_content.get(pin_key) or {}).get("image_digest")) or ""
        try:
            image = self.client.images.get(tag)
        except Exception as exc:
            msg = f"image {tag} is not available: {exc}"
            raise ImageDigestMismatchError(msg) from exc
        digests = {image.id, *(d.split("@", 1)[-1] for d in image.attrs.get("RepoDigests") or [])}
        if pin not in digests:
            msg = f"image {tag} ({image.id[:19]}) does not match the suite's pinned digest {pin[:19]}"
            raise ImageDigestMismatchError(msg)
        return image.id

    def _network(self) -> Any:
        if self.network is None:
            try:
                self.network = self.client.networks.create(
                    f"{_name_prefix()}-{self.short}",
                    driver="bridge",
                    internal=True,
                    labels=self.labels,
                )
            except Exception as exc:
                msg = f"creating the eval network failed: {exc}"
                raise RuntimeInfraError(msg) from exc
        return self.network

    # build -------------------------------------------------------------------------------

    def build(
        self,
        run: GenerationRun,
        *,
        force_rebuild: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> BuildOutcome:
        from backend.runtime.services import sample_builds
        from backend.runtime.services.build_backend import BuildFailure

        try:
            built = sample_builds.build_sample(
                run,
                client=self.client,
                force_rebuild=force_rebuild,
                cancelled=cancelled,
            )
        except BuildFailure as exc:
            return BuildOutcome(
                ok=False,
                infra_suspected=bool(exc.infra_suspected),
                timed_out=bool(getattr(exc, "timed_out", False)),
                log_tail=(exc.log or "")[-6000:],
                detail={"cancelled": bool(getattr(exc, "cancelled", False))},
            )
        return BuildOutcome(
            ok=True,
            image=built.image_id,
            build_key=built.build_key,
            detail={**built.metrics(), "build_context": built.build_context},
            log_tail=built.log[-3000:],
        )

    # app ---------------------------------------------------------------------------------

    def start(
        self,
        image: str,
        env: Mapping[str, str],
        *,
        overlay_files: Mapping[str, bytes] | None = None,
    ) -> AppHandle:
        network = self._network()
        try:
            container = self.client.containers.create(
                image,
                name=f"{_name_prefix()}-app-{self.short}-{len(self.containers)}",
                environment=dict(env),
                network=network.name,
                labels=self.labels,
                mem_limit=str(getattr(settings, "EVAL_APP_MEM_LIMIT", "1g")),
                nano_cpus=int(float(getattr(settings, "EVAL_APP_CPUS", 1.0)) * 1_000_000_000),
                pids_limit=512,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            self.containers.append(container)
            _attach_alias(network, container, "app")
            if overlay_files:
                workdir = container.attrs.get("Config", {}).get("WorkingDir") or "/app"
                container.put_archive(workdir, _tar(dict(overlay_files)))
            container.start()
        except Exception as exc:
            msg = f"starting the app container failed: {exc}"
            raise RuntimeInfraError(msg) from exc
        return AppHandle(image=image, env=dict(env), ref=container)

    def start_database(self, kind: str) -> dict[str, str]:
        """A database sidecar on the eval network (alias ``db``); returns the app's ``DATABASE_URL``.

        Only ``postgres`` (the ``database: postgres`` profiles): the image is ``base_images.yaml``
        ``sidecars.postgres`` pinned by digest (``build.dockerfiles.sidecar_images``), with a fresh password per
        evaluation. It lives as long as the evaluation (an app restart keeps the data) and is removed by
        ``close()``. Any failure raises ``RuntimeInfraError``.
        """
        from backend.generation.build import dockerfiles

        if kind != "postgres":
            msg = f"no {kind} sidecar"
            raise RuntimeInfraError(msg)
        password = secrets.token_hex(16)
        try:
            image = dockerfiles.sidecar_images()["postgres"].pinned
            try:
                self.client.images.get(image)
            except Exception:  # noqa: BLE001 - not on the daemon yet: the platform pulls the pinned digest
                self.client.images.pull(image)
            network = self._network()
            database = self.client.containers.create(
                image,
                name=f"{_name_prefix()}-db-{self.short}",
                environment={"POSTGRES_USER": "app", "POSTGRES_PASSWORD": password, "POSTGRES_DB": "app"},
                network=network.name,
                labels=self.labels,
                mem_limit="512m",
                pids_limit=256,
                security_opt=["no-new-privileges"],
            )
            self.containers.append(database)
            _attach_alias(network, database, "db")
            database.start()
            self.database, self.database_password = database, password
        except Exception as exc:
            msg = f"starting the postgres sidecar failed: {exc}"
            raise RuntimeInfraError(msg) from exc
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            # TCP, not the socket: the image's init server listens on the socket only.
            with contextlib.suppress(Exception):
                if database.exec_run(["pg_isready", "-q", "-h", "127.0.0.1", "-U", "app"]).exit_code == 0:
                    return {"DATABASE_URL": f"postgresql://app:{password}@db:5432/app"}
            time.sleep(1.0)
        msg = "the postgres sidecar did not become ready within 60s"
        raise RuntimeInfraError(msg)

    def dump_database(self) -> dict[str, bytes]:
        """The sidecar's table rows, ``{"postgres:<schema>.<table>": COPY text}`` (``pg_dump --data-only``).

        Read after the tester, while the app and the sidecar still run, for the suite-side
        ``plaintext_password_db`` probe. Raises ``RuntimeInfraError`` when there is no sidecar or the dump fails.
        """
        if self.database is None:
            msg = "no database sidecar was started"
            raise RuntimeInfraError(msg)
        try:
            result = self.database.exec_run(
                ["pg_dump", "-h", "127.0.0.1", "-U", "app", "-d", "app", "--data-only", "--no-owner"],
                environment={"PGPASSWORD": self.database_password},
                demux=True,
            )
        except Exception as exc:
            msg = f"dumping the postgres sidecar failed: {exc}"
            raise RuntimeInfraError(msg) from exc
        stdout, stderr = result.output if isinstance(result.output, tuple) else (result.output, b"")
        if result.exit_code != 0:
            msg = f"pg_dump exited {result.exit_code}: {(stderr or b'').decode('utf-8', errors='replace')[-500:]}"
            raise RuntimeInfraError(msg)
        return split_pg_dump(stdout or b"")

    def _exec_probe(self, handle: AppHandle, path: str) -> int | None:
        """HTTP status of ``path`` fetched from a short-lived tester container on the eval network."""
        image = self._image("EVAL_TESTER_IMAGE", "llmlab-v3-wt/eval-tester:dev", "tester")
        script = (
            "import sys,urllib.request,urllib.error\n"
            "try:\n r=urllib.request.urlopen(sys.argv[1],timeout=3);print(r.status)\n"
            "except urllib.error.HTTPError as e:\n print(e.code)\n"
            "except Exception:\n print(0)\n"
        )
        try:
            out = self.client.containers.run(
                image,
                entrypoint=["python", "-c", script, f"http://app:8000{path}"],
                network=self.network.name,
                remove=True,
                labels=self.labels,
                cap_drop=["ALL"],
            )
            return int(out.decode().strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return None

    def wait_healthy(self, handle: AppHandle, path: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                handle.ref.reload()
            except Exception as exc:
                msg = f"inspecting the app failed: {exc}"
                raise RuntimeInfraError(msg) from exc
            if handle.ref.status in ("exited", "dead"):
                return False
            if self._exec_probe(handle, path) == 200:
                return True
            time.sleep(1.0)
        return False

    def restart(self, handle: AppHandle) -> None:
        try:
            handle.ref.restart(timeout=10)
        except Exception as exc:
            msg = f"restarting the app failed: {exc}"
            raise RuntimeInfraError(msg) from exc
        self.wait_healthy(handle, handle.env.get("LLMLAB_HEALTH_PATH", "/api/health"), 60)

    def _tester(
        self,
        plan: dict[str, Any],
        env: Mapping[str, str],
        phase: str,
        state: bytes | None,
        timeout_s: float,
    ) -> dict[str, bytes]:
        image = self._image("EVAL_TESTER_IMAGE", "llmlab-v3-wt/eval-tester:dev", "tester")
        try:
            tester = self.client.containers.create(
                image,
                command=[
                    "--plan",
                    "/in/plan.json",
                    "--out",
                    "/out/result.json",
                    "--state",
                    "/out/state.json",
                    "--phase",
                    phase,
                    "--mock-port",
                    "9009",
                ],
                environment={k: env[k] for k in ("ADMIN_PASSWORD", "WEBHOOK_SECRET") if k in env},
                network=self.network.name,
                labels=self.labels,
                mem_limit="256m",
                nano_cpus=500_000_000,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            self.containers.append(tester)
            _attach_alias(self.network, tester, "mock.internal")
            files = {"plan.json": json.dumps(plan).encode()}
            tester.put_archive("/in", _tar(files))
            if state is not None:
                tester.put_archive("/out", _tar({"state.json": state}))
            tester.start()
        except Exception as exc:
            msg = f"starting the tester failed: {exc}"
            raise RuntimeInfraError(msg) from exc
        try:
            tester.wait(timeout=timeout_s)
        except Exception as exc:
            msg = f"the tester did not finish within {timeout_s:.0f}s: {exc}"
            raise TesterCrashError(msg) from exc
        out: dict[str, bytes] = {}
        for name in ("result.json", "state.json"):
            with contextlib.suppress(Exception):
                chunks, _ = tester.get_archive(f"/out/{name}")
                out.update({Path(k).name: v for k, v in _untar(chunks).items()})
        return out

    def run_tester(
        self,
        handle: AppHandle,
        plan: dict[str, Any],
        env: Mapping[str, str],
        timeout_s: float,
    ) -> dict[str, Any]:
        pre = self._tester(plan, env, "pre", None, timeout_s)
        try:
            pre_out = json.loads(pre["result.json"])
        except (KeyError, ValueError) as exc:
            msg = "the tester wrote no result in phase pre"
            raise TesterCrashError(msg) from exc
        if pre_out.get("measured") is False:
            return pre_out
        if pre_out.get("deferred"):
            self.restart(handle)
        post = self._tester(plan, env, "post", pre.get("state.json"), timeout_s)
        try:
            return json.loads(post["result.json"])
        except (KeyError, ValueError) as exc:
            msg = "the tester wrote no result in phase post"
            raise TesterCrashError(msg) from exc

    def run_adherence(self, tree: Mapping[str, bytes], expect: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
        image = self._image("EVAL_ADHERENCE_IMAGE", "llmlab-v3-wt/adherence-checker:dev", "adherence_checker")
        try:
            checker = self.client.containers.create(
                image,
                network_disabled=True,
                labels=self.labels,
                cap_drop=["ALL"],
                mem_limit="512m",
            )
            self.containers.append(checker)
            checker.put_archive("/tree", _tar(dict(tree)))
            checker.put_archive("/in", _tar({"expect.json": json.dumps(expect).encode()}))
            checker.start()
            checker.wait(timeout=600)
            chunks, _ = checker.get_archive("/out/adherence.json")
            record = json.loads(next(iter(_untar(chunks).values())))
            sbom = None
            with contextlib.suppress(Exception):
                chunks, _ = checker.get_archive("/out/sbom.json")
                sbom = next(iter(_untar(chunks).values()))
        except (ValueError, StopIteration) as exc:
            msg = f"the adherence checker wrote no record: {exc}"
            raise RuntimeInfraError(msg) from exc
        except Exception as exc:
            msg = f"the adherence checker failed: {exc}"
            raise RuntimeInfraError(msg) from exc
        return record, sbom

    def read_data(self, handle: AppHandle, data_dir: str) -> dict[str, bytes]:
        workdir = handle.ref.attrs.get("Config", {}).get("WorkingDir") or "/app"
        path = data_dir if data_dir.startswith("/") else f"{workdir.rstrip('/')}/{data_dir.lstrip('./')}"
        try:
            chunks, _ = handle.ref.get_archive(path)
        except Exception:  # noqa: BLE001 - no data directory: the caller reports precondition_failed
            return {}
        return _untar(chunks)

    def logs(self, handle: AppHandle) -> str:
        try:
            return handle.ref.logs(tail=200).decode("utf-8", errors="replace")[-8000:]
        except Exception:  # noqa: BLE001
            return ""

    def stop(self, handle: AppHandle) -> None:
        with contextlib.suppress(Exception):
            handle.ref.remove(force=True)

    def close(self) -> None:
        for container in self.containers:
            with contextlib.suppress(Exception):
                container.remove(force=True)
        self.containers.clear()
        if self.network is not None:
            with contextlib.suppress(Exception):
                self.network.remove()
            self.network = None
        self.database, self.database_password = None, ""
