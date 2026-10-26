"""lizard adapter: cyclomatic complexity per function across languages (CSV).

``-i -1`` keeps the exit status at 0 whatever the warnings, so any non-zero
status is a failure. The CSV (no header) has one row per function:
NLOC, CCN, tokens, parameters, length, location, file, function, long name,
start line, end line; a malformed row fails the parse. lizard tokenizes
rather than parses, so it reports no unparseable files; files without
functions simply have no rows.

Functions with CCN above ``ccn_threshold`` (``-C``, lizard's own warning
rule) become ``cyclomatic-complexity`` findings with ``extra["ccn"]``.

lizard picks its reader by file extension and tokenizes a file without one
(``.pyi``, ``.pyw``, ``.mts``, ``.cts``) as C, silently: such files are never
passed to it and are coverage ``skipped`` entries. lizard drops a function
whose comments say ``#lizard forgive`` and stops reading a file at any
comment containing ``GENERATED CODE`` (the generation scaffolds' own header
says "...YOUR GENERATED CODE"); no option turns that off, so when a selected
file contains either marker lizard runs on staged copies with the markers
neutralized (``staged_argv``; line numbers are kept).
"""

from __future__ import annotations

import csv
import io
from pathlib import PurePosixPath
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import STAGE
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import complexity_summary
from backend.analysis.engine.adapters.base import coverage
from backend.analysis.engine.adapters.base import rel_path
from backend.analysis.engine.adapters.base import src_path
from backend.analysis.engine.adapters.base import staged_argv
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

REPORT = "lizard.csv"
COLUMNS = ("nloc", "ccn", "tokens", "params", "length", "location", "file", "function", "long_name", "start", "end")
# Sample language -> lizard language reader.
LANGUAGES = {
    "python": "python",
    "javascript": "javascript",
    "jsx": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "vue": "vue",
}
# File extensions lizard 1.24 has a reader for (anything else is read as C).
READER_EXTENSIONS = frozenset({".py", ".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx", ".vue"})
# In-file markers lizard honours unconditionally, and their neutralized form.
MARKERS = {b"lizard forgive": "lizard-forgive", b"GENERATED CODE": "GENERATED-CODE"}
SED_NEUTRALIZE = [f"s/{marker.decode()}/{replacement}/g" for marker, replacement in MARKERS.items()]


def marked_files(files: list[SampleFile]) -> list[str]:
    """Paths of the files containing a marker that would hide functions from lizard."""
    return [f.path for f in files if any(marker in f.content for marker in MARKERS)]


@register
class LizardAdapter(ToolAdapter):
    slug = "lizard"

    def unsupported(self, file: SampleFile) -> str | None:
        suffix = PurePosixPath(file.path).suffix.lower()
        if suffix in READER_EXTENSIONS:
            return None
        return f"lizard has no reader for {suffix or 'extension-less'} files (it would tokenize them as C)"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        files = self.analysable(files)
        argv = [f"{BIN}/lizard", "--csv", "-o", f"{OUT}/{REPORT}", "-i", "-1", "-C", str(cfg["ccn_threshold"])]
        if cfg["modified_ccn"]:
            argv.append("-m")
        for language in sorted({LANGUAGES[f.language] for f in files if f.language in LANGUAGES}):
            argv += ["-l", language]
        if marked_files(files):
            return self.make_invocation(staged_argv([*argv, "--"], files, SED_NEUTRALIZE), report_files=[REPORT])
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "lizard")
        cfg = self.effective_config(config)
        neutralized = marked_files(self.analysable(files))
        root = STAGE if neutralized else SRC
        data = outcome.out_files.get(REPORT)
        if data is None:
            raise ToolFailure(reason="tool_error", message=f"lizard wrote no report {OUT}/{REPORT}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolFailure(reason="parse_error", message=f"lizard report is not UTF-8: {exc}") from exc
        functions = []
        for number, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if not row:
                continue
            if len(row) != len(COLUMNS):
                raise ToolFailure(
                    reason="parse_error",
                    message=f"lizard CSV row {number} has {len(row)} columns, not {len(COLUMNS)}",
                )
            record = dict(zip(COLUMNS, row, strict=True))
            try:
                functions.append(
                    {
                        "path": rel_path(record["file"], root=root),
                        "name": record["function"],
                        "line": int(record["start"]),
                        "end_line": int(record["end"]),
                        "ccn": int(record["ccn"]),
                        "nloc": int(record["nloc"]),
                        "tokens": int(record["tokens"]),
                        "params": int(record["params"]),
                        "length": int(record["length"]),
                    },
                )
            except ValueError as exc:
                raise ToolFailure(
                    reason="parse_error",
                    message=f"lizard CSV row {number} is malformed: {exc}",
                ) from exc
        findings = [_finding(f) for f in functions if f["ccn"] > cfg["ccn_threshold"]]
        metrics = {
            "complexity": complexity_summary([f["ccn"] for f in functions]),
            "functions": functions,
            "nloc": sum(f["nloc"] for f in functions),
            "ccn_threshold": cfg["ccn_threshold"],
            "modified_ccn": cfg["modified_ccn"],
            "markers_neutralized": neutralized,
        }
        notes = []
        if neutralized:
            notes.append(
                f"'#lizard forgive' / 'GENERATED CODE' markers neutralized in {len(neutralized)} files "
                "(lizard would have dropped their functions)",
            )
        cov = coverage(files, skipped=self.unsupported_coverage(files))
        return build_parse("lizard", files, findings, cov, metrics=metrics, notes=notes)


def _finding(function: dict[str, Any]) -> RawFinding:
    return RawFinding(
        rule_id="cyclomatic-complexity",
        message=f"function {function['name']} has cyclomatic complexity {function['ccn']} "
        f"({function['nloc']} lines of code, {function['params']} parameters)",
        file_path=function["path"],
        line=function["line"],
        end_line=function["end_line"],
        title=f"Complexity {function['ccn']}: {function['name']}",
        extra={
            "ccn": function["ccn"],
            "nloc": function["nloc"],
            "tokens": function["tokens"],
            "params": function["params"],
            "function": function["name"],
        },
    )
