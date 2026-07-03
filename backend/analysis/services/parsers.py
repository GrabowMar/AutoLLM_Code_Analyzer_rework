"""Parsers that turn raw tool output into normalized findings and metrics.

Each parser maps a tool's JSON (or text) output to ``list[FindingData]`` or,
for tools whose output is (partly) metric-shaped, a ``ParsedOutput`` carrying
aggregate metrics alongside any findings. Parsers are looked up by the
``parser_key`` declared on a catalog tool.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from backend.analysis.services.base import FindingData
from backend.analysis.services.base import ParsedOutput

logger = logging.getLogger(__name__)

_PARSERS: dict[str, Callable[[str], list[FindingData] | ParsedOutput]] = {}


def register(key: str) -> Callable[[Callable], Callable]:
    def _wrap(fn: Callable[[str], list[FindingData] | ParsedOutput]) -> Callable:
        _PARSERS[key] = fn
        return fn

    return _wrap


def parse(parser_key: str, raw_output: str) -> ParsedOutput:
    """Run the parser registered for *parser_key* on *raw_output*."""
    fn = _PARSERS.get(parser_key)
    if fn is None:
        logger.warning("No parser registered for key %r", parser_key)
        return ParsedOutput()
    try:
        result = fn(raw_output or "")
    except Exception:
        logger.exception("Parser %r failed", parser_key)
        return ParsedOutput()
    if isinstance(result, ParsedOutput):
        return result
    return ParsedOutput(findings=result)


def has_parser(parser_key: str) -> bool:
    return parser_key in _PARSERS


def _loads(raw: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return None
    # Tools sometimes emit warnings before the JSON payload; trim to first
    # JSON delimiter to be forgiving.
    for opener in ("[", "{"):
        idx = raw.find(opener)
        if idx > 0:
            try:
                return json.loads(raw[idx:])
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Tool produced no JSON (e.g. an empty scan or a CLI error banner).
        # Treat as "no findings" rather than raising a noisy parser error.
        return None


@register("bandit")
def parse_bandit(raw: str) -> list[FindingData]:
    data = _loads(raw) or {}
    sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
    conf_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
    findings: list[FindingData] = []
    for item in data.get("results", []):
        findings.append(
            FindingData(
                severity=sev_map.get(item.get("issue_severity", "LOW"), "low"),
                category="security",
                title=item.get("issue_text", "Security issue"),
                description=item.get("issue_text", ""),
                file_path=item.get("filename", ""),
                line_number=item.get("line_number"),
                code_snippet=(item.get("code") or "")[:500],
                rule_id=item.get("test_id", ""),
                confidence=conf_map.get(item.get("issue_confidence", "MEDIUM"), "medium"),
                tool_specific_data={"test_name": item.get("test_name", "")},
            ),
        )
    return findings


@register("ruff")
def parse_ruff(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
    findings: list[FindingData] = []
    for item in data:
        loc = item.get("location") or {}
        findings.append(
            FindingData(
                severity="low",
                category="quality",
                title=item.get("message", "Lint issue"),
                description=item.get("message", ""),
                file_path=item.get("filename", ""),
                line_number=loc.get("row"),
                column_number=loc.get("column"),
                rule_id=item.get("code") or "",
                confidence="high",
            ),
        )
    return findings


@register("eslint")
def parse_eslint(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
    sev_map = {2: "high", 1: "low"}
    findings: list[FindingData] = []
    for file_entry in data:
        file_path = file_entry.get("filePath", "")
        for msg in file_entry.get("messages", []):
            findings.append(
                FindingData(
                    severity=sev_map.get(msg.get("severity", 1), "low"),
                    category="quality",
                    title=msg.get("message", "Lint issue"),
                    description=msg.get("message", ""),
                    file_path=file_path,
                    line_number=msg.get("line"),
                    column_number=msg.get("column"),
                    rule_id=msg.get("ruleId") or "",
                    confidence="high",
                ),
            )
    return findings


@register("semgrep")
def parse_semgrep(raw: str) -> list[FindingData]:
    data = _loads(raw) or {}
    sev_map = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    findings: list[FindingData] = []
    for item in data.get("results", []):
        extra = item.get("extra") or {}
        start = item.get("start") or {}
        end = item.get("end") or {}
        message = extra.get("message") or item.get("check_id", "Semgrep finding")
        findings.append(
            FindingData(
                severity=sev_map.get(str(extra.get("severity", "INFO")).upper(), "low"),
                category="security",
                title=(message.splitlines() or ["Semgrep finding"])[0][:200],
                description=message,
                file_path=item.get("path", ""),
                line_number=start.get("line"),
                column_number=start.get("col"),
                code_snippet=(extra.get("lines") or "")[:500],
                rule_id=item.get("check_id", ""),
                confidence=str((extra.get("metadata") or {}).get("confidence", "medium")).lower() or "medium",
                tool_specific_data={"end_line": end.get("line")},
            ),
        )
    return findings


@register("pylint")
def parse_pylint(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
    sev_map = {
        "fatal": "high",
        "error": "high",
        "warning": "medium",
        "refactor": "low",
        "convention": "low",
        "info": "info",
    }
    findings: list[FindingData] = []
    for item in data:
        msg_type = str(item.get("type", "warning")).lower()
        findings.append(
            FindingData(
                severity=sev_map.get(msg_type, "low"),
                category="quality",
                title=item.get("message", "Lint issue"),
                description=item.get("message", ""),
                file_path=item.get("path", ""),
                line_number=item.get("line"),
                column_number=item.get("column"),
                rule_id=item.get("symbol") or item.get("message-id") or "",
                confidence="high",
                tool_specific_data={"obj": item.get("obj", "")},
            ),
        )
    return findings


_MYPY_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<level>error|warning|note):\s*(?P<msg>.*?)"
    r"(?:\s+\[(?P<code>[\w-]+)\])?$",
)


@register("mypy")
def parse_mypy(raw: str) -> list[FindingData]:
    """Mypy has no stable JSON output; parse its ``file:line: level: msg`` text."""
    sev_map = {"error": "medium", "warning": "low", "note": "info"}
    findings: list[FindingData] = []
    for raw_line in (raw or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = _MYPY_LINE_RE.match(line)
        if not m:
            continue
        # "note" lines are usually continuations of the preceding error.
        if m.group("level") == "note":
            continue
        findings.append(
            FindingData(
                severity=sev_map.get(m.group("level"), "low"),
                category="quality",
                title=m.group("msg").strip(),
                description=m.group("msg").strip(),
                file_path=m.group("file"),
                line_number=int(m.group("line")),
                column_number=int(m.group("col")) if m.group("col") else None,
                rule_id=m.group("code") or "",
                confidence="high",
            ),
        )
    return findings


@register("radon")
def parse_radon(raw: str) -> ParsedOutput:
    """Radon ``cc -j`` output: ``{file: [block]}`` with nested methods/closures.

    Complexity is metric-shaped, so every block feeds the metrics; only the
    truly pathological ranks (E/F) also become findings.
    """
    data = _loads(raw) or {}
    if not isinstance(data, dict):
        return ParsedOutput()
    sev_map = {"E": "high", "F": "critical"}
    blocks: list[dict[str, Any]] = []
    findings: list[FindingData] = []

    def _walk(file_path: str, block: dict) -> None:
        rank = str(block.get("rank", ""))
        complexity = block.get("complexity")
        blocks.append(
            {
                "file": file_path,
                "name": block.get("name", "?"),
                "type": block.get("type", "block"),
                "line": block.get("lineno"),
                "complexity": complexity if isinstance(complexity, int) else 0,
                "rank": rank,
            },
        )
        if rank in sev_map:
            findings.append(
                FindingData(
                    severity=sev_map[rank],
                    category="performance",
                    title=(
                        f"{block.get('type', 'block')} '{block.get('name', '?')}' has "
                        f"cyclomatic complexity {complexity} (rank {rank})"
                    ),
                    file_path=file_path,
                    line_number=block.get("lineno"),
                    column_number=block.get("col_offset"),
                    rule_id=f"CC-{rank}",
                    confidence="high",
                    tool_specific_data={"complexity": complexity, "endline": block.get("endline")},
                ),
            )
        for child in (block.get("methods") or []) + (block.get("closures") or []):
            _walk(file_path, child)

    for file_path, file_blocks in data.items():
        # A file radon failed to parse maps to {"error": "..."} — skip it.
        if not isinstance(file_blocks, list):
            continue
        for block in file_blocks:
            _walk(file_path, block)

    if not blocks:
        return ParsedOutput(findings=findings)

    complexities = [b["complexity"] for b in blocks]
    rank_distribution = dict.fromkeys("ABCDEF", 0)
    for b in blocks:
        if b["rank"] in rank_distribution:
            rank_distribution[b["rank"]] += 1
    top_functions = sorted(blocks, key=lambda b: b["complexity"], reverse=True)[:25]
    metrics = {
        "total_blocks": len(blocks),
        "average_complexity": round(sum(complexities) / len(complexities), 2),
        "max_complexity": max(complexities),
        "rank_distribution": rank_distribution,
        "top_functions": top_functions,
    }
    return ParsedOutput(findings=findings, metrics=metrics)


_VULTURE_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): (?P<msg>.+?) \((?P<conf>\d+)% confidence\)$",
)


@register("vulture")
def parse_vulture(raw: str) -> list[FindingData]:
    """Vulture has no JSON output; parse ``file:line: msg (NN% confidence)`` text."""
    findings: list[FindingData] = []
    for raw_line in (raw or "").splitlines():
        m = _VULTURE_LINE_RE.match(raw_line.rstrip())
        if not m:
            continue
        msg = m.group("msg")
        conf = int(m.group("conf"))
        findings.append(
            FindingData(
                severity="medium" if msg.startswith("unreachable code") else "low",
                category="quality",
                title=msg,
                description=msg,
                file_path=m.group("file"),
                line_number=int(m.group("line")),
                rule_id="-".join(msg.split()[:2]).lower(),
                confidence="high" if conf >= 90 else "medium" if conf >= 60 else "low",
                tool_specific_data={"confidence_pct": conf},
            ),
        )
    return findings


@register("detect_secrets")
def parse_detect_secrets(raw: str) -> list[FindingData]:
    """detect-secrets baseline JSON: ``{"results": {path: [item]}}``."""
    data = _loads(raw) or {}
    if not isinstance(data, dict):
        return []
    findings: list[FindingData] = []
    for path, items in (data.get("results") or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            secret_type = item.get("type", "secret")
            findings.append(
                FindingData(
                    severity="high",
                    category="secrets",
                    title=f"Potential secret: {secret_type}",
                    description=f"detect-secrets flagged a {secret_type} in {path}.",
                    file_path=item.get("filename") or path,
                    line_number=item.get("line_number"),
                    rule_id=secret_type,
                    confidence="medium",
                    tool_specific_data={
                        "hashed_secret": item.get("hashed_secret", ""),
                        "is_verified": item.get("is_verified", False),
                    },
                ),
            )
    return findings


@register("jscpd")
def parse_jscpd(raw: str) -> ParsedOutput:
    """jscpd JSON report: ``{"duplicates": [...], "statistics": {"total": {...}}}``.

    Clones are location-anchored and stay findings; the overall duplication
    statistics are metric-shaped and would otherwise be lost.
    """
    data = _loads(raw) or {}
    if not isinstance(data, dict):
        return ParsedOutput()
    findings: list[FindingData] = []
    for dup in data.get("duplicates") or []:
        first = dup.get("firstFile") or {}
        second = dup.get("secondFile") or {}
        lines = dup.get("lines") or 0
        findings.append(
            FindingData(
                severity="medium" if lines >= 25 else "low",
                category="quality",
                title=(
                    f"Duplicated block ({lines} lines) also found in {second.get('name', '?')}:{second.get('start')}"
                ),
                file_path=first.get("name", ""),
                line_number=first.get("start"),
                code_snippet=(dup.get("fragment") or "")[:500],
                rule_id="duplicate-code",
                confidence="high",
                tool_specific_data={
                    "tokens": dup.get("tokens"),
                    "second_file": second.get("name", ""),
                    "second_start": second.get("start"),
                },
            ),
        )

    total = ((data.get("statistics") or {}).get("total")) or {}
    metrics = (
        {
            "clones": total.get("clones", 0),
            "duplicated_lines": total.get("duplicatedLines", 0),
            "duplicated_tokens": total.get("duplicatedTokens", 0),
            "total_lines": total.get("lines", 0),
            "total_sources": total.get("sources", 0),
            "duplication_percentage": round(float(total.get("percentage", 0.0)), 2),
            "token_duplication_percentage": round(float(total.get("percentageTokens", 0.0)), 2),
        }
        if total
        else {}
    )
    return ParsedOutput(findings=findings, metrics=metrics)


@register("hadolint")
def parse_hadolint(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
    if not isinstance(data, list):
        return []
    sev_map = {"error": "high", "warning": "medium", "info": "low", "style": "info"}
    findings: list[FindingData] = []
    for item in data:
        findings.append(
            FindingData(
                severity=sev_map.get(str(item.get("level", "warning")).lower(), "low"),
                category="quality",
                title=item.get("message", "Dockerfile issue"),
                description=item.get("message", ""),
                file_path=item.get("file", ""),
                line_number=item.get("line"),
                column_number=item.get("column"),
                rule_id=item.get("code", ""),
                confidence="high",
            ),
        )
    return findings


_CODESPELL_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\s+(?P<wrong>\S+) ==> (?P<right>.+)$",
)


@register("codespell")
def parse_codespell(raw: str) -> list[FindingData]:
    """codespell has no JSON output; parse ``file:line: wrong ==> right`` text."""
    findings: list[FindingData] = []
    for raw_line in (raw or "").splitlines():
        m = _CODESPELL_LINE_RE.match(raw_line.rstrip())
        if not m:
            continue
        wrong, right = m.group("wrong"), m.group("right")
        findings.append(
            FindingData(
                severity="info",
                category="quality",
                title=f"Possible typo: '{wrong}'",
                description=f"'{wrong}' looks like a misspelling.",
                suggestion=f"Replace '{wrong}' with '{right}'",
                file_path=m.group("file"),
                line_number=int(m.group("line")),
                rule_id="typo",
                confidence="medium",
            ),
        )
    return findings


@register("gitleaks")
def parse_gitleaks(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
    if not isinstance(data, list):
        return []
    findings: list[FindingData] = []
    for item in data:
        rule = item.get("RuleID") or item.get("Rule") or "secret"
        desc = item.get("Description") or f"Potential secret ({rule})"
        findings.append(
            FindingData(
                severity="high",
                category="secrets",
                title=desc[:200],
                description=desc,
                file_path=item.get("File", ""),
                line_number=item.get("StartLine"),
                column_number=item.get("StartColumn"),
                code_snippet=(item.get("Match") or item.get("Secret") or "")[:500],
                rule_id=rule,
                confidence="high",
                tool_specific_data={"entropy": item.get("Entropy")},
            ),
        )
    return findings
