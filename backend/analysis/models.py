"""Models for the analysis engine (v2).

Architecture
------------
* :class:`AnalyzerTool` — catalog entry, seeded from ``data/tools/*.yaml``.
  Tools are baked into one pinned analyzer image; nothing is installed at
  run time. ``version`` comes from the built image's manifest.
* :class:`AnalysisProfile` — a named, versioned analysis plan preset: tool
  set + per-tool config, AI reviewer (engine, harness, model, samples),
  scope and outcome rules. Content is immutable per version.
* :class:`AnalysisStudy` — the research process: targets (experiment,
  batch, jobs), a pinned profile, exclusion rules, a frozen protocol
  (pre-run report) and the post-run report.
* :class:`AnalysisRun` — one target (a generation job or inline source)
  analysed under one frozen plan. Execution is a durable DB state machine
  (lease + heartbeat), so a dead worker is detected and the run resumed.
* :class:`ToolResult` — one tool execution in a run, with an explicit
  outcome: a failed, timed-out or unavailable tool is never "0 findings".
* :class:`Finding` — a normalized finding (severity, category, CWE,
  origin of the code it points at, fingerprint, dedup issue key).
* :class:`ReviewSample` — one replicate of the AI review (k per run), with
  usage, cost, grounding and provenance.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class ToolKind(models.TextChoices):
    STATIC = "static", _("Static analyzer")
    METRICS = "metrics", _("Metrics")
    AI = "ai", _("AI reviewer")


class ToolCategory(models.TextChoices):
    SECURITY = "security", _("Security")
    SECRETS = "secrets", _("Secrets")
    LINT = "lint", _("Lint / bugs")
    TYPES = "types", _("Types")
    DEAD_CODE = "dead_code", _("Dead code")
    COMPLEXITY = "complexity", _("Complexity")
    DUPLICATION = "duplication", _("Duplication")
    STYLE = "style", _("Style / typos")
    CONTAINER = "container", _("Container config")
    AI = "ai", _("AI review")


class FindingCategory(models.TextChoices):
    """Normalized finding categories (mapped to ISO/IEC 25010 in the docs)."""

    SECURITY = "security", _("Security")
    SECRETS = "secrets", _("Secrets")
    BUG = "bug", _("Bug / correctness")
    TYPE = "type", _("Type error")
    MAINTAINABILITY = "maintainability", _("Maintainability")
    DEAD_CODE = "dead_code", _("Dead code")
    DUPLICATION = "duplication", _("Duplication")
    COMPLEXITY = "complexity", _("Complexity")
    STYLE = "style", _("Style / typo")
    CONTAINER = "container", _("Container config")
    DEPENDENCY = "dependency", _("Dependency")
    OTHER = "other", _("Other")


class FileOrigin(models.TextChoices):
    """Who wrote the code a finding points at (line-level for modified files)."""

    AUTHORED = "authored", _("Authored (non-agent sample)")
    ADDED = "added", _("Added by agent")
    MODIFIED = "modified", _("Modified by agent")
    SCAFFOLD = "scaffold", _("Stack scaffold")
    PLATFORM = "platform", _("Platform-owned")
    UNKNOWN = "unknown", _("Unknown")


class AnalyzerTool(models.Model):
    """A catalog tool (read model of ``data/tools/<slug>.yaml``)."""

    slug = models.SlugField(_("slug"), max_length=100, unique=True)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    category = models.CharField(_("category"), max_length=20, choices=ToolCategory, db_index=True)
    kind = models.CharField(_("kind"), max_length=20, choices=ToolKind, default=ToolKind.STATIC)
    languages = models.JSONField(_("languages"), default=list, blank=True)
    output_kind = models.CharField(_("output kind"), max_length=20, default="findings")
    icon = models.CharField(_("icon"), max_length=100, blank=True, default="")
    docs_url = models.URLField(_("documentation URL"), blank=True, default="")
    config_schema = models.JSONField(_("config schema"), default=list, blank=True)
    default_config = models.JSONField(_("default config"), default=dict, blank=True)
    spec = models.JSONField(_("spec"), default=dict, blank=True, help_text=_("Full normalized YAML spec"))
    spec_hash = models.CharField(_("spec hash"), max_length=64, blank=True, default="")
    version = models.CharField(
        _("version"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("Tool version from the analyzer image manifest"),
    )
    is_enabled = models.BooleanField(_("enabled"), default=True, db_index=True)
    display_order = models.IntegerField(_("display order"), default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Analyzer Tool")
        verbose_name_plural = _("Analyzer Tools")
        ordering = ["display_order", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    @classmethod
    def ai_slugs(cls) -> set[str]:
        """Slugs of AI-kind tools, whose findings are opinions, not measurements."""
        return set(cls.objects.filter(kind=ToolKind.AI).values_list("slug", flat=True))


class AnalysisProfile(models.Model):
    """A named, versioned analysis plan preset (``engine/plan.py``).

    ``config`` is the normalized profile config (tools, reviewer, scope,
    outcomes). A content edit of a profile creates version+1; runs and
    studies freeze the resolved plan, so the FK is provenance only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(_("slug"), max_length=100)
    version = models.PositiveIntegerField(_("version"), default=1)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    config = models.JSONField(_("config"), default=dict, blank=True)
    content_hash = models.CharField(_("content hash"), max_length=64, blank=True, default="")
    is_system = models.BooleanField(_("system profile"), default=False)
    is_archived = models.BooleanField(_("archived"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Analysis Profile")
        verbose_name_plural = _("Analysis Profiles")
        ordering = ["slug", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["slug", "version"], name="analysis_profile_slug_version_uniq"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="analysis_profile_version_gte_1"),
        ]

    def __str__(self) -> str:
        return f"{self.slug} v{self.version}"


class AnalysisStudy(models.Model):
    """A research study: which samples, analysed how, judged by which rules."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        LOCKED = "locked", _("Protocol locked")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    research_questions = models.JSONField(
        _("research questions"),
        default=list,
        blank=True,
        help_text=_("[{id, text, kind: confirmatory|exploratory}]"),
    )
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    target_spec = models.JSONField(_("target spec"), default=dict, blank=True)
    profile = models.ForeignKey(
        AnalysisProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="studies",
    )
    exclusion_rules = models.JSONField(_("exclusion rules"), default=dict, blank=True)
    analysis_design = models.JSONField(
        _("analysis design"),
        default=dict,
        blank=True,
        help_text=_("{group_by, block_by, alpha, correction, bootstrap_resamples, primary_outcome}"),
    )
    preflight = models.JSONField(_("preflight"), default=dict, blank=True)
    plan = models.JSONField(_("frozen plan"), default=dict, blank=True)
    plan_hash = models.CharField(_("plan hash"), max_length=64, blank=True, default="", db_index=True)
    protocol = models.JSONField(_("protocol"), default=dict, blank=True)
    protocol_hash = models.CharField(_("protocol hash"), max_length=64, blank=True, default="")
    locked_at = models.DateTimeField(_("locked at"), null=True, blank=True)
    launched_at = models.DateTimeField(_("launched at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    report = models.JSONField(_("report"), default=dict, blank=True)
    report_hash = models.CharField(_("report hash"), max_length=64, blank=True, default="")
    report_generated_at = models.DateTimeField(_("report generated at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analysis_studies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Analysis Study")
        verbose_name_plural = _("Analysis Studies")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name


class AnalysisRun(models.Model):
    """One target analysed under one frozen plan."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        PARTIAL = "partial", _("Partial")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    class Purpose(models.TextChoices):
        RESEARCH = "research", _("Research (study)")
        ADHOC = "adhoc", _("Ad-hoc")
        CANARY = "canary", _("Tool self-test")

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)
    FINISHED_STATUSES = (Status.COMPLETED, Status.PARTIAL)
    TERMINAL_STATUSES = (Status.COMPLETED, Status.PARTIAL, Status.FAILED, Status.CANCELLED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200, blank=True, default="")
    purpose = models.CharField(_("purpose"), max_length=20, choices=Purpose.choices, default=Purpose.ADHOC)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )
    study = models.ForeignKey(
        AnalysisStudy,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="runs",
    )
    generation_job = models.ForeignKey(
        "generation.GenerationJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_runs",
    )
    source_code = models.JSONField(_("inline source"), null=True, blank=True)
    profile = models.ForeignKey(
        AnalysisProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    plan = models.JSONField(_("frozen plan"), default=dict, blank=True)
    plan_hash = models.CharField(_("plan hash"), max_length=64, blank=True, default="", db_index=True)
    target_meta = models.JSONField(
        _("target metadata"),
        default=dict,
        blank=True,
        help_text=_("Snapshot of the job's identity and quality flags at submission"),
    )
    sample_manifest = models.JSONField(_("sample manifest"), default=dict, blank=True)
    sample_hash = models.CharField(_("sample hash"), max_length=64, blank=True, default="")
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    is_complete = models.BooleanField(
        _("complete"),
        default=False,
        db_index=True,
        help_text=_("Every required tool succeeded (or was not applicable)"),
    )
    cancel_requested = models.BooleanField(_("cancel requested"), default=False)
    attempt = models.PositiveIntegerField(_("attempt"), default=0)
    lease_owner = models.CharField(_("lease owner"), max_length=200, blank=True, default="")
    heartbeat_at = models.DateTimeField(_("heartbeat at"), null=True, blank=True)
    phase = models.CharField(_("phase"), max_length=50, blank=True, default="")
    summary = models.JSONField(_("summary"), default=dict, blank=True)
    measurement = models.JSONField(_("measurement"), default=dict, blank=True)
    error_message = models.TextField(_("error message"), blank=True, default="")
    duration_seconds = models.FloatField(_("duration (s)"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Analysis Run")
        verbose_name_plural = _("Analysis Runs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["generation_job", "plan_hash"]),
            models.Index(fields=["status", "heartbeat_at"]),
        ]

    def __str__(self) -> str:
        return f"run<{self.id}>"

    @property
    def tool_slugs(self) -> list[str]:
        """Tools in the frozen plan (incl. the AI reviewer), in plan order."""
        slugs = [t["slug"] for t in (self.plan or {}).get("tools", []) if isinstance(t, dict) and t.get("slug")]
        reviewer = (self.plan or {}).get("reviewer") or {}
        if reviewer.get("enabled"):
            slugs.append(reviewer.get("tool_slug") or "ai_review")
        return slugs

    @classmethod
    def latest_ids_per_job(cls, job_ids=None, *, plan_hash: str | None = None, complete_only: bool = False) -> list:
        """The canonical run per generation job: its most recent finished run.

        A job analysed N times has N sets of findings for the same code;
        aggregations must count one run per job or they double-count.
        Pass *plan_hash* to restrict to runs made with one frozen plan (only
        those are comparable), and *complete_only* to drop runs where a
        required tool failed (their totals are missing data, not zeros).
        """
        qs = (
            cls.objects.filter(
                generation_job__isnull=False,
                status__in=list(cls.FINISHED_STATUSES),
            )
            .exclude(purpose=cls.Purpose.CANARY)
            # v1 runs (migrated, no frozen plan) were fail-open: never canonical.
            .exclude(plan_hash="")
        )
        if job_ids is not None:
            qs = qs.filter(generation_job_id__in=list(job_ids))
        if plan_hash:
            qs = qs.filter(plan_hash=plan_hash)
        if complete_only:
            qs = qs.filter(is_complete=True)
        return list(
            qs.order_by("generation_job_id", "-created_at").distinct("generation_job_id").values_list("id", flat=True),
        )


class ToolResult(models.Model):
    """One tool execution within an :class:`AnalysisRun`."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        TIMED_OUT = "timed_out", _("Timed out")
        NOT_APPLICABLE = "not_applicable", _("Not applicable")
        SKIPPED = "skipped", _("Skipped")
        CANCELLED = "cancelled", _("Cancelled")

    class FailureReason(models.TextChoices):
        NONE = "", _("None")
        TOOL_ERROR = "tool_error", _("Tool error")
        TIMEOUT = "timeout", _("Timeout")
        OOM = "oom", _("Out of memory")
        PARSE_ERROR = "parse_error", _("Unparseable output")
        INFRA_ERROR = "infra_error", _("Infrastructure error")
        UNAVAILABLE = "unavailable", _("Tool unavailable")
        INVALID_CONFIG = "invalid_config", _("Invalid config")
        INVALID_OUTPUT = "invalid_output", _("Invalid AI output")
        BUDGET = "budget", _("Budget exhausted")
        CANCELLED = "cancelled", _("Cancelled")

    OK_STATUSES = (Status.SUCCEEDED, Status.NOT_APPLICABLE)
    TERMINAL_STATUSES = (
        Status.SUCCEEDED,
        Status.FAILED,
        Status.TIMED_OUT,
        Status.NOT_APPLICABLE,
        Status.SKIPPED,
        Status.CANCELLED,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="results")
    tool_slug = models.CharField(_("tool slug"), max_length=100, db_index=True)
    tool_kind = models.CharField(_("tool kind"), max_length=20, choices=ToolKind, default=ToolKind.STATIC)
    category = models.CharField(_("category"), max_length=20, blank=True, default="")
    position = models.PositiveIntegerField(_("position"), default=0)
    required = models.BooleanField(_("required"), default=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(
        _("failure reason"),
        max_length=30,
        choices=FailureReason.choices,
        blank=True,
        default="",
    )
    attempt = models.PositiveIntegerField(_("attempt"), default=0)
    tool_version = models.CharField(_("tool version"), max_length=100, blank=True, default="")
    image_ref = models.CharField(_("image"), max_length=300, blank=True, default="")
    config = models.JSONField(_("effective config"), default=dict, blank=True)
    config_hash = models.CharField(_("config hash"), max_length=64, blank=True, default="")
    argv = models.JSONField(_("argv"), default=list, blank=True)
    exit_code = models.IntegerField(_("exit code"), null=True, blank=True)
    duration_seconds = models.FloatField(_("duration (s)"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    coverage = models.JSONField(_("coverage"), default=dict, blank=True)
    metrics = models.JSONField(_("metrics"), default=dict, blank=True)
    summary = models.JSONField(_("summary"), default=dict, blank=True)
    diagnostics = models.JSONField(_("diagnostics"), default=dict, blank=True)
    artifacts = models.JSONField(_("artifacts"), default=dict, blank=True)
    error_message = models.TextField(_("error message"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Tool Result")
        verbose_name_plural = _("Tool Results")
        ordering = ["position", "tool_slug"]
        constraints = [
            models.UniqueConstraint(fields=["run", "tool_slug"], name="analysis_toolresult_run_tool_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.tool_slug} ({self.status})"

    @property
    def is_ok(self) -> bool:
        return self.status in self.OK_STATUSES


class ReviewSample(models.Model):
    """One replicate of the AI review of a run (k per run)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        INVALID_OUTPUT = "invalid_output", _("Invalid output")
        TRUNCATED = "truncated", _("Truncated")
        BUDGET_EXHAUSTED = "budget_exhausted", _("Budget exhausted")
        TIMED_OUT = "timed_out", _("Timed out")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result = models.ForeignKey(ToolResult, on_delete=models.CASCADE, related_name="review_samples")
    index = models.PositiveIntegerField(_("sample index"), default=0)
    engine = models.CharField(_("engine"), max_length=30, default="single_shot")
    harness = models.CharField(_("harness"), max_length=50, blank=True, default="")
    harness_version = models.CharField(_("harness version"), max_length=50, blank=True, default="")
    model_slug = models.CharField(_("model"), max_length=200, blank=True, default="")
    model_canonical_slug = models.CharField(_("model canonical slug"), max_length=200, blank=True, default="")
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(_("failure reason"), max_length=50, blank=True, default="")
    error_message = models.TextField(_("error message"), blank=True, default="")
    prompt_version = models.CharField(_("prompt version"), max_length=50, blank=True, default="")
    prompt_sha256 = models.CharField(_("prompt sha256"), max_length=64, blank=True, default="")
    schema_sha256 = models.CharField(_("schema sha256"), max_length=64, blank=True, default="")
    file_order_seed = models.BigIntegerField(_("file order seed"), null=True, blank=True)
    files_reviewed = models.JSONField(_("files reviewed"), default=list, blank=True)
    coverage = models.JSONField(_("coverage"), default=dict, blank=True)
    usage = models.JSONField(_("usage"), default=dict, blank=True)
    providers = models.JSONField(_("providers"), default=dict, blank=True)
    finish_reasons = models.JSONField(_("finish reasons"), default=dict, blank=True)
    grounding = models.JSONField(_("grounding"), default=dict, blank=True)
    finding_count = models.PositiveIntegerField(_("finding count"), default=0)
    repaired = models.BooleanField(_("repaired"), default=False)
    latency_seconds = models.FloatField(_("latency (s)"), null=True, blank=True)
    artifacts = models.JSONField(_("artifacts"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Review Sample")
        verbose_name_plural = _("Review Samples")
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(fields=["result", "index"], name="analysis_reviewsample_result_index_uniq"),
        ]

    def __str__(self) -> str:
        return f"review#{self.index} ({self.status})"


class Finding(models.Model):
    """A normalized finding produced by a tool (or one AI review sample)."""

    class Severity(models.TextChoices):
        CRITICAL = "critical", _("Critical")
        HIGH = "high", _("High")
        MEDIUM = "medium", _("Medium")
        LOW = "low", _("Low")
        INFO = "info", _("Info")

    class Grounding(models.TextChoices):
        NONE = "", _("Not applicable")
        EXACT = "exact", _("Exact")
        RELOCATED = "relocated", _("Relocated")
        UNGROUNDED = "ungrounded", _("Ungrounded")
        INVALID_FILE = "invalid_file", _("Invalid file")
        OUT_OF_RANGE = "out_of_range", _("Out of range")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result = models.ForeignKey(ToolResult, on_delete=models.CASCADE, related_name="findings")
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="findings")
    tool_slug = models.CharField(_("tool slug"), max_length=100, db_index=True)
    severity = models.CharField(
        _("severity"),
        max_length=20,
        choices=Severity.choices,
        default=Severity.INFO,
        db_index=True,
    )
    severity_rank = models.PositiveSmallIntegerField(_("severity rank"), default=4)
    native_severity = models.CharField(_("native severity"), max_length=50, blank=True, default="")
    category = models.CharField(_("category"), max_length=30, choices=FindingCategory, default="other")
    cwe = models.JSONField(_("CWE ids"), default=list, blank=True)
    confidence = models.CharField(_("confidence"), max_length=20, blank=True, default="medium")
    title = models.CharField(_("title"), max_length=500)
    description = models.TextField(_("description"), blank=True, default="")
    suggestion = models.TextField(_("suggestion"), blank=True, default="")
    rule_id = models.CharField(_("rule id"), max_length=200, blank=True, default="")
    file_path = models.CharField(_("file path"), max_length=500, blank=True, default="")
    line_number = models.IntegerField(_("line number"), null=True, blank=True)
    end_line = models.IntegerField(_("end line"), null=True, blank=True)
    column_number = models.IntegerField(_("column number"), null=True, blank=True)
    code_snippet = models.TextField(_("code snippet"), blank=True, default="")
    file_origin = models.CharField(
        _("file origin"),
        max_length=20,
        choices=FileOrigin,
        default=FileOrigin.UNKNOWN,
    )
    in_measured_scope = models.BooleanField(_("in measured scope"), default=True)
    counted = models.BooleanField(
        _("counted"),
        default=True,
        db_index=True,
        help_text=_("Counts toward outcomes (static: in scope and not style; AI: grounded consensus)"),
    )
    fingerprint = models.CharField(_("fingerprint"), max_length=64, blank=True, default="", db_index=True)
    issue_key = models.CharField(_("issue key"), max_length=64, blank=True, default="", db_index=True)
    is_ai = models.BooleanField(_("AI finding"), default=False, db_index=True)
    review_sample = models.ForeignKey(
        ReviewSample,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="findings",
    )
    grounding = models.CharField(
        _("grounding"),
        max_length=20,
        choices=Grounding.choices,
        blank=True,
        default="",
    )
    is_consensus = models.BooleanField(_("consensus"), default=False)
    tool_specific_data = models.JSONField(_("tool specific data"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Finding")
        verbose_name_plural = _("Findings")
        ordering = ["severity_rank", "file_path", "line_number", "id"]
        indexes = [
            models.Index(fields=["run", "severity_rank"]),
            models.Index(fields=["run", "counted"]),
            models.Index(fields=["rule_id"]),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.title}"

    def save(self, *args, **kwargs):
        self.severity_rank = SEVERITY_RANK.get(self.severity, 4)
        if self.result_id and not self.run_id:
            self.run_id = self.result.run_id
        if self.result_id and not self.tool_slug:
            self.tool_slug = self.result.tool_slug
        super().save(*args, **kwargs)
