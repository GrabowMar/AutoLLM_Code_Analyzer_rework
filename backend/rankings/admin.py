from django.contrib import admin

from backend.rankings.models import RankingSnapshot
from backend.rankings.models import SourceSnapshot


@admin.register(RankingSnapshot)
class RankingSnapshotAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "status", "version", "inputs_sha256", "confirmed_at")
    list_filter = ("kind", "status", "version")
    readonly_fields = (
        "created_at",
        "kind",
        "payload",
        "version",
        "params",
        "inputs_sha256",
        "status",
        "created_by",
        "confirmed_at",
        "confirmed_by",
        "confirmation",
        "code_version",
    )


@admin.register(SourceSnapshot)
class SourceSnapshotAdmin(admin.ModelAdmin):
    list_display = ("fetched_at", "source", "as_of", "window_start", "window_end", "sha256")
    list_filter = ("source",)
    readonly_fields = (
        "source",
        "fetched_at",
        "as_of",
        "window_start",
        "window_end",
        "params",
        "payload",
        "sha256",
        "license",
        "citation",
    )
