from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "report_id",
        "title",
        "report_type",
        "status",
        "progress_percent",
        "created_by",
        "created_at",
    )
    list_filter = ("report_type", "status")
    search_fields = ("report_id", "title", "description")
    readonly_fields = (
        "id",
        "report_id",
        "created_at",
        "completed_at",
        "report_data",
        "summary",
        "config",
    )
    ordering = ("-created_at",)
