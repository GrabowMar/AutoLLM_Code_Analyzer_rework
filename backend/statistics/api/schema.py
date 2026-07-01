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
    apps_success_rate: float
    total_analyses: int
    analyses_completed: int
    analyses_failed: int
    analyses_running: int
    analyses_pending: int
    analyses_success_rate: float
    avg_analysis_duration_seconds: float
    total_findings: int


class SeverityBucketSchema(Schema):
    severity: str
    count: int
    percent: float


class SeverityDistributionSchema(Schema):
    total: int
    distribution: list[SeverityBucketSchema]


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
    model_id: str
    name: str
    provider: str
    apps: int
    apps_completed: int
    success_rate: float
    avg_duration_seconds: float
    cost_efficiency: float
    security: float
    quality: float
    performance: float
    mss: float
    findings: dict[str, int]


class ToolRowSchema(Schema):
    name: str
    type: str
    scans: int
    findings: int
    avg_per_scan: float
    top_rule: str


class TopFindingSchema(Schema):
    title: str
    severity: str
    count: int


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
