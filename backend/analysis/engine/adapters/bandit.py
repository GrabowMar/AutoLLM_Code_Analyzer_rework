"""Bandit adapter.

Exit codes (bandit 1.9): 0 = no issues, 1 = issues found, 2 = usage error.
The JSON report's ``errors`` list holds files bandit could not read or parse
(coverage); ``issue_cwe`` gives the CWE. ``--ignore-nosec`` keeps an app's
``# nosec`` comments from hiding findings; no ``-c``/``--ini`` is passed and
only explicit files are scanned, so no app config (.bandit, pyproject) is read.

``--exclude ""``: bandit's default exclude list (.git, CVS, .hg, .tox, .eggs,
.svn, .bzr, __pycache__, *.egg) is matched as a *substring* of every path,
explicit files included, so ``.github/scripts/deploy.py`` or ``CVSSScore.py``
would be dropped without a word. What bandit really scanned is read from the
report's per-file ``metrics``; a selected file missing there (and not an
error) is a coverage ``skipped`` entry, never "scanned, no issues".
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

REPORT = "bandit.json"


@register
class BanditAdapter(ToolAdapter):
    slug = "bandit"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/bandit",
            "--format",
            "json",
            "--output",
            f"{OUT}/{REPORT}",
            "--quiet",
            "--ignore-nosec",
            "--severity-level",
            cfg["severity"],
            "--confidence-level",
            cfg["confidence"],
            # No default excludes (substring matches on explicit paths): only the selected files are passed.
            "--exclude",
            "",
        ]
        if cfg["skip"]:
            argv += ["--skip", cfg["skip"]]
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "bandit")
        report = load_json(read_report(outcome, REPORT, "bandit"), what="bandit report", expect=dict)
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        with report_structure("bandit report"):
            results = report["results"]
            errors = report["errors"]
            totals = report["metrics"]["_totals"]
            if (code == 1) != bool(results):
                raise ToolFailure(
                    reason="parse_error",
                    message=f"bandit exited {code} but its report holds {len(results)} issues",
                )
            unparseable = [{"path": rel_path(e["filename"]), "reason": e["reason"]} for e in errors]
            for item in results:
                cwe = item.get("issue_cwe") or {}
                line_range = [int(n) for n in item.get("line_range") or []]
                findings.append(
                    RawFinding(
                        rule_id=str(item["test_id"]),
                        message=str(item["issue_text"]),
                        file_path=rel_path(item["filename"]),
                        line=as_int(item.get("line_number")),
                        end_line=max(line_range) if line_range else None,
                        # bandit columns are 0-based offsets
                        column=int(item["col_offset"]) + 1 if item.get("col_offset") is not None else None,
                        native_severity=str(item["issue_severity"]),
                        native_confidence=str(item["issue_confidence"]),
                        cwe=[f"CWE-{int(cwe['id'])}"] if cwe.get("id") else [],
                        title=str(item.get("test_name") or item["test_id"]),
                        snippet=str(item.get("code") or ""),
                        extra={
                            "more_info": item.get("more_info") or "",
                            "test_name": item.get("test_name") or "",
                            "line_range": line_range,
                            "end_col_offset": item.get("end_col_offset"),
                        },
                    ),
                )
            metrics = {
                "loc": int(totals.get("loc", 0)),
                "nosec": int(totals.get("nosec", 0)),
                "skipped_tests": int(totals.get("skipped_tests", 0)),
            }
            reported = {rel_path(path) for path in report["metrics"] if path != "_totals"}
        failed = {entry["path"] for entry in unparseable}
        skipped = [
            {"path": f.path, "reason": "bandit did not scan the file (it is not in the report's metrics)"}
            for f in files
            if f.path not in reported and f.path not in failed
        ]
        cov = coverage(files, unparseable=unparseable, skipped=skipped, scanned=sorted(reported))
        return build_parse("bandit", files, findings, cov, metrics=metrics)
