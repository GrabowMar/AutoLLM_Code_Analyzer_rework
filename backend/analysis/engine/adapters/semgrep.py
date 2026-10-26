"""Semgrep adapter (semgrep CE, vendored rule packs, offline).

Exit codes (semgrep 1.180 without --error): 0 = scan completed, findings or
not; anything else (2 fatal, 3-8 config/rule/target errors) = failure.

The JSON report's ``errors`` are classified: rule/config errors without a
file (level "error") fail the tool, because the rule set that ran is then
unknown; per-file syntax errors are unparseable files; partial parses and
per-file rule timeouts are "partial" coverage. ``paths.scanned`` lists what
was really scanned. Rule ids are the registry form baked into the image
(``python.lang.security.audit.eval-detected.eval-detected``), kept verbatim
with ``--no-rewrite-rule-ids``. Without a login semgrep CE replaces the
matched lines by "requires login"; that placeholder is dropped.
"""

from __future__ import annotations

import re
from typing import Any

from backend.analysis.engine.adapters import register
from backend.analysis.engine.adapters.base import ANALYZER
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
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

REPORT = "semgrep.json"
RULES_DIR = f"{ANALYZER}/rules/semgrep"
REDACTED = "requires login"
_CWE_RE = re.compile(r"CWE-(\d+)", re.IGNORECASE)
# Metadata kept with each finding (the normalizer maps category / CWE / impact).
METADATA_KEYS = (
    "category",
    "subcategory",
    "confidence",
    "likelihood",
    "impact",
    "cwe",
    "owasp",
    "technology",
    "vulnerability_class",
    "references",
)
# Sample languages each vendored pack has rules for (Vue templates are matched
# only by the regex rule avoid-v-html, which lives in javascript-audit).
PACK_LANGUAGES: dict[str, frozenset[str]] = {
    "python": frozenset({"python"}),
    "python-audit": frozenset({"python"}),
    "javascript": frozenset({"javascript", "jsx", "typescript", "tsx"}),
    "javascript-audit": frozenset({"javascript", "jsx", "typescript", "tsx", "vue"}),
    "html": frozenset({"html"}),
}
# Error types meaning "this file could not be parsed".
_SYNTAX_TYPES = ("Syntax error", "Lexical error", "Other syntax error", "Parse error")
_PARTIAL_TYPES = ("PartialParsing", "Timeout", "Out of memory", "Too many matches", "Fixpoint timeout")


@register
class SemgrepAdapter(ToolAdapter):
    slug = "semgrep"

    def select_files(self, sample: Sample, config: dict[str, Any]) -> list[SampleFile]:
        """Files of the languages the configured rule packs cover (none -> not applicable)."""
        packs = self.effective_config(config)["rulesets"]
        covered = frozenset().union(*(PACK_LANGUAGES[pack] for pack in packs))
        return [f for f in super().select_files(sample, config) if f.language in covered]

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        cfg = self.effective_config(config)
        argv = [
            f"{BIN}/semgrep",
            "scan",
            "--json",
            "--output",
            f"{OUT}/{REPORT}",
            "--quiet",
            "--metrics",
            "off",
            "--disable-version-check",
            "--oss-only",
            "--no-git-ignore",  # explicit targets; an app .gitignore cannot hide files
            "--disable-nosem",  # the app's "nosemgrep" comments cannot hide findings
            "--no-rewrite-rule-ids",
            "--timeout",
            str(cfg["per_file_timeout"]),
            "--timeout-threshold",
            "3",
            "--max-target-bytes",
            "4000000",
            "--jobs",
            "1",
        ]
        for pack in cfg["rulesets"]:
            argv += ["--config", f"{RULES_DIR}/{pack}.json"]
        for rule in filter(None, (r.strip() for r in cfg["exclude_rules"].split(","))):
            argv += ["--exclude-rule", rule]
        return self.make_invocation([*argv, "--", *(src_path(f) for f in files)], report_files=[REPORT])

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        check_exit(outcome, {0}, "semgrep")
        report = load_json(read_report(outcome, REPORT, "semgrep"), what="semgrep report", expect=dict)
        with report_structure("semgrep report"):
            unparseable, partial, fatal = _classify_errors(report.get("errors") or [])
            if fatal:
                raise ToolFailure(reason="tool_error", message="semgrep reported errors: " + "; ".join(fatal[:5]))
            paths = report["paths"]
            scanned = [rel_path(p) for p in paths.get("scanned") or []]
            findings = [_finding(item) for item in report["results"]]
        selected = {f.path for f in files}
        bad = {u["path"] for u in unparseable}
        skipped = [
            {"path": path, "reason": "not scanned by semgrep (no rule for its language)"}
            for path in sorted(selected - set(scanned) - bad)
        ]
        cov = coverage(
            files,
            unparseable=unparseable,
            skipped=skipped,
            partial=partial,
            scanned=[p for p in scanned if p in selected and p not in bad],
        )
        packs = self.effective_config(config)["rulesets"]
        return build_parse("semgrep", files, findings, cov, metrics={"rulesets": packs, "errors": len(partial)})


def _classify_errors(errors: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[str]]:
    unparseable: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    fatal: list[str] = []
    for error in errors:
        kind = error.get("type")
        kind_name = kind[0] if isinstance(kind, list) and kind else str(kind or "")
        message = " ".join(str(error.get("message") or kind_name).split())
        path = error.get("path")
        if not path:
            # A rule/config/engine error: the rule set that actually ran is unknown.
            if str(error.get("level", "error")).lower() == "error":
                fatal.append(f"{kind_name}: {message[:300]}")
            continue
        entry = {"path": rel_path(path), "reason": f"{kind_name}: {message}"}
        if kind_name in _PARTIAL_TYPES:
            partial.append(entry)
        elif kind_name in _SYNTAX_TYPES or str(error.get("level", "")).lower() == "error":
            unparseable.append(entry)
        else:
            partial.append(entry)
    return unparseable, partial, fatal


def _finding(item: dict[str, Any]) -> RawFinding:
    extra = item.get("extra") or {}
    metadata = extra.get("metadata") or {}
    start = item.get("start") or {}
    end = item.get("end") or {}
    rule_id = str(item["check_id"])
    lines = str(extra.get("lines") or "")
    cwe: list[str] = []
    raw_cwe = metadata.get("cwe") or []
    for entry in raw_cwe if isinstance(raw_cwe, list) else [raw_cwe]:
        match = _CWE_RE.search(str(entry))
        if match and f"CWE-{int(match.group(1))}" not in cwe:
            cwe.append(f"CWE-{int(match.group(1))}")
    fingerprint = str(extra.get("fingerprint") or "")
    return RawFinding(
        rule_id=rule_id,
        message=" ".join(str(extra.get("message") or "").split()),
        file_path=rel_path(item["path"]),
        line=as_int(start.get("line")),
        end_line=as_int(end.get("line")),
        column=as_int(start.get("col")),
        native_severity=str(extra.get("severity") or ""),
        native_confidence=str(metadata.get("confidence") or ""),
        cwe=cwe,
        title=rule_id.rsplit(".", 1)[-1],
        snippet="" if lines == REDACTED else lines,
        extra={
            "metadata": {key: metadata[key] for key in METADATA_KEYS if key in metadata},
            "end_column": as_int(end.get("col")),
            "engine_kind": extra.get("engine_kind") or "",
            "fingerprint": "" if fingerprint == REDACTED else fingerprint,
        },
    )
