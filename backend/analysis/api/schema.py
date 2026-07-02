"""Ninja schemas for the analysis / analyzers API."""

from __future__ import annotations

from typing import Any

from ninja import Schema

# --- Catalog tools ----------------------------------------------------------


class AnalyzerToolSchema(Schema):
    slug: str
    name: str
    description: str
    category: str
    kind: str
    target_language: str
    icon: str
    version: str
    docs_url: str
    config_schema: list[dict[str, Any]]
    default_config: dict[str, Any]
    run_timeout: int
    is_enabled: bool
    # Per-user install state (annotated by the view):
    installed: bool = False
    install_status: str = ""
    installed_version: str = ""


# --- Workspace --------------------------------------------------------------


class WorkspaceSchema(Schema):
    id: str
    status: str
    image: str
    error_message: str
    container_name: str
    last_used_at: str | None
    installed_count: int


class InstalledToolSchema(Schema):
    id: str
    tool_slug: str
    tool_name: str
    category: str
    status: str
    installed_version: str
    config: dict[str, Any]
    install_log: str


class InstallToolSchema(Schema):
    tool_slug: str


class ToolConfigSchema(Schema):
    config: dict[str, Any]


class TestToolSchema(Schema):
    config: dict[str, Any] = {}


class TestResultSchema(Schema):
    available: bool
    message: str
    findings: list[dict[str, Any]]
    raw_output: dict[str, Any]


# --- Runs / findings --------------------------------------------------------


class RunCreateSchema(Schema):
    name: str = ""
    tool_slugs: list[str]
    generation_job_id: str | None = None
    source_code: dict[str, str] | None = None
    auto_start: bool = True


class ToolResultSchema(Schema):
    id: str
    tool_slug: str
    category: str
    status: str
    summary: dict[str, Any]
    error_message: str


class FindingSchema(Schema):
    id: str
    severity: str
    category: str
    confidence: str
    title: str
    description: str
    suggestion: str
    file_path: str
    line_number: int | None
    column_number: int | None
    code_snippet: str
    rule_id: str
    tool_slug: str


class AnalysisRunSchema(Schema):
    id: str
    name: str
    status: str
    tool_slugs: list[str]
    summary: dict[str, Any]
    error_message: str
    generation_job_id: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    results: list[ToolResultSchema]


class AnalysisRunListSchema(Schema):
    id: str
    name: str
    status: str
    tool_slugs: list[str]
    summary: dict[str, Any]
    created_at: str


class PaginatedRunsSchema(Schema):
    items: list[AnalysisRunListSchema]
    total: int
    page: int
    per_page: int
    pages: int


class PaginatedFindingsSchema(Schema):
    items: list[FindingSchema]
    total: int
    page: int
    per_page: int
    pages: int
