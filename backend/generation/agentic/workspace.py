"""Workspace seed and the tar plumbing around it (spec sections 6.4 and 7.6).

* **App mode.** The Starter's files come from the blob store (``StarterContent.files``: ``{path: {blob,
  mode}}``) and are written verbatim. The root ``.gitignore`` is the starter's own when it has one; the
  platform injects no excludes into it (the entry script's excludes live in ``.git/info/exclude``).
* **Slot agent.** The parent's frozen tree (``RunView`` app scope) minus artifact rows, minus the parent's
  tests unless ``include_tests``, with the hole applied by ``slot.executors.restricted_agent.hole_overlay``
  (WP-12) when that hook exists.

Every path goes through :func:`sanitize_rel_path`. The seed's tree hash is recorded as
``result.seed_tree_hash``. Tars are read without extracting to disk: links, devices and paths escaping the
root are dropped, so a hostile workspace can never point outside it.
"""

from __future__ import annotations

import io
import logging
import tarfile
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import IO
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation import blobs
from backend.generation import identity

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

logger = logging.getLogger(__name__)

__all__ = [
    "SANDBOX_UID",
    "SeedFile",
    "UnsafePathError",
    "make_tar",
    "read_tar",
    "sanitize_rel_path",
    "seed_files",
    "seed_tree_hash",
    "seed_workspace",
    "slot_agent_seed",
    "starter_seed",
]

SANDBOX_UID: Final = 1000  # the agent-base image's "node" user
_DIR_MODE: Final = 0o755
_FILE_MODE: Final = 0o644
_EXEC_BITS: Final = 0o111
_MAX_PATH_BYTES: Final = 1024  # RunFile.path


class UnsafePathError(ValueError):
    """A path that is absolute, escapes its root, or is otherwise not a plain relative POSIX path."""


@dataclass(frozen=True)
class SeedFile:
    data: bytes
    mode: int = _FILE_MODE

    @property
    def executable(self) -> bool:
        return bool(self.mode & _EXEC_BITS)


def sanitize_rel_path(path: str) -> str:
    """``path`` as a normalized relative POSIX path, or :class:`UnsafePathError` (ported from v2).

    Refuses absolute paths, ``..``/``.``/empty segments, backslashes, NUL and control characters, and
    paths longer than ``RunFile.path`` holds. A leading ``./`` (tar member names) is stripped.
    """
    if not isinstance(path, str):
        msg = f"path must be a string, got {type(path).__name__}"
        raise UnsafePathError(msg)
    raw = path.removeprefix("./")
    if not raw or raw.startswith("/") or "\\" in raw or any(ord(ch) < 0x20 for ch in raw):
        msg = f"unsafe workspace path {path!r}"
        raise UnsafePathError(msg)
    parts = raw.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        msg = f"unsafe workspace path {path!r}"
        raise UnsafePathError(msg)
    clean = "/".join(parts)
    if len(clean.encode("utf-8")) > _MAX_PATH_BYTES:
        msg = f"workspace path longer than {_MAX_PATH_BYTES} bytes: {clean[:80]!r}..."
        raise UnsafePathError(msg)
    return identity.normalize_text(clean)


def _mode(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return _DIR_MODE if value & _EXEC_BITS else _FILE_MODE
    return _FILE_MODE


def starter_seed(run: GenerationRun) -> dict[str, SeedFile]:
    """The run's Starter files from the blob store (``{}`` when the run has no starter)."""
    starter = run.starter
    if starter is None:
        return {}
    files = (starter.content or {}).get("files") or {}
    out: dict[str, SeedFile] = {}
    for raw_path, entry in sorted(files.items()):
        path = sanitize_rel_path(raw_path)
        out[path] = SeedFile(blobs.get_bytes(entry["blob"]), _mode(entry.get("mode")))
    return out


def slot_agent_seed(run: GenerationRun, *, include_tests: bool = False) -> dict[str, SeedFile]:
    """The parent tree of a ``slot_agent`` run with the hole applied (section 7.6).

    Artifact rows are never seeded; the parent's test files are left out unless ``include_tests``. The hole
    comes from ``slot.executors.restricted_agent.hole_overlay(run) -> {path: bytes | None}`` (``None``
    deletes the path). A missing hook or an empty overlay raises ``runner.ConfigError``: the parent tree is
    never seeded unchanged (that would leak the original implementation of the target).
    """
    from backend.generation.runs.view import run_view
    from backend.reports.services.loc import is_test_path

    parent = run.parent_run
    if parent is None:
        msg = f"slot run {run.pk} has no parent run"
        raise ValueError(msg)
    view = run_view(parent)
    out: dict[str, SeedFile] = {}
    for entry in view.files("app"):
        if entry.origin in {"artifact", "deleted"} or not entry.blob:
            continue
        if not include_tests and (entry.is_test or is_test_path(entry.path)):
            continue
        path = sanitize_rel_path(entry.path)
        out[path] = SeedFile(blobs.get_bytes(entry.blob), _mode(entry.mode))
    from backend.generation.agentic.runner import ConfigError
    from backend.generation.slot.executors import restricted_agent

    overlay_hook = getattr(restricted_agent, "hole_overlay", None)
    try:
        overlay = overlay_hook(run) if overlay_hook is not None else None
    except NotImplementedError:
        overlay = None
    if not overlay:
        # Seeding the parent unchanged would show the agent the original implementation of its target: the
        # variant would leak it (appendix A #16, no silent fallbacks).
        msg = f"slot run {run.pk}: no hole overlay for its target; refusing to seed the original implementation"
        raise ConfigError(msg)
    for raw_path, data in overlay.items():
        path = sanitize_rel_path(raw_path)
        if data is None:
            out.pop(path, None)
        else:
            previous = out.get(path)
            out[path] = SeedFile(bytes(data), previous.mode if previous else _FILE_MODE)
    return out


def seed_files(run: GenerationRun, *, include_tests: bool = False) -> dict[str, SeedFile]:
    """The seed of ``run`` by kind (app: starter; slot agent: hole-applied parent tree)."""
    if run.kind == "slot_agent":
        return slot_agent_seed(run, include_tests=include_tests)
    return starter_seed(run)


def seed_tree_hash(files: Mapping[str, SeedFile]) -> str:
    """``identity.tree_hash`` of a seed (blob shas are the sha256 of the bytes)."""
    return identity.tree_hash((path, entry.mode, identity.bytes_sha(entry.data)) for path, entry in files.items())


def seed_workspace(run: GenerationRun, dest: Path) -> str:
    """Write the seed of ``run`` into ``dest``; returns its tree hash."""
    files = seed_files(run)
    for path, entry in files.items():
        target = dest.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(entry.data)
        target.chmod(entry.mode)
    return seed_tree_hash(files)


def make_tar(files: Mapping[str, SeedFile | bytes], *, uid: int = SANDBOX_UID) -> bytes:
    """A tar of ``files`` owned by the sandbox user, with explicit directory entries (for ``put_archive``)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        dirs: set[str] = set()
        for rel in sorted(files):
            path = sanitize_rel_path(rel)
            entry = files[rel]
            seed = entry if isinstance(entry, SeedFile) else SeedFile(bytes(entry))
            parts = path.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                directory = "/".join(parts[:depth])
                if directory in dirs:
                    continue
                dirs.add(directory)
                info = tarfile.TarInfo(directory)
                info.type = tarfile.DIRTYPE
                info.mode = _DIR_MODE
                info.uid = info.gid = uid
                tar.addfile(info)
            info = tarfile.TarInfo(path)
            info.size = len(seed.data)
            info.mode = seed.mode
            info.uid = info.gid = uid
            tar.addfile(info, io.BytesIO(seed.data))
    return buffer.getvalue()


def _member_path(name: str, strip_prefix: str) -> str | None:
    prefix = strip_prefix.strip("/")
    name = name.removeprefix("./")
    if prefix and prefix != ".":
        if name == prefix:
            return None
        if not name.startswith(prefix + "/"):
            return None
        name = name[len(prefix) + 1 :]
    try:
        return sanitize_rel_path(name)
    except UnsafePathError:
        logger.warning("Dropping unsafe tar member %r", name)
        return None


def read_tar(
    source: bytes | Path | IO[bytes] | Iterable[bytes],
    *,
    strip_prefix: str = "",
    max_member_bytes: int | None = None,
) -> dict[str, SeedFile]:
    """Regular files of a tar (bytes, a path, a file object or ``get_archive`` chunks) without extracting.

    Links and special files are ignored. ``strip_prefix`` removes a leading directory (``get_archive`` of
    ``/logs`` yields ``logs/...``). Members larger than ``max_member_bytes`` are returned empty with their
    mode (the collector still sees the path and labels it ``too_large`` from :func:`member_sizes`).
    """
    if isinstance(source, Path):
        handle: IO[bytes] = source.open("rb")
    elif isinstance(source, (bytes, bytearray)):
        handle = io.BytesIO(bytes(source))
    elif hasattr(source, "read"):
        handle = source  # type: ignore[assignment]
    else:
        handle = io.BytesIO(b"".join(source))  # type: ignore[arg-type]
    out: dict[str, SeedFile] = {}
    with handle, tarfile.open(fileobj=handle, mode="r:*") as tar:
        for member in tar:
            if not member.isreg():
                continue
            path = _member_path(member.name, strip_prefix)
            if path is None:
                continue
            if max_member_bytes is not None and member.size > max_member_bytes:
                out[path] = SeedFile(b"", member.mode)
                continue
            extracted = tar.extractfile(member)
            if extracted is not None:
                out[path] = SeedFile(extracted.read(), member.mode)
    return out


def member_sizes(source: bytes | Path, *, strip_prefix: str = "") -> dict[str, int]:
    """``{path: size}`` of the regular members of a tar."""
    handle: IO[bytes] = source.open("rb") if isinstance(source, Path) else io.BytesIO(source)
    sizes: dict[str, int] = {}
    with handle, tarfile.open(fileobj=handle, mode="r:*") as tar:
        for member in tar:
            if member.isreg():
                path = _member_path(member.name, strip_prefix)
                if path is not None:
                    sizes[path] = member.size
    return sizes
