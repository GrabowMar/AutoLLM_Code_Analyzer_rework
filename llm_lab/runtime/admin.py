from django.contrib import admin

from .models import ContainerAction
from .models import ContainerInstance
from .models import PortAllocation


@admin.register(ContainerInstance)
class ContainerInstanceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "status",
        "image",
        "backend_port",
        "frontend_port",
        "health_status",
        "created_by",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("name", "image", "container_id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ContainerAction)
class ContainerActionAdmin(admin.ModelAdmin):
    list_display = (
        "action_id",
        "action_type",
        "status",
        "progress_percent",
        "container",
        "triggered_by",
        "created_at",
    )
    list_filter = ("action_type", "status")
    search_fields = ("action_id",)
    readonly_fields = ("id", "created_at", "started_at", "completed_at")
    ordering = ("-created_at",)


@admin.register(PortAllocation)
class PortAllocationAdmin(admin.ModelAdmin):
    list_display = ("backend_port", "frontend_port", "container", "allocated_at")
    list_filter: list = []
    readonly_fields = ("allocated_at",)
