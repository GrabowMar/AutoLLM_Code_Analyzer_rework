"""Shared in-memory shapes of the analysis engine.

Every engine module exchanges these dataclasses; none of them touch the
database. Persistence (``executor``) maps them onto the models.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Versions of the deterministic engine stages, frozen into every plan so a
# change in resolution/counting/normalization/dedup is visible in provenance.
SAMPLE_RESOLVER_VERSION = "sample-v2"
SLOC_VERSION = "sloc-v2"
NORMALIZER_VERSION = "norm-v2"
DEDUP_VERSION = "dedup-v2"
MEASUREMENT_VERSION = "measure-v2"


# --------------------------------------------------------------------------
# Sample (what gets analysed)
# --------------------------------------------------------------------------


@dataclass
class SampleFile:
    """One file of a sample with everything needed to scope and attribute it."""

    path: str  # posix, relative, no leading "./" or "/"
    content: bytes
    sha256: str
    size: int
    language: str  # python, javascript, jsx, typescript, tsx, vue, svelte, html, css, json, yaml, toml,
    # markdown, dockerfile, shell, text, env, other
    kind: str  # source | config | manifest | lockfile | doc | asset | build | env | other
    origin: str  # FileOrigin value: authored | added | modified | scaffold | platform | unknown
    included: bool = True  # handed to tools at all
    exclude_reason: str = ""  # lockfile | build_artifact | binary | too_large | minified | vendored | glob
    sloc: int = 0  # code lines (sloc-v1), 0 for non-code
    measured: bool = False  # included AND origin in the plan's measured origins AND code
    # Line-level attribution for origin == "modified": 1-based line numbers
    # that differ from the scaffold baseline. None = whole file attributed to
    # the file origin (no baseline available).
    changed_lines: list[int] | None = None
    measured_sloc: int = 0  # sloc restricted to attributed lines when changed_lines is set
    # Inline suppression comments (ruff/flake8, bandit, eslint, semgrep, gitleaks, detect-secrets markers).
    suppression_markers: int = 0

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def line_origin(self, line: int | None) -> str:
        """Origin of one line: scaffold for unchanged lines of a modified file."""
        if self.origin == "modified" and self.changed_lines is not None and line is not None:
            return "modified" if line in set(self.changed_lines) else "scaffold"
        return self.origin

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "language": self.language,
            "kind": self.kind,
            "origin": self.origin,
            "included": self.included,
            "exclude_reason": self.exclude_reason,
            "sloc": self.sloc,
            "measured": self.measured,
            "measured_sloc": self.measured_sloc,
            "changed_lines": len(self.changed_lines) if self.changed_lines is not None else None,
            "suppression_markers": self.suppression_markers,
        }


@dataclass
class Sample:
    """A resolved analysis target."""

    files: list[SampleFile]
    source: str  # agent | scaffolding | custom | legacy | inline
    sample_hash: str  # sha256 over sorted (path, sha256) of included files
    # Snapshot of job identity + quality flags (see sample.target_meta()).
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    resolver_version: str = SAMPLE_RESOLVER_VERSION

    def included(self) -> list[SampleFile]:
        return [f for f in self.files if f.included]

    def by_path(self) -> dict[str, SampleFile]:
        return {f.path: f for f in self.files}

    def manifest(self) -> dict[str, Any]:
        """JSON stored on AnalysisRun.sample_manifest."""
        inc = self.included()
        return {
            "resolver_version": self.resolver_version,
            "sloc_version": SLOC_VERSION,
            "source": self.source,
            "files": [f.manifest_entry() for f in self.files],
            "totals": {
                "files": len(self.files),
                "included": len(inc),
                "measured": sum(1 for f in inc if f.measured),
                "sloc": sum(f.sloc for f in inc),
                "measured_sloc": sum(f.measured_sloc for f in inc if f.measured),
                "suppression_markers": sum(f.suppression_markers for f in inc if f.measured),
            },
            "warnings": list(self.warnings),
        }


class SampleUnavailable(Exception):  # noqa: N818 - domain wording
    """The target cannot be analysed (failed job, no code, unknown shape)."""

    def __init__(self, message: str, *, code: str = "no_code") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Container execution
# --------------------------------------------------------------------------


@dataclass
class Invocation:
    """How to run one tool: argv inside the analyzer image + resources.

    The sample is mounted read-only at ``/src`` (cwd is ``/tmp``, a tmpfs);
    tools write report files into ``/out``, which is collected afterwards.
    """

    argv: list[str]
    timeout_s: int = 120
    mem_limit: str = "768m"
    env: dict[str, str] = field(default_factory=dict)
    # Files the tool reads besides /src (e.g. a generated file list), put
    # into /out before start: {name: bytes}.
    inputs: dict[str, bytes] = field(default_factory=dict)
    # Report files expected in /out after the run (names relative to /out).
    report_files: tuple[str, ...] = ()


@dataclass
class ExecOutcome:
    """What happened when a container ran (never interpreted here)."""

    exit_code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    out_files: dict[str, bytes] = field(default_factory=dict)
    timed_out: bool = False
    oom_killed: bool = False
    cancelled: bool = False
    wall_seconds: float = 0.0
    image_id: str = ""
    container_name: str = ""
    infra_error: str | None = None  # daemon/SDK failure: the tool never ran properly
    # /out exceeded the collection cap: some report files were not read. Adapters
    # must treat a missing report as a failure, never as an empty result.
    out_truncated: bool = False


# --------------------------------------------------------------------------
# Tool interpretation
# --------------------------------------------------------------------------


@dataclass
class RawFinding:
    """A finding as a tool reported it, before normalization."""

    rule_id: str
    message: str
    file_path: str  # relative to /src (adapters strip the /src/ prefix)
    line: int | None = None
    end_line: int | None = None
    column: int | None = None
    native_severity: str = ""
    native_confidence: str = ""
    cwe: list[str] = field(default_factory=list)  # "CWE-78" strings when the tool provides them
    title: str = ""
    description: str = ""
    suggestion: str = ""
    snippet: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolParse:
    """Successful interpretation of one tool execution."""

    findings: list[RawFinding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    # Keys: files_selected, files_scanned, scanned (paths), and unparseable / skipped /
    # partial as lists of path + reason entries.
    coverage: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class ToolFailure(Exception):  # noqa: N818 - domain wording
    """The tool did not produce a trustworthy result. Never becomes 0 findings."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason  # ToolResult.FailureReason value
        self.message = message


class NotApplicable(Exception):  # noqa: N818 - domain wording
    """Nothing in scope for this tool (e.g. no Python files for bandit)."""


@dataclass
class NormalizedFinding:
    """A finding mapped onto the shared vocabulary (persisted as Finding)."""

    tool_slug: str
    rule_id: str
    title: str
    severity: str  # one of SEVERITIES
    native_severity: str
    category: str  # FindingCategory value
    cwe: list[str]
    confidence: str  # high | medium | low
    file_path: str
    line: int | None
    end_line: int | None
    column: int | None
    description: str = ""
    suggestion: str = ""
    snippet: str = ""
    file_origin: str = "unknown"
    in_measured_scope: bool = True
    counted: bool = True
    fingerprint: str = ""
    issue_key: str = ""
    is_ai: bool = False
    grounding: str = ""
    is_consensus: bool = False
    sample_index: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
