"""flake8 adapter (pyflakes + pycodestyle + mccabe; delimited text report in /out).

flake8 has no JSON formatter without a plugin, so it prints one line per
diagnostic in a fixed ``--format`` whose fields are separated by the ASCII
unit separator (``\\x1f``), which neither paths nor messages contain; a line
that does not split into exactly five fields fails the parse. Exit codes
(no ``--exit-zero``): 0 = no diagnostics, 1 = diagnostics; anything else is a
failure, and the report must agree with the exit code.

Isolation: ``--isolated`` (no setup.cfg / tox.ini / .flake8 of the app) and
``--disable-noqa`` (an app's ``# noqa`` cannot hide a diagnostic). E999
(syntax error) and E902 (unreadable file) mean a file was not analysed:
coverage, not findings. ``--jobs=1`` keeps flake8 from forking workers inside
the memory-limited container.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

REPORT = "flake8.txt"
SEP = "\x1f"
FORMAT = SEP.join(("%(path)s", "%(row)d", "%(col)d", "%(code)s", "%(text)s"))
PARSE_FAILURE_CODES = frozenset({"E999"})
IO_FAILURE_CODES = frozenset({"E902"})


@register
class Flake8Adapter(ToolAdapter):
    slug = "flake8"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/flake8",
            "--isolated",  # never read the app's setup.cfg / tox.ini / .flake8
            "--disable-noqa",  # the app's noqa comments cannot hide a diagnostic
            "--jobs=1",
            f"--format={FORMAT}",
            f"--output-file={OUT}/{REPORT}",
            f"--max-line-length={cfg['max_line_length']}",
            f"--max-complexity={cfg['max_complexity']}",
            f"--select={cfg['select']}",
        ]
        if cfg["extend_ignore"]:
            argv.append(f"--extend-ignore={cfg['extend_ignore']}")
        # No "--" before the paths: flake8 7 then treats every option given before it (its
        # preliminary parse re-reads them) as a file name. The paths all start with /src/.
        return self.make_invocation([*argv, *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        code = check_exit(outcome, {0, 1}, "flake8")
        data = outcome.out_files.get(REPORT)
        if data is None:
            raise ToolFailure(reason="tool_error", message=f"flake8 wrote no report {OUT}/{REPORT}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolFailure(reason="parse_error", message=f"flake8 report is not UTF-8: {exc}") from exc
        if text and not text.endswith("\n"):
            raise ToolFailure(reason="parse_error", message="flake8 report ends mid-line (truncated)")
        lines = [line for line in text.split("\n") if line]
        if (code == 1) != bool(lines):
            raise ToolFailure(
                reason="parse_error",
                message=f"flake8 exited {code} but its report holds {len(lines)} diagnostics",
            )
        selected = {f.path for f in files}
        findings: list[RawFinding] = []
        unparseable: list[dict[str, Any]] = []
        by_rule: dict[str, int] = {}
        for number, line in enumerate(lines, start=1):
            fields = line.split(SEP)
            if len(fields) != 5:
                raise ToolFailure(
                    reason="parse_error",
                    message=f"flake8 report line {number} has {len(fields)} fields, not 5: {line[:200]!r}",
                )
            path, row, col, rule, message = fields
            rel = rel_path(path)
            if rel not in selected:
                raise ToolFailure(reason="parse_error", message=f"flake8 reported a file it was not given: {path}")
            try:
                row_n, col_n = int(row), int(col)
            except ValueError as exc:
                raise ToolFailure(reason="parse_error", message=f"flake8 report line {number}: {exc}") from exc
            if rule in PARSE_FAILURE_CODES or rule in IO_FAILURE_CODES:
                unparseable.append({"path": rel, "reason": f"line {row_n}: {rule} {message}"})
                continue
            findings.append(
                RawFinding(
                    rule_id=rule,
                    message=message,
                    file_path=rel,
                    line=row_n if row_n > 0 else None,
                    column=col_n if col_n > 0 else None,
                    title=rule,
                ),
            )
            by_rule[rule] = by_rule.get(rule, 0) + 1
        cov = coverage(files, unparseable=unparseable)
        return build_parse("flake8", files, findings, cov, metrics={"by_rule": dict(sorted(by_rule.items()))})
