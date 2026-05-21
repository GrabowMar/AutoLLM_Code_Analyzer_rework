"""Report model — stores generated reports as JSON in DB."""

from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _new_report_id() -> str:
    return f"rpt_{secrets.token_hex(8)}"


class Report(models.Model):
    """Generated analysis/aggregate reports stored as JSON in DB."""

    class Type(models.TextChoices):
        MODEL_ANALYSIS = "model_analysis", _("Model analysis")
        TEMPLATE_COMPARISON = "template_comparison", _("Template comparison")
        TOOL_ANALYSIS = "tool_analysis", _("Tool analysis")
        GENERATION_ANALYTICS = "generation_analytics", _("Generation analytics")
        COMPREHENSIVE = "comprehensive", _("Comprehensive")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        GENERATING = "generating", _("Generating")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report_id = models.SlugField(
        _("public id"),
        max_length=64,
        unique=True,
        default=_new_report_id,
    )

    report_type = models.CharField(
        _("type"),
        max_length=40,
        choices=Type.choices,
    )
    title = models.CharField(_("title"), max_length=500)
    description = models.TextField(_("description"), blank=True, default="")

    # Configuration parameters used to generate the report.
    config = models.JSONField(_("config"), default=dict, blank=True)
    # Full report content.
    report_data = models.JSONField(_("report data"), default=dict, blank=True)
    # Quick-glance summary for list views.
    summary = models.JSONField(_("summary"), default=dict, blank=True)

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(_("error message"), blank=True, default="")
    progress_percent = models.PositiveSmallIntegerField(
        _("progress %"),
        default=0,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)

    # Optional links back to source data.
    generation_job = models.ForeignKey(
        "generation.GenerationJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    analysis_task = models.ForeignKey(
        "analysis.AnalysisTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )

    class Meta:
        verbose_name = _("Report")
        verbose_name_plural = _("Reports")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["report_type", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.report_id})"

    # ---- helpers --------------------------------------------------------
    def mark_generating(self) -> None:
        self.status = self.Status.GENERATING
        self.progress_percent = 0
        self.error_message = ""
        self.save(update_fields=["status", "progress_percent", "error_message"])

    def mark_completed(self) -> None:
        self.status = self.Status.COMPLETED
        self.completed_at = timezone.now()
        self.progress_percent = 100
        self.error_message = ""
        self.save(
            update_fields=[
                "status",
                "completed_at",
                "progress_percent",
                "error_message",
            ],
        )

    def mark_failed(self, message: str) -> None:
        self.status = self.Status.FAILED
        self.error_message = message[:5000]
        self.completed_at = timezone.now()
        self.save(
            update_fields=["status", "error_message", "completed_at"],
        )

    def update_progress(self, percent: int) -> None:
        self.progress_percent = max(0, min(100, int(percent)))
        self.save(update_fields=["progress_percent"])

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at < timezone.now())
