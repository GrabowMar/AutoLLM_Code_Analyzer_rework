from django.contrib import admin

from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import CopilotIteration
from llm_lab.generation.models import GenerationArtifact
from llm_lab.generation.models import GenerationBatch
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.models import PromptTemplate
from llm_lab.generation.models import ScaffoldingTemplate


@admin.register(ScaffoldingTemplate)
class ScaffoldingTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_default", "created_at"]
    list_filter = ["is_default"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(AppRequirementTemplate)
class AppRequirementTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "category", "is_default", "created_at"]
    list_filter = ["category", "is_default"]
    search_fields = ["name", "slug", "category"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "stage", "role", "is_default", "version", "created_at"]
    list_filter = ["stage", "role", "is_default"]
    search_fields = ["name", "slug"]
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
