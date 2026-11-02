"""Schemas for statistics endpoints."""

from __future__ import annotations

from typing import Any

from ninja import Schema


class OverviewSchema(Schema):
    total_models: int
    models_in_use: int
    total_apps: int
    apps_completed: int
    apps_failed: int
    apps_running: int
    apps_pending: int
    apps_cancelled: int = 0
    # completed / (completed + failed); None until something finished.
    apps_success_rate: float | None = None
    total_analyses: int
    analyses_completed: int
    analyses_partial: int = 0
    analyses_failed: int
    analyses_running: int
    analyses_pending: int
    analyses_cancelled: int = 0
    # completed / (completed + partial + failed); None until something finished.
    analyses_success_rate: float | None = None
    # Mean over completed analyses; None until one completed.
    avg_analysis_duration_seconds: float | None = None
    total_findings: int


class SeverityBucketSchema(Schema):
    severity: str
    count: int
    percent: float


class SeverityDistributionSchema(Schema):
    total: int
    distribution: list[SeverityBucketSchema]
    by_source: dict[str, dict[str, int]] | None = None


class TrendPointSchema(Schema):
    date: str
    label: str
    total: int
    completed: int
    failed: int


class TrendsSchema(Schema):
    days: int
    total: int
    series: list[TrendPointSchema]


class ModelComparisonRowSchema(Schema):
    """Shared rankings scoring: mss/empirical/composite are 0..1 scales."""

    model_id: str
    name: str
    provider: str
    apps: int
    apps_completed: int
    apps_failed: int = 0
    # Over finished generations (completed + failed); None when none finished.
    success_rate: float | None = None
    # Mean over completed generations; None when none completed.
    avg_duration_seconds: float | None = None
    mss: float
    empirical_quality: float | None = None
    composite: float
    smoke_pass_rate: float | None = None
    n_trials: int = 0
    # Code issues the empirical score counts; None when no app of the model was measured.
    findings: dict[str, int] | None = None
    ai_findings: dict[str, int]


class ToolRowSchema(Schema):
    name: str
    type: str
    # Executions that measured the app (completed, or skipped as not applicable).
    scans: int
    # Failed, cancelled or app-not-running executions: they measured nothing.
    not_measured: int = 0
    findings: int
    # findings / scans; None when no execution measured anything.
    avg_per_scan: float | None = None
    top_rule: str


class TopFindingSchema(Schema):
    title: str
    tool: str = ""
    # The level exactly as the tool printed it; "" when the tool rates nothing.
    tool_severity: str = ""
    count: int
    # A known-vulnerability advisory about a package, not a code issue (P2).
    dependency_advisory: bool = False


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
    pending: int = 0
    cancelled: int = 0
    # completed / (completed + failed); None until something finished.
    success_rate: float | None = None
    # Mean over completed generations; None until one completed.
    avg_duration_seconds: float | None = None
    total_tokens: int
    total_cost_usd: float
    # Source LOC (reports.services.loc), the study tables' denominator; legacy
    # jobs without stored code fall back to metrics.lines_of_code.
    total_lines_of_code: int
    unique_templates: int


class AnalyzerInfoRowSchema(Schema):
    name: str
    type: str
    display_name: str
    available: bool
    message: str


class AnalyzerHealthSchema(Schema):
    total: int
    online: int
    offline: int
    by_type: dict[str, dict[str, int]]
    analyzers: list[AnalyzerInfoRowSchema]


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


_ = Any  # silence unused-import on minimal type usage
