"""mypy adapter (JSON lines output, unparseable files pre-filtered in the image).

``scripts/mypy_prefilter.py`` parses and compiles every selected file with
the interpreter mypy runs on, writes ``/out/mypy-files.json`` (checked /
unparseable / skipped imports) and runs mypy on the files that compile, with
``follow_imports = skip`` for every module that does not (an import of a
broken module would abort the check as well), ``# mypy:`` inline
configuration comments neutralized, and a bounded re-run when mypy still
blocks on syntax errors in sample files. Exit codes (mypy 2.4): 0 = no
errors, 1 = type errors, 2 = fatal (crash, duplicate module, bad flag) ->
failure. Every stdout line must be a JSON object; ``note`` lines belong to
the error before them and are attached to it (not counted as findings of
their own).
"""

from __future__ import annotations

import json
from typing import Any

from backend.analysis.engine import sloc
from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import SCRIPTS
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import VENVS
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
from backend.analysis.engine.adapters.base import stdout_of
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

FILES_REPORT = "mypy-files.json"


@register
class MypyAdapter(ToolAdapter):
    slug = "mypy"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{VENVS}/mypy/bin/python",
            f"{SCRIPTS}/mypy_prefilter.py",
            f"{OUT}/{FILES_REPORT}",
            cfg["python_version"],
            f"--config-file={CONFIG}/mypy.ini",
            "--output=json",
            "--check-untyped-defs" if cfg["check_untyped_defs"] else "--no-check-untyped-defs",
            "--",
            *(src_path(f) for f in files),
        ]
        # Module names relative to /src: app.py and tests/app.py stay distinct modules.
        return self.make_invocation(argv, env={"MYPYPATH": SRC}, report_files=[FILES_REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "mypy")
        prefilter = load_json(read_report(outcome, FILES_REPORT, "mypy"), what="mypy pre-filter report", expect=dict)
        with report_structure("mypy pre-filter report"):
            checked = [rel_path(p) for p in prefilter["checked"]]
            unparseable = [
                {
                    "path": rel_path(u["path"]),
                    "reason": f"line {u['line']}: {u['reason']}" if u.get("line") else str(u["reason"]),
                }
                for u in prefilter["unparseable"]
            ]
            skipped_imports = sorted(str(m) for m in prefilter.get("skipped_imports") or [])
            neutralized = sorted(rel_path(p) for p in prefilter.get("inline_config_neutralized") or [])
            runs = prefilter.get("runs")
        findings = _parse_lines(stdout_of(outcome, "mypy"))
        selected = {f.path for f in files}
        if stray := sorted({f.file_path for f in findings} - selected):
            # e.g. a staged copy's path the pre-filter did not map back: never store it as a finding.
            raise ToolFailure(reason="parse_error", message=f"mypy reported files it was not given: {stray[:5]}")
        if not checked and (outcome.stdout.strip() or code != 0):
            raise ToolFailure(reason="parse_error", message="mypy produced output although no file was checked")
        if (code == 1) != bool(findings):
            raise ToolFailure(reason="parse_error", message=f"mypy exited {code} but reported {len(findings)} errors")
        cov = coverage(files, unparseable=unparseable, scanned=checked)
        by_code: dict[str, int] = {}
        for finding in findings:
            by_code[finding.rule_id] = by_code.get(finding.rule_id, 0) + 1
        metrics = {
            "by_code": dict(sorted(by_code.items())),
            "skipped_imports": skipped_imports,
            "inline_config_neutralized": neutralized,
            "runs": runs,
        }
        notes = []
        if skipped_imports:
            notes.append(f"imports of {len(skipped_imports)} modules that do not compile were not followed (Any)")
        if neutralized:
            notes.append(f"'# mypy:' inline configuration neutralized in {len(neutralized)} files")
        return build_parse("mypy", files, findings, cov, metrics=metrics, notes=notes)


def _parse_lines(stdout: bytes) -> list[RawFinding]:
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure(reason="parse_error", message=f"mypy output is not UTF-8: {exc}") from exc
    findings: list[RawFinding] = []
    # One JSON object per "\n"-terminated line: str.splitlines would also split
    # inside a message holding a form feed or U+2028 (mypy quotes source text).
    for number, line in enumerate(sloc.split_lines(text), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ToolFailure(
                reason="parse_error",
                message=f"mypy output line {number} is not JSON: {line[:200]!r}",
            ) from exc
        if not isinstance(item, dict):
            raise ToolFailure(reason="parse_error", message=f"mypy output line {number} is not a JSON object")
        with report_structure("mypy output"):
            severity = str(item["severity"])
            message = str(item["message"])
            if severity == "note":
                # A note explains the error before it (or stands alone as a hint).
                if findings and findings[-1].file_path == rel_path(item["file"]):
                    findings[-1].description = "\n".join(filter(None, [findings[-1].description, message]))
                continue
            if severity != "error":
                raise ToolFailure(reason="parse_error", message=f"unexpected mypy severity {severity!r}")
            column = item.get("column")
            end_column = item.get("end_column")
            findings.append(
                RawFinding(
                    rule_id=str(item.get("code") or "misc"),
                    message=message,
                    file_path=rel_path(item["file"]),
                    line=as_int(item.get("line")),
                    end_line=as_int(item.get("end_line")),
                    # mypy JSON columns are 0-based
                    column=int(column) + 1 if isinstance(column, int) and column >= 0 else None,
                    native_severity=severity,
                    title=str(item.get("code") or "misc"),
                    suggestion=str(item.get("hint") or ""),
                    extra={
                        "end_column": int(end_column) + 1 if isinstance(end_column, int) and end_column >= 0 else None,
                    },
                ),
            )
    return findings
