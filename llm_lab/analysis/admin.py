from django.contrib import admin

from .models import AnalysisResult
from .models import AnalysisTask
from .models import AnalyzerConfig
from .models import Finding


@admin.register(AnalysisTask)
class AnalysisTaskAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "status",
        "generation_job",
        "created_by",
        "started_at",
        "completed_at",
        "duration_seconds",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["name", "id"]
    readonly_fields = ["id", "started_at", "completed_at", "duration_seconds"]


@admin.register(AnalysisResult)
class AnalysisResultAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "task",
        "analyzer_type",
        "analyzer_name",
        "status",
        "duration_seconds",
        "created_at",
    ]
    list_filter = ["analyzer_type", "analyzer_name", "status"]
    search_fields = ["analyzer_name"]


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "result",
        "severity",
        "category",
        "title",
        "file_path",
        "line_number",
        "rule_id",
    ]
    list_filter = ["severity", "category", "confidence"]
    search_fields = ["title", "description", "rule_id", "file_path"]


@admin.register(AnalyzerConfig)
class AnalyzerConfigAdmin(admin.ModelAdmin):
    list_display = ["name", "analyzer_name", "enabled", "created_at", "updated_at"]
    list_filter = ["enabled", "analyzer_name"]
    search_fields = ["name", "analyzer_name"]
