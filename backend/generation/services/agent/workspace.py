"""Seed an agent sandbox's workspace and collect what the agent left behind.

Seeding provisions the job's pinned stack skeleton (``apply_scaffold`` seed
phase) and ships it in as a tar. Collection reads the workspace tar back,
keeps the authored source (text files, size-capped; dependency folders,
build output, caches and VCS metadata skipped) and classifies every file
against the seed, so analyses can tell agent-written code from untouched
scaffold.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import tarfile
import tempfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from backend.runtime.services.scaffolding import ScaffoldPhase
from backend.runtime.services.scaffolding import apply_scaffold

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

SANDBOX_UID = 1000  # the image's "node" user
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".vite",
        ".cache",
        ".npm",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".local",
        ".config",
    },
)
# Runtime state the app writes, not code the agent authored (data/ is also
# excluded from the build by the stacks' .dockerignore; static/ is replaced
# by the frontend build output).
SKIP_TOP_LEVEL = frozenset({"data", "static"})
SKIP_SUFFIXES = (".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".log")
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seed_files(job: GenerationJob) -> dict[str, bytes]:
    """The stack skeleton the agent starts from, as {relative path: bytes}."""
    with tempfile.TemporaryDirectory(prefix="agent-seed-") as tmp:
        root = Path(tmp)
        apply_scaffold(job, root, phase=ScaffoldPhase.SEED)
        return {
            path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()
        }


def make_tar(files: dict[str, bytes], *, mode: int = 0o644) -> bytes:
    """A tar of *files* owned by the sandbox user (for ``put_archive``)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dirs: set[str] = set()
        for rel in sorted(files):
            parts = rel.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    info = tarfile.TarInfo(d)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.uid = info.gid = SANDBOX_UID
                    tar.addfile(info)
            data = files[rel]
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = mode
            info.uid = info.gid = SANDBOX_UID
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def read_tar(stream_chunks, *, strip_prefix: str) -> dict[str, bytes]:
    """Regular files from a ``get_archive`` stream, paths relative to *strip_prefix*.

    Members are never extracted to disk; links and special files are ignored,
    so a hostile workspace cannot point paths outside it.
    """
    raw = b"".join(stream_chunks)
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        for member in tar:
            if not member.isreg():
                continue
            name = member.name
            prefix = strip_prefix.rstrip("/") + "/"
            if name.startswith(prefix):
                name = name[len(prefix) :]
            elif name == strip_prefix.rstrip("/"):
                continue
            parts = name.split("/")
            if not name or ".." in parts or name.startswith("/"):
                continue
            extracted = tar.extractfile(member)
            if extracted is not None:
                out[name] = extracted.read()
    return out


def _skipped_by_path(rel: str, *, has_frontend: bool, artifacts: tuple[str, ...] = ()) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    # The harness's own files (caches, chat histories) are not part of the app.
    if any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in artifacts):
        return True
    if parts[0] in SKIP_TOP_LEVEL and len(parts) > 1 and (parts[0] != "static" or has_frontend):
        return True
    return rel.endswith(SKIP_SUFFIXES)


@dataclass
class CollectedWorkspace:
    files: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    deleted_scaffold_files: list[str] = field(default_factory=list)
    protected_modified: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        origins: dict[str, int] = {}
        for entry in self.manifest.values():
            origins[entry["origin"]] = origins.get(entry["origin"], 0) + 1
        return origins


def collect(
    final: dict[str, bytes],
    seed: dict[str, bytes],
    *,
    protected: tuple[str, ...],
    has_frontend: bool,
    artifacts: tuple[str, ...] = (),
) -> CollectedWorkspace:
    """Classify the final workspace against the seed and keep the source text."""
    result = CollectedWorkspace()
    total = 0
    for rel in sorted(final):
        data = final[rel]
        if rel in protected:
            if seed.get(rel) != data:
                result.protected_modified.append(rel)
            continue
        if _skipped_by_path(rel, has_frontend=has_frontend, artifacts=artifacts):
            continue
        digest = _sha256(data)
        if rel not in seed:
            origin = "added"
        elif seed[rel] == data:
            origin = "scaffold"
        else:
            origin = "modified"
        entry = {"sha256": digest, "size": len(data), "origin": origin}
        reason = None
        if len(data) > MAX_FILE_BYTES:
            reason = "too_large"
        elif total + len(data) > MAX_TOTAL_BYTES:
            reason = "total_size_cap"
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                reason = "binary"
            else:
                if "\x00" in text:
                    reason = "binary"
        if reason:
            result.skipped.append({"path": rel, "reason": reason, **entry})
            continue
        total += len(data)
        result.files[rel] = text
        result.manifest[rel] = entry
    for rel in sorted(seed):
        if rel not in final and rel not in protected:
            result.deleted_scaffold_files.append(rel)
    # Protected files the agent deleted count as tampering too.
    for rel in protected:
        if rel in seed and rel not in final:
            result.protected_modified.append(rel)
    return result
