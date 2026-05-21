"""Pydantic schemas for reports API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import ModelSchema
from ninja import Schema

from llm_lab.reports.models import Report


class ReportSummarySchema(ModelSchema):
    id: UUID
    generation_job_id: UUID | None = None
    analysis_task_id: UUID | None = None

    class Meta:
        model = Report
        fields = (
            "report_id",
            "report_type",
            "title",
            "description",
            "status",
            "error_message",
            "progress_percent",
            "summary",
            "created_at",
            "completed_at",
            "expires_at",
        )


class ReportDetailSchema(ModelSchema):
    id: UUID
    generation_job_id: UUID | None = None
    analysis_task_id: UUID | None = None

    class Meta:
        model = Report
        fields = (
            "report_id",
            "report_type",
            "title",
            "description",
            "config",
            "status",
            "error_message",
            "progress_percent",
            "summary",
            "report_data",
            "created_at",
            "completed_at",
            "expires_at",
        )


class ReportListResponse(Schema):
    reports: list[ReportSummarySchema]
    pagination: dict[str, int]


class GenerateReportIn(Schema):
    report_type: str
    config: dict[str, Any] = {}
    title: str | None = None
    description: str = ""
    expires_in_days: int | None = 30


class ReportDataResponse(Schema):
    report_id: str
    report_type: str
    title: str
    status: str
    progress: int
    data: dict[str, Any] | None = None


class GenericResponse(Schema):
    success: bool
    message: str = ""


__all__ = [
    "GenerateReportIn",
    "GenericResponse",
    "ReportDataResponse",
    "ReportDetailSchema",
    "ReportListResponse",
    "ReportSummarySchema",
]


# silence unused import warning — datetime kept for runtime usage in subclasses
_ = (datetime,)
