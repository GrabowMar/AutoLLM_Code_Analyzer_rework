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
    analysis_mode: str = "static"
    technologies: list[str] = []
    icon: str
    version: str
    docs_url: str
    output_kind: str = "findings"
    config_schema: list[dict[str, Any]]
    default_config: dict[str, Any]
    run_timeout: int
    is_enabled: bool
    # Per-user install state (annotated by the view):
    installed: bool = False
    install_status: str = ""
    installed_version: str = ""
    update_available: bool = False


class CatalogEntrySchema(Schema):
    slug: str
    label: str
    description: str = ""
    group: str = ""


class CatalogMetaSchema(Schema):
    """Display vocabulary for the shop (from ``data/tools/catalog.yaml``)."""

    categories: list[CatalogEntrySchema]
    modes: list[CatalogEntrySchema]
    technology_groups: list[CatalogEntrySchema]
    technologies: list[CatalogEntrySchema]


class AppCoverageSchema(Schema):
    """How the user's generated apps map onto the catalog."""

    apps_total: int
    apps_considered: int
    # {technology slug: number of apps containing it}
    technologies: dict[str, int]
    # {tool slug: number of apps containing at least one technology it scans}
    tools: dict[str, int]


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
    catalog_version: str = ""
    latest_version: str = ""
    update_available: bool = False
    config: dict[str, Any]
    install_log: str
    updated_at: str | None = None


class InstallToolSchema(Schema):
    tool_slug: str


class BulkInstallSchema(Schema):
    tool_slugs: list[str]


class UpdateAllSchema(Schema):
    # False reinstalls every installed tool (repair after a container rebuild).
    only_outdated: bool = True


class UninstallResultSchema(Schema):
    status: str
    tool_slug: str
    message: str = ""


class ToolConfigSchema(Schema):
    config: dict[str, Any]


class TestToolSchema(Schema):
    config: dict[str, Any] = {}


class TestResultSchema(Schema):
    available: bool
    message: str
    findings: list[dict[str, Any]]
    metrics: dict[str, Any] = {}
    raw_output: dict[str, Any]


# --- Runs / findings --------------------------------------------------------


class RunCreateSchema(Schema):
    name: str = ""
    tool_slugs: list[str]
    generation_job_id: str | None = None
    source_code: dict[str, str] | None = None
    auto_start: bool = True


class BatchCreateSchema(Schema):
    name: str = ""
    tool_slugs: list[str]
    generation_job_ids: list[str]


class BatchCreatedSchema(Schema):
    batch_id: str
    run_ids: list[str]


class RunPreviewRequestSchema(Schema):
    tool_slugs: list[str] = []
    generation_job_ids: list[str] = []
    source_code: dict[str, str] | None = None


class PreviewTargetSchema(Schema):
    job_id: str | None
    label: str
    files: int
    lines: int
    app_running: bool


class PreviewToolSchema(Schema):
    slug: str
    name: str
    known: bool
    installed: bool
    install_status: str
    mode: str
    kind: str
    # Targets the tool would run on / skip (nothing it reads; app not running).
    runs: int
    not_applicable: int
    app_not_running: int


class RunPreviewSchema(Schema):
    workspace_status: str
    targets: list[PreviewTargetSchema]
    tools: list[PreviewToolSchema]
    tool_runs: int
    warnings: list[str]


class RerunSchema(Schema):
    name: str = ""
    # Empty: all of the run's tools (or, with only_unmeasured, those that measured nothing).
    tool_slugs: list[str] = []
    only_unmeasured: bool = False


class BulkActionSchema(Schema):
    action: str
    ids: list[str]


class BulkResultSchema(Schema):
    affected: int
    skipped: int


class RunStatsSchema(Schema):
    total: int
    active: int
    by_status: dict[str, int]


class ToolResultSchema(Schema):
    id: str
    tool_slug: str
    category: str
    status: str
    summary: dict[str, Any]
    metrics: dict[str, Any] = {}
    error_message: str
    started_at: str | None = None
    duration_seconds: float | None = None


class ToolResultDetailSchema(ToolResultSchema):
    raw_output: dict[str, Any]
    tool_name: str
    tool_docs_url: str
    findings_count: int


class FindingSchema(Schema):
    id: str
    # Normalized level (a heuristic for tools that rate nothing); display tool_severity.
    severity: str
    # The level exactly as the tool printed it; "" when the tool rates nothing.
    tool_severity: str = ""
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
    tool_specific_data: dict[str, Any] = {}
    # A code finding located in test code (reports.services.loc.is_test_path):
    # the study counts it apart from the app's own code issues.
    in_tests: bool = False


class RunTargetSchema(Schema):
    kind: str  # "job" | "code"
    label: str
    job_id: str | None = None
    job_status: str = ""
    mode: str = ""
    model_name: str = ""
    model_id: str = ""
    app_name: str = ""
    app_slug: str = ""
    stack_slug: str = ""
    experiment_id: str | None = None
    file_count: int | None = None


class RunProgressSchema(Schema):
    total: int
    done: int
    running: list[str]
    findings: int


class SourceFileSchema(Schema):
    path: str
    lines: int


class AnalysisRunListSchema(Schema):
    id: str
    name: str
    status: str
    tool_slugs: list[str]
    summary: dict[str, Any]
    error_message: str = ""
    generation_job_id: str | None = None
    batch_id: str | None = None
    target: RunTargetSchema
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    # While running: waiting_for_workspace | preparing | running_tools.
    phase: str = ""
    # Active runs only.
    progress: RunProgressSchema | None = None


class AnalysisRunSchema(AnalysisRunListSchema):
    results: list[ToolResultSchema]
    source_files: list[SourceFileSchema] = []


class PaginatedRunsSchema(Schema):
    items: list[AnalysisRunListSchema]
    total: int
    page: int
    per_page: int
    pages: int


class GroupedFindingSchema(FindingSchema):
    # With group=true: the other findings (often of other tools) that report the
    # same issue (statistics.services.dedup), and that issue's class.
    duplicates: list[FindingSchema] = []
    issue_class: str = ""


class PaginatedFindingsSchema(Schema):
    items: list[GroupedFindingSchema]
    # Issues when grouped, else findings.
    total: int
    page: int
    per_page: int
    pages: int
    # With group=true: the findings behind the `total` issues.
    findings_total: int | None = None


class FacetValueSchema(Schema):
    value: str
    count: int
    label: str = ""


class LevelFacetSchema(Schema):
    value: str
    count: int
    # 0 = most severe: the rank of the label word itself (runs.level_rank,
    # the UI's LEVEL_WORDS table; CVSS scores by band), never the normalized level.
    rank: int


class FindingFacetsSchema(Schema):
    total: int
    tools: list[FacetValueSchema]
    levels: list[LevelFacetSchema]
    unrated: int
    # {"code": n, "dependencies": n, "runtime": n} over all of the run's findings:
    # code findings, known-vulnerability dependency advisories, dynamic tools' findings.
    # None for a scope no tool of the run measured (and with no findings): unmeasured is not 0.
    scopes: dict[str, int | None] = {}
    # {"source": n, "tests": n, "excluded": n}: the code findings split by test files (tests/, *.test.*,
    # conftest.py, smoke scripts; counted apart, as in the study) and those in NON_SOURCE_DIRS files
    # (read by a run finalized before the exclusion rule: neither); None when code is unmeasured.
    code_parts: dict[str, int | None] = {}
    categories: list[FacetValueSchema]
    files: list[FacetValueSchema]
    files_total: int
    rules: list[FacetValueSchema]
    rules_total: int


# --- Run context (GET /runs/{id}/context/) -------------------------------------


class RunSetupSchema(Schema):
    """The generation setup behind the analyzed app: its design cell and method."""

    id: str
    mode: str
    status: str = ""
    # LLMModel primary key (model pages), its id string, display name and URL slug.
    model_pk: int | None = None
    model_id: str = ""
    model_name: str = ""
    model_slug: str = ""
    app_id: int | None = None
    app_slug: str = ""
    app_name: str = ""
    stack_slug: str = ""
    batch_id: str | None = None
    experiment_id: str | None = None
    experiment_name: str = ""
    condition_id: int | None = None
    condition_label: str = ""
    # 0-based trial within the (condition, app) cell, and the repeats planned at launch.
    repeat_index: int | None = None
    planned_repeats: int | None = None
    harness: str | None = None
    bundle_key: str = ""
    config_fingerprint: str | None = None


class ExcludedPathsSchema(Schema):
    # Stored files inside dependency / cache / scratch directories (analysis.services.base.NON_SOURCE_DIRS).
    count: int
    sample: list[str] = []
    # {excluded directory name (the first NON_SOURCE_DIRS part of the path): files}, e.g. {".tmp": 81}.
    dirs: dict[str, int] = {}
    # Whether this run's tools left them out: True when the run recorded them
    # (summary.excluded_paths); False for a run finalized before the rule (its tools read
    # them, and ``findings_in_excluded`` of its findings lie in them); None when unknown
    # (no tool ran, or the run did not finish).
    left_out: bool | None = None
    findings_in_excluded: int = 0


class SourceLocSchema(Schema):
    """Source size by the study's definition (reports.services.loc): non-empty, non-comment lines of source files."""

    total: int
    files: int
    # {"backend", "frontend", "tests"}; tests is None when the code has no paths (unknown, not zero).
    by_layer: dict[str, int | None]
    test_files: int | None = None
    # Stored files that are not source (manifests, lockfiles, docs, configs); None without paths.
    other_files: int | None = None
    excluded_paths: ExcludedPathsSchema


class CodeSplitRowSchema(Schema):
    tool: str
    findings_in_tests: int
    findings_in_source: int
    # Findings in NON_SOURCE_DIRS files (a run finalized before the exclusion rule read them): neither of the above.
    findings_in_excluded: int = 0


class RulePrevalenceSchema(Schema):
    tool: str
    rule_id: str
    rule_name: str = ""
    title: str = ""
    # The study's cross-tool class of the rule's findings in this run; "" when they span several
    # classes (nuclei's http-missing-security-headers: one class per header), so no single
    # finding is labelled with another finding's class.
    issue_class: str = ""
    # code | dependencies | runtime (runner.tool_scope; advisories by category).
    scope: str = "code"
    count_in_run: int
    # Analyzed apps (generation jobs) of the corpus whose latest result of this tool measured
    # the code (completed or not applicable), and those of them with at least one finding of the rule.
    apps_with_rule: int
    apps_measured: int
    # The same, restricted to apps of the analyzed app's model; None without a model.
    model_apps_with_rule: int | None = None
    model_apps_measured: int | None = None


class RunContextSchema(Schema):
    run_id: str
    # The corpus the prevalence is counted over: experiment | mine; None when there is none.
    scope: str | None = None
    scopes_available: list[str] = []
    scope_label: str = ""
    # Analyzed apps (jobs with a finished tool result) in the corpus, and those of this model.
    n: int = 0
    model_n: int | None = None
    # Whether the analyzed app itself is one of the corpus apps (a superseded attempt is not).
    self_in_corpus: bool = False
    # The experiment corpus while the experiment is still running: counts are interim.
    interim: bool = False
    job: RunSetupSchema | None = None
    source_loc: SourceLocSchema | None = None
    test_split: list[CodeSplitRowSchema] = []
    rule_prevalence: list[RulePrevalenceSchema] = []
    notes: list[str] = []
