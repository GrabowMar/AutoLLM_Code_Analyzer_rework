"""Parsers that turn raw tool output into normalized findings.

Each parser maps a tool's JSON (or text) output to ``list[FindingData]``.
Parsers are looked up by the ``parser_key`` declared on a catalog tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from typing import Callable

from llm_lab.analysis.services.base import FindingData

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
    return json.loads(raw)


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
