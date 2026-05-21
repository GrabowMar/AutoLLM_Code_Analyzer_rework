"""Automation models: Pipeline, PipelineStep, PipelineRun, PipelineStepRun, Batch, BatchItem, Schedule."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Pipeline(models.Model):
    """A reusable automation pipeline definition."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ACTIVE = "active", _("Active")
        ARCHIVED = "archived", _("Archived")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pipelines",
    )
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    version = models.PositiveIntegerField(_("version"), default=1)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    config = models.JSONField(_("config"), default=dict, blank=True)
    tags = models.JSONField(_("tags"), default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Pipeline")
        verbose_name_plural = _("Pipelines")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_pipeline_owner_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} (v{self.version})"


class PipelineStep(models.Model):
    """A single step in a pipeline definition."""

    class Kind(models.TextChoices):
        GENERATE = "generate", _("Generate")
        ANALYZE = "analyze", _("Analyze")
        REPORT = "report", _("Report")
        WAIT = "wait", _("Wait")
        NOTIFY = "notify", _("Notify")
        SCRIPT = "script", _("Script")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(
        Pipeline,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    order = models.PositiveIntegerField(_("order"), default=0)
    name = models.CharField(_("name"), max_length=255)
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    config = models.JSONField(_("config"), default=dict, blank=True)
    depends_on = models.JSONField(_("depends_on"), default=list, blank=True)

    class Meta:
        verbose_name = _("Pipeline Step")
        verbose_name_plural = _("Pipeline Steps")
        ordering = ["order"]

    def __str__(self) -> str:
        return f"{self.pipeline.name} / {self.name}"


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    SUCCEEDED = "succeeded", _("Succeeded")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class PipelineRun(models.Model):
    """An execution instance of a pipeline."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(
        Pipeline,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_runs",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=RunStatus,
        default=RunStatus.PENDING,
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True, default="")
    result_summary = models.JSONField(_("result summary"), default=dict, blank=True)
    params = models.JSONField(_("params"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Pipeline Run")
        verbose_name_plural = _("Pipeline Runs")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Run {self.id} ({self.status})"


class PipelineStepRun(models.Model):
    """The execution record for a single step within a run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        PipelineRun,
        on_delete=models.CASCADE,
        related_name="step_runs",
    )
    step = models.ForeignKey(
        PipelineStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="step_runs",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=RunStatus,
        default=RunStatus.PENDING,
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    output = models.JSONField(_("output"), default=dict, blank=True)
    error = models.TextField(_("error"), blank=True, default="")
    attempt = models.PositiveIntegerField(_("attempt"), default=1)
    retries_remaining = models.PositiveIntegerField(_("retries remaining"), default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Pipeline Step Run")
        verbose_name_plural = _("Pipeline Step Runs")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"StepRun {self.id} ({self.status})"


class Batch(models.Model):
    """A fan-out batch over a matrix of (models x templates)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="batches",
    )
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True, default="")
    config = models.JSONField(_("config"), default=dict, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Batch")
        verbose_name_plural = _("Batches")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name


class BatchItem(models.Model):
    """One item within a batch (corresponds to a single pipeline run)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    pipeline_run = models.ForeignKey(
        PipelineRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batch_items",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    params = models.JSONField(_("params"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Batch Item")
        verbose_name_plural = _("Batch Items")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"BatchItem {self.id} ({self.status})"


class Schedule(models.Model):
    """A cron-based schedule that triggers a pipeline."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(
        Pipeline,
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    cron_expression = models.CharField(_("cron expression"), max_length=100)
    enabled = models.BooleanField(_("enabled"), default=True)
    next_run_at = models.DateTimeField(_("next run at"), null=True, blank=True)
    last_run_at = models.DateTimeField(_("last run at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Schedule")
        verbose_name_plural = _("Schedules")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Schedule {self.cron_expression} → {self.pipeline.name}"
