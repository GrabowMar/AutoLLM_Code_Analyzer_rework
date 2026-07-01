"""Models for the container-based analysis engine.

Architecture
------------
* :class:`AnalyzerTool` — a catalog ("shop") entry, seeded from
  ``data/tools/*.yaml``.  Describes how a tool is installed into and invoked
  inside the analyzer container, plus how its output is parsed.
* :class:`AnalyzerWorkspace` — a per-user, long-lived container into which
  tools are installed; backed by a :class:`runtime.ContainerInstance`.
* :class:`InstalledTool` — a tool the user has installed into their workspace.
* :class:`AnalysisRun` / :class:`ToolResult` / :class:`Finding` — an execution,
  its per-tool results, and the normalized findings.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ToolKind(models.TextChoices):
    CONTAINER = "container", _("Container CLI")
    AI = "ai", _("AI reviewer")


class ToolCategory(models.TextChoices):
    SECURITY = "security", _("Security")
    LINT = "lint", _("Lint / quality")
    PERFORMANCE = "performance", _("Performance")
    SECRETS = "secrets", _("Secrets")
    AI = "ai", _("AI review")


class TargetLanguage(models.TextChoices):
    PYTHON = "python", _("Python")
    JAVASCRIPT = "javascript", _("JavaScript / TypeScript")
    ANY = "any", _("Any")


class AnalyzerTool(models.Model):
    """A catalog tool that can be installed into an analyzer workspace."""

    slug = models.SlugField(_("slug"), max_length=100, unique=True)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    category = models.CharField(
        _("category"),
        max_length=20,
        choices=ToolCategory.choices,
        default=ToolCategory.LINT,
        db_index=True,
    )
    kind = models.CharField(
        _("kind"),
        max_length=20,
        choices=ToolKind.choices,
        default=ToolKind.CONTAINER,
    )
    target_language = models.CharField(
        _("target language"),
        max_length=20,
        choices=TargetLanguage.choices,
        default=TargetLanguage.ANY,
    )
    icon = models.CharField(_("icon"), max_length=100, blank=True, default="")
    version = models.CharField(_("version"), max_length=50, blank=True, default="")

    # Container execution (kind == container)
    install_cmd = models.TextField(_("install command"), blank=True, default="")
    verify_cmd = models.TextField(_("verify command"), blank=True, default="")
    run_cmd = models.TextField(
        _("run command"),
        blank=True,
        default="",
        help_text=_("Template; '{target}' is replaced with the code path."),
    )
    parser_key = models.CharField(_("parser key"), max_length=50, blank=True, default="")

    # Configuration surface rendered by the frontend
    config_schema = models.JSONField(_("config schema"), default=list, blank=True)
    default_config = models.JSONField(_("default config"), default=dict, blank=True)

    # Resource / timing hints
    run_timeout = models.PositiveIntegerField(_("run timeout (s)"), default=120)
    install_timeout = models.PositiveIntegerField(_("install timeout (s)"), default=300)

    # A sample snippet used by the "test" action
    sample_code = models.TextField(_("sample code"), blank=True, default="")

    is_system = models.BooleanField(_("system tool"), default=True)
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


class AnalyzerWorkspace(models.Model):
    """A per-user, long-lived analyzer container."""

    class Status(models.TextChoices):
        ABSENT = "absent", _("Absent")
        PROVISIONING = "provisioning", _("Provisioning")
        READY = "ready", _("Ready")
        STOPPED = "stopped", _("Stopped")
        ERROR = "error", _("Error")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analyzer_workspace",
    )
    container = models.ForeignKey(
        "runtime.ContainerInstance",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analyzer_workspaces",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.ABSENT,
        db_index=True,
    )
    image = models.CharField(_("image"), max_length=300, blank=True, default="")
    error_message = models.TextField(_("error message"), blank=True, default="")
    last_used_at = models.DateTimeField(_("last used at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Analyzer Workspace")
        verbose_name_plural = _("Analyzer Workspaces")

    def __str__(self) -> str:
        return f"workspace<{self.user_id}>"

    @property
    def container_name(self) -> str:
        return self.container.name if self.container else ""


class InstalledTool(models.Model):
    """A tool installed into a user's workspace."""

    class Status(models.TextChoices):
        INSTALLING = "installing", _("Installing")
        INSTALLED = "installed", _("Installed")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        AnalyzerWorkspace,
        on_delete=models.CASCADE,
        related_name="installed_tools",
    )
    tool = models.ForeignKey(
        AnalyzerTool,
        on_delete=models.CASCADE,
        related_name="installations",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.INSTALLING,
    )
    installed_version = models.CharField(
        _("installed version"),
        max_length=100,
        blank=True,
        default="",
    )
    config = models.JSONField(_("config"), default=dict, blank=True)
    install_log = models.TextField(_("install log"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Installed Tool")
        verbose_name_plural = _("Installed Tools")
        unique_together = ("workspace", "tool")
        ordering = ["tool__display_order", "tool__name"]

    def __str__(self) -> str:
        return f"{self.tool.slug}@{self.workspace_id}"


class AnalysisRun(models.Model):
    """A single analysis execution against some target code."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        PARTIAL = "partial", _("Partial")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )
    workspace = models.ForeignKey(
        AnalyzerWorkspace,
        on_delete=models.SET_NULL,
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
    source_code = models.JSONField(_("source code"), null=True, blank=True)
    tool_slugs = models.JSONField(_("tool slugs"), default=list, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    summary = models.JSONField(_("summary"), default=dict, blank=True)
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
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"run<{self.id}>"

    def get_code_for_analysis(self) -> dict[str, str]:
        """Resolve the code map to analyze (inline source or generation job)."""
        if self.source_code:
            return self.source_code
        if self.generation_job_id:
            data = getattr(self.generation_job, "result_data", None) or {}
            if isinstance(data, dict):
                # Copilot jobs carry a real per-file map ({relpath: content}) under
                # "files"; prefer it so files keep their true paths/extensions.
                files = data.get("files")
                if isinstance(files, dict):
                    file_map = {k: v for k, v in files.items() if isinstance(v, str) and v.strip()}
                    if file_map:
                        return file_map
                # Scaffolding/custom jobs: top-level string blobs
                # (backend_code/frontend_code/content).
                return {k: v for k, v in data.items() if isinstance(v, str)}
        return {}


class ToolResult(models.Model):
    """Result of running one tool within an :class:`AnalysisRun`."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="results",
    )
    tool_slug = models.CharField(_("tool slug"), max_length=100, db_index=True)
    category = models.CharField(_("category"), max_length=20, blank=True, default="")
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    raw_output = models.JSONField(_("raw output"), default=dict, blank=True)
    summary = models.JSONField(_("summary"), default=dict, blank=True)
    error_message = models.TextField(_("error message"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Tool Result")
        verbose_name_plural = _("Tool Results")
        unique_together = ("run", "tool_slug")
        ordering = ["tool_slug"]

    def __str__(self) -> str:
        return f"{self.tool_slug} ({self.status})"


class Finding(models.Model):
    """A normalized issue produced by a tool."""

    class Severity(models.TextChoices):
        CRITICAL = "critical", _("Critical")
        HIGH = "high", _("High")
        MEDIUM = "medium", _("Medium")
        LOW = "low", _("Low")
        INFO = "info", _("Info")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result = models.ForeignKey(
        ToolResult,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    severity = models.CharField(
        _("severity"),
        max_length=20,
        choices=Severity.choices,
        default=Severity.INFO,
        db_index=True,
    )
    category = models.CharField(_("category"), max_length=50, blank=True, default="")
    confidence = models.CharField(_("confidence"), max_length=20, blank=True, default="medium")
    title = models.CharField(_("title"), max_length=500)
    description = models.TextField(_("description"), blank=True, default="")
    suggestion = models.TextField(_("suggestion"), blank=True, default="")
    file_path = models.CharField(_("file path"), max_length=500, blank=True, default="")
    line_number = models.IntegerField(_("line number"), null=True, blank=True)
    column_number = models.IntegerField(_("column number"), null=True, blank=True)
    code_snippet = models.TextField(_("code snippet"), blank=True, default="")
    rule_id = models.CharField(_("rule id"), max_length=100, blank=True, default="")
    tool_specific_data = models.JSONField(_("tool specific data"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Finding")
        verbose_name_plural = _("Findings")
        ordering = ["severity", "file_path", "line_number"]
        indexes = [
            models.Index(fields=["severity"]),
            models.Index(fields=["rule_id"]),
        ]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.title}"
