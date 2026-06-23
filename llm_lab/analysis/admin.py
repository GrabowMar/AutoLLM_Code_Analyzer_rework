from django.contrib import admin

from .models import AnalysisRun
from .models import AnalyzerTool
from .models import AnalyzerWorkspace
from .models import Finding
from .models import InstalledTool
from .models import ToolResult


@admin.register(AnalyzerTool)
class AnalyzerToolAdmin(admin.ModelAdmin):
    list_display = [
        "slug",
        "name",
        "category",
        "kind",
        "target_language",
        "is_enabled",
        "is_system",
        "display_order",
    ]
    list_filter = ["category", "kind", "target_language", "is_enabled", "is_system"]
    search_fields = ["slug", "name", "description"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(AnalyzerWorkspace)
class AnalyzerWorkspaceAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "status", "container", "last_used_at", "updated_at"]
    list_filter = ["status"]
    search_fields = ["user__email", "user__username"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(InstalledTool)
class InstalledToolAdmin(admin.ModelAdmin):
    list_display = ["id", "workspace", "tool", "status", "installed_version", "updated_at"]
    list_filter = ["status"]
    search_fields = ["tool__slug"]


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "name",
        "status",
        "created_by",
        "generation_job",
        "started_at",
        "completed_at",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["name", "id"]
    readonly_fields = ["id", "started_at", "completed_at", "duration_seconds"]


@admin.register(ToolResult)
class ToolResultAdmin(admin.ModelAdmin):
    list_display = ["id", "run", "tool_slug", "category", "status", "created_at"]
    list_filter = ["category", "status"]
    search_fields = ["tool_slug"]


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
