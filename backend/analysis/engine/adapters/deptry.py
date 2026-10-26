"""deptry adapter: Python dependency problems per project (JSON report per project).

A project is a directory holding a ``requirements.txt``; its Python files (the
deepest project owns a file) and requirement files are staged and deptry runs
there (``_projects``), so the app's pyproject.toml ``[tool.deptry]``,
.gitignore and excluded files are never seen. ``--config`` names the baked
``config/deptry.toml`` (package -> module map for the packages whose import
name differs; the app's dependencies are not installed); ``--exclude '^$'``
replaces deptry's default excludes (tests/, venv/, ...), so every staged file
is checked. Dependencies are read from ``requirements.txt`` (dev:
``requirements-dev.txt`` / ``dev-requirements.txt``); a project declaring its
dependencies only in pyproject.toml has no requirements.txt and is not a
deptry project here.

Exit codes per project: 0 = no issue, 1 = issues (the JSON list must agree);
anything else fails the tool. A Python file deptry could not parse is
reported on stderr ("Skipping processing of <file> ...") -> unparseable
coverage. Python files outside every project are coverage ``skipped``.

Rules: DEP001 imported but not declared (the app fails at import time),
DEP002 declared but unused (reported on the requirements file, at the line
declaring it), DEP003 transitive dependency imported, DEP004 dev dependency
used in app code, DEP005 standard-library module declared.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine.adapters import _projects
from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import select_files
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

TOOL = "deptry"
MANIFEST = "requirements.txt"
DEV_MANIFESTS = ("requirements-dev.txt", "dev-requirements.txt")
MANIFESTS = frozenset({MANIFEST, *DEV_MANIFESTS})
ROOT_MANIFESTS = frozenset({MANIFEST})
_SKIPPED_RE = re.compile(r'Skipping processing of (.+?) because of the following error: "(.*)"\.?\s*$', re.MULTILINE)
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@register
class DeptryAdapter(ToolAdapter):
    slug = TOOL

    def select_files(self, sample: Sample, config: dict[str, Any]) -> list[SampleFile]:
        """Python files + requirement files; nothing when the sample has no requirements.txt."""
        files = [
            f
            for f in select_files(sample, self.spec)
            if f.language == "python" or (f.kind == "manifest" and PurePosixPath(f.path).name in MANIFESTS)
        ]
        items, _orphans = _projects.projects(files, ROOT_MANIFESTS)
        return files if items else []

    def _plan(self, files: list[SampleFile]) -> tuple[list[_projects.Project], list[SampleFile]]:
        return _projects.projects(files, ROOT_MANIFESTS)

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        items, _orphans = self._plan(files)
        command = [
            f"{BIN}/deptry",
            ".",
            "--config",
            f"{CONFIG}/deptry.toml",
            "--exclude",
            "^$",
            "--requirements-files",
            MANIFEST,
            "--requirements-files-dev",
            ",".join(DEV_MANIFESTS),
            "--json-output",
            f"{OUT}/{TOOL}-{{index}}.json",
            "--no-ansi",
        ]
        if cfg["ignore"]:
            command += ["--ignore", cfg["ignore"]]
        return self.make_invocation(_projects.driver_argv(TOOL, items, command))

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, TOOL)
        items, orphans = self._plan(files)
        by_path = {f.path: f for f in files}
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        by_rule: dict[str, int] = {}
        for project in items:
            result = _projects.project_outcome(outcome, TOOL, project, frozenset({0, 1}))
            name = f"{TOOL}-{project.index}.json"
            if name not in outcome.out_files:
                raise ToolFailure(reason="tool_error", message=f"deptry wrote no report {OUT}/{name}")
            report = load_json(outcome.out_files[name], what=f"deptry report {name}", expect=list)
            if (result.rc == 1) != bool(report):
                raise ToolFailure(
                    reason="parse_error",
                    message=f"deptry exited {result.rc} on {project.root or '.'} but reported {len(report)} issues",
                )
            stderr = result.stderr.decode("utf-8", errors="replace")
            for match in _SKIPPED_RE.finditer(stderr):
                unparseable.append({"path": project.sample_path(match.group(1)), "reason": match.group(2)})
            with report_structure("deptry report"):
                for item in report:
                    finding = _finding(item, project, by_path)
                    findings.append(finding)
                    by_rule[finding.rule_id] = by_rule.get(finding.rule_id, 0) + 1
        skipped = [
            {"path": f.path, "reason": "no requirements.txt in this or a parent directory (no deptry project)"}
            for f in orphans
        ]
        cov = coverage(files, unparseable=unparseable, skipped=skipped)
        metrics = {
            "projects": [p.root or "." for p in items],
            "by_rule": dict(sorted(by_rule.items())),
        }
        return build_parse(TOOL, files, findings, cov, metrics=metrics)


def _finding(item: dict[str, Any], project: _projects.Project, by_path: dict[str, SampleFile]) -> RawFinding:
    error = item["error"]
    code = str(error["code"])
    module = str(item.get("module") or "")
    location = item.get("location") or {}
    path = project.sample_path(str(location["file"]))
    if path not in by_path:
        raise ToolFailure(reason="parse_error", message=f"deptry reported a file it was not given: {path}")
    line = as_int(location.get("line"))
    if line is None and PurePosixPath(path).name in MANIFESTS:
        wanted = _normalized(module)
        line = _projects.manifest_line(
            by_path[path].content,
            lambda text: bool((m := _NAME_RE.match(text)) and _normalized(m.group(1)) == wanted),
        )
    return RawFinding(
        rule_id=code,
        message=str(error["message"]),
        file_path=path,
        line=line,
        column=as_int(location.get("column")),
        title=f"{code} {module}".strip(),
        extra={"module": module, "project": project.root or "."},
    )
