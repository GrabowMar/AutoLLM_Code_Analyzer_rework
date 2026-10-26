"""Ruff adapter.

Exit codes (ruff 0.16, without --exit-zero): 0 = no diagnostics, 1 =
diagnostics (incl. syntax errors), 2 = CLI/config error. The JSON report must
agree with the exit code (1 <=> non-empty), otherwise the run is not trusted.
Syntax errors (``invalid-syntax`` / E999) and unreadable files (E902) are
coverage, not findings.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import read_report
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

REPORT = "ruff.json"
# Pseudo-rules that mean "this file was not (fully) analysed".
PARSE_FAILURE_CODES = frozenset({"invalid-syntax", "E999", "syntax-error"})
IO_FAILURE_CODES = frozenset({"E902"})


@register
class RuffAdapter(ToolAdapter):
    slug = "ruff"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/ruff",
            "check",
            "--isolated",  # never read the app's pyproject.toml / ruff.toml
            "--no-cache",  # never write .ruff_cache next to the sources
            "--ignore-noqa",  # the app's noqa comments cannot hide a finding
            "--no-fix",
            "--output-format",
            "json",
            "--output-file",
            f"{OUT}/{REPORT}",
            "--target-version",
            cfg["target_version"],
            "--select",
            cfg["select"],
        ]
        if cfg["ignore"]:
            argv += ["--ignore", cfg["ignore"]]
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "ruff")
        items = load_json(read_report(outcome, REPORT, "ruff"), what="ruff report", expect=list)
        if (code == 1) != bool(items):
            raise ToolFailure(
                reason="parse_error",
                message=f"ruff exited {code} but its report holds {len(items)} diagnostics",
            )
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        by_rule: dict[str, int] = {}
        with report_structure("ruff report"):
            for item in items:
                path = rel_path(item["filename"])
                rule = item.get("code") or "invalid-syntax"
                location = item.get("location") or {}
                end = item.get("end_location") or {}
                if rule in PARSE_FAILURE_CODES or rule in IO_FAILURE_CODES:
                    reason = item["message"]
                    if location.get("row"):
                        reason = f"line {location['row']}: {reason}"
                    unparseable.append({"path": path, "reason": reason})
                    continue
                fix = item.get("fix") or {}
                findings.append(
                    RawFinding(
                        rule_id=str(rule),
                        message=str(item["message"]),
                        file_path=path,
                        line=as_int(location.get("row")),
                        end_line=as_int(end.get("row")),
                        column=as_int(location.get("column")),
                        title=str(item.get("name") or rule),
                        suggestion=str(fix.get("message") or ""),
                        extra={
                            "name": item.get("name") or "",
                            "url": item.get("url") or "",
                            "end_column": as_int(end.get("column")),
                            "fixable": bool(fix),
                        },
                    ),
                )
                by_rule[rule] = by_rule.get(rule, 0) + 1
        cov = coverage(files, unparseable=unparseable)
        return build_parse("ruff", files, findings, cov, metrics={"by_rule": dict(sorted(by_rule.items()))})
