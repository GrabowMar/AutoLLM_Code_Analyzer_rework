"""Django admin registrations for the automation app."""

from __future__ import annotations

from django.contrib import admin

from llm_lab.automation.models import Batch
from llm_lab.automation.models import BatchItem
from llm_lab.automation.models import Pipeline
from llm_lab.automation.models import PipelineRun
from llm_lab.automation.models import PipelineStep
from llm_lab.automation.models import PipelineStepRun
from llm_lab.automation.models import Schedule


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "version", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "description")


@admin.register(PipelineStep)
class PipelineStepAdmin(admin.ModelAdmin):
    list_display = ("name", "pipeline", "kind", "order")
    list_filter = ("kind",)
    search_fields = ("name",)


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ("id", "pipeline", "status", "started_at", "completed_at")
    list_filter = ("status",)


@admin.register(PipelineStepRun)
class PipelineStepRunAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "step", "status", "attempt", "created_at")
    list_filter = ("status",)


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "description")


@admin.register(BatchItem)
class BatchItemAdmin(admin.ModelAdmin):
    list_display = ("id", "batch", "status", "created_at")
    list_filter = ("status",)


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "pipeline",
        "owner",
        "cron_expression",
        "enabled",
        "next_run_at",
        "last_run_at",
    )
    list_filter = ("enabled",)
