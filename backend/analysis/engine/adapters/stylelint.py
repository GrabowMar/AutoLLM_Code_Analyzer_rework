"""Stylelint adapter (JSON report in /out).

Exit codes (stylelint 17): 0 = no error-severity problem, 2 = problems
(CSS syntax errors included); 1 (fatal error), 64 (CLI usage) and 78 (invalid
configuration) fail the tool. The report lists every file it was given and
must agree with the exit code (2 <=> some file ``errored``).

Isolation: ``--config`` (no lookup of the app's .stylelintrc / package.json
``stylelint`` key), ``--config-basedir`` pointing at the image's node_modules
(the ``extends`` of the baked config resolves there only), ``--ignore-disables``
(an app's ``/* stylelint-disable */`` comments cannot hide a problem); it runs
from the empty /tmp, so no .stylelintignore applies. ``CssSyntaxError``
warnings mean a file could not be parsed: coverage, not findings. Option
warnings (``invalidOptionWarnings``) mean the baked config is broken: failure.

Only plain ``.css`` is analysed: the image has no SCSS / Sass / Less syntax
for stylelint, which would report their syntax as CSS errors, so such files
are coverage ``skipped``.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import ANALYZER
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
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

REPORT = "stylelint.json"
EXIT_PROBLEMS = 2
PARSE_FAILURE_RULES = frozenset({"CssSyntaxError"})
SUPPORTED_SUFFIXES = frozenset({".css"})


@register
class StylelintAdapter(ToolAdapter):
    slug = "stylelint"

    def unsupported(self, file: SampleFile) -> str | None:
        suffix = PurePosixPath(file.path).suffix.lower()
        if suffix in SUPPORTED_SUFFIXES:
            return None
        return f"stylelint has no {suffix or 'extension-less'} syntax in this image (it would report it as CSS errors)"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        self.effective_config(config)
        argv = [
            f"{BIN}/stylelint",
            "--config",
            f"{CONFIG}/stylelint.json",
            "--config-basedir",
            ANALYZER,
            "--ignore-disables",
            "--formatter",
            "json",
            "--output-file",
            f"{OUT}/{REPORT}",
            "--no-color",
        ]
        targets = [src_path(f) for f in self.analysable(files)]
        return self.make_invocation([*argv, "--", *targets], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, EXIT_PROBLEMS}, "stylelint")
        items = load_json(read_report(outcome, REPORT, "stylelint"), what="stylelint report", expect=list)
        given = {f.path for f in self.analysable(files)}
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        scanned: list[str] = []
        errored = False
        with report_structure("stylelint report"):
            for entry in items:
                path = rel_path(entry["source"])
                if path not in given:
                    raise ToolFailure(
                        reason="parse_error",
                        message=f"stylelint reported a file it was not given: {path}",
                    )
                if entry.get("invalidOptionWarnings"):
                    texts = [str(w.get("text")) for w in entry["invalidOptionWarnings"]]
                    raise ToolFailure(reason="tool_error", message=f"stylelint configuration problem: {texts[:3]}")
                scanned.append(path)
                errored = errored or bool(entry.get("errored"))
                for warning in [*entry["warnings"], *(entry.get("parseErrors") or [])]:
                    rule = str(warning.get("rule") or "")
                    if rule in PARSE_FAILURE_RULES:
                        unparseable.append({"path": path, "reason": f"line {warning.get('line')}: {warning['text']}"})
                        continue
                    findings.append(
                        RawFinding(
                            rule_id=rule or "parse-warning",
                            message=str(warning["text"]),
                            file_path=path,
                            line=as_int(warning.get("line")),
                            end_line=as_int(warning.get("endLine")),
                            column=as_int(warning.get("column")),
                            native_severity=str(warning.get("severity") or ""),
                            title=rule or "parse-warning",
                        ),
                    )
        if missing := sorted(given - set(scanned)):
            raise ToolFailure(
                reason="parse_error",
                message=f"stylelint report lacks files it was given: {missing[:5]}",
            )
        if (code == EXIT_PROBLEMS) != errored:
            raise ToolFailure(
                reason="parse_error",
                message=f"stylelint exited {code} but its report says errored={errored}",
            )
        cov = coverage(files, unparseable=unparseable, skipped=self.unsupported_coverage(files), scanned=scanned)
        return build_parse("stylelint", files, findings, cov)
