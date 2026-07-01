"""Parsers that turn raw tool output into normalized findings.

Each parser maps a tool's JSON (or text) output to ``list[FindingData]``.
Parsers are looked up by the ``parser_key`` declared on a catalog tool.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from typing import Callable

from backend.analysis.services.base import FindingData

logger = logging.getLogger(__name__)

_PARSERS: dict[str, Callable[[str], list[FindingData]]] = {}


def register(key: str) -> Callable[[Callable], Callable]:
    def _wrap(fn: Callable[[str], list[FindingData]]) -> Callable:
        _PARSERS[key] = fn
        return fn

    return _wrap


def parse(parser_key: str, raw_output: str) -> list[FindingData]:
    """Run the parser registered for *parser_key* on *raw_output*."""
    fn = _PARSERS.get(parser_key)
    if fn is None:
        logger.warning("No parser registered for key %r", parser_key)
        return []
    try:
        return fn(raw_output or "")
    except Exception:  # noqa: BLE001
        logger.exception("Parser %r failed", parser_key)
        return []


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
                confidence=str((extra.get("metadata") or {}).get("confidence", "medium")).lower()
                or "medium",
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
    for line in (raw or "").splitlines():
        line = line.rstrip()
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
