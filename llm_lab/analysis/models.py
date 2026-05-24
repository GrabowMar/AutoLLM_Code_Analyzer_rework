import uuid

from django.conf import settings as django_settings
from django.db import models


class AnalysisTask(models.Model):
    """Top-level analysis task that orchestrates multiple analyzer runs."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        PARTIAL = "partial", "Partial"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=300, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Source: either a generation job or inline code
    generation_job = models.ForeignKey(
        "generation.GenerationJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_tasks",
    )
    source_code = models.JSONField(
        default=dict,
        blank=True,
        help_text="Inline code for analysis: {'backend': '...', 'frontend': '...'}",
    )

    # Configuration
    configuration = models.JSONField(
        default=dict,
        blank=True,
        help_text="Which analyzers to run and their settings",
    )

    # Optional link to the profile this task was created from
    profile = models.ForeignKey(
        "AnalysisProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )

    # Threshold evaluation result
    class ThresholdStatus(models.TextChoices):
        NOT_CONFIGURED = "not_configured", "Not configured"
        PASSED = "passed", "Passed"
        EXCEEDED = "exceeded", "Exceeded"

    threshold_status = models.CharField(
        max_length=20,
        choices=ThresholdStatus.choices,
        default=ThresholdStatus.NOT_CONFIGURED,
    )

    # Results summary (aggregated after all analyzers finish)
    results_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Aggregated findings count by severity, overall score, etc.",
    )

    # Ownership and timestamps
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analysis_tasks",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        source = self.name or (f"Job {self.generation_job_id}" if self.generation_job_id else "inline code")
        return f"Analysis: {source} ({self.status})"

    def get_code_for_analysis(self) -> dict[str, str]:
        """Return code to analyze from the linked job or inline source."""
        if self.source_code:
            return self.source_code

        if self.generation_job and self.generation_job.result_data:
            result = self.generation_job.result_data
            return {
                "backend": result.get("backend_code", ""),
                "frontend": result.get("frontend_code", ""),
            }

        return {}


class AnalysisResult(models.Model):
    """Result from a single analyzer run within an analysis task."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class AnalyzerType(models.TextChoices):
        STATIC = "static", "Static Analysis"
        DYNAMIC = "dynamic", "Dynamic Analysis"
        PERFORMANCE = "performance", "Performance Analysis"
        AI = "ai", "AI Review"

    task = models.ForeignKey(
        AnalysisTask,
        on_delete=models.CASCADE,
        related_name="results",
    )
    analyzer_type = models.CharField(max_length=20, choices=AnalyzerType.choices)
    analyzer_name = models.CharField(
        max_length=100,
        help_text="Specific tool name, e.g. 'bandit', 'eslint', 'zap', 'llm_review'",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Raw tool output
    raw_output = models.JSONField(default=dict, blank=True)

    # Parsed summary metrics
    summary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Parsed summary: score, metrics, tool version, etc.",
    )

    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["analyzer_type", "analyzer_name"]
        unique_together = [("task", "analyzer_name")]
        indexes = [
            models.Index(fields=["task", "status"]),
            models.Index(fields=["task", "analyzer_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.analyzer_name} ({self.status}) → Task {self.task_id}"


class Finding(models.Model):
    """Individual finding/issue discovered by an analyzer."""

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        INFO = "info", "Info"

    class Category(models.TextChoices):
        SECURITY = "security", "Security"
        QUALITY = "quality", "Code Quality"
        PERFORMANCE = "performance", "Performance"
        STYLE = "style", "Style"
        BEST_PRACTICE = "best_practice", "Best Practice"
        ACCESSIBILITY = "accessibility", "Accessibility"
        SEO = "seo", "SEO"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    result = models.ForeignKey(
        AnalysisResult,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    severity = models.CharField(max_length=20, choices=Severity.choices)
    category = models.CharField(max_length=20, choices=Category.choices)
    confidence = models.CharField(
        max_length=20,
        choices=Confidence.choices,
        default=Confidence.MEDIUM,
    )

    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")
    suggestion = models.TextField(
        blank=True,
        default="",
        help_text="Recommended fix or improvement",
    )

    # Location
    file_path = models.CharField(max_length=500, blank=True, default="")
    line_number = models.PositiveIntegerField(null=True, blank=True)
    column_number = models.PositiveIntegerField(null=True, blank=True)
    code_snippet = models.TextField(blank=True, default="")

    # Tool-specific info
    rule_id = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Tool-specific rule identifier, e.g. 'B105', 'no-unused-vars'",
    )
    tool_specific_data = models.JSONField(default=dict, blank=True)

    # Suppression (false-positive management)
    suppressed = models.BooleanField(default=False)
    suppression_reason = models.TextField(blank=True, default="")
    suppressed_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suppressed_findings",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = [
            models.Case(
                models.When(severity="critical", then=0),
                models.When(severity="high", then=1),
                models.When(severity="medium", then=2),
                models.When(severity="low", then=3),
                models.When(severity="info", then=4),
                default=5,
                output_field=models.IntegerField(),
            ),
            "-created_at",
        ]
        indexes = [
            models.Index(fields=["severity"]),
            models.Index(fields=["category"]),
            models.Index(fields=["result", "severity"]),
            models.Index(fields=["suppressed"]),
            models.Index(fields=["result", "suppressed"]),
        ]

    def __str__(self) -> str:
        location = f" ({self.file_path}:{self.line_number})" if self.file_path else ""
        return f"[{self.severity.upper()}] {self.title}{location}"


class AnalysisProfile(models.Model):
    """Reusable named bundle of analyzers + settings, optionally set as default."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    analyzers = models.JSONField(
        default=list,
        blank=True,
        help_text="List of analyzer names to run, e.g. ['bandit', 'eslint']",
    )
    settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-analyzer config overrides: {analyzer_name: {key: value}}",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Whether this is the user's default profile",
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="analysis_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]
        unique_together = [("created_by", "name")]

    def __str__(self) -> str:
        suffix = " [default]" if self.is_default else ""
        return f"{self.name}{suffix}"
