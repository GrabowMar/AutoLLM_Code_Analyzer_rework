"""depcheck adapter: JavaScript dependency problems per package.json project (JSON on stdout).

A project is a directory holding a ``package.json``; its JavaScript /
TypeScript / Vue files (the deepest project owns a file) and the package.json
are staged and depcheck runs there (``_projects``), so the app's
``.depcheckrc``, the ``depcheck`` key of its package.json, .gitignore and
excluded files are never used: ``--config`` names the baked
``config/depcheck.json``, whose only content is an ``ignores`` list of build
tooling referenced from config files depcheck cannot read (Tailwind/PostCSS
plugins, type packages, linters, Vite).

Exit codes per project: 0 = nothing to report, 255 = unused or missing
dependencies (the JSON must agree; depcheck also exits 255 when it cannot
run, which then has no valid JSON on stdout); anything else fails the tool.
``invalidFiles`` (files depcheck could not parse) -> unparseable coverage;
``invalidDirs`` -> failure.

Rules: ``missing-dependency`` (imported but not declared: the build fails;
one finding per importing file, at the first line naming the package),
``unused-dependency`` / ``unused-dev-dependency`` (declared but never used;
on the package.json line declaring it).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine.adapters import _projects
from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import ToolAdapter
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

TOOL = "depcheck"
MANIFEST = "package.json"
MANIFESTS = frozenset({MANIFEST})
EXIT_ISSUES = 255
UNUSED = {
    "dependencies": ("unused-dependency", "Unused dependency"),
    "devDependencies": ("unused-dev-dependency", "Unused dev dependency"),
}


@register
class DepcheckAdapter(ToolAdapter):
    slug = TOOL

    def select_files(self, sample: Sample, config: dict[str, Any]) -> list[SampleFile]:
        """Script files + package.json manifests; nothing when the sample has no package.json."""
        files = [
            f
            for f in select_files(sample, self.spec)
            if f.language != "json" or (f.kind == "manifest" and PurePosixPath(f.path).name == MANIFEST)
        ]
        items, _orphans = _projects.projects(files, MANIFESTS)
        return files if items else []

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        items, _orphans = _projects.projects(files, MANIFESTS)
        command = [f"{BIN}/depcheck", ".", "--json", f"--config={CONFIG}/depcheck.json"]
        if cfg["skip_missing"]:
            command.append("--skip-missing=true")
        return self.make_invocation(_projects.driver_argv(TOOL, items, command))

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, TOOL)
        items, orphans = _projects.projects(files, MANIFESTS)
        by_path = {f.path: f for f in files}
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        for project in items:
            result = _projects.project_outcome(outcome, TOOL, project, frozenset({0, EXIT_ISSUES}))
            where = f"depcheck output for {project.root or '.'}"
            report = load_json(result.stdout, what=where, expect=dict)
            with report_structure(where):
                if report["invalidDirs"]:
                    raise ToolFailure(
                        reason="tool_error",
                        message=f"depcheck could not read directories: {sorted(report['invalidDirs'])[:5]}",
                    )
                for path, error in sorted(report["invalidFiles"].items()):
                    first = str(error).strip().split("\n", 1)[0]
                    unparseable.append({"path": _known(project.sample_path(path), by_path), "reason": first})
                project_findings = _findings(report, project, by_path)
            if (result.rc == EXIT_ISSUES) != bool(project_findings):
                raise ToolFailure(
                    reason="parse_error",
                    message=f"depcheck exited {result.rc} on {project.root or '.'} "
                    f"but reported {len(project_findings)} issues",
                )
            findings += project_findings
        skipped = [{"path": f.path, "reason": "no package.json in this or a parent directory"} for f in orphans]
        cov = coverage(files, unparseable=unparseable, skipped=skipped)
        return build_parse(TOOL, files, findings, cov, metrics={"projects": [p.root or "." for p in items]})


def _known(path: str, by_path: dict[str, SampleFile]) -> str:
    if path not in by_path:
        raise ToolFailure(reason="parse_error", message=f"depcheck reported a file it was not given: {path}")
    return path


def _findings(report: dict[str, Any], project: _projects.Project, by_path: dict[str, SampleFile]) -> list[RawFinding]:
    manifest = f"{project.root}/{MANIFEST}" if project.root else MANIFEST
    out: list[RawFinding] = []
    for key, (rule, label) in UNUSED.items():
        for package in sorted(report[key]):
            name = str(package)
            out.append(
                RawFinding(
                    rule_id=rule,
                    message=f"'{name}' is declared in {key} but never used",
                    file_path=manifest,
                    line=_projects.manifest_line(by_path[manifest].content, _quoted(name)),
                    title=f"{label}: {name}",
                    extra={"package": name, "section": key, "project": project.root or "."},
                ),
            )
    for package, users in sorted(report["missing"].items()):
        name = str(package)
        for user in sorted({_known(project.sample_path(u), by_path) for u in users}):
            out.append(
                RawFinding(
                    rule_id="missing-dependency",
                    message=f"'{name}' is imported but not declared in {manifest}",
                    file_path=user,
                    line=_projects.manifest_line(by_path[user].content, _quoted(name, prefix=True)),
                    title=f"Missing dependency: {name}",
                    extra={"package": name, "manifest": manifest, "project": project.root or "."},
                ),
            )
    return out


def _quoted(name: str, *, prefix: bool = False):
    """Predicate: the line names the package as a quoted string (or a quoted subpath when *prefix*)."""
    tail = r"(?:/[^'\"`]*)?" if prefix else ""
    pattern = re.compile(rf"""['"`]{re.escape(name)}{tail}['"`]""")
    return lambda text: bool(pattern.search(text))
