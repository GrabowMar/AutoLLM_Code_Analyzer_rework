"""Django admin for the analysis engine (v2).

Runs, tool results, review samples and findings are engine output: they are
read-only here (hand edits would break provenance and plan hashes), with a
cooperative "request cancel" action on active runs. Profiles' configs are
versioned content, edited through the API (``PUT /api/analysis/profiles/{id}/``
creates a new version), so the admin only toggles archival. A study's frozen
plan, protocol and report are read-only; their hashes are shown for audit.
Like ``PATCH /api/analysis/studies/{id}/``, the admin edits a study only while
it is a draft: once locked, every field is read-only, because the protocol
pre-registered its research questions, design, targets, exclusions and
profile (an edit would silently change e.g. which outcomes are confirmatory).
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib import messages
from django.db.models import Count

from backend.analysis.models import AnalysisProfile
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult


def _short(value: str, n: int = 12) -> str:
    return (value or "")[:n]


class ReadOnlyAdmin(admin.ModelAdmin):
    """Engine output: viewable, deletable by superusers, never hand-edited."""

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_superuser)


@admin.register(AnalyzerTool)
class AnalyzerToolAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "kind", "category", "version", "is_enabled", "display_order")
    list_filter = ("kind", "category", "is_enabled")
    search_fields = ("slug", "name")
    ordering = ("display_order", "name")
    readonly_fields = (
        "slug",
        "kind",
        "category",
        "languages",
        "output_kind",
        "config_schema",
        "default_config",
        "spec",
        "spec_hash",
        "version",
        "created_at",
        "updated_at",
    )


@admin.register(AnalysisProfile)
class AnalysisProfileAdmin(admin.ModelAdmin):
    list_display = ("slug", "version", "name", "is_system", "is_archived", "created_by", "hash", "created_at")
    list_filter = ("is_system", "is_archived")
    search_fields = ("slug", "name", "content_hash")
    ordering = ("slug", "-version")
    raw_id_fields = ("created_by",)
    readonly_fields = ("id", "slug", "version", "config", "content_hash", "is_system", "created_at", "updated_at")
    actions = ("archive", "unarchive")

    @admin.display(description="content hash")
    def hash(self, obj: AnalysisProfile) -> str:
        return _short(obj.content_hash)

    @admin.action(description="Archive selected profile versions")
    def archive(self, request, queryset) -> None:
        updated = queryset.update(is_archived=True)
        self.message_user(request, f"{updated} profile version(s) archived.", messages.SUCCESS)

    @admin.action(description="Unarchive selected profile versions")
    def unarchive(self, request, queryset) -> None:
        updated = queryset.update(is_archived=False)
        self.message_user(request, f"{updated} profile version(s) unarchived.", messages.SUCCESS)


@admin.register(AnalysisStudy)
class AnalysisStudyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "status",
        "profile",
        "created_by",
        "run_count",
        "launch_status",
        "report_status",
        "protocol",
        "locked_at",
        "launched_at",
        "completed_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("name", "description", "protocol_hash", "plan_hash")
    raw_id_fields = ("profile", "created_by")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "status",
        "preflight",
        "plan",
        "plan_hash",
        "protocol",
        "protocol_hash",
        "report",
        "report_hash",
        "locked_at",
        "launched_at",
        "completed_at",
        "report_generated_at",
        "created_at",
        "updated_at",
    )

    def has_change_permission(self, request, obj=None) -> bool:
        if obj is not None and obj.status != AnalysisStudy.Status.DRAFT:
            return False  # pre-registered (or running/finished): view only, as through the API
        return super().has_change_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status != AnalysisStudy.Status.DRAFT:
            fields = [f.name for f in self.model._meta.concrete_fields]  # noqa: SLF001 - Django's Options API
            return tuple(dict.fromkeys([*self.readonly_fields, *fields]))
        return self.readonly_fields

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_runs=Count("runs"))

    @admin.display(description="runs", ordering="_runs")
    def run_count(self, obj: AnalysisStudy) -> int:
        return getattr(obj, "_runs", 0)

    @admin.display(description="protocol hash")
    def protocol(self, obj: AnalysisStudy) -> str:
        return _short(obj.protocol_hash)

    @admin.display(description="launch")
    def launch_status(self, obj: AnalysisStudy) -> str:
        from backend.analysis.research import study as study_mod

        state = study_mod.launch_state(obj)
        # A launch record without a status predates background launches (it was complete).
        return (state.get("status") or "submitted") if state else ""

    @admin.display(description="report")
    def report_status(self, obj: AnalysisStudy) -> str:
        from backend.analysis.research import study as study_mod

        state = study_mod.report_state(obj)
        return state["status"] if state else ""


class ToolResultInline(admin.TabularInline):
    model = ToolResult
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "position",
        "tool_slug",
        "required",
        "status",
        "failure_reason",
        "attempt",
        "exit_code",
        "duration_seconds",
        "tool_version",
    )
    readonly_fields = fields
    ordering = ("position", "tool_slug")

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(AnalysisRun)
class AnalysisRunAdmin(ReadOnlyAdmin):
    list_display = (
        "short_id",
        "name",
        "purpose",
        "status",
        "is_complete",
        "phase",
        "attempt",
        "created_by",
        "heartbeat_at",
        "duration_seconds",
        "created_at",
    )
    list_filter = ("status", "purpose", "is_complete", "cancel_requested")
    search_fields = ("name", "plan_hash", "sample_hash")
    raw_id_fields = ("generation_job", "study", "profile", "created_by")
    date_hierarchy = "created_at"
    inlines = (ToolResultInline,)
    actions = ("request_cancel",)

    @admin.display(description="id")
    def short_id(self, obj: AnalysisRun) -> str:
        return _short(str(obj.id), 8)

    @admin.action(description="Request cancel (cooperative)")
    def request_cancel(self, request, queryset) -> None:
        from backend.analysis.engine import executor

        cancelled = 0
        for run in queryset.filter(status__in=AnalysisRun.ACTIVE_STATUSES):
            executor.request_cancel(run)
            cancelled += 1
        self.message_user(request, f"Cancel requested for {cancelled} active run(s).", messages.SUCCESS)


class ReviewSampleInline(admin.TabularInline):
    model = ReviewSample
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("index", "engine", "harness", "model_slug", "status", "failure_reason", "finding_count", "repaired")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ToolResult)
class ToolResultAdmin(ReadOnlyAdmin):
    list_display = ("tool_slug", "run", "status", "failure_reason", "exit_code", "duration_seconds", "tool_version")
    list_filter = ("status", "failure_reason", "tool_kind", "tool_slug")
    search_fields = ("tool_slug", "config_hash", "run__name")
    raw_id_fields = ("run",)
    inlines = (ReviewSampleInline,)


@admin.register(ReviewSample)
class ReviewSampleAdmin(ReadOnlyAdmin):
    list_display = ("result", "index", "engine", "harness", "model_slug", "status", "finding_count", "repaired")
    list_filter = ("engine", "harness", "status", "repaired")
    search_fields = ("model_slug", "prompt_sha256", "result__run__name")
    raw_id_fields = ("result",)


@admin.register(Finding)
class FindingAdmin(ReadOnlyAdmin):
    list_display = (
        "title",
        "tool_slug",
        "severity",
        "category",
        "file_path",
        "line_number",
        "file_origin",
        "counted",
        "is_ai",
        "is_consensus",
    )
    list_filter = ("severity", "category", "is_ai", "counted", "is_consensus", "file_origin", "tool_slug")
    search_fields = ("title", "rule_id", "file_path", "fingerprint", "issue_key")
    raw_id_fields = ("result", "run", "review_sample")
    ordering = ("severity_rank", "file_path", "line_number", "id")
