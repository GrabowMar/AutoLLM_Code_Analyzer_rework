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
    # The severity/level exactly as the tool printed it ("HIGH", "error",
    # "convention"); empty when the tool rates nothing. ``severity`` above is
    # a normalization (a heuristic for unrated tools) that only aggregate
    # statistics use: the UI shows this field, never an invented level.
    tool_severity: str = ""


@dataclass
class ParsedOutput:
    """What a parser extracts from raw tool output.

    Finding-shaped tools fill ``findings``; metric-shaped tools (complexity,
    duplication, …) fill ``metrics`` with aggregate data that would be lost
    if forced into per-finding rows. Mixed tools fill both.
    """

    findings: list[FindingData] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    # Coverage gaps (e.g. one of several manifests could not be audited): the
    # result stands but is incomplete.
    warnings: list[str] = field(default_factory=list)
    # The output shows the tool did not measure anything: the result must be
    # recorded as failed, not as a clean (zero-finding) one.
    error: str | None = None


@dataclass
class AnalyzerOutput:
    """Standard output from running a single tool."""

    findings: list[FindingData] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    raw_output: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
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
