"""Django admin for the human-evaluation models.

Secrets and hashes (access-code / session-token / device hashes, invite tokens)
are excluded. Research data (JSON snapshots, answers, contexts) and uploaded
files are read-only: edits go through the API so the audit rules apply.
"""

from __future__ import annotations

from django.contrib import admin

from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import EvaluatorProfile
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response


@admin.register(EvalTask)
class EvalTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "mode", "status", "created_by", "opened_at", "closed_at", "created_at")
    list_filter = ("status", "mode")
    search_fields = ("title", "id", "created_by__email")
    raw_id_fields = ("created_by", "experiment")
    readonly_fields = (
        "id",
        "design",
        "settings",
        "design_hash",
        "seed",
        "frozen_at",
        "opened_at",
        "closed_at",
        "first_closed_at",
        "protocol_log",
        "created_at",
        "updated_at",
    )


@admin.register(EvalTaskApp)
class EvalTaskAppAdmin(admin.ModelAdmin):
    list_display = ("label", "task", "job", "is_active", "pinned", "started_by_task", "last_activity_at")
    list_filter = ("is_active", "pinned", "started_by_task")
    search_fields = ("label", "task__title", "job__id")
    raw_id_fields = ("task", "job", "container")
    readonly_fields = ("id", "snapshot", "finding_sample", "finding_sample_at", "finding_frame", "created_at")


@admin.register(EvalItem)
class EvalItemAdmin(admin.ModelAdmin):
    list_display = ("__str__", "task", "app_a", "app_b", "group_key", "position", "is_active")
    list_filter = ("is_active",)
    search_fields = ("task__title", "group_key")
    raw_id_fields = ("task", "app_a", "app_b")
    readonly_fields = ("id", "created_at")


@admin.register(EvalInvite)
class EvalInviteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "task", "uses", "max_uses", "expires_at", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("label", "task__title")
    raw_id_fields = ("task", "created_by")
    exclude = ("token",)
    readonly_fields = ("id", "uses", "created_at")


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("__str__", "task", "kind", "status", "excluded", "joined_at", "last_seen_at")
    list_filter = ("kind", "status", "excluded")
    search_fields = ("task__title", "user__email", "access_code_hint")
    raw_id_fields = ("task", "user", "invite", "excluded_by")
    exclude = ("access_code_hash", "session_token_hash", "device_hash")
    readonly_fields = ("id", "number", "pre_answers", "post_answers", "completion_code", "created_at")


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "task", "participant", "status", "position", "swapped", "excluded", "submitted_at")
    list_filter = ("status", "excluded", "swapped")
    search_fields = ("id", "task__title")
    raw_id_fields = ("task", "participant", "item", "excluded_by")
    readonly_fields = ("id", "context", "app_views", "created_at")


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ("__str__", "block_id", "updated_at")
    search_fields = ("block_id", "assignment__id")
    raw_id_fields = ("assignment",)
    readonly_fields = ("id", "answers", "created_at", "updated_at")


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "severity", "category", "task_app", "created_at")
    list_filter = ("kind", "severity")
    search_fields = ("title", "description")
    raw_id_fields = ("assignment", "task_app")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "observation", "content_type", "size", "created_at")
    raw_id_fields = ("observation",)
    readonly_fields = ("id", "file", "content_type", "size", "original_name", "created_at")


@admin.register(FindingVerdict)
class FindingVerdictAdmin(admin.ModelAdmin):
    list_display = ("__str__", "verdict", "task_app", "updated_at")
    list_filter = ("verdict",)
    search_fields = ("finding_key",)
    raw_id_fields = ("assignment", "task_app", "finding")
    readonly_fields = ("id", "finding_key", "finding_snapshot", "created_at", "updated_at")


@admin.register(HttpExchange)
class HttpExchangeAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status_code", "elapsed_ms", "task_app", "created_at")
    search_fields = ("path",)
    raw_id_fields = ("assignment", "task_app")
    readonly_fields = (
        "id",
        "method",
        "path",
        "request_headers",
        "request_body",
        "status_code",
        "response_headers",
        "response_body",
        "response_truncated",
        "elapsed_ms",
        "error",
        "created_at",
    )


@admin.register(EvaluatorProfile)
class EvaluatorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_restricted", "created_by", "created_at")
    list_filter = ("is_restricted",)
    search_fields = ("user__email",)
    raw_id_fields = ("user", "created_by")
    readonly_fields = ("created_at",)
