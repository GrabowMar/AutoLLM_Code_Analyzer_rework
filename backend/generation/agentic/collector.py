"""``RunFile`` classification, source LOC and excludes (spec section 6.8).

The collector reads ``workspace.tar`` (never ``git ls-files``: an agent can ``git rm --cached`` or ignore
files) and diffs it against the seed tree:

========== ======================================================================================
origin     rule
========== ======================================================================================
starter    the path is in the seed with the same bytes
modified   the path is in the seed with different bytes
authored   the path is not in the seed
deleted    the path is in the seed but not in the final tree (row kept with ``blob=""``)
artifact   generated output: ``dist/`` or ``build/`` at a frontend root, a ``static/`` directory the
           Dockerfile fills from a build stage, ``__pycache__``/``*.pyc``, ``.tmp/``/``tmp/``, logs,
           ``*.db``/``*.sqlite*`` under the data dir and coverage output. ``node_modules/`` is never collected.
========== ======================================================================================

Nested directories are classified by these rules only: ``backend/app/data/models.py`` or
``src/build/index.ts`` are source, not artifacts (the anchored-excludes bug of v2).

``excluded`` labels (first match wins): ``non_source`` (``analysis.services.base.NON_SOURCE_DIRS``),
``lockfile``, ``vendored``, ``binary`` (a NUL byte in the first 8 KB), ``too_large`` (> 1 MiB; lockfiles up
to 8 MiB). Artifact rows carry ``build_output`` or ``data``. Every row keeps its blob: builds use the whole
tree (``RunView`` app scope) and the evidence must be complete.

``loc`` is ``reports.services.loc.source_loc`` (0 for tests and non-source files) and ``is_test`` is
``is_test_path``. **Empty-output rule:** without a row of origin authored/modified, ``excluded=''`` and
``loc > 0`` the run has no model code and its outcome is ``output_invalid`` (``no_model_code``).
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.outcome import CollectionSummary

if TYPE_CHECKING:
    from backend.generation.agentic.workspace import SeedFile
    from backend.generation.models import GenerationRun

__all__ = [
    "LOCKFILE_MAX_BYTES",
    "LOCKFILE_NAMES",
    "MAX_FILE_BYTES",
    "Collected",
    "FileRow",
    "classify_tree",
    "collect",
    "python_static_check",
]

MAX_FILE_BYTES: Final = 1024 * 1024  # 1 MiB
LOCKFILE_MAX_BYTES: Final = 8 * 1024 * 1024  # lockfiles are exempt up to 8 MiB (the 512 KB package-lock drop)
BINARY_SNIFF_BYTES: Final = 8 * 1024
LOCKFILE_NAMES: Final = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "go.sum",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "gradle.lockfile",
        "maven.lock",
    },
)
_HASHED_REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.(txt|lock)$")
_VENDOR_DIRS: Final = frozenset({"vendor", "vendors", "third_party", "third-party", "bower_components"})
_NEVER_COLLECTED: Final = frozenset({"node_modules"})
_ARTIFACT_DIRS_ANYWHERE: Final = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_ARTIFACT_TOP_DIRS: Final = frozenset({".tmp", "tmp", "coverage", "htmlcov", ".nyc_output"})
_ARTIFACT_SUFFIXES: Final = (".pyc", ".pyo", ".log")
_DATA_GLOBS: Final = ("*.db", "*.sqlite", "*.sqlite3", "*.db-journal", "*.sqlite-journal", "*.db-wal", "*.db-shm")
_COVERAGE_FILES: Final = frozenset({".coverage", "coverage.xml", "lcov.info"})
_FRONTEND_MANIFESTS: Final = ("package.json",)
_BUILD_DIRS: Final = frozenset({"dist", "build"})
_DOCKER_STATIC = re.compile(r"^\s*COPY\s+--from=\S+\s+\S+\s+(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_LANGUAGES: Final[Mapping[str, str]] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
    ".svelte": "svelte",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".cs": "csharp",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "shell",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
}
_PY_STUB = re.compile(r"def\s+\w+\s*\([^)]*\)\s*(?:->[^:]+)?:\s*\n\s+(?:pass|\.\.\.)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FileRow:
    """One classified path (the fields of a ``RunFile`` row, plus its bytes when it has a blob)."""

    path: str
    origin: str
    size: int
    mode: int
    language: str
    loc: int
    is_test: bool
    excluded: str
    data: bytes | None = field(default=None, repr=False)
    text: str | None = field(default=None, repr=False)

    @property
    def blob(self) -> str:
        return identity.bytes_sha(self.data) if self.data is not None else ""

    @property
    def is_model_code(self) -> bool:
        return self.origin in {"authored", "modified"} and not self.excluded and self.loc > 0


@dataclass
class Collected:
    """What :func:`collect` wrote: the rows, the tree hash, the workspace blob and the summary."""

    rows: list[FileRow]
    tree_hash: str
    workspace_blob: str
    summary: CollectionSummary
    static: dict[str, Any] = field(default_factory=dict)
    # Set by a slot-agent collector (WP-12) for a variant that is invalid as output (``edit_violation``).
    invalid_reason: str | None = None
    # Slot variants store only their ``slot`` rows; their model code is decided by the slot collector.
    model_code: bool | None = None

    @property
    def has_model_code(self) -> bool:
        if self.model_code is not None:
            return self.model_code
        return any(row.is_model_code for row in self.rows)


def _is_lockfile(name: str, data: bytes) -> bool:
    if name in LOCKFILE_NAMES:
        return True
    return bool(_HASHED_REQUIREMENTS.match(name)) and b"--hash=" in data[:65536]


def _frontend_roots(paths: set[str]) -> set[str]:
    """Directories holding a frontend manifest (``""`` is the repository root)."""
    roots = set()
    for path in paths:
        pure = PurePosixPath(path)
        if pure.name in _FRONTEND_MANIFESTS and not any(part in _NEVER_COLLECTED for part in pure.parts):
            roots.add(pure.parent.as_posix() if pure.parent.as_posix() != "." else "")
    return roots


def _docker_static_dirs(final: Mapping[str, SeedFile]) -> set[str]:
    """``static/``-like target directories that a Dockerfile fills from another build stage."""
    targets: set[str] = set()
    for path, entry in final.items():
        name = PurePosixPath(path).name
        if name != "Dockerfile" and not name.endswith(".Dockerfile"):
            continue
        text = entry.data.decode("utf-8", errors="replace")
        base = PurePosixPath(path).parent
        for match in _DOCKER_STATIC.finditer(text):
            # Container paths (/app/backend/static) map onto the repository relative to WORKDIR /app.
            dest = match.group(1).strip().rstrip("/").removeprefix("/app/").removeprefix("./").lstrip("/")
            if dest and PurePosixPath(dest).name == "static" and ".." not in PurePosixPath(dest).parts:
                targets.add((base / dest).as_posix() if base.as_posix() != "." else dest)
    return targets


def _under(path: str, directory: str) -> bool:
    return path.startswith(directory.rstrip("/") + "/")


def _artifact_label(path: str, *, frontend_roots: set[str], static_dirs: set[str]) -> str | None:  # noqa: PLR0911
    """``build_output`` / ``data`` when ``path`` is generated output, else ``None``."""
    pure = PurePosixPath(path)
    parts = pure.parts
    if any(part in _ARTIFACT_DIRS_ANYWHERE for part in parts[:-1]) or path.endswith(_ARTIFACT_SUFFIXES):
        return "build_output"
    if parts[0] in _ARTIFACT_TOP_DIRS and len(parts) > 1:
        return "build_output"
    if pure.name in _COVERAGE_FILES and len(parts) == 1:
        return "build_output"
    if "data" in parts[:-1] and any(fnmatch.fnmatch(pure.name, glob) for glob in _DATA_GLOBS):
        return "data"
    for root in frontend_roots:
        for build_dir in _BUILD_DIRS:
            directory = f"{root}/{build_dir}" if root else build_dir
            if _under(path, directory):
                return "build_output"
    if any(_under(path, directory) for directory in static_dirs):
        return "build_output"
    return None


def _exclusion(path: str, data: bytes) -> str:
    from backend.analysis.services.base import is_non_source_path

    pure = PurePosixPath(path)
    if is_non_source_path(path):
        return "non_source"
    lockfile = _is_lockfile(pure.name, data)
    if lockfile:
        return "too_large" if len(data) > LOCKFILE_MAX_BYTES else "lockfile"
    if any(part in _VENDOR_DIRS for part in pure.parts[:-1]) or ".min." in pure.name:
        return "vendored"
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return "binary"
    if len(data) > MAX_FILE_BYTES:
        return "too_large"
    return ""


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _row(path: str, entry: SeedFile, origin: str, excluded: str) -> FileRow:
    from backend.reports.services.loc import is_test_path
    from backend.reports.services.loc import source_loc

    text = _decode(entry.data) if excluded not in {"binary", "too_large"} else None
    loc = source_loc(path, text) if text is not None and not excluded else 0
    return FileRow(
        path=path,
        origin=origin,
        size=len(entry.data),
        mode=entry.mode & 0o777,
        language=_LANGUAGES.get(PurePosixPath(path).suffix.lower(), ""),
        loc=loc,
        is_test=is_test_path(path),
        excluded=excluded,
        data=entry.data,
        text=text,
    )


def classify_tree(final: Mapping[str, SeedFile], seed: Mapping[str, SeedFile]) -> list[FileRow]:
    """Classify every path of the final tree (and every seed path it lost) by the rules above."""
    collected = {
        path: entry
        for path, entry in final.items()
        if not any(part in _NEVER_COLLECTED for part in PurePosixPath(path).parts)
    }
    frontend_roots = _frontend_roots(set(collected))
    static_dirs = _docker_static_dirs(collected)
    rows: list[FileRow] = []
    for path in sorted(collected):
        entry = collected[path]
        artifact = _artifact_label(path, frontend_roots=frontend_roots, static_dirs=static_dirs)
        if artifact is not None:
            rows.append(_row(path, entry, "artifact", artifact))
            continue
        if path not in seed:
            origin = "authored"
        elif seed[path].data == entry.data:
            origin = "starter"
        else:
            origin = "modified"
        rows.append(_row(path, entry, origin, _exclusion(path, entry.data)))
    rows.extend(
        FileRow(path=path, origin="deleted", size=0, mode=0, language="", loc=0, is_test=False, excluded="")
        for path in sorted(set(seed) - set(collected))
        if not any(part in _NEVER_COLLECTED for part in PurePosixPath(path).parts)
    )
    return rows


def tree_hash_of(rows: list[FileRow]) -> str:
    """``tree_hash`` over the non-deleted, non-artifact, non-binary entries."""
    return identity.tree_hash(
        (row.path, row.mode, row.blob)
        for row in rows
        if row.origin not in {"deleted", "artifact"} and row.excluded != "binary"
    )


def python_static_check(rows: list[FileRow]) -> dict[str, Any]:
    """The ported Python AST compile check over model-scope ``.py`` files (informational, never gating)."""
    errors: list[dict[str, Any]] = []
    stubs = 0
    checked = 0
    for row in rows:
        if not row.path.endswith(".py") or row.origin not in {"authored", "modified"} or row.text is None:
            continue
        checked += 1
        try:
            ast.parse(row.text, filename=row.path)
        except SyntaxError as exc:
            errors.append({"path": row.path, "line": exc.lineno, "message": exc.msg})
            continue
        stubs += len(_PY_STUB.findall(row.text))
    if not checked:
        return {"status": "not_applicable", "files": 0}
    return {"status": "errors" if errors else "ok", "files": checked, "errors": errors[:50], "stub_functions": stubs}


def collect(
    run: GenerationRun,
    workspace: Path | None,
    *,
    attempt: int,
    seed: Mapping[str, SeedFile],
    final: Mapping[str, SeedFile] | None = None,
    workspace_truncated: bool = False,
) -> Collected:
    """Classify the final workspace, write its blobs and ``RunFile`` rows, and return what was collected.

    ``workspace`` is the attempt's ``workspace.tar`` (read when ``final`` is not given). A truncated
    workspace (larger than ``run.max_workspace_bytes``) writes no rows: there is no tree to evaluate.
    Existing rows of the run are replaced (a retried attempt starts clean).
    """
    from django.db import transaction

    from backend.generation.agentic.workspace import read_tar
    from backend.generation.models import RunFile

    if workspace_truncated or (final is None and (workspace is None or not workspace.exists())):
        summary = CollectionSummary(workspace_truncated=workspace_truncated)
        return Collected(rows=[], tree_hash="", workspace_blob="", summary=summary)
    tree = final if final is not None else read_tar(workspace)  # type: ignore[arg-type]
    rows = classify_tree(tree, seed)
    for row in rows:
        if row.data is not None:
            blobs.put_bytes(row.data)
    workspace_blob = blobs.put_file(workspace, media_type="application/x-tar") if workspace is not None else ""
    with transaction.atomic():
        RunFile.objects.filter(run=run).delete()
        RunFile.objects.bulk_create(
            [
                RunFile(
                    run=run,
                    path=row.path,
                    blob=row.blob,
                    size=row.size,
                    mode=row.mode,
                    origin=row.origin,
                    language=row.language,
                    loc=row.loc,
                    is_test=row.is_test,
                    excluded=row.excluded,
                )
                for row in rows
            ],
            batch_size=500,
        )
    by_origin = Counter(row.origin for row in rows)
    excluded = Counter(row.excluded for row in rows if row.excluded)
    summary = CollectionSummary(
        files=len(rows),
        by_origin=dict(by_origin),
        excluded=dict(excluded),
        loc_model=sum(row.loc for row in rows if row.is_model_code and not row.is_test),
        workspace_bytes=workspace.stat().st_size if workspace is not None else sum(row.size for row in rows),
        workspace_truncated=False,
    )
    return Collected(
        rows=rows,
        tree_hash=tree_hash_of(rows),
        workspace_blob=workspace_blob,
        summary=summary,
        static={"python": python_static_check(rows)},
    )
