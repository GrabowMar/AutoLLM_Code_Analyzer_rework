"""ESLint adapter (flat config baked into the image, JSON formatter to /out).

ESLint is started from /src (``env -C /src``) because ESLint 10 ignores files
outside the base path (the cwd when ``-c`` is given) with a warning and exit
0 — a fail-open trap; /src is read-only and ESLint writes nothing without
``--cache``. Exit codes: 0 = no errors, 1 = at least one severity-2 message
(including parse errors), 2 = crash/config error -> failure. The exit code
must agree with the report's error counts.

* ``fatal: true`` messages are parse failures -> unparseable files;
* messages without a rule id ("File ignored ...") -> skipped files;
* ``vue/no-parsing-error`` (a broken <template>) -> partial coverage;
* everything else -> a finding (native severity "2"/"1" as configured).
"""

from __future__ import annotations

import json
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import as_int
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import load_json
from backend.analysis.engine.adapters.base import read_report
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import report_structure
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

REPORT = "eslint.json"
TEMPLATE_PARSE_RULES = frozenset({"vue/no-parsing-error"})


@register
class EslintAdapter(ToolAdapter):
    slug = "eslint"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            "env",
            "-C",
            SRC,
            f"{BIN}/eslint",
            "--no-config-lookup",  # never load the app's eslint.config.*
            "--config",
            f"{CONFIG}/eslint.config.mjs",
            "--no-inline-config",  # the app's eslint-disable comments cannot hide findings
            "--format",
            "json",
            "--output-file",
            f"{OUT}/{REPORT}",
        ]
        for rule in cfg["opt_in_rules"]:
            argv += ["--rule", json.dumps({rule: "error"})]
        # Relative to /src ("./" so a path can never look like an option).
        return self.make_invocation([*argv, "--", *(f"./{f.path}" for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "eslint")
        results = load_json(read_report(outcome, REPORT, "eslint"), what="eslint report", expect=list)
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        errors = warnings = 0
        reported: set[str] = set()
        with report_structure("eslint report"):
            for result in results:
                path = rel_path(result["filePath"])
                reported.add(path)
                errors += int(result["errorCount"])
                warnings += int(result["warningCount"])
                for msg in result["messages"]:
                    rule = msg.get("ruleId")
                    text = " ".join(str(msg.get("message") or "").split())
                    if msg.get("fatal"):
                        unparseable.append({"path": path, "reason": f"line {msg.get('line')}: {text}"})
                    elif not rule:
                        skipped.append({"path": path, "reason": text})
                    elif rule in TEMPLATE_PARSE_RULES:
                        partial.append({"path": path, "reason": f"line {msg.get('line')}: {text}"})
                    else:
                        findings.append(_finding(msg, path, text))
        if (code == 1) != (errors > 0):
            raise ToolFailure(
                reason="parse_error",
                message=f"eslint exited {code} but its report counts {errors} errors",
            )
        missing = sorted({f.path for f in files} - reported)
        skipped += [{"path": p, "reason": "not in the eslint report"} for p in missing]
        cov = coverage(files, unparseable=unparseable, skipped=skipped, partial=partial)
        opt_in = self.effective_config(config)["opt_in_rules"]
        metrics = {"error_count": errors, "warning_count": warnings, "opt_in_rules": opt_in}
        return build_parse("eslint", files, findings, cov, metrics=metrics)


def _finding(msg: dict[str, Any], path: str, text: str) -> RawFinding:
    return RawFinding(
        rule_id=str(msg["ruleId"]),
        message=text,
        file_path=path,
        line=as_int(msg.get("line")),
        end_line=as_int(msg.get("endLine")),
        column=as_int(msg.get("column")),
        native_severity=str(msg["severity"]),
        title=str(msg["ruleId"]),
        extra={
            "end_column": as_int(msg.get("endColumn")),
            "message_id": msg.get("messageId") or "",
            "node_type": msg.get("nodeType") or "",
        },
    )
