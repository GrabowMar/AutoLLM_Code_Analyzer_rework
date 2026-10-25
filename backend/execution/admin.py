"""Django admin registrations for the execution app."""

from __future__ import annotations

from django.contrib import admin

from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings


@admin.register(ExecutorInstance)
class ExecutorInstanceAdmin(admin.ModelAdmin):
    list_display = ("id", "hostname", "pid", "is_leader", "code_version", "started_at", "last_seen_at", "stopped_at")
    list_filter = ("is_leader",)
    readonly_fields = ("started_at",)


@admin.register(ExecutorSettings)
class ExecutorSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "sandbox_slots", "eval_slots", "llm_job_threads", "post_run_slots", "draining", "updated_at")
