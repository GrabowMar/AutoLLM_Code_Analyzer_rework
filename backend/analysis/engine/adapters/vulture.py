"""Vulture adapter (text output).

Exit codes (vulture 2.16): 0 = no dead code, 3 = dead code found, 1 = invalid
input (a file could not be parsed or read), 2 = invalid arguments. Exit 1 is
accepted only when stderr explains it with per-file errors (which become
unparseable files). Every stdout line must match the report format.

A file vulture cannot read (``Error: Could not read file <path> - ...``, e.g.
a coding cookie naming an unknown codec) is unparseable too. vulture reports
exit 3 when other files have dead code, so this cannot be left to the exit
code.

vulture honours flake8-style ``# noqa`` comments and has no switch to ignore
them; ``scripts/vulture_isolated.py`` runs vulture with its noqa parser
replaced by one that finds no suppressions, so an app cannot hide its dead
code.
"""

from __future__ import annotations

import re
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import SCRIPTS
from backend.analysis.engine.adapters.base import VENVS
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.adapters.base import stdout_of
from backend.analysis.engine.adapters.base import tail
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

# /src/app.py:9: unused import 'json' (90% confidence)
# /src/app.py:42: unused function 'helper' (60% confidence, 3 lines)
_FINDING_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<message>.+?) \((?P<confidence>\d+)% confidence(?:, (?P<size>\d+) lines?)?\)$",
)
# /src/broken.py:1: invalid syntax at "def f(:"
_ERROR_RE = re.compile(r"^(?P<path>/src/.+?):(?:(?P<line>\d+):)? (?P<reason>.+)$")
# Error: Could not read file /src/gz.py - unknown encoding: gzip
_READ_ERROR_RE = re.compile(r"^Error: Could not read file (?P<path>/src/.+?) - ?(?P<reason>.*)$")


def rule_for(message: str) -> str:
    """'unused import 'json'' -> unused-import; 'unreachable code after 'return'' -> unreachable-code."""
    head = message.split(" '", 1)[0].strip()
    if head.startswith("unreachable code"):
        return "unreachable-code"
    return "-".join(head.split()) or "dead-code"


@register
class VultureAdapter(ToolAdapter):
    slug = "vulture"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{VENVS}/vulture/bin/python",
            "-I",
            f"{SCRIPTS}/vulture_isolated.py",
            "--config",
            f"{CONFIG}/vulture.toml",
            "--min-confidence",
            str(cfg["min_confidence"]),
            "--",
            *(src_path(f) for f in files),
        ]
        return self.make_invocation(argv)

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1, 3}, "vulture")
        findings = _parse_findings(stdout_of(outcome, "vulture"))
        unparseable = _parse_errors(stdout_of(outcome, "vulture", stderr=True))
        if code == 1 and not unparseable:
            raise ToolFailure(
                reason="tool_error",
                message=f"vulture reported invalid input without naming a file: {tail(outcome)}",
            )
        if code == 3 and not findings:
            raise ToolFailure(reason="parse_error", message="vulture exited 3 (dead code found) but reported none")
        if code == 0 and findings:
            raise ToolFailure(reason="parse_error", message="vulture exited 0 but reported dead code")
        cov = coverage(files, unparseable=unparseable)
        metrics = {"min_confidence": self.effective_config(config)["min_confidence"]}
        return build_parse("vulture", files, findings, cov, metrics=metrics)


def _parse_findings(stdout: bytes) -> list[RawFinding]:
    findings = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        match = _FINDING_RE.match(line.strip())
        if not match:
            raise ToolFailure(reason="parse_error", message=f"unexpected vulture output line: {line[:200]!r}")
        message = match.group("message")
        findings.append(
            RawFinding(
                rule_id=rule_for(message),
                message=message,
                file_path=rel_path(match.group("path")),
                line=int(match.group("line")),
                title=rule_for(message),
                extra={
                    "confidence": int(match.group("confidence")),
                    "size": int(match.group("size")) if match.group("size") else None,
                },
            ),
        )
    return findings


def _parse_errors(stderr: bytes) -> list[dict[str, Any]]:
    errors = []
    for line in stderr.decode("utf-8", errors="replace").splitlines():
        read_error = _READ_ERROR_RE.match(line.strip())
        if read_error:
            reason = read_error.group("reason").strip() or "vulture could not read the file"
            errors.append({"path": rel_path(read_error.group("path")), "reason": f"unreadable: {reason}"})
            continue
        match = _ERROR_RE.match(line.strip())
        if match:
            reason = match.group("reason")
            if match.group("line"):
                reason = f"line {match.group('line')}: {reason}"
            errors.append({"path": rel_path(match.group("path")), "reason": reason})
    return errors
