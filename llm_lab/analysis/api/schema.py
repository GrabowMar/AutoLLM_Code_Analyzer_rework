"""Django Ninja schemas for analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import ModelSchema
from ninja import Schema

from llm_lab.analysis.models import AnalysisProfile
from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding

# ── Request schemas ───────────────────────────────────────────────────


class AnalysisTaskCreateSchema(Schema):
    name: str = ""
    generation_job_id: str | None = None
    source_code: dict[str, str] = {}
    analyzers: list[str] = []
    settings: dict[str, Any] = {}
    auto_start: bool = True
    live_target: bool = False
    profile_id: int | None = None
    thresholds: dict[str, int] = {}


class SuppressSchema(Schema):
    reason: str = ""


# ── Task schemas ──────────────────────────────────────────────────────


class AnalysisTaskSchema(ModelSchema):
    generation_job_id: str | None = None
    generation_job_name: str | None = None
    created_by_email: str = ""
    results_count: int = 0
    findings_count: int = 0
    target_url: str | None = None
    container_instance_id: str | None = None
    profile_id: int | None = None

    class Meta:
        model = AnalysisTask
        fields = [
            "id",
            "name",
            "status",
            "threshold_status",
            "source_code",
            "configuration",
            "results_summary",
            "started_at",
            "completed_at",
            "duration_seconds",
            "error_message",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_generation_job_id(obj: AnalysisTask) -> str | None:
        if obj.generation_job_id:
            return str(obj.generation_job_id)
        return None

    @staticmethod
    def resolve_generation_job_name(obj: AnalysisTask) -> str | None:
        if obj.generation_job:
            return str(obj.generation_job)
        return None

    @staticmethod
    def resolve_created_by_email(obj: AnalysisTask) -> str:
        if obj.created_by:
            return obj.created_by.email
        return ""

    @staticmethod
    def resolve_results_count(obj: AnalysisTask) -> int:
        return obj.results.count()

    @staticmethod
    def resolve_findings_count(obj: AnalysisTask) -> int:
        return Finding.objects.filter(result__task=obj).count()

    @staticmethod
    def resolve_target_url(obj: AnalysisTask) -> str | None:
        return obj.configuration.get("target_url") or None

    @staticmethod
    def resolve_container_instance_id(obj: AnalysisTask) -> str | None:
        return obj.configuration.get("container_instance_id") or None

    @staticmethod
    def resolve_profile_id(obj: AnalysisTask) -> int | None:
        return obj.profile_id


class AnalysisTaskListSchema(Schema):
    id: UUID
    name: str
    status: str
    threshold_status: str = "not_configured"
    created_at: datetime
    updated_at: datetime
    generation_job_id: str | None = None
    created_by_email: str = ""
    results_summary: dict = {}
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    container_instance_id: str | None = None
    target_url: str | None = None
    profile_id: int | None = None


class PaginatedAnalysisTasksSchema(Schema):
    items: list[AnalysisTaskListSchema]
    total: int
    page: int
    per_page: int
    pages: int


# ── Result schemas ────────────────────────────────────────────────────


class AnalysisResultSchema(ModelSchema):
    task_id: str = ""
    findings_count: int = 0
    finding_summary: dict = {}

    class Meta:
        model = AnalysisResult
        fields = [
            "id",
            "analyzer_type",
            "analyzer_name",
            "status",
            "raw_output",
            "summary",
            "error_message",
            "started_at",
            "completed_at",
            "duration_seconds",
            "created_at",
        ]

    @staticmethod
    def resolve_task_id(obj: AnalysisResult) -> str:
        return str(obj.task_id)

    @staticmethod
    def resolve_findings_count(obj: AnalysisResult) -> int:
        return obj.findings.count()

    @staticmethod
    def resolve_finding_summary(obj: AnalysisResult) -> dict:
        counts: dict[str, int] = {}
        for severity in Finding.Severity:
            count = obj.findings.filter(severity=severity.value).count()
            if count:
                counts[severity.value] = count
        return counts


# ── Finding schemas ───────────────────────────────────────────────────


class FindingSchema(ModelSchema):
    analyzer_name: str = ""
    result_id: int = 0
    suppressed_by_email: str | None = None

    class Meta:
        model = Finding
        fields = [
            "id",
            "severity",
            "category",
            "confidence",
            "title",
            "description",
            "suggestion",
            "file_path",
            "line_number",
            "column_number",
            "code_snippet",
            "rule_id",
            "tool_specific_data",
            "suppressed",
            "suppression_reason",
            "created_at",
        ]

    @staticmethod
    def resolve_analyzer_name(obj: Finding) -> str:
        return obj.result.analyzer_name

    @staticmethod
    def resolve_result_id(obj: Finding) -> int:
        return obj.result_id

    @staticmethod
    def resolve_suppressed_by_email(obj: Finding) -> str | None:
        if obj.suppressed_by_id:
            return obj.suppressed_by.email
        return None


class PaginatedFindingsSchema(Schema):
    items: list[FindingSchema]
    total: int
    page: int
    per_page: int
    pages: int


# ── Action response schema ────────────────────────────────────────────


class ActionResponseSchema(Schema):
    success: bool
    message: str = ""
    status: str = ""


# ── Analyzer info schemas ─────────────────────────────────────────────


class ConfigFieldSchema(Schema):
    name: str
    type: str
    label: str
    description: str
    default: Any
    options: list[dict]
    required: bool
    min: float | None
    max: float | None
    placeholder: str


class AnalyzerInfoSchema(Schema):
    name: str
    type: str
    display_name: str
    description: str
    available: bool
    availability_message: str
    default_config: dict
    config_schema: list[ConfigFieldSchema]
    supports_live_target: bool
    supported_code_types: list[str]


# ── Stats schema ──────────────────────────────────────────────────────


class AnalysisStatsSchema(Schema):
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    running_tasks: int
    total_findings: int
    findings_by_severity: dict[str, int]
    findings_by_category: dict[str, int]
    most_common_issues: list[dict]


# ── Profile schemas ───────────────────────────────────────────────────


class AnalysisProfileSchema(ModelSchema):
    created_by_email: str | None = None

    class Meta:
        model = AnalysisProfile
        fields = [
            "id",
            "name",
            "description",
            "analyzers",
            "settings",
            "is_default",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_created_by_email(obj: AnalysisProfile) -> str | None:
        if obj.created_by:
            return obj.created_by.email
        return None


class AnalysisProfileCreateSchema(Schema):
    name: str
    description: str = ""
    analyzers: list[str] = []
    settings: dict[str, Any] = {}
    is_default: bool = False
