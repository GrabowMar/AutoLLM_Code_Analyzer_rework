"""radon adapter: cyclomatic complexity (cc), maintainability index (mi), raw counts.

One container runs the three radon commands on the selected files, each
writing its JSON report to /out (``set -e``: the first failing command fails
the tool). radon exits 0 even when a file does not parse — such files appear
as ``{"error": ...}`` entries and become unparseable files.

Metrics cover functions, methods and closures (classes aggregate their
methods and would be counted twice). Blocks at or above ``finding_min_rank``
become ``cyclomatic-complexity`` findings with the rank letter as native
severity and ``extra["complexity"]``.

radon reads only ``*.py`` files: an explicit ``.pyi``/``.pyw`` path is
silently dropped from all three reports. Such files are never passed to it
and are coverage ``skipped`` entries; a sample without any ``.py`` file is
not applicable.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import BIN
from backend.analysis.engine.adapters.base import OUT
from backend.analysis.engine.adapters.base import ToolAdapter
from backend.analysis.engine.adapters.base import build_parse
from backend.analysis.engine.adapters.base import check_exit
from backend.analysis.engine.adapters.base import complexity_summary
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

CC_REPORT = "radon-cc.json"
MI_REPORT = "radon-mi.json"
RAW_REPORT = "radon-raw.json"
RANKS = "ABCDEF"
SCRIPT = (
    "set -e; "
    f'{BIN}/radon cc --json --show-complexity --output-file {OUT}/{CC_REPORT} -- "$@"; '
    f'{BIN}/radon mi --json --show --output-file {OUT}/{MI_REPORT} -- "$@"; '
    f'{BIN}/radon raw --json --output-file {OUT}/{RAW_REPORT} -- "$@"'
)
RAW_KEYS = ("loc", "lloc", "sloc", "comments", "multi", "blank", "single_comments")


@register
class RadonAdapter(ToolAdapter):
    slug = "radon"

    def unsupported(self, file: SampleFile) -> str | None:
        # radon's file filter (cli/tools.py _is_python_file): *.py, *.ipynb or a python shebang.
        if file.path.endswith(".py"):
            return None
        return "radon analyses only *.py files (it silently drops other explicit paths)"

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        self.effective_config(config)
        argv = ["sh", "-c", SCRIPT, "radon", *(src_path(f) for f in self.analysable(files))]
        return self.make_invocation(argv, report_files=[CC_REPORT, MI_REPORT, RAW_REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "radon")
        cfg = self.effective_config(config)
        min_rank = RANKS.index(cfg["finding_min_rank"])
        cc = load_json(read_report(outcome, CC_REPORT, "radon cc"), what="radon cc report", expect=dict)
        mi = load_json(read_report(outcome, MI_REPORT, "radon mi"), what="radon mi report", expect=dict)
        raw = load_json(read_report(outcome, RAW_REPORT, "radon raw"), what="radon raw report", expect=dict)
        selected = {f.path for f in self.analysable(files)}
        unparseable: list[dict[str, Any]] = []
        findings: list[RawFinding] = []
        functions: list[dict[str, Any]] = []
        with report_structure("radon cc report"):
            for reported, blocks in sorted(cc.items()):
                path = rel_path(reported)
                if isinstance(blocks, dict):
                    unparseable.append({"path": path, "reason": str(blocks.get("error") or blocks)})
                    continue
                for block in _walk(blocks):
                    entry = {
                        "path": path,
                        "name": _qualified(block),
                        "type": block["type"],
                        "line": int(block["lineno"]),
                        "end_line": int(block.get("endline") or block["lineno"]),
                        "complexity": int(block["complexity"]),
                        "rank": str(block["rank"]),
                    }
                    functions.append(entry)
                    if RANKS.index(entry["rank"]) >= min_rank:
                        findings.append(_finding(entry))
        maintainability = _mi_metrics(mi, unparseable)
        raw_totals = _raw_metrics(raw, unparseable)
        # cc lists only files with blocks; raw lists every file it read.
        missing = selected - {rel_path(p) for p in raw}
        if missing:
            raise ToolFailure(reason="parse_error", message=f"radon raw did not report {len(missing)} selected files")
        metrics = {
            "complexity": complexity_summary([f["complexity"] for f in functions]),
            "functions": functions,
            "maintainability": maintainability,
            "raw": raw_totals,
        }
        cov = coverage(files, unparseable=unparseable, skipped=self.unsupported_coverage(files))
        return build_parse("radon", files, findings, cov, metrics=metrics)


def _walk(blocks: list[dict[str, Any]], parent: str = "") -> list[dict[str, Any]]:
    """Functions, methods and closures, depth first (class blocks are containers only)."""
    out = []
    for block in blocks:
        kind = block["type"]
        if kind == "class":
            out += _walk(block.get("methods") or [], parent=block["name"])
            continue
        out.append({**block, "classname": block.get("classname") or parent})
        out += _walk(block.get("closures") or [], parent=f"{parent}.{block['name']}" if parent else block["name"])
    return out


def _qualified(block: dict[str, Any]) -> str:
    return f"{block['classname']}.{block['name']}" if block.get("classname") else str(block["name"])


def _finding(entry: dict[str, Any]) -> RawFinding:
    return RawFinding(
        rule_id="cyclomatic-complexity",
        message=f"{entry['type']} {entry['name']} has cyclomatic complexity {entry['complexity']} "
        f"(rank {entry['rank']})",
        file_path=entry["path"],
        line=entry["line"],
        end_line=entry["end_line"],
        native_severity=entry["rank"],
        title=f"Complexity {entry['complexity']}: {entry['name']}",
        extra={"complexity": entry["complexity"], "function": entry["name"], "block_type": entry["type"]},
    )


def _mi_metrics(mi: dict[str, Any], unparseable: list[dict[str, Any]]) -> dict[str, Any]:
    per_file = {}
    with report_structure("radon mi report"):
        for reported, value in sorted(mi.items()):
            path = rel_path(reported)
            if "error" in value:
                unparseable.append({"path": path, "reason": str(value["error"])})
                continue
            per_file[path] = {"mi": round(float(value["mi"]), 4), "rank": str(value["rank"])}
    scores = [v["mi"] for v in per_file.values()]
    return {
        "mean": round(sum(scores) / len(scores), 4) if scores else None,
        "min": min(scores) if scores else None,
        "per_file": per_file,
    }


def _raw_metrics(raw: dict[str, Any], unparseable: list[dict[str, Any]]) -> dict[str, Any]:
    totals = dict.fromkeys(RAW_KEYS, 0)
    files = 0
    with report_structure("radon raw report"):
        for reported, value in sorted(raw.items()):
            if "error" in value:
                unparseable.append({"path": rel_path(reported), "reason": str(value["error"])})
                continue
            files += 1
            for key in RAW_KEYS:
                totals[key] += int(value.get(key, 0))
    return {"files": files, **totals}
