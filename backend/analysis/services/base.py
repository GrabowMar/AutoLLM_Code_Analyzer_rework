"""Shared dataclasses for the container-based analysis engine.

The old code-registry (``BaseAnalyzer`` ABC + ``AnalyzerRegistry``) has been
replaced by a data-driven tool catalog (:class:`~backend.analysis.models.AnalyzerTool`)
that runs tools inside a per-user container.  These dataclasses remain the
normalized in-memory shapes that parsers and runners produce.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import field
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "info")

# Known-vulnerability scanners. Every ``dependency`` finding of theirs is about
# a package (often one the stack scaffold pinned), not code the model wrote, so
# it is counted apart from code findings: this is the study tables' rule
# (``statistics.dedup.IssueCluster.is_advisory``), and it includes packages
# that do not exist (``nonexistent-*``): the study reports them with the other
# dependency findings, split by who declared the package (model or scaffold).
# Manifest hygiene (deptry, depcheck: missing/unused dependencies) is the
# app's own code issue.
ADVISORY_TOOLS = frozenset({"pip-audit", "npm-audit", "osv-scanner", "trivy", "retire"})


# Directories that never hold the app's own source: dependency trees, virtual
# environments, caches, build output and agent scratch space. Jobs stored
# before the generator skipped them can still contain them; the analyzed tree
# leaves them out (``summary.excluded_paths``).
NON_SOURCE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".cache",
        ".npm",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tmp",
        ".temp",
        "tmp",
    },
)


def is_non_source_path(path: str) -> bool:
    """Whether *path* lies inside one of :data:`NON_SOURCE_DIRS` (any directory level)."""
    return any(part in NON_SOURCE_DIRS for part in path.strip("/").split("/")[:-1])


def is_dependency_advisory(tool_slug: str, category: str, rule_id: str = "") -> bool:
    """Whether a finding is a dependency advisory of a known-vulnerability scanner (not a code issue).

    *rule_id* is accepted for callers written against the earlier rule; it does not change the answer.
    """
    return tool_slug in ADVISORY_TOOLS and category == "dependency"


def fingerprint(text: str) -> str:
    """Short, stable content hash for provenance (12 hex chars of SHA-256)."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


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
    # Report items the parser read but deliberately did not count (mypy errors
    # that only reflect uninstalled dependencies, ESLint directives naming
    # unloaded rules). They show the tool ran and reported, so a non-zero exit
    # with no counted finding is still a measurement, not a crashed tool.
    recognized: int = 0


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
