"""Django admin for the v3 generation models: inspection first, edits only where rows are mutable.

Library versions, manifests, plans and the spend ledger are immutable, so their admins show the frozen
columns read-only (the models refuse such writes anyway). New versions are created through the library
API (WP-03), never by editing a row here.
"""

from __future__ import annotations

from django.contrib import admin

from backend.generation import models as m

_LIBRARY_FROZEN = ["slug", "version", "schema_version", "content", "content_hash", "derived_from", "created_at"]


class VersionedEntityAdmin(admin.ModelAdmin):
    list_display = ["slug", "version", "name", "is_system", "is_archived", "visibility", "created_by", "created_at"]
    list_filter = ["is_system", "is_archived", "visibility"]
    search_fields = ["slug", "name", "content_hash"]
    raw_id_fields = ["created_by", "derived_from"]
    ordering = ["slug", "-version"]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ["content_hash", "created_at"]
        return [*_LIBRARY_FROZEN, *obj.CONTENT_COLUMNS]


for _model in (
    m.StackProfile,
    m.Starter,
    m.StyleDirective,
    m.Guidance,
    m.PromptTemplate,
    m.PromptSet,
    m.HarnessPreset,
    m.SamplingPreset,
    m.ResourcePolicy,
    m.EvalSuite,
    m.PatternDetector,
):
    admin.site.register(_model, VersionedEntityAdmin)


class TaskSpecVersionInline(admin.TabularInline):
    model = m.TaskSpecVersion
    fields = ["version", "name", "content_hash", "created_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(m.TaskSpec)
class TaskSpecAdmin(admin.ModelAdmin):
    list_display = ["slug", "latest_version", "is_system", "is_archived", "visibility", "created_at"]
    list_filter = ["is_system", "is_archived", "visibility"]
    search_fields = ["slug"]
    raw_id_fields = ["created_by"]
    inlines = [TaskSpecVersionInline]


@admin.register(m.TaskSpecVersion)
class TaskSpecVersionAdmin(admin.ModelAdmin):
    list_display = ["task", "version", "name", "content_hash", "created_at"]
    search_fields = ["task__slug", "name", "content_hash"]
    raw_id_fields = ["task", "created_by", "derived_from"]
    readonly_fields = [
        "task",
        "version",
        "schema_version",
        "content",
        "content_hash",
        "structured_hash",
        "prose_hashes",
        "created_at",
    ]


@admin.register(m.Blob)
class BlobAdmin(admin.ModelAdmin):
    list_display = ["sha256", "media_type", "size", "codec", "created_at"]
    search_fields = ["sha256"]
    readonly_fields = ["sha256", "size", "codec", "media_type", "created_at"]


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


class StudyFactorInline(admin.TabularInline):
    model = m.StudyFactor
    extra = 0


@admin.register(m.Study)
class StudyAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "mode", "status", "repeats", "spent_usd", "locked_at", "created_by", "created_at"]
    list_filter = ["kind", "mode", "status"]
    search_fields = ["name", "slug", "design_hash", "prereg_hash"]
    raw_id_fields = ["created_by", "resource_policy", "active_plan", "cloned_from"]
    readonly_fields = [
        "id",
        "design_hash",
        "kind",
        "mode",
        "repeats",
        "base_seed",
        "order_seed",
        "scheduling",
        "analysis_plan_hash",
        "prereg_hash",
        "locked_at",
        "spent_usd",
        "spent_list_usd",
        "reserved_usd",
        "spent_tokens",
        "reserved_tokens",
        "created_at",
        "updated_at",
    ]
    inlines = [StudyFactorInline]


@admin.register(m.StudyPlan)
class StudyPlanAdmin(admin.ModelAdmin):
    list_display = ["plan_hash", "study", "status", "n_conditions", "n_units", "n_cells", "created_at"]
    list_filter = ["status"]
    search_fields = ["plan_hash", "design_hash"]
    raw_id_fields = ["study", "created_by"]
    readonly_fields = [*(f for f in m.StudyPlan.IMMUTABLE_FIELDS if f != "study"), "created_at"]


@admin.register(m.StudyCondition)
class StudyConditionAdmin(admin.ModelAdmin):
    list_display = ["study", "index", "label", "model", "is_baseline", "paused"]
    list_filter = ["is_baseline", "paused"]
    raw_id_fields = ["study", "plan", "model", "harness_preset"]


@admin.register(m.StudyCell)
class StudyCellAdmin(admin.ModelAdmin):
    list_display = ["study", "condition", "unit", "repeat_index", "stage", "state", "sequence", "current_run"]
    list_filter = ["stage", "state"]
    raw_id_fields = ["study", "plan", "condition", "unit", "current_run"]


@admin.register(m.StudySchedule)
class StudyScheduleAdmin(admin.ModelAdmin):
    list_display = ["study", "action", "cron", "enabled", "next_run_at", "last_run_at"]
    list_filter = ["action", "enabled"]
    raw_id_fields = ["study", "created_by"]


@admin.register(m.StudyPostRun)
class StudyPostRunAdmin(admin.ModelAdmin):
    list_display = ["id", "study", "kind", "trigger", "status", "created_at", "completed_at"]
    list_filter = ["kind", "trigger", "status"]
    raw_id_fields = ["study", "report", "pattern_run"]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunAttemptInline(admin.TabularInline):
    model = m.RunAttempt
    fields = ["attempt", "termination", "outcome", "infra_error", "oom_killed", "started_at", "ended_at"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(m.GenerationRun)
class GenerationRunAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "status", "phase", "outcome", "usable", "model", "study", "created_at"]
    list_filter = ["kind", "status", "phase", "outcome", "usable", "lane"]
    search_fields = ["id", "config_fingerprint", "run_fingerprint", "tree_hash"]
    readonly_fields = [
        "id",
        "kind",
        "protocol",
        *m.GenerationRun.IDENTITY_FIELDS,
        "tree_hash",
        "started_at",
        "completed_at",
        "duration_seconds",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = [
        "created_by",
        "study",
        "condition",
        "cell",
        "model",
        "task_version",
        "stack_profile",
        "starter",
        "style",
        "guidance",
        "prompt_set",
        "harness_preset",
        "sampling_preset",
        "resource_policy",
        "slot_target",
        "parent_run",
        "endpoint",
    ]
    inlines = [RunAttemptInline]


@admin.register(m.RunManifest)
class RunManifestAdmin(admin.ModelAdmin):
    list_display = ["run", "protocol", "manifest_hash", "created_at"]
    search_fields = ["manifest_hash", "run__id"]
    raw_id_fields = ["run"]
    readonly_fields = ["run", "protocol", "manifest", "manifest_hash", "stimuli", "created_at"]


@admin.register(m.RunEvaluation)
class RunEvaluationAdmin(admin.ModelAdmin):
    list_display = ["id", "run", "eval_suite", "eval_level", "mode", "status", "created_at"]
    list_filter = ["eval_level", "mode", "status"]
    raw_id_fields = ["run", "eval_suite", "reused_from", "retest_of", "baseline"]


@admin.register(m.EvalBaseline)
class EvalBaselineAdmin(admin.ModelAdmin):
    list_display = ["id", "starter", "task_version", "auth_mode", "eval_suite", "agent_pass_rate"]
    raw_id_fields = ["starter", "task_version", "eval_suite", "reference_run"]


@admin.register(m.GenerationArtifact)
class GenerationArtifactAdmin(admin.ModelAdmin):
    list_display = ["id", "run", "attempt", "stage", "prompt_tokens", "completion_tokens", "total_cost", "created_at"]
    raw_id_fields = ["run"]


# ---------------------------------------------------------------------------
# Slot mode and patterns
# ---------------------------------------------------------------------------


@admin.register(m.SlotSet)
class SlotSetAdmin(admin.ModelAdmin):
    list_display = ["name", "parent_run", "task_version", "parent_model", "parent_harness", "created_at"]
    search_fields = ["name", "frozen_tree_hash"]
    raw_id_fields = ["parent_run", "task_version", "parent_eval", "index", "created_by"]


@admin.register(m.SlotTarget)
class SlotTargetAdmin(admin.ModelAdmin):
    list_display = ["label", "slot_set", "kind", "language", "match_key", "default_hole_mode", "created_at"]
    list_filter = ["kind", "default_hole_mode", "edit_policy"]
    search_fields = ["label", "match_key", "target_hash"]
    raw_id_fields = ["slot_set", "original_run", "created_by"]
    readonly_fields = ["target_hash", "original_sha", "original_blob", "created_at"]


@admin.register(m.PatternRun)
class PatternRunAdmin(admin.ModelAdmin):
    list_display = ["id", "study", "label", "is_live", "status", "created_at", "completed_at"]
    list_filter = ["label", "is_live", "status"]
    raw_id_fields = ["study", "created_by"]


@admin.register(m.DetectorAudit)
class DetectorAuditAdmin(admin.ModelAdmin):
    list_display = ["id", "pattern_run", "feature_key", "status", "precision", "npv"]
    list_filter = ["status"]
    raw_id_fields = ["pattern_run", "eval_task", "created_by"]


# ---------------------------------------------------------------------------
# Spend
# ---------------------------------------------------------------------------


@admin.register(m.SpendLedgerEntry)
class SpendLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "source", "study", "run", "user", "amount_usd", "list_cost_usd", "created_at"]
    list_filter = ["kind", "source"]
    search_fields = ["dedupe_key", "model_slug"]
    raw_id_fields = ["study", "run", "user", "endpoint"]

    def has_change_permission(self, request, obj=None) -> bool:
        return False  # append-only

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(m.SpendCap)
class SpendCapAdmin(admin.ModelAdmin):
    list_display = ["scope", "user", "endpoint", "model_slug", "period", "unit", "limit", "action", "enabled"]
    list_filter = ["scope", "period", "unit", "action", "enabled"]
    raw_id_fields = ["user", "endpoint", "created_by"]


@admin.register(m.CostPrior)
class CostPriorAdmin(admin.ModelAdmin):
    list_display = ["key", "n", "cost_p50", "cost_p90", "wall_s_p50", "updated_at"]
    search_fields = ["key"]
