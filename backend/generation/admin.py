from django.contrib import admin

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import CopilotIteration
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob


@admin.register(AppRequirementTemplate)
class AppRequirementTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "category", "is_default", "created_at"]
    list_filter = ["category", "is_default"]
    search_fields = ["name", "slug", "category"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(GenerationBatch)
class GenerationBatchAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "mode",
        "status",
        "total_jobs",
        "completed_jobs",
        "failed_jobs",
        "created_at",
    ]
    list_filter = ["mode", "status"]
    readonly_fields = ["id"]


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "mode",
        "status",
        "model",
        "created_by",
        "started_at",
        "completed_at",
    ]
    list_filter = ["mode", "status"]
    readonly_fields = ["id", "started_at", "completed_at", "duration_seconds"]
    raw_id_fields = ["model", "created_by", "batch"]


@admin.register(GenerationArtifact)
class GenerationArtifactAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "job",
        "stage",
        "prompt_tokens",
        "completion_tokens",
        "total_cost",
        "created_at",
    ]
    raw_id_fields = ["job"]


@admin.register(CopilotIteration)
class CopilotIterationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "job",
        "iteration_number",
        "action",
        "build_success",
        "created_at",
    ]
    list_filter = ["action", "build_success"]
    raw_id_fields = ["job"]
