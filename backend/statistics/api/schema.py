"""Schemas for statistics endpoints."""

from __future__ import annotations

from typing import Any

from ninja import Schema
from pydantic import Field


class OverviewSchema(Schema):
    total_models: int
    models_in_use: int
    total_apps: int
    apps_completed: int
    apps_failed: int
    apps_running: int
    apps_pending: int
    apps_success_rate: float
    total_analyses: int
    analyses_completed: int
    analyses_failed: int
    analyses_running: int
    analyses_pending: int
    analyses_success_rate: float
    avg_analysis_duration_seconds: float
    # Counted static findings of each analysed job's representative run
    # (over ``static_measured_jobs`` jobs; AI over ``ai_reviewed_jobs``).
    # None when no job has such a measurement: no data, never "0 findings".
    total_findings: int | None
    analyses_partial: int = 0
    analyses_cancelled: int = 0
    total_ai_findings: int | None = None
    measured_jobs: int = 0
    static_measured_jobs: int = 0
    ai_reviewed_jobs: int = 0


class SeverityBucketSchema(Schema):
    severity: str
    count: int
    percent: float


class SeverityDistributionSchema(Schema):
    total: int
    distribution: list[SeverityBucketSchema]
    by_source: dict[str, dict[str, int]] | None = None
    # Jobs behind ``by_source.static`` / ``by_source.ai``: zero counts over
    # zero jobs are no data, not clean code.
    static_measured_jobs: int = 0
    ai_reviewed_jobs: int = 0


class TrendPointSchema(Schema):
    date: str
    label: str
    total: int
    completed: int
    failed: int
    partial: int = 0


class TrendsSchema(Schema):
    days: int
    total: int
    series: list[TrendPointSchema]


class ModelComparisonRowSchema(Schema):
    """Shared rankings scoring: mss/empirical/composite are 0..1 scales.

    As on the rankings rows, ``findings`` / ``issues`` are zero-filled over
    the ``n_static`` trials with an available static measurement and
    ``ai_findings`` over the ``n_ai`` trials with a successful AI review:
    read those counts before the severities.
    """

    model_id: str
    name: str
    provider: str
    apps: int
    apps_completed: int
    success_rate: float
    avg_duration_seconds: float
    mss: float
    empirical_quality: float | None = None
    composite: float
    # Legacy name of ``functional_pass_rate`` (the conformance evaluator's
    # pass rate; the runtime smoke probe is never counted).
    smoke_pass_rate: float | None = None
    functional_pass_rate: float | None = None
    functional_n: int = 0
    n_trials: int = 0
    n_static: int = 0
    n_ai: int = 0
    findings: dict[str, int]
    ai_findings: dict[str, int]
    issues: dict[str, int] | None = None
    measured_sloc: int | None = None
    plan_hash: str | None = None


class ToolRowSchema(Schema):
    """One analyzer on the tool card: its *behaviour*, not code quality.

    Population: each of the user's jobs' latest finished-or-failed analysis
    run (COMPLETED, PARTIAL or FAILED; v2 runs only; cancelled runs and
    self-tests excluded), so a tool failure that failed the whole run still
    counts against the tool.
    """

    name: str = Field(description="Tool slug.")
    type: str = Field(description="Tool category.")
    scans: int = Field(
        description="Succeeded executions only: failed, timed-out, not-applicable and skipped ones are not scans.",
    )
    findings: int = Field(
        description="Every finding of the succeeded executions (raw tool output, counted or not; "
        "for the AI reviewer every replicate's findings).",
    )
    avg_per_scan: float | None = Field(
        default=None,
        description="findings / scans; null when the tool never scanned (scans == 0).",
    )
    top_rule: str = Field(description="Most frequent rule among `findings` ('' when none).")
    runs: int = Field(default=0, description="Executions in the runs considered, whatever their status.")
    failed: int = 0
    timed_out: int = 0
    not_applicable: int = Field(default=0, description="Executions with no file in scope (nothing to scan).")
    skipped: int = 0
    success_rate: float | None = Field(
        default=None,
        description="scans / (scans + failed + timed_out); null when the tool never attempted a scan.",
    )
    counted_findings: int = Field(
        default=0,
        description="The part of `findings` the measurement counts (in the measured scope, counted category).",
    )
    versions: list[str] = Field(default=[], description="Tool versions of the succeeded executions.")


class TopFindingSchema(Schema):
    title: str
    severity: str
    count: int
    rule_id: str = ""
    tool: str = ""
    is_ai: bool = False


class ActivityItemSchema(Schema):
    kind: str
    id: str
    title: str
    status: str
    created_at: str


class CodeGenerationSchema(Schema):
    total_apps: int
    completed: int
    failed: int
    running: int
    success_rate: float
    avg_duration_seconds: float
    total_tokens: int
    total_cost_usd: float
    # SLOC of included files of the analysed jobs (lockfiles/build output excluded).
    total_lines_of_code: int
    unique_templates: int
    measured_lines_of_code: int = 0
    loc_jobs_covered: int = 0
    unique_stacks: int = 0


class AnalyzerInfoRowSchema(Schema):
    name: str
    type: str
    display_name: str
    # True only when enabled AND passing the latest canary self-test.
    available: bool
    message: str
    status: str = ""  # passed | failed | untested | disabled
    version: str = ""


class AnalyzerHealthSchema(Schema):
    total: int
    online: int
    offline: int
    by_type: dict[str, dict[str, int]]
    analyzers: list[AnalyzerInfoRowSchema]
    selftest: dict[str, Any] | None = None


class DashboardSchema(Schema):
    overview: OverviewSchema
    severity: SeverityDistributionSchema
    trends: TrendsSchema
    models: list[ModelComparisonRowSchema]
    tools: list[ToolRowSchema]
    top_findings: list[TopFindingSchema]
    code_generation: CodeGenerationSchema
    analyzer_health: AnalyzerHealthSchema
    recent_activity: list[ActivityItemSchema]


__all__ = [
    "ActivityItemSchema",
    "AnalyzerHealthSchema",
    "AnalyzerInfoRowSchema",
    "CodeGenerationSchema",
    "DashboardSchema",
    "ModelComparisonRowSchema",
    "OverviewSchema",
    "SeverityBucketSchema",
    "SeverityDistributionSchema",
    "ToolRowSchema",
    "TopFindingSchema",
    "TrendPointSchema",
    "TrendsSchema",
]
