from django.contrib import admin

from backend.rankings.models import BenchmarkResult
from backend.rankings.models import RankingSnapshot


@admin.register(BenchmarkResult)
class BenchmarkResultAdmin(admin.ModelAdmin):
    list_display = ("model_id", "benchmark", "score", "source", "fetched_at")
    list_filter = ("benchmark", "source")
    search_fields = ("model_id",)


@admin.register(RankingSnapshot)
class RankingSnapshotAdmin(admin.ModelAdmin):
    list_display = ("created_at", "version")
    readonly_fields = ("created_at", "payload", "version")
