"""Helpers for tools that analyse a project directory, not a file list (deptry, depcheck).

Dependency checkers compare a manifest (``requirements.txt``,
``package.json``) with the imports of the code beside it, so they run once per
**project root** (a directory holding the manifest) with that directory as
their working directory. To keep the adapter contract (only selected files are
analysed; the app cannot configure or hide anything), each project is staged:
the selected files of the project — and nothing else, so no ``.gitignore``,
tool config or excluded file of the app is present — are copied from /src to
``/tmp/llmlab-src/p<i>/`` (tmpfs), and the tool runs there. A file belongs to
the deepest root that contains it, so nested projects are never analysed
twice.

Every project writes ``/out/<tool>-<i>.out`` (stdout), ``.err`` (stderr) and
``.rc`` (its exit status); the driver script itself exits 0 unless staging
failed, so the adapter checks each project's status separately.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import STAGE
from backend.analysis.engine.types import ToolFailure

if TYPE_CHECKING:
    from backend.analysis.engine.types import ExecOutcome
    from backend.analysis.engine.types import SampleFile


@dataclass
class Project:
    index: int
    root: str  # sample-relative directory ("" = the sample root)
    files: list[SampleFile] = field(default_factory=list)

    @property
    def stage(self) -> str:
        return f"{STAGE}/p{self.index}"

    def relative(self, file: SampleFile) -> str:
        return file.path[len(self.root) + 1 :] if self.root else file.path

    def sample_path(self, relative: str) -> str:
        """A path the tool reported relative to the project (or absolute in the stage) -> sample path."""
        text = str(relative).replace("\\", "/")
        prefix = self.stage + "/"
        if text.startswith(prefix):
            text = text[len(prefix) :]
        while text.startswith("./"):
            text = text[2:]
        return f"{self.root}/{text}" if self.root else text


def parent(path: str) -> str:
    value = str(PurePosixPath(path).parent)
    return "" if value == "." else value


def contains(root: str, path: str) -> bool:
    return not root or path.startswith(root + "/")


def project_roots(files: list[SampleFile], manifest_names: frozenset[str]) -> list[str]:
    """Directories holding one of *manifest_names*, sorted."""
    return sorted({parent(f.path) for f in files if PurePosixPath(f.path).name in manifest_names})


def owner(path: str, roots: list[str]) -> str | None:
    """The deepest root containing *path* (None: no project)."""
    candidates = [r for r in roots if contains(r, path)]
    return max(candidates, key=len) if candidates else None


def projects(files: list[SampleFile], manifest_names: frozenset[str]) -> tuple[list[Project], list[SampleFile]]:
    """(projects with their files, files outside every project)."""
    roots = project_roots(files, manifest_names)
    by_root = {root: Project(index=i, root=root) for i, root in enumerate(roots)}
    orphans = []
    for file in sorted(files, key=lambda f: f.path):
        root = owner(file.path, roots)
        if root is None:
            orphans.append(file)
        else:
            by_root[root].files.append(file)
    return list(by_root.values()), orphans


def driver_argv(tool: str, items: list[Project], command: list[str]) -> list[str]:
    """``sh -c`` argv staging each project and running *command* in it (see the module docstring).

    *command* may contain ``{index}``, replaced by the project's index.
    """
    parts = ["set -eu", f"mkdir {STAGE}"]
    for project in items:
        src_dir = f"{SRC}/{project.root}" if project.root else SRC
        rel = " ".join(shlex.quote(project.relative(f)) for f in project.files)
        cmd = shlex.join([c.replace("{index}", str(project.index)) for c in command])
        out = f"{OUT}/{tool}-{project.index}"
        parts += [
            f"mkdir {project.stage}",
            f"cd {shlex.quote(src_dir)}",
            f"cp --parents -- {rel} {project.stage}/",
            f"cd {project.stage}",
            f"rc=0; {cmd} > {out}.out 2> {out}.err || rc=$?",
            f"echo $rc > {out}.rc",
        ]
    return ["sh", "-c", "; ".join(parts)]


@dataclass
class ProjectOutcome:
    rc: int
    stdout: bytes
    stderr: bytes


def project_outcome(outcome: ExecOutcome, tool: str, project: Project, allowed: frozenset[int]) -> ProjectOutcome:
    """The recorded status and outputs of one project run (missing / unexpected -> ToolFailure)."""
    base = f"{tool}-{project.index}"
    files = outcome.out_files
    if f"{base}.rc" not in files or f"{base}.out" not in files:
        raise ToolFailure(
            reason="tool_error",
            message=f"{tool} left no result for project {project.root or '.'} ({OUT}/{base}.rc missing)",
        )
    try:
        rc = int(files[f"{base}.rc"].decode().strip())
    except ValueError as exc:
        raise ToolFailure(reason="parse_error", message=f"{tool}: unreadable exit status for {base}") from exc
    stderr = files.get(f"{base}.err", b"")
    if rc not in allowed:
        text = stderr.decode("utf-8", errors="replace").strip()[-1500:] or "(no output)"
        expected = ", ".join(str(c) for c in sorted(allowed))
        raise ToolFailure(
            reason="tool_error",
            message=f"{tool} exited with status {rc} on project {project.root or '.'} (expected {expected}): {text}",
        )
    return ProjectOutcome(rc=rc, stdout=files[f"{base}.out"], stderr=stderr)


def manifest_line(content: bytes, predicate) -> int | None:
    """1-based number of the first line of *content* for which ``predicate(line)`` is true."""
    text = content.decode("utf-8", errors="replace")
    for number, line in enumerate(text.split("\n"), start=1):
        if predicate(line):
            return number
    return None
