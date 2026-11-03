"""The parent tree as slot mode sees it: paths, bytes and flags of a run's ``app`` scope (spec sections 7.3-7.7).

Everything in slot mode reads the parent through :class:`ParentTree`: the symbol index, holes, context,
leak scan, validation trees, the restricted agent's workspace and assembly. Blobs are immutable, so a tree is
cached per run id for the lifetime of the object only (never globally).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Final

from backend.generation import blobs
from backend.generation.slot.index.languages import language_for

if TYPE_CHECKING:
    from collections.abc import Iterator

    from backend.generation.models import GenerationRun

__all__ = [
    "ARTIFACT_DIRS",
    "DEPENDENCY_FILES",
    "NON_SOURCE_EXCLUSIONS",
    "ParentTree",
    "TreeFile",
    "is_dependency_file",
    "is_readme",
    "is_test_path",
    "parent_tree",
]

# Rows that are not source for slot purposes (no index, no context, no leak scope).
NON_SOURCE_EXCLUSIONS: Final = frozenset({"binary", "build_output", "vendored", "lockfile", "too_large", "data"})
# Directories a workspace may grow that are never edits (dependency installs, caches, build output).
ARTIFACT_DIRS: Final = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        ".next",
        ".svelte-kit",
        ".cache",
    },
)
# Dependency manifests and lockfiles (edits to them are out-of-slot unless allowlisted; section 7.6).
DEPENDENCY_FILES: Final = frozenset(
    {
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.lock",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "pipfile",
        "pipfile.lock",
        "setup.py",
        "setup.cfg",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "go.mod",
        "go.sum",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
    },
)


def is_dependency_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in DEPENDENCY_FILES or (name.startswith("requirements") and name.endswith(".txt"))


def is_readme(path: str) -> bool:
    return PurePosixPath(path).name.lower().startswith("readme")


def is_test_path(path: str) -> bool:
    """Parent-authored tests by path (``tests/``, ``test_*.py``, ``*.test.ts``, ``*_test.go``, ...)."""
    pure = PurePosixPath(path)
    name = pure.name.lower()
    parts = {part.lower() for part in pure.parts[:-1]}
    return (
        bool(parts & {"tests", "test", "__tests__", "spec"})
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", "test.java"))
        or ".test." in name
        or ".spec." in name
    )


@dataclass(frozen=True)
class TreeFile:
    path: str
    blob: str
    mode: int
    origin: str  # the stored origin (``parent`` for an overlaid parent row of a variant)
    is_test: bool
    excluded: str
    language: str

    @property
    def is_source(self) -> bool:
        return self.excluded not in NON_SOURCE_EXCLUSIONS


@dataclass
class ParentTree:
    """The ``app`` scope of a run, with lazily read and cached bytes."""

    run_id: str
    tree_hash: str
    files: dict[str, TreeFile]
    _bytes: dict[str, bytes] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[TreeFile]:
        return iter(self.files[path] for path in sorted(self.files))

    def __contains__(self, path: object) -> bool:
        return path in self.files

    def read(self, path: str) -> bytes:
        if path not in self._bytes:
            self._bytes[path] = blobs.get_bytes(self.files[path].blob)
        return self._bytes[path]

    def text(self, path: str) -> str:
        return self.read(path).decode("utf-8", errors="replace")

    def is_test(self, path: str) -> bool:
        entry = self.files[path]
        return entry.is_test or is_test_path(path)

    def source_paths(self, *, include_tests: bool = True) -> list[str]:
        """Text source paths (excluded rows dropped; tests dropped unless ``include_tests``)."""
        return [
            entry.path
            for entry in self
            if entry.is_source and entry.blob and (include_tests or not self.is_test(entry.path))
        ]


def parent_tree(run: GenerationRun) -> ParentTree:
    """The ``app`` scope of ``run`` (a variant's view overlays its parent's rows)."""
    from backend.generation.runs.view import run_view

    entries = run_view(run).files("app")
    files = {
        entry.path: TreeFile(
            path=entry.path,
            blob=entry.blob,
            mode=entry.mode,
            origin=entry.origin,
            is_test=entry.is_test,
            excluded=entry.excluded,
            language=language_for(entry.path),
        )
        for entry in entries
        if entry.blob
    }
    return ParentTree(run_id=str(run.pk), tree_hash=run.tree_hash, files=files)
