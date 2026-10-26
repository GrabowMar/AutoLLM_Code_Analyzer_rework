"""jscpd adapter (copy/paste detection; JSON report in /out/jscpd/).

jscpd 4 is given the selected source files explicitly, an explicit --config
(so an app's .jscpd.json is never looked up) and a disjoint format/extension
map: by default .jsx/.vue files are tokenized under several formats and
counted twice in jscpd's own statistics. Exit code 0 is required (no
--exit-code / --threshold is passed) and the report must exist.

Each clone pair becomes one finding anchored at the first location, the
second location in ``extra``. The duplication percentage is recomputed from
the clones: every (file, line) covered by any clone fragment counts once,
divided by the physical lines of the scanned files (``sloc.split_lines``:
numbered on ``\n`` like the clone ranges) — jscpd's own totals are kept for
reference only. Metrics: ``{percentage, clones, duplicated_lines,
total_lines, files: {path: {lines, duplicated_lines}}, jscpd_total}``, so the
measurement can restrict the percentage to measured files.

jscpd skips every region between ``jscpd:ignore-start`` and
``jscpd:ignore-end`` (anywhere in a file) and has no option to stop that;
when a selected file contains the marker, jscpd runs on staged copies with
the markers neutralized (``staged_argv``; line numbers are kept).
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine import sloc
from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import CONFIG
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import SRC
from backend.analysis.engine.adapters.base import STAGE
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
from backend.analysis.engine.adapters.base import staged_argv
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolParse

REPORT = "jscpd/jscpd-report.json"
FORMATS = "python,javascript,jsx,typescript,tsx,vue,css,markup"
FORMATS_EXTS = (
    "python:py,pyw,pyi;javascript:js,mjs,cjs;jsx:jsx;typescript:ts,mts,cts;tsx:tsx;"
    "vue:vue;css:css,scss,sass,less;markup:html,htm"
)
SNIPPET_LIMIT = 4000
IGNORE_MARKER = b"jscpd:ignore-"
SED_NEUTRALIZE = [r"s/jscpd:ignore-(start|end)/jscpd-ignore-\1/g"]


def marked_files(files: list[SampleFile]) -> list[str]:
    """Paths of the files containing a ``jscpd:ignore-start/end`` marker."""
    return [f.path for f in files if IGNORE_MARKER in f.content]


@register
class JscpdAdapter(ToolAdapter):
    slug = "jscpd"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/jscpd",
            "--config",
            f"{CONFIG}/jscpd.json",
            "--min-tokens",
            str(cfg["min_tokens"]),
            "--min-lines",
            str(cfg["min_lines"]),
            "--max-lines",
            "1000000",
            "--max-size",
            "8mb",
            "--mode",
            cfg["mode"],
            "--format",
            FORMATS,
            "--formats-exts",
            FORMATS_EXTS,
            "--reporters",
            "json",
            "--output",
            f"{OUT}/jscpd",
            "--absolute",
            "--no-gitignore",
            "--silent",
            "--",
        ]
        if marked_files(files):
            return self.make_invocation(staged_argv(argv, files, SED_NEUTRALIZE), report_files=[REPORT])
        return self.make_invocation([*argv, *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "jscpd")
        report = load_json(read_report(outcome, REPORT, "jscpd"), what="jscpd report", expect=dict)
        selected = {f.path: f for f in files}
        neutralized = marked_files(files)
        root = STAGE if neutralized else SRC
        findings: list[RawFinding] = []
        covered: dict[str, set[int]] = {}
        with report_structure("jscpd report"):
            statistics = report["statistics"]
            scanned: set[str] = set()
            for stats in statistics["formats"].values():
                scanned.update(rel_path(path, root=root) for path in stats.get("sources") or {})
            for clone in report["duplicates"]:
                first, second = clone["firstFile"], clone["secondFile"]
                first_path, second_path = rel_path(first["name"], root=root), rel_path(second["name"], root=root)
                for path, part in ((first_path, first), (second_path, second)):
                    covered.setdefault(path, set()).update(range(int(part["start"]), int(part["end"]) + 1))
                lines = int(clone["lines"])
                findings.append(
                    RawFinding(
                        rule_id="duplicate-code",
                        message=f"{lines} duplicated lines ({clone['tokens']} tokens), also at "
                        f"{second_path}:{second['start']}-{second['end']}",
                        file_path=first_path,
                        line=as_int(first["start"]),
                        end_line=as_int(first["end"]),
                        title="Duplicated code",
                        snippet=str(clone.get("fragment") or "")[:SNIPPET_LIMIT],
                        extra={
                            "lines": lines,
                            "tokens": int(clone["tokens"]),
                            "format": str(clone.get("format") or ""),
                            "second_file": second_path,
                            "second_start": as_int(second["start"]),
                            "second_end": as_int(second["end"]),
                        },
                    ),
                )
            jscpd_total = dict(statistics.get("total") or {})
        scanned &= set(selected)
        # jscpd leaves out files shorter than the minimum clone: analysed, nothing to find.
        min_lines = self.effective_config(config)["min_lines"]
        too_small = {p for p in set(selected) - scanned if _code_lines(selected[p]) < min_lines}
        scanned |= too_small
        skipped = [{"path": p, "reason": "not tokenized by jscpd"} for p in sorted(set(selected) - scanned)]
        per_file = {}
        for path in sorted(scanned):
            # Lines as jscpd numbers its clone ranges and the other tools number theirs
            # (``sloc.split_lines``: "\n" only), so numerator and denominator agree.
            total = len(_lines(selected[path]))
            per_file[path] = {"lines": total, "duplicated_lines": len(covered.get(path, set()))}
        total_lines = sum(v["lines"] for v in per_file.values())
        duplicated = sum(v["duplicated_lines"] for v in per_file.values())
        # Shape read by engine/measurement (restricted to measured files via "files").
        metrics = {
            "percentage": round(100 * duplicated / total_lines, 4) if total_lines else None,
            "clones": len(findings),
            "duplicated_lines": duplicated,
            "total_lines": total_lines,
            "files": per_file,
            "jscpd_total": jscpd_total,
            "markers_neutralized": neutralized,
        }
        notes = []
        if neutralized:
            notes.append(f"jscpd:ignore markers neutralized in {len(neutralized)} files")
        cov = coverage(files, skipped=skipped, scanned=sorted(scanned))
        return build_parse("jscpd", files, findings, cov, metrics=metrics, notes=notes)


def _lines(file: SampleFile) -> list[str]:
    return sloc.split_lines(file.content.decode("utf-8", errors="replace"))


def _code_lines(file: SampleFile) -> int:
    return sum(1 for line in _lines(file) if line.strip())
