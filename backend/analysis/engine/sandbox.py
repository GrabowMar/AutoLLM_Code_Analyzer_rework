"""Ephemeral tool containers on the apps daemon (owner: executor stage).

One pinned analyzer image (``images/analyzer``, content-addressed tag
``llmlab/analyzer:<hash>``). Per run, a labelled volume holds the sample
(mounted read-only at /src). Per tool, a fresh container: no network,
read-only rootfs, non-root, tmpfs /tmp, anonymous /out volume, mem/pids/cpu
limits, init. stdout and stderr are collected separately (capped), /out is
collected via get_archive, and everything is removed in ``finally``.

Docker facts this relies on (probed on apps-dind, docker 29):

* ``put_archive`` / ``get_archive`` work on volume mounts of a read-only
  container (created or exited), not on tmpfs mounts — hence /src and /out
  are volumes and only /tmp is a tmpfs.
* A fresh anonymous volume copies the image's directory (content and
  ownership) at create time, so the image must ship ``/out`` owned by the
  tool user (uid 10001) for tools to write their reports there.
* ``container.wait(timeout=...)`` raises a requests ``ConnectionError`` (not
  ``ReadTimeout``) when the timeout passes; both are treated as "still running".

Any daemon/SDK failure becomes :class:`SandboxError` (or
``ExecOutcome.infra_error`` from :func:`run_tool`): infrastructure, never a
tool result.

The apps daemon prunes unused images older than a week (``docker image
prune --all``). An image used by any container, even one never started, is
never pruned, so the volume's never-started fill container is kept for the
whole :func:`run_volume` block: it pins the analyzer image while the run's
tools execute (between tools no tool container exists). A tool that still
finds its image gone reports it (:func:`image_missing`), and the executor
re-ensures the image instead of retrying the dead image id.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import re
import secrets
import tarfile
import threading
import time
from collections import deque
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from django.conf import settings

from backend.analysis.engine import locks
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.generation.services.agent.images import _dir_hash
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

IMAGE_NAME = "analyzer"
IMAGE_REPO = f"llmlab/{IMAGE_NAME}"
IMAGE_DIR = Path(__file__).resolve().parent.parent / "images" / IMAGE_NAME
LABEL_RUN = "llmlab.analysis-run"
LABEL_TOOL = "llmlab.analysis-tool"
LABEL_STARTED = "llmlab.started"
LABEL_IMAGE = "llmlab.analysis-image"
MANIFEST_PATH = "/opt/analyzer/manifest.json"

SRC = "/src"
OUT = "/out"
WORKDIR = "/tmp"  # noqa: S108 - a per-container tmpfs, not the host's /tmp
TOOL_UID = 10001
TOOL_USER = f"{TOOL_UID}:{TOOL_UID}"
# Pseudo run id on helper containers that read an image's manifest; they are
# never "live", so reap() removes any that a crash left behind.
MANIFEST_READER_RUN = "image-manifest"

POLL_SECONDS = 2.0
CAPACITY_POLL_SECONDS = 5.0
KILL_WAIT_SECONDS = 30
BUILD_TIMEOUT_SECONDS = 3 * 3600
BUILD_LOCK_POLL_SECONDS = 5.0
MANIFEST_CAP_BYTES = 8 * 1024 * 1024
LOG_TAIL_LINES = 200

# Environment every tool container starts with: caches and HOME on the tmpfs,
# never in the read-only sample; adapters may add / override keys.
BASE_ENV = {
    "HOME": WORKDIR,
    "TMPDIR": WORKDIR,
    "XDG_CACHE_HOME": f"{WORKDIR}/.cache",
    "PYTHONDONTWRITEBYTECODE": "1",
    "NO_COLOR": "1",
    "LANG": "C.UTF-8",
}

_MEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)
_MEM_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


class SandboxError(Exception):
    """The daemon or image is unavailable (infra), not a tool failure."""


class ImageUnavailable(SandboxError):  # noqa: N818 - reads as a state, like SandboxError's callers expect
    """The analyzer image cannot be used for this plan (definition changed, missing, build failed).

    Unlike a plain :class:`SandboxError` this is not transient: retrying the
    same run on the same platform state fails the same way.
    """


# --------------------------------------------------------------------------
# Docker client
# --------------------------------------------------------------------------

_local = threading.local()


def _client():
    """A Docker client for the apps daemon, one per thread (the SDK is not thread-safe)."""
    client = getattr(_local, "client", None)
    if client is None:
        try:
            client = docker_manager.new_client()
        except Exception as exc:
            msg = f"Docker daemon unavailable: {exc}"
            raise SandboxError(msg) from exc
        _local.client = client
    return client


def _docker_errors() -> tuple[type[BaseException], ...]:
    import docker
    import requests

    return (docker.errors.DockerException, requests.exceptions.RequestException, OSError)


def image_missing(infra_error: str | None) -> bool:
    """True when a :func:`run_tool` infra error says the image no longer exists (e.g. pruned mid-run)."""
    text = str(infra_error or "")
    return text.startswith("ImageNotFound") or "No such image" in text


def daemon_ok() -> bool:
    try:
        _client().ping()
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False
    return True


# --------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------

_tag_lock = threading.Lock()
_tag_cache: dict[str, Any] = {"signature": None, "tag": ""}
_manifest_cache: dict[str, dict[str, Any]] = {}
_building = threading.Event()


def _image_files(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def image_tag() -> str:
    """``llmlab/analyzer:<sha12>`` over the image definition directory.

    Computed exactly like generation's agent images
    (``backend.generation.services.agent.images._dir_hash(IMAGE_DIR, {})``):
    any edit of the Dockerfile, lock files, configs or rules changes the tag.
    Cached on a (path, size, mtime) signature so frequent callers do not
    re-read a large rule pack.
    """
    directory = IMAGE_DIR
    if not directory.is_dir() or not (directory / "Dockerfile").is_file():
        msg = f"The analyzer image definition is missing ({directory}/Dockerfile)"
        raise SandboxError(msg)
    files = _image_files(directory)
    signature = (str(directory), tuple((p.as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in files))
    with _tag_lock:
        if _tag_cache["signature"] == signature:
            return _tag_cache["tag"]
        tag = f"{IMAGE_REPO}:{_dir_hash(directory, {})}"
        _tag_cache.update(signature=signature, tag=tag)
        return tag


def image_reference(tag: str, image_id: str) -> str:
    """``llmlab/analyzer:<sha12>@<image id>``: the content tag a plan freezes, pinned to the exact image.

    Stored as ``ToolResult.image_ref`` so drift checks can compare the tag
    with ``plan.analyzer_image.tag`` and tell two images apart by id.
    """
    if not image_id:
        return tag
    return f"{tag}@{image_id}" if tag else image_id


def _get_image(client, ref: str):
    """The image object or None when it does not exist."""
    import docker

    try:
        return client.images.get(ref)
    except docker.errors.ImageNotFound:
        return None
    except _docker_errors() as exc:
        msg = f"Inspecting image {ref} failed: {exc}"
        raise SandboxError(msg) from exc


def is_building() -> bool:
    """True while any process builds the analyzer image."""
    if _building.is_set():
        return True
    try:
        return locks.is_held(locks.IMAGE_BUILD_KEY)
    except Exception:  # noqa: BLE001 - the DB being unavailable only hides the flag
        return False


def ensure_image(
    *,
    expect_tag: str = "",
    build: bool = True,
    heartbeat: Callable[[], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """(tag, image id); builds when missing (if *build*); refuses a changed definition.

    *expect_tag* is the tag a frozen plan recorded. Tags are content
    addresses, so an image still present under that tag *is* the frozen
    toolchain and is used even when the definition on disk moved on (a
    study keeps its toolchain across edits). If it is gone and the
    definition changed, it cannot be rebuilt: the run must not silently
    execute on a different toolchain, so this raises :class:`ImageUnavailable`.
    Concurrent callers (any process) wait for one build instead of building twice.
    """
    tag = image_tag()
    if expect_tag and tag != expect_tag:
        frozen = _get_image(_client(), expect_tag)
        if frozen is not None:
            return expect_tag, frozen.id
        msg = (
            f"The analyzer image definition changed since this plan was frozen ({expect_tag} -> {tag}) and the "
            "frozen image is no longer on the daemon; submit a new run (with a freshly frozen plan) to analyse "
            "with the current toolchain"
        )
        raise ImageUnavailable(msg)
    client = _client()
    image = _get_image(client, tag)
    if image is not None:
        return tag, image.id
    if not build:
        msg = (
            f"The analyzer image {tag} is not built (build it from the Tools page or `manage.py analysis_build_image`)"
        )
        raise ImageUnavailable(msg)
    return build_image(heartbeat=heartbeat, log=log)


def build_image(
    *,
    heartbeat: Callable[[], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Build the analyzer image under its content tag (no-op when it exists); (tag, image id)."""
    tag = image_tag()
    beat = heartbeat or (lambda: None)
    emit = log or logger.debug
    client = _client()
    with _build_guard(beat):
        # Another process may have finished the same build while we waited.
        image = _get_image(client, tag)
        if image is None:
            _run_build(client, tag, beat, emit)
            image = _get_image(client, tag)
            if image is None:
                msg = f"Building {tag} reported success but the image does not exist"
                raise ImageUnavailable(msg)
            _sync_tool_versions(image.id)
    return tag, image.id


@contextmanager
def _build_guard(heartbeat: Callable[[], None]) -> Iterator[None]:
    """Hold the cross-process image-build lock (polling, so the caller keeps heartbeating)."""
    _building.set()
    have_db_lock = False
    try:
        while True:
            try:
                have_db_lock = locks.try_lock(locks.IMAGE_BUILD_KEY)
            except Exception:  # noqa: BLE001 - no DB (e.g. a bare script): in-process guard only
                logger.warning("Image build lock unavailable; building without cross-process guard")
                break
            if have_db_lock:
                break
            heartbeat()
            time.sleep(BUILD_LOCK_POLL_SECONDS)
        yield
    finally:
        if have_db_lock:
            try:
                locks.unlock(locks.IMAGE_BUILD_KEY)
            except Exception:  # noqa: BLE001 - the lock dies with the connection anyway
                logger.warning("Releasing the image build lock failed", exc_info=True)
        _building.clear()


def _run_build(client, tag: str, heartbeat: Callable[[], None], emit: Callable[[str], None]) -> None:
    logger.info("Building analyzer image %s", tag)
    errors: list[str] = []
    tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)
    try:
        stream = client.api.build(
            path=str(IMAGE_DIR),
            tag=tag,
            rm=True,
            forcerm=True,
            decode=True,
            labels={LABEL_IMAGE: "1"},
            timeout=BUILD_TIMEOUT_SECONDS,
        )
        for chunk in stream:
            line = str(chunk.get("stream") or chunk.get("status") or "").rstrip()
            if line:
                tail.append(line)
                emit(line)
            if chunk.get("error"):
                errors.append(str(chunk["error"]))
            heartbeat()
    except _docker_errors() as exc:
        msg = f"Building {tag} failed (daemon): {exc}"
        raise SandboxError(msg) from exc
    if errors:
        log_tail = "\n".join(list(tail)[-40:])
        msg = f"Building {tag} failed: {errors[-1]}\n{log_tail}"
        raise ImageUnavailable(msg)


def _sync_tool_versions(image_id: str) -> None:
    """Refresh AnalyzerTool.version from the freshly built image (best effort)."""
    try:
        from backend.analysis import seeding

        seeding.sync_tool_versions(image_manifest(image_id))
    except Exception:  # noqa: BLE001 - catalog display only; runs record versions themselves
        logger.warning("Syncing tool versions from the analyzer image failed", exc_info=True)


def image_manifest(image_ref: str) -> dict[str, Any]:
    """The image's /opt/analyzer/manifest.json (tool versions, rule pack hashes), cached by image id."""
    client = _client()
    image = _get_image(client, image_ref)
    if image is None:
        msg = f"Image {image_ref} does not exist"
        raise ImageUnavailable(msg)
    cached = _manifest_cache.get(image.id)
    if cached is not None:
        return copy.deepcopy(cached)
    labels = {LABEL_RUN: MANIFEST_READER_RUN, LABEL_STARTED: str(int(time.time()))}
    try:
        # Never started: the archive API reads the image filesystem of a created container.
        container = client.containers.create(image.id, command=["true"], network_mode="none", labels=labels)
    except _docker_errors() as exc:
        msg = f"Reading the manifest of {image_ref} failed: {exc}"
        raise SandboxError(msg) from exc
    try:
        stream, _stat = container.get_archive(MANIFEST_PATH)
        files, _truncated = _read_archive(stream, cap=MANIFEST_CAP_BYTES)
    except _docker_errors() as exc:
        import docker

        if isinstance(exc, docker.errors.NotFound):
            msg = f"The analyzer image {image_ref} has no {MANIFEST_PATH}"
            raise ImageUnavailable(msg) from exc
        msg = f"Reading the manifest of {image_ref} failed: {exc}"
        raise SandboxError(msg) from exc
    finally:
        _remove_container(container)
    raw = files.get(Path(MANIFEST_PATH).name)
    try:
        manifest = json.loads(raw) if raw else None
    except ValueError:
        manifest = None
    if not isinstance(manifest, dict):
        msg = f"The analyzer image {image_ref} has an unreadable {MANIFEST_PATH}"
        raise ImageUnavailable(msg)
    _manifest_cache[image.id] = manifest
    return copy.deepcopy(manifest)


def tool_version(manifest: dict[str, Any] | None, slug: str) -> str:
    """A tool's version from an image manifest ('' when it does not list the tool).

    Accepts ``{"tools": {slug: {"version": ...}}}``, ``{"tools": {slug: "x"}}``
    and ``{"tools": [{"slug"|"name": slug, "version": ...}]}``.
    """
    tools = (manifest or {}).get("tools")
    entry: Any = None
    if isinstance(tools, dict):
        entry = tools.get(slug)
    elif isinstance(tools, list):
        entry = next((t for t in tools if isinstance(t, dict) and slug in (t.get("slug"), t.get("name"))), None)
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ("version", "tool_version"):
            if entry.get(key):
                return str(entry[key]).strip()
    return ""


def manifest_digest(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    """The provenance-relevant part of an image manifest (versions, lock/binary/rule/config hashes).

    Drops bulky free text (``version_output``) so it can be copied into every
    frozen plan without bloating it.
    """
    if not manifest:
        return None
    tools: dict[str, Any] = {}
    raw_tools = manifest.get("tools")
    for slug, entry in raw_tools.items() if isinstance(raw_tools, dict) else []:
        if not isinstance(entry, dict):
            tools[slug] = {"version": str(entry)}
            continue
        item = {k: entry[k] for k in ("version", "runtime", "lock_sha256", "sha256", "packages") if k in entry}
        rules = entry.get("rules")
        if isinstance(rules, dict):
            item["rules"] = {k: v for k, v in rules.items() if isinstance(v, (str, int, float, bool))}
        tools[slug] = item
    return {
        "schema_version": manifest.get("schema_version"),
        "built_at": manifest.get("built_at"),
        "release_cutoff": manifest.get("release_cutoff"),
        "runtime": manifest.get("runtime"),
        "configs": manifest.get("configs"),
        "tools": tools,
    }


def image_status() -> dict[str, Any]:
    """{tag, built, image_id, manifest|None, building, daemon_ok, error}."""
    status: dict[str, Any] = {
        "tag": "",
        "built": False,
        "image_id": "",
        "created": "",
        "size_bytes": None,
        "manifest": None,
        "building": is_building(),
        "daemon_ok": False,
        "error": "",
    }
    try:
        status["tag"] = image_tag()
    except SandboxError as exc:
        status["error"] = str(exc)
        return status
    try:
        client = _client()
        client.ping()
        status["daemon_ok"] = True
        image = _get_image(client, status["tag"])
    except Exception as exc:  # noqa: BLE001 - reported, never raised from a status call
        status["error"] = f"Docker daemon unavailable: {exc}"
        return status
    if image is None:
        return status
    status.update(
        built=True,
        image_id=image.id,
        created=str(image.attrs.get("Created") or ""),
        size_bytes=image.attrs.get("Size"),
    )
    try:
        status["manifest"] = image_manifest(image.id)
    except SandboxError as exc:
        status["error"] = str(exc)
    return status


def request_image_build() -> dict[str, Any]:
    """Start a background build (Celery when a worker answers, else a thread); returns image_status()."""
    from backend.analysis import tasks

    tasks.dispatch_image_build()
    status = image_status()
    status["building"] = True
    return status


# --------------------------------------------------------------------------
# Sample volume
# --------------------------------------------------------------------------


def check_path(path: str) -> None:
    """Raise ValueError unless *path* is a safe relative posix path (no ``..``, no absolute, no empty parts)."""
    parts = path.split("/")
    if not path or path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts):
        msg = f"Unsafe sample path: {path!r}"
        raise ValueError(msg)


def _make_tar(files: dict[str, bytes], *, uid: int = 0, file_mode: int = 0o644) -> bytes:
    """A tar of *files* with explicit directory entries (world-readable, owned by *uid*)."""
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dirs: set[str] = set()
        for rel in sorted(files):
            parts = rel.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                directory = "/".join(parts[:i])
                if directory not in dirs:
                    dirs.add(directory)
                    info = tarfile.TarInfo(directory)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.uid = info.gid = uid
                    info.mtime = now
                    tar.addfile(info)
            data = files[rel]
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = file_mode
            info.uid = info.gid = uid
            info.mtime = now
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _short(run_id: str) -> str:
    return str(run_id).replace("-", "")[:12]


def _labels(run_id: str, **extra: str) -> dict[str, str]:
    return {LABEL_RUN: str(run_id), LABEL_STARTED: str(int(time.time())), **extra}


@contextmanager
def run_volume(run_id: str, files: dict[str, bytes], *, image: str = "") -> Iterator[str]:
    """Create + populate a labelled volume with the sample; yields its name; always removes it.

    The volume is filled once through a never-started helper container
    (*image*, default: the analyzer image) and then mounted read-only into
    every tool container of the run. The helper lives as long as the block:
    it keeps the image in use, so the daemon's image prune cannot delete it
    between two tools (see module docstring).
    """
    for path in files:
        check_path(path)
    client = _client()
    name = f"llmlab-an-{_short(run_id)}-{secrets.token_hex(4)}"
    labels = _labels(run_id)
    try:
        client.volumes.create(name=name, labels=labels)
    except _docker_errors() as exc:
        msg = f"Creating the sample volume failed: {exc}"
        raise SandboxError(msg) from exc
    helper = None
    try:
        helper = _populate_volume(client, name, files, image=image or image_tag(), labels=labels)
        yield name
    finally:
        if helper is not None:
            _remove_container(helper)
        _remove_volume(client, name)


def _populate_volume(client, volume: str, files: dict[str, bytes], *, image: str, labels: dict[str, str]):
    """Fill *volume* through a never-started helper container; return the helper (the caller removes it)."""
    from docker.types import Mount

    helper = None
    try:
        helper = client.containers.create(
            image,
            command=["true"],
            name=f"{volume}-fill",
            mounts=[Mount(SRC, volume, type="volume")],
            network_mode="none",
            labels=labels,
        )
        if files and not helper.put_archive(SRC, _make_tar(files)):
            msg = "The daemon refused the sample archive"
            raise SandboxError(msg)
    except BaseException as exc:
        if helper is not None:
            _remove_container(helper)
        if isinstance(exc, _docker_errors()):
            msg = f"Populating the sample volume failed: {exc}"
            raise SandboxError(msg) from exc
        raise
    return helper


def _remove_volume(client, name: str) -> None:
    try:
        client.volumes.get(name).remove(force=True)
    except Exception as exc:  # noqa: BLE001 - reap() removes leftovers later
        logger.warning("Removing analysis volume %s failed: %s", name, exc)


def _remove_container(container) -> None:
    try:
        container.remove(force=True, v=True)
    except Exception as exc:  # noqa: BLE001 - reap() removes leftovers later
        logger.warning("Removing analysis container %s failed: %s", getattr(container, "name", "?"), exc)


# --------------------------------------------------------------------------
# Tool execution
# --------------------------------------------------------------------------


class _CapExceededError(Exception):
    pass


class _ChunkReader(io.RawIOBase):
    """A file-like view of an iterator of byte chunks that refuses to read past *cap* bytes."""

    def __init__(self, chunks: Iterable[bytes], cap: int) -> None:
        self._chunks = iter(chunks)
        self._pending = b""
        self._read = 0
        self._cap = cap

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while not self._pending:
            try:
                self._pending = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(buffer), len(self._pending))
        if self._read + size > self._cap:
            raise _CapExceededError
        buffer[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        self._read += size
        return size


def _read_archive(chunks: Iterable[bytes], *, cap: int) -> tuple[dict[str, bytes], bool]:
    """Regular files of a ``get_archive`` tar stream as {member name: bytes}, read at most *cap* bytes.

    Never extracts to disk; links, devices and names with ``..`` or a leading
    ``/`` are ignored. Returns ``(files, truncated)``: on overflow (or a
    corrupt stream) only the members read completely are returned.
    """
    files: dict[str, bytes] = {}
    truncated = False
    # Unbuffered on purpose: tarfile's stream mode reads 10 KiB records, so the
    # cap is enforced within one record of the limit (no read-ahead).
    reader = _ChunkReader(chunks, cap)
    try:
        with tarfile.open(fileobj=reader, mode="r|*") as tar:
            for member in tar:
                if not member.isreg():
                    continue
                name = member.name[2:] if member.name.startswith("./") else member.name
                parts = name.split("/")
                if not name or name.startswith("/") or ".." in parts:
                    continue
                extracted = tar.extractfile(member)
                if extracted is not None:
                    files[name] = extracted.read()
    except _CapExceededError:
        truncated = True
    except (tarfile.TarError, EOFError, OSError) as exc:
        logger.warning("Reading a container archive failed: %s", exc)
        truncated = True
    return files, truncated


def _read_logs(container, *, stdout: bool, cap: int) -> tuple[bytes, bool]:
    """One stream of a container's logs, at most *cap* bytes; ``(data, truncated)``."""
    stream = container.logs(stdout=stdout, stderr=not stdout, stream=True, follow=False)
    buf = bytearray()
    truncated = False
    try:
        for chunk in stream:
            room = cap - len(buf)
            if len(chunk) > room:
                buf += chunk[:room]
                truncated = True
                break
            buf += chunk
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001 - only frees the connection early
                logger.debug("Closing a log stream failed", exc_info=True)
    return bytes(buf), truncated


def _container_name(run_id: str, tool_slug: str) -> str:
    slug = _NAME_UNSAFE.sub("-", tool_slug)[:40] or "tool"
    return f"llmlab-an-{_short(run_id)}-{slug}-{secrets.token_hex(3)}"


def run_tool(
    *,
    image: str,
    volume: str,
    invocation: Invocation,
    run_id: str,
    tool_slug: str,
    is_cancelled: Callable[[], bool] = lambda: False,
    heartbeat: Callable[[], None] = lambda: None,
) -> ExecOutcome:
    """Run one tool container to completion (or timeout/cancel) and collect everything.

    Never raises for daemon trouble: an SDK/daemon failure is returned as
    ``ExecOutcome.infra_error`` (the tool did not run properly; retryable).
    Interpretation of the exit code / outputs is the adapter's job.
    """
    if is_cancelled():
        return ExecOutcome(exit_code=None, cancelled=True)
    from docker.types import Mount

    mem_bytes = parse_mem(invocation.mem_limit)
    output_cap = int(getattr(settings, "ANALYSIS_OUTPUT_CAP_BYTES", 16 * 1024 * 1024))
    out_cap = int(getattr(settings, "ANALYSIS_OUT_DIR_CAP_BYTES", 128 * 1024 * 1024))
    name = _container_name(run_id, tool_slug)
    container = None
    outcome = ExecOutcome(exit_code=None, container_name=name)
    try:
        client = _client()
        container = client.containers.create(
            image,
            command=list(invocation.argv),
            name=name,
            working_dir=WORKDIR,
            user=TOOL_USER,
            environment={**BASE_ENV, **invocation.env},
            network_mode="none",
            read_only=True,
            init=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=int(getattr(settings, "ANALYSIS_TOOL_PIDS_LIMIT", 512)),
            mem_limit=mem_bytes,
            # Equal to mem_limit: no swap, so an over-budget tool is OOM-killed
            # (and reported) instead of crawling until its timeout.
            memswap_limit=mem_bytes,
            nano_cpus=int(getattr(settings, "ANALYSIS_TOOL_NANO_CPUS", 1_000_000_000)),
            tmpfs={WORKDIR: f"size={getattr(settings, 'ANALYSIS_TOOL_TMPFS_SIZE', '512m')},mode=1777"},
            mounts=[Mount(SRC, volume, type="volume", read_only=True), Mount(OUT, None, type="volume")],
            labels=_labels(run_id, **{LABEL_TOOL: tool_slug}),
            stdin_open=False,
            tty=False,
        )
        outcome.image_id = str(container.attrs.get("Image") or "")
        if invocation.inputs and not container.put_archive(OUT, _make_tar(dict(invocation.inputs), uid=TOOL_UID)):
            outcome.infra_error = "The daemon refused the tool's input files"
            return outcome
        started = time.monotonic()
        container.start()
        _wait(container, outcome, started, invocation.timeout_s, is_cancelled, heartbeat)
        container.reload()
        state = container.attrs.get("State") or {}
        exit_code = state.get("ExitCode")
        outcome.exit_code = int(exit_code) if exit_code is not None else None
        outcome.oom_killed = bool(state.get("OOMKilled"))
        outcome.stdout, outcome.stdout_truncated = _read_logs(container, stdout=True, cap=output_cap)
        outcome.stderr, outcome.stderr_truncated = _read_logs(container, stdout=False, cap=output_cap)
        stream, _stat = container.get_archive(OUT)
        files, outcome.out_truncated = _read_archive(stream, cap=out_cap)
        prefix = Path(OUT).name + "/"
        outcome.out_files = {k[len(prefix) :]: v for k, v in files.items() if k.startswith(prefix)}
        if outcome.out_truncated:
            logger.warning("Run %s tool %s: /out exceeded %s bytes, collected partially", run_id, tool_slug, out_cap)
    except (SandboxError, *_docker_errors()) as exc:
        outcome.infra_error = f"{type(exc).__name__}: {exc}"[:2000]
        logger.warning("Run %s tool %s: sandbox infra error: %s", run_id, tool_slug, exc)
    finally:
        if container is not None:
            _remove_container(container)
    return outcome


def _wait(
    container,
    outcome: ExecOutcome,
    started: float,
    timeout_s: float,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
) -> None:
    """Block until the container exits; kill it on timeout or cancellation."""
    import requests

    deadline = started + max(1.0, float(timeout_s))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outcome.timed_out = True
            break
        try:
            container.wait(timeout=max(0.2, min(POLL_SECONDS, remaining)))
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            pass
        else:
            outcome.wall_seconds = round(time.monotonic() - started, 3)
            return
        heartbeat()
        if is_cancelled():
            outcome.cancelled = True
            break
    outcome.wall_seconds = round(time.monotonic() - started, 3)
    _kill(container)


def _kill(container) -> None:
    import docker
    import requests

    try:
        container.kill()
    except docker.errors.APIError as exc:
        # 409: it exited on its own between the last poll and the kill.
        logger.debug("Killing %s: %s", container.name, exc)
    try:
        container.wait(timeout=KILL_WAIT_SECONDS)
    except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
        logger.warning("Container %s did not stop within %ss of SIGKILL", container.name, KILL_WAIT_SECONDS)


# --------------------------------------------------------------------------
# Capacity and cleanup
# --------------------------------------------------------------------------


def capacity() -> dict[str, Any]:
    """{budget_bytes, used_bytes, containers: [{name, mem_bytes, labels}]} on the apps daemon.

    ``used_bytes`` sums the memory limits of every running container on the
    daemon (not only ours): sample apps, agent sandboxes and evaluations
    share the same 4 GB. A container without a limit counts as 0 bytes and
    is flagged ``unlimited``.
    """
    budget = parse_mem(str(getattr(settings, "ANALYSIS_DIND_MEMORY_BUDGET", "3500m")))
    try:
        listed = _client().containers.list(ignore_removed=True)
    except _docker_errors() as exc:
        msg = f"Listing containers failed: {exc}"
        raise SandboxError(msg) from exc
    containers = []
    for container in listed:
        host = container.attrs.get("HostConfig") or {}
        mem = int(host.get("Memory") or 0)
        labels = dict(container.labels or {})
        containers.append(
            {
                "name": container.name,
                "mem_bytes": mem,
                "unlimited": mem == 0,
                "analysis": LABEL_RUN in labels,
                "labels": labels,
            },
        )
    used = sum(c["mem_bytes"] for c in containers)
    return {"budget_bytes": budget, "used_bytes": used, "free_bytes": max(0, budget - used), "containers": containers}


def wait_for_capacity(
    mem_bytes: int,
    *,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
    timeout_s: int,
) -> bool:
    """Block until *mem_bytes* fit into the daemon budget; False on cancel or timeout.

    A tool larger than the whole budget is admitted only when nothing else
    runs on the daemon (rather than waiting forever). Raises SandboxError
    when the daemon cannot be listed.
    """
    deadline = time.monotonic() + max(0, timeout_s)
    while True:
        snapshot = capacity()
        used = snapshot["used_bytes"]
        if used + mem_bytes <= snapshot["budget_bytes"] or used == 0:
            return True
        if is_cancelled() or time.monotonic() >= deadline:
            return False
        logger.info(
            "Waiting for apps-daemon memory: need %s, in use %s of %s",
            mem_bytes,
            used,
            snapshot["budget_bytes"],
        )
        pause_until = min(deadline, time.monotonic() + CAPACITY_POLL_SECONDS)
        while time.monotonic() < pause_until:
            heartbeat()
            if is_cancelled():
                return False
            time.sleep(min(1.0, max(0.0, pause_until - time.monotonic())))


def reap(
    *,
    live_run_ids: set[str],
    older_than_s: int = 0,
    run_ids: set[str] | None = None,
) -> dict[str, int]:
    """Remove analysis containers/volumes whose run is not live.

    Age comes from the ``llmlab.started`` label, so a resource created a
    moment ago for a run that was just (re)claimed survives when
    *older_than_s* covers the gap between listing the live runs and reaping.
    *run_ids* restricts the sweep to those runs' resources (e.g. one run's
    leftovers) instead of every analysis resource on the daemon.
    """
    client = _client()
    live = {str(r) for r in live_run_ids}
    only = {str(r) for r in run_ids} if run_ids is not None else None
    now = time.time()
    removed = {"containers": 0, "volumes": 0, "errors": 0}

    def _expired(labels: dict[str, str]) -> bool:
        run = labels.get(LABEL_RUN, "")
        if run in live or (only is not None and run not in only):
            return False
        try:
            started = float(labels.get(LABEL_STARTED) or 0)
        except ValueError:
            started = 0.0
        return now - started >= older_than_s

    try:
        containers = client.containers.list(all=True, filters={"label": LABEL_RUN}, ignore_removed=True)
        volumes = client.volumes.list(filters={"label": LABEL_RUN})
    except _docker_errors() as exc:
        msg = f"Listing analysis resources failed: {exc}"
        raise SandboxError(msg) from exc
    for container in containers:
        if not _expired(dict(container.labels or {})):
            continue
        try:
            container.remove(force=True, v=True)
            removed["containers"] += 1
        except _docker_errors() as exc:
            removed["errors"] += 1
            logger.warning("Reaping container %s failed: %s", container.name, exc)
    for volume in volumes:
        if not _expired(dict(volume.attrs.get("Labels") or {})):
            continue
        try:
            volume.remove(force=True)
            removed["volumes"] += 1
        except _docker_errors() as exc:
            removed["errors"] += 1
            logger.warning("Reaping volume %s failed: %s", volume.name, exc)
    return removed


def parse_mem(value: str) -> int:
    """Docker-style memory size (``768m``, ``1.5g``, ``512mb``, ``1024``) to bytes (binary units)."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            msg = f"Invalid memory size: {value!r}"
            raise ValueError(msg)
        return value
    match = _MEM_RE.match(str(value or ""))
    if not match:
        msg = f"Invalid memory size: {value!r}"
        raise ValueError(msg)
    number, unit = match.groups()
    size = int(float(number) * _MEM_UNITS[unit.lower()])
    if size <= 0:
        msg = f"Invalid memory size: {value!r}"
        raise ValueError(msg)
    return size
