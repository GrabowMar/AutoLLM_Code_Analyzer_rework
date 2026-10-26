"""Ninja schemas for the analysis API (v2).

Request schemas validate payload shape; nested research JSON (plans,
measurements, preflight reports, protocols) is passed through as objects
because its structure is versioned by the engine (``schema_version`` keys),
not by the HTTP layer.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from ninja import Field
from ninja import Schema
from pydantic import ValidationInfo
from pydantic import field_validator

# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------

# Postgres text and jsonb cannot store NUL, and an unpaired UTF-16 surrogate
# (valid in JSON as "\ud800") cannot be encoded as UTF-8 at all.
_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_TEXT_DEPTH = 64


def _child(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if _IDENTIFIER.match(str(key)) else f"{path}[{str(key)!r}]"


def unstorable_text(value: Any, path: str, depth: int = 0) -> str | None:
    """Where *value* holds a string the database cannot store (NUL or an unpaired surrogate), else None.

    Walks lists and dicts (keys too), so nested JSON (research questions,
    configs, inline source files and their paths) is covered.
    """
    if isinstance(value, str):
        return path if _UNSTORABLE.search(value) else None
    if depth >= MAX_TEXT_DEPTH:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _UNSTORABLE.search(key):
                return f"{path} (a key)"
            if found := unstorable_text(item, _child(path, key), depth + 1):
                return found
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            if found := unstorable_text(item, _child(path, i), depth + 1):
                return found
    return None


class InputSchema(Schema):
    """Request bodies: every string (nested ones too) must be storable text.

    A NUL character or an unpaired surrogate is a 422 at its field instead
    of a database or encoding error (500) when the value is saved; binary
    files cannot be sent as inline source text.
    """

    @field_validator("*", mode="after")
    @classmethod
    def _storable_text(cls, value: Any, info: ValidationInfo) -> Any:
        if found := unstorable_text(value, info.field_name or "value"):
            msg = (
                f"contains a NUL character or an unpaired surrogate at {found}; "
                "send text only (binary files cannot be analysed as inline source)"
            )
            raise ValueError(msg)
        return value


class PageMeta(Schema):
    total: int
    page: int
    per_page: int
    pages: int


class ProfileRefSchema(Schema):
    id: str
    slug: str
    version: int
    name: str


# --------------------------------------------------------------------------
# Tools / image / reviewer / self-test
# --------------------------------------------------------------------------


class ToolSchema(Schema):
    slug: str
    name: str
    description: str
    kind: str
    category: str
    languages: list[str]
    file_kinds: list[str]
    output_kind: str
    icon: str
    docs_url: str
    display_order: int
    default_enabled: bool
    default_required: bool
    mem_limit: str
    timeout_s: int
    config_schema: list[dict[str, Any]]
    default_config: dict[str, Any]
    spec_hash: str
    version: str
    version_source: str
    is_enabled: bool
    expectations: dict[str, Any] | None = None
    canary: dict[str, Any] | None = None


class SelftestSchema(InputSchema):
    tool_slugs: list[str] | None = None


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


class ProfileCreateSchema(InputSchema):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    slug: str | None = Field(None, max_length=100, pattern=r"^[-a-zA-Z0-9_]+$")
    config: dict[str, Any]


class ProfileUpdateSchema(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    config: dict[str, Any] | None = None


class ProfileCloneSchema(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, max_length=100, pattern=r"^[-a-zA-Z0-9_]+$")


class ProfileValidateSchema(InputSchema):
    config: dict[str, Any]


class ProfileSchema(Schema):
    id: str
    slug: str
    version: int
    latest_version: int
    name: str
    description: str
    config: dict[str, Any]
    content_hash: str
    is_system: bool
    is_archived: bool
    is_owner: bool
    created_by_id: int | None
    summary: dict[str, Any]
    created_at: str | None
    updated_at: str | None


class ProfileVersionSchema(Schema):
    id: str
    version: int
    content_hash: str
    is_archived: bool
    created_at: str | None


class ProfileDetailSchema(ProfileSchema):
    versions: list[ProfileVersionSchema]
    usage: dict[str, int]


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


class RunCreateSchema(InputSchema):
    """Ad-hoc run: one target (a job or inline files) and one plan source (a profile or a config)."""

    name: str = Field("", max_length=200)
    generation_job_id: UUID | None = None
    source_code: dict[str, str] | None = None
    profile_id: UUID | None = None
    config: dict[str, Any] | None = None


class RunRetrySchema(InputSchema):
    tools: list[str] | None = None


class RunListItemSchema(Schema):
    id: str
    name: str
    purpose: str
    status: str
    phase: str
    is_complete: bool
    cancel_requested: bool
    attempt: int
    source: str
    generation_job_id: str | None
    study_id: str | None
    profile: ProfileRefSchema | None
    plan_hash: str
    sample_hash: str
    target: dict[str, Any]
    tool_slugs: list[str]
    tools: dict[str, int]
    summary: dict[str, Any]
    error_message: str
    stale: bool
    heartbeat_at: str | None
    created_by_id: int
    created_at: str | None
    started_at: str | None
    completed_at: str | None
    duration_seconds: float | None


class RunPageSchema(PageMeta):
    items: list[RunListItemSchema]


class ArtifactSchema(Schema):
    name: str
    bytes: int | None
    sha256: str
    compressed: bool
    url: str


class ToolResultSchema(Schema):
    id: str
    tool_slug: str
    tool_kind: str
    category: str
    position: int
    required: bool
    status: str
    failure_reason: str
    attempt: int
    tool_version: str
    image_ref: str
    config: dict[str, Any]
    config_hash: str
    argv: list[Any]
    exit_code: int | None
    duration_seconds: float | None
    started_at: str | None
    finished_at: str | None
    coverage: dict[str, Any]
    metrics: dict[str, Any]
    summary: dict[str, Any]
    diagnostics: dict[str, Any]
    artifacts: list[ArtifactSchema]
    error_message: str
    findings: dict[str, int]


class ReviewSampleSchema(Schema):
    id: str
    result_id: str
    index: int
    engine: str
    harness: str
    harness_version: str
    model_slug: str
    model_canonical_slug: str
    status: str
    failure_reason: str
    error_message: str
    prompt_version: str
    prompt_sha256: str
    schema_sha256: str
    file_order_seed: int | None
    files_reviewed: list[Any]
    coverage: dict[str, Any]
    usage: dict[str, Any]
    providers: dict[str, Any]
    finish_reasons: dict[str, Any]
    grounding: dict[str, Any]
    finding_count: int
    repaired: bool
    latency_seconds: float | None
    artifacts: list[ArtifactSchema]
    created_at: str | None


class RunDetailSchema(RunListItemSchema):
    plan: dict[str, Any]
    target_meta: dict[str, Any]
    manifest: dict[str, Any]
    results: list[ToolResultSchema]
    review_samples: list[ReviewSampleSchema]
    measurement: dict[str, Any]
    outcomes: dict[str, Any] | None
    lease_owner: str
    updated_at: str | None
    canary: dict[str, Any] | None


# --------------------------------------------------------------------------
# Findings / issues / files
# --------------------------------------------------------------------------


class FindingSchema(Schema):
    id: str
    result_id: str
    tool_slug: str
    rule_id: str
    title: str
    description: str
    suggestion: str
    severity: str
    severity_rank: int
    native_severity: str
    category: str
    cwe: list[Any]
    confidence: str
    file_path: str
    line_number: int | None
    end_line: int | None
    column_number: int | None
    code_snippet: str
    file_origin: str
    in_measured_scope: bool
    counted: bool
    fingerprint: str
    issue_key: str
    is_ai: bool
    review_sample_id: str | None
    review_sample_index: int | None
    grounding: str
    is_consensus: bool
    tool_specific_data: dict[str, Any]
    created_at: str | None


class FindingPageSchema(PageMeta):
    items: list[FindingSchema]
    ordering: str
    facets: dict[str, dict[str, int]]


class IssueSchema(Schema):
    issue_key: str
    kind: str
    severity: str
    severity_rank: int
    title: str
    category: str
    categories: list[str]
    cwe: list[str]
    file_path: str
    files: list[str]
    line_start: int | None
    line_end: int | None
    tools: list[str]
    rule_ids: list[str]
    findings: int
    samples: list[int]


class IssuePageSchema(PageMeta):
    items: list[IssueSchema]
    kind: str


# --------------------------------------------------------------------------
# Studies / targets
# --------------------------------------------------------------------------


class StudyCreateSchema(InputSchema):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=20000)
    research_questions: list[dict[str, Any]] = Field(default_factory=list)
    target_spec: dict[str, Any] = Field(default_factory=dict)
    profile_id: UUID | None = None
    exclusion_rules: dict[str, Any] = Field(default_factory=dict)
    analysis_design: dict[str, Any] = Field(default_factory=dict)


class StudyUpdateSchema(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=20000)
    research_questions: list[dict[str, Any]] | None = None
    target_spec: dict[str, Any] | None = None
    profile_id: UUID | None = None
    exclusion_rules: dict[str, Any] | None = None
    analysis_design: dict[str, Any] | None = None


class StudySchema(Schema):
    id: str
    name: str
    description: str
    status: str
    research_questions: list[dict[str, Any]]
    target_spec: dict[str, Any]
    profile: ProfileRefSchema | None
    exclusion_rules: dict[str, Any]
    analysis_design: dict[str, Any]
    plan_hash: str
    protocol_hash: str
    report_hash: str
    preflight_summary: dict[str, Any] | None
    run_counts: dict[str, int]
    complete_runs: int | None = None
    launch: dict[str, Any] | None
    report_state: dict[str, Any] | None
    actions: dict[str, bool]
    created_by_id: int
    locked_at: str | None
    launched_at: str | None
    completed_at: str | None
    report_generated_at: str | None
    created_at: str | None
    updated_at: str | None


class StudyDetailSchema(StudySchema):
    preflight: dict[str, Any]
    plan: dict[str, Any]
    protocol_verified: bool | None
    # Only on POST /studies/: what was taken from the target experiment's frozen analysis plan.
    defaults_applied: dict[str, Any] | None = None


class StudyPageSchema(PageMeta):
    items: list[StudySchema]


class TargetResolveSchema(InputSchema):
    target_spec: dict[str, Any]
    exclusion_rules: dict[str, Any] | None = None
    profile_id: UUID | None = None
    page: int = Field(1, ge=1)
    per_page: int = Field(25, ge=1, le=100)
