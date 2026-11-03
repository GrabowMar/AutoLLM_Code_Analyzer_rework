"""Sample-image builders, sharing the apps daemon's endpoint and trust boundary.

Buildx is invoked explicitly: the Docker Python SDK's ``api.build`` method does
not establish a BuildKit session. The container driver retains its own state
volume and bounds all build steps together, rather than only a RUN instruction.
The artifact service serializes calls across processes before entering here.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import requests
from django.conf import settings

_INFRA_PATTERNS = re.compile(
    r"ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|Temporary failure in name resolution|"
    r"Could not resolve host|Read timed out|Connection reset by peer|503 Service Unavailable|"
    r"TLS handshake timeout|network is unreachable|no space left on device|"
    r"Cannot connect to the Docker daemon|failed to dial|failed to connect|"
    r"rpc error: code = (?:Unavailable|Internal)|error reading from server: EOF|failed to receive status|"
    r"out of memory|cannot allocate memory|exit code: 137|signal: killed",
    re.IGNORECASE,
)
_BUILDER_LOCK = threading.Lock()
_LOG_LIMIT = 2 * 1024 * 1024
logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    image_id: str
    image_tag: str
    log: str
    seconds: float
    base_images: dict[str, str] = field(default_factory=dict)
    cpu_seconds: float | None = None
    peak_memory_bytes: int | None = None


class BuildFailure(RuntimeError):  # noqa: N818 - shared service failure contract
    """A build failed, retaining its diagnostics and evaluation classification."""

    def __init__(
        self,
        log: str,
        *,
        infra_suspected: bool = False,
        timed_out: bool = False,
        cancelled: bool = False,
    ) -> None:
        super().__init__(log[-6000:] or "Sample image build failed")
        self.log = log
        self.infra_suspected = infra_suspected
        self.timed_out = timed_out
        self.cancelled = cancelled


def docker_environment(client) -> dict[str, str]:
    """Select the same daemon as the SDK, without leaking a saved CLI context.

    An explicit APPS_DOCKER_HOST replaces inherited TLS selectors, just as
    ``docker_manager.new_client`` does. Otherwise inspect the SDK transport,
    because docker.from_env does not honor the CLI's DOCKER_CONTEXT selector.
    Registry credentials/proxy configuration in DOCKER_CONFIG remain intact.
    """
    env = os.environ.copy()
    env.pop("DOCKER_CONTEXT", None)
    host = getattr(settings, "APPS_DOCKER_HOST", "")
    if host:
        for key in ("DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            env.pop(key, None)
        env["DOCKER_HOST"] = host
        cert_path = getattr(settings, "APPS_DOCKER_CERT_PATH", "")
        if cert_path:
            env.update(DOCKER_TLS_VERIFY="1", DOCKER_CERT_PATH=cert_path)
    else:
        base_url = client.api.base_url
        if isinstance(base_url, str) and base_url.startswith("http+docker://"):
            socket_path = "/var/run/docker.sock"
            for adapter in client.api.adapters.values():
                candidate = getattr(adapter, "socket_path", None)
                if isinstance(candidate, str):
                    socket_path = candidate
                    break
            env["DOCKER_HOST"] = f"unix://{socket_path}"
            for key in ("DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
                env.pop(key, None)
        elif isinstance(base_url, str) and base_url.startswith(("http://", "https://")):
            env["DOCKER_HOST"] = "tcp://" + base_url.split("://", 1)[1]
            if base_url.startswith("http://"):
                for key in ("DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
                    env.pop(key, None)
    env["BUILDX_METADATA_PROVENANCE"] = "max"
    return env


def _terminate(process: subprocess.Popen) -> None:
    """Close the CLI session so BuildKit cancels its active solve as well."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _command(
    args: list[str],
    env: dict[str, str],
    *,
    timeout_s: float,
    cancelled: Callable[[], bool] | None = None,
    sample: Callable[[], None] | None = None,
) -> tuple[int, str]:
    """Drain streamed output without blocking timeout/cancellation checks."""
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is platform-owned, never a shell string
            args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        message = f"Cannot start Docker Buildx: {exc}"
        raise BuildFailure(message, infra_suspected=True) from exc

    output: queue.Queue[str | None] = queue.Queue(maxsize=256)
    stop_reading = threading.Event()

    def enqueue(line: str | None) -> None:
        while not stop_reading.is_set():
            try:
                output.put(line, timeout=0.1)
            except queue.Full:
                continue
            return

    def drain() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if stop_reading.is_set():
                    break
                enqueue(line)
        finally:
            enqueue(None)

    reader = threading.Thread(target=drain, name="sample-build-log", daemon=True)
    reader.start()
    log = ""
    deadline = time.monotonic() + timeout_s
    next_sample = 0.0
    finished = False
    try:
        while not finished:
            if cancelled and cancelled():
                raise BuildFailure(log + "\nBuild cancelled", infra_suspected=True, cancelled=True)
            now = time.monotonic()
            if now >= deadline:
                raise BuildFailure(log + f"\nBuild timed out after {timeout_s:g}s", timed_out=True)
            if sample is not None and now >= next_sample:
                sample()
                next_sample = now + 1
            try:
                line = output.get(timeout=min(0.2, max(0.001, deadline - now)))
            except queue.Empty:
                continue
            if line is None:
                finished = True
            else:
                log = (log + line)[-_LOG_LIMIT:]
        return process.wait(timeout=max(0.001, deadline - time.monotonic())), log
    except subprocess.TimeoutExpired as exc:
        raise BuildFailure(log + f"\nBuild timed out after {timeout_s:g}s", timed_out=True) from exc
    finally:
        stop_reading.set()
        _terminate(process)
        reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()


def _builder_config() -> str:
    budget = int(getattr(settings, "SAMPLE_BUILD_CACHE_BYTES", 5 * 1024**3))
    return (
        "insecure-entitlements = []\n"
        "[worker.oci]\n"
        "  max-parallelism = 1\n"
        "  gc = true\n"
        "  reservedSpace = 0\n"
        f"  maxUsedSpace = {budget}\n"
        "  minFreeSpace = 0\n"
    )


def _setup_command(args, env, *, timeout_s, cancelled) -> tuple[int, str]:
    try:
        return _command(args, env, timeout_s=timeout_s, cancelled=cancelled)
    except BuildFailure as exc:
        # Preparing the platform's builder is infrastructure even when its
        # bootstrap/pull timed out; no sample build instruction has run yet.
        exc.infra_suspected = True
        raise


def _builder_name() -> str:
    name = getattr(settings, "SAMPLE_BUILDER_NAME", "llmlab-samples")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,62}", name):
        message = "Invalid SAMPLE_BUILDER_NAME"
        raise BuildFailure(message, infra_suspected=True)
    return name


def _build_context(
    env: dict[str, str],
    name: str,
    *,
    cancelled: Callable[[], bool] | None,
) -> dict[str, str]:
    """Persist the existing client TLS setup in a named CLI context.

    Buildx cannot create a container driver from TLS environment variables.
    Context creation copies the already provisioned client material into the
    worker's private DOCKER_CONFIG; it neither reads nor prints its contents.
    Updating refreshes rotated certificates before selecting this context.
    """
    if not env.get("DOCKER_TLS_VERIFY"):
        return env
    context = f"{name}-daemon"
    host = env.get("DOCKER_HOST", "")
    cert_path = Path(env.get("DOCKER_CERT_PATH") or env.get("DOCKER_CONFIG") or Path.home() / ".docker")
    endpoint_parts = [
        f"host={host}",
        f"ca={cert_path / 'ca.pem'}",
        f"cert={cert_path / 'cert.pem'}",
        f"key={cert_path / 'key.pem'}",
        "skip-tls-verify=false",
    ]
    endpoint = ",".join(endpoint_parts)
    context_env = env.copy()
    for key in ("DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        context_env.pop(key, None)
    code, _ = _setup_command(
        ["docker", "context", "inspect", context],
        context_env,
        timeout_s=30,
        cancelled=cancelled,
    )
    operation = "create" if code else "update"
    code, log = _setup_command(
        ["docker", "context", operation, context, "--docker", endpoint],
        context_env,
        timeout_s=30,
        cancelled=cancelled,
    )
    if code:
        raise BuildFailure(log, infra_suspected=True)
    context_env["DOCKER_CONTEXT"] = context
    return context_env


def _ensure_builder(env: dict[str, str], *, cancelled: Callable[[], bool] | None) -> str:
    name = _builder_name()
    with _BUILDER_LOCK:
        code, _ = _setup_command(["docker", "buildx", "inspect", name], env, timeout_s=60, cancelled=cancelled)
        if code:
            # Buildx metadata lives in each worker's DOCKER_CONFIG. Reusing a
            # stable name also reuses the remote container and its state volume
            # after a worker container has been recreated.
            with tempfile.TemporaryDirectory(prefix="sample-builder-") as tmp:
                config = Path(tmp) / "buildkitd.toml"
                config.write_text(_builder_config(), encoding="utf-8")
                cpus = float(getattr(settings, "SAMPLE_BUILD_CPUS", 2))
                args = [
                    "docker",
                    "buildx",
                    "create",
                    "--name",
                    name,
                    "--driver",
                    "docker-container",
                    "--driver-opt",
                    f"memory={getattr(settings, 'SAMPLE_BUILD_MEMORY', '1536m')}",
                    "--driver-opt",
                    f"memory-swap={getattr(settings, 'SAMPLE_BUILD_MEMORY', '1536m')}",
                    "--driver-opt",
                    f"cpu-period=100000,cpu-quota={int(cpus * 100000)}",
                    "--driver-opt",
                    "network=bridge",
                    "--buildkitd-flags=--oci-worker-net=bridge",
                    "--buildkitd-config",
                    str(config),
                    "--bootstrap",
                ]
                code, log = _setup_command(args, env, timeout_s=120, cancelled=cancelled)
                if code:
                    raise BuildFailure(log, infra_suspected=True)
        else:
            code, log = _setup_command(
                ["docker", "buildx", "inspect", "--bootstrap", name],
                env,
                timeout_s=120,
                cancelled=cancelled,
            )
            if code:
                raise BuildFailure(log, infra_suspected=True)
    return name


class _Resources:
    """Best-effort per-build CPU delta and sampled builder memory high-water."""

    def __init__(self, client, name: str) -> None:
        self.client = client
        self.name = f"buildx_buildkit_{name}0"
        self.cpu_start: int | None = None
        self.cpu_end: int | None = None
        self.memory: int | None = None

    def sample(self) -> None:
        try:
            info = self.client.containers.get(self.name).stats(stream=False, one_shot=True)
            cpu = info.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage")
            memory = info.get("memory_stats", {}).get("usage")
            if isinstance(cpu, int):
                if self.cpu_start is None:
                    self.cpu_start = cpu
                self.cpu_end = cpu
            if isinstance(memory, int):
                self.memory = max(self.memory or 0, memory)
        except Exception:  # noqa: BLE001 - telemetry must never fail a build
            logger.debug("Sample builder resource stats unavailable", exc_info=True)

    @property
    def seconds(self) -> float | None:
        if self.cpu_start is None or self.cpu_end is None:
            return None
        return round(max(0, self.cpu_end - self.cpu_start) / 1_000_000_000, 3)


def _materials(path: Path) -> dict[str, str]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        provenance = metadata.get("buildx.build.provenance", {})
        materials = provenance.get("materials", []) or provenance.get("buildDefinition", {}).get(
            "resolvedDependencies",
            [],
        )
        return {
            material["uri"]: "sha256:" + material["digest"]["sha256"]
            for material in materials
            if material.get("uri", "").startswith("pkg:docker/") and material.get("digest", {}).get("sha256")
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _stop_builder(env: dict[str, str], name: str) -> None:
    # Stopping preserves the BuildKit volume, while releasing memory before a
    # 3 GiB agent sandbox can be admitted on the shared 4 GiB daemon.
    try:
        code, log = _command(["docker", "buildx", "stop", name], env, timeout_s=30)
        if code:
            logger.warning("Stopping sample builder failed: %s", log[-1000:])
    except BuildFailure:
        logger.warning("Stopping sample builder failed", exc_info=True)


def _builder_oom(client, name: str) -> bool:
    try:
        builder = client.containers.get(f"buildx_buildkit_{name}0")
        builder.reload()
        return builder.attrs.get("State", {}).get("OOMKilled") is True
    except Exception:  # noqa: BLE001 - only supplementary failure diagnostics
        return False


def _buildkit(path, tag, *, client, labels, force_rebuild, cache_namespace, timeout_s, cancelled) -> BuildResult:
    started = time.monotonic()
    env = docker_environment(client)
    name = _builder_name()
    try:
        env = _build_context(env, name, cancelled=cancelled)
        _ensure_builder(env, cancelled=cancelled)
        resources = _Resources(client, name)
        with tempfile.TemporaryDirectory(prefix="sample-build-") as tmp:
            metadata = Path(tmp) / "metadata.json"
            args = [
                "docker",
                "buildx",
                "build",
                "--builder",
                name,
                "--load",
                "--progress=plain",
                "--metadata-file",
                str(metadata),
                "--tag",
                tag,
                "--build-arg",
                f"SAMPLE_CACHE_NAMESPACE={cache_namespace}",
                "--build-arg",
                f"BUILDKIT_CACHE_MOUNT_NS={cache_namespace}",
            ]
            for key, value in sorted((labels or {}).items()):
                args += ["--label", f"{key}={value}"]
            if force_rebuild:
                args += ["--pull"]
                dockerfile = Path(path) / "Dockerfile"
                recipe = dockerfile.read_text(encoding="utf-8") if dockerfile.is_file() else ""
                if re.search(r"(?m)^ARG\s+SAMPLE_DEPENDENCY_REFRESH(?:=|\s*$)", recipe):
                    # --no-cache also clears BuildKit cache mounts. A token
                    # invalidates the install instruction while preserving
                    # package downloads in the owner-scoped mounts.
                    args += ["--build-arg", f"SAMPLE_DEPENDENCY_REFRESH={uuid.uuid4().hex}"]
                else:
                    # Historical immutable recipes do not declare the token
                    # (nor the new package caches); retain true refresh there.
                    args += ["--no-cache"]
            args += [str(Path(path).resolve())]
            resources.sample()
            remaining = max(0.001, timeout_s - (time.monotonic() - started))
            code, log = _command(args, env, timeout_s=remaining, cancelled=cancelled, sample=resources.sample)
            resources.sample()
            if code:
                infra = bool(_INFRA_PATTERNS.search(log)) or _builder_oom(client, name)
                raise BuildFailure(log, infra_suspected=infra)
            base_images = _materials(metadata)
        try:
            image = client.images.get(tag)
        except Exception as exc:
            raise BuildFailure(log + f"\nCannot inspect built image: {exc}", infra_suspected=True) from exc
        return BuildResult(
            image_id=image.id,
            image_tag=tag,
            log=log,
            seconds=round(time.monotonic() - started, 3),
            base_images=base_images,
            cpu_seconds=resources.seconds,
            peak_memory_bytes=resources.memory,
        )
    finally:
        _stop_builder(env, name)


def _memory_bytes(raw: str | int) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?", str(raw), re.IGNORECASE)
    if not match:
        message = "Invalid SAMPLE_BUILD_MEMORY"
        raise BuildFailure(message, infra_suspected=True)
    return int(float(match[1]) * 1024 ** " kmgt".index(match[2].lower() or " "))


def _legacy(path, tag, *, client, labels, force_rebuild, timeout_s, cancelled) -> BuildResult:
    """Compatibility path for old stack recipes; SDK read timeouts stay sample failures."""
    started = time.monotonic()
    lines: list[str] = []
    failed = False
    stream = None
    try:
        stream = client.api.build(
            path=path,
            tag=tag,
            rm=True,
            forcerm=True,
            decode=True,
            timeout=timeout_s,
            labels=labels or {},
            nocache=force_rebuild,
            pull=force_rebuild,
            container_limits={
                "memory": _memory_bytes(getattr(settings, "SAMPLE_BUILD_MEMORY", "1536m")),
                "memswap": _memory_bytes(getattr(settings, "SAMPLE_BUILD_MEMORY", "1536m")),
                # docker-py exposes relative shares, not CFS quota, for its
                # legacy build API. Hard CPU limits use the BuildKit backend.
                "cpushares": int(float(getattr(settings, "SAMPLE_BUILD_CPUS", 2)) * 1024),
            },
        )
        for chunk in stream:
            if cancelled and cancelled():
                raise BuildFailure("".join(lines) + "\nBuild cancelled", infra_suspected=True, cancelled=True)
            if chunk.get("stream"):
                lines.append(chunk["stream"])
            if chunk.get("error"):
                lines.append("ERROR: " + chunk["error"] + "\n")
                failed = True
        log = "".join(lines)
        if failed:
            raise BuildFailure(log, infra_suspected=bool(_INFRA_PATTERNS.search(log)))
        image = client.images.get(tag)
        return BuildResult(image.id, tag, log, round(time.monotonic() - started, 3))
    except BuildFailure:
        raise
    except requests.exceptions.ReadTimeout as exc:
        raise BuildFailure("".join(lines) + f"\nBuild timed out: {exc}", timed_out=True) from exc
    except Exception as exc:
        raise BuildFailure("".join(lines) + f"\n{type(exc).__name__}: {exc}", infra_suspected=True) from exc
    finally:
        if stream is not None:
            close = getattr(stream, "close", None)
            if close:
                close()


def build(
    path: str,
    tag: str,
    *,
    client,
    labels: dict[str, str] | None = None,
    force_rebuild: bool = False,
    cache_namespace: str = "sample",
    timeout_s: int = 1200,
    cancelled: Callable[[], bool] | None = None,
) -> BuildResult:
    """Build one sample image; callers hold the shared build-admission lease."""
    if cancelled and cancelled():
        message = "Build cancelled"
        raise BuildFailure(message, infra_suspected=True, cancelled=True)
    backend = getattr(settings, "SAMPLE_BUILD_BACKEND", "buildkit")
    if backend == "buildkit":
        return _buildkit(
            path,
            tag,
            client=client,
            labels=labels,
            force_rebuild=force_rebuild,
            cache_namespace=cache_namespace,
            timeout_s=timeout_s,
            cancelled=cancelled,
        )
    if backend == "legacy":
        return _legacy(
            path,
            tag,
            client=client,
            labels=labels,
            force_rebuild=force_rebuild,
            timeout_s=timeout_s,
            cancelled=cancelled,
        )
    message = f"Unknown sample build backend: {backend}"
    raise BuildFailure(message, infra_suspected=True)
