"""Shared dataclasses for the container-based analysis engine.

The old code-registry (``BaseAnalyzer`` ABC + ``AnalyzerRegistry``) has been
replaced by a data-driven tool catalog (:class:`~backend.analysis.models.AnalyzerTool`)
that runs tools inside a per-user container.  These dataclasses remain the
normalized in-memory shapes that parsers and runners produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass
class FindingData:
    """Normalized finding produced by a tool parser."""

    severity: str  # critical, high, medium, low, info
    category: str  # security, quality, performance, style, best_practice, …
    title: str
    description: str = ""
    suggestion: str = ""
    file_path: str = ""
    line_number: int | None = None
    column_number: int | None = None
    code_snippet: str = ""
    rule_id: str = ""
    confidence: str = "medium"  # high, medium, low
    tool_specific_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalyzerOutput:
    """Standard output from running a single tool."""

    findings: list[FindingData] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    raw_output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def has_error(self) -> bool:
        return self.error is not None

    @property
    def finding_counts(self) -> dict[str, int]:
        return build_severity_counts(self.findings)


def build_severity_counts(findings: list[FindingData]) -> dict[str, int]:
    """Return a severity→count dict from a list of findings."""
    counts: dict[str, int] = dict.fromkeys(SEVERITIES, 0)
    for f in findings:
        if f.severity in counts:
            counts[f.severity] += 1
    return counts
