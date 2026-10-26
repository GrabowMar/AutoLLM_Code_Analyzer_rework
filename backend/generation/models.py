"""Sample generation models for scaffolding templates, prompts, jobs, and artifacts."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


def default_stack_slug() -> str:
    """``settings.GENERATION_DEFAULT_STACK``, read when a row is created (not frozen at import)."""
    return settings.GENERATION_DEFAULT_STACK


class AppRequirementTemplate(models.Model):
    """Application specification template (e.g., todo app, blog platform)."""

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200, unique=True)
    category = models.CharField(_("category"), max_length=100, blank=True, default="")
    description = models.TextField(_("description"), blank=True, default="")
    backend_requirements = models.JSONField(
        _("backend requirements"),
        default=list,
        blank=True,
    )
    frontend_requirements = models.JSONField(
        _("frontend requirements"),
        default=list,
        blank=True,
    )
    admin_requirements = models.JSONField(
        _("admin requirements"),
        default=list,
        blank=True,
    )
    api_endpoints = models.JSONField(_("API endpoints"), default=list, blank=True)
    data_model = models.JSONField(_("data model"), default=dict, blank=True)
    admin_api_endpoints = models.JSONField(
        _("admin API endpoints"),
        default=list,
        blank=True,
    )
    difficulty = models.CharField(
        _("difficulty"),
        max_length=20,
        choices=[("basic", _("Basic")), ("standard", _("Standard")), ("advanced", _("Advanced"))],
        blank=True,
        default="",
    )
    version = models.PositiveIntegerField(
        _("version"),
        default=1,
        help_text="Bumped by the seeder when the spec content changes; single mutable row per slug",
    )
    content_hash = models.CharField(_("content hash"), max_length=64, blank=True, default="")
    spec_schema_version = models.PositiveIntegerField(_("spec schema version"), default=1)
    is_default = models.BooleanField(_("system default"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="app_requirement_templates",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("App Requirement Template")
        verbose_name_plural = _("App Requirement Templates")
        ordering = ["-is_default", "category", "name"]

    def __str__(self) -> str:
        return self.name


class ContentBlock(models.Model):
    """Composable fragment for prompts, rules, validation, or rubrics."""

    class BlockType(models.TextChoices):
        REQUIREMENT = "requirement", _("Requirement")
        API_SCHEMA = "api_schema", _("API schema")
        PROMPT_TONE = "prompt_tone", _("Prompt tone")
        PROMPT_RULES = "prompt_rules", _("Prompt rules")
        SCAFFOLD_HINT = "scaffold_hint", _("Scaffold hint")
        VALIDATION = "validation", _("Validation")
        EVAL_RUBRIC = "eval_rubric", _("Eval rubric")
        PROMPT_STAGE = "prompt_stage", _("Prompt stage")

    block_type = models.CharField(
        _("block type"),
        max_length=30,
        choices=BlockType.choices,
    )
    slug = models.SlugField(_("slug"), max_length=200)
    version = models.PositiveIntegerField(_("version"), default=1)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    content = models.TextField(_("content"))
    metadata = models.JSONField(
        _("metadata"),
        default=dict,
        blank=True,
        help_text='e.g. {"stage": "backend", "role": "system"} for prompt_stage blocks',
    )
    content_hash = models.CharField(_("content hash"), max_length=64, blank=True, default="")
    is_system = models.BooleanField(
        _("system block"),
        default=False,
        help_text="Shipped defaults; readable by all users",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_blocks",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Content Block")
        verbose_name_plural = _("Content Blocks")
        ordering = ["block_type", "slug", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "version"],
                name="generation_contentblock_slug_version_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.slug} v{self.version} ({self.block_type})"


class GenerationProfile(models.Model):
    """Ordered set of content blocks + scaffold slug + LLM defaults for reproducible runs."""

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200)
    version = models.PositiveIntegerField(
        _("version"),
        default=1,
        help_text="Versions are immutable: editing a profile creates version+1",
    )
    content_hash = models.CharField(_("content hash"), max_length=64, blank=True, default="")
    is_archived = models.BooleanField(
        _("archived"),
        default=False,
        help_text="Hidden from pickers but kept for jobs that reference it",
    )
    description = models.TextField(_("description"), blank=True, default="")
    scaffolding_slug = models.SlugField(
        _("scaffolding slug"),
        max_length=200,
        default=default_stack_slug,
        help_text="Canonical stack slug (runtime Stack)",
    )
    block_refs = models.JSONField(
        _("block references"),
        default=list,
        blank=True,
        help_text='List of {"type", "slug", "version"} objects',
    )
    llm_config = models.JSONField(
        _("LLM config defaults"),
        default=dict,
        blank=True,
    )
    is_system = models.BooleanField(_("system profile"), default=False)
    is_default = models.BooleanField(_("default profile"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_profiles",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Generation Profile")
        verbose_name_plural = _("Generation Profiles")
        ordering = ["-is_default", "-is_system", "name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "version"],
                name="generation_generationprofile_slug_version_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class RunPreset(models.Model):
    """A named, versioned piece of agent-run configuration (services/presets.py).

    ``harness``: harness + its non-default options; ``sampling``: model-agnostic
    LLM params (+ sampling policy) applied identically to every model;
    ``budget``: request/time/token/USD limits and network policy. Content is
    immutable per version (a content edit creates version+1); a job applies a
    preset's values into its own fields at creation, so the FKs on
    :class:`GenerationJob` are provenance only.
    """

    class Kind(models.TextChoices):
        HARNESS = "harness", _("Harness")
        SAMPLING = "sampling", _("Sampling")
        BUDGET = "budget", _("Budget")

    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    slug = models.SlugField(_("slug"), max_length=100)
    version = models.PositiveIntegerField(_("version"), default=1)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    config = models.JSONField(_("config"), default=dict, blank=True, help_text="Normalized config for the kind")
    content_hash = models.CharField(_("content hash"), max_length=64, blank=True, default="")
    is_system = models.BooleanField(_("system preset"), default=False)
    is_archived = models.BooleanField(
        _("archived"),
        default=False,
        help_text="Hidden from pickers but kept for jobs that reference it",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="run_presets",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Run Preset")
        verbose_name_plural = _("Run Presets")
        ordering = ["kind", "slug", "-version"]
        constraints = [
            # Slugs are global per kind (whatever the owner), so a slug@version
            # names exactly one configuration.
            models.UniqueConstraint(
                fields=["kind", "slug", "version"],
                name="generation_runpreset_kind_slug_version_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="generation_runpreset_version_gte_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}/{self.slug} v{self.version}"


BUILD_HEALING_CHOICES = [
    ("full", _("Full: infer dependencies and patch runtime issues")),
    ("no_deps", _("Patches only: no dependency inference")),
    ("none", _("None: build the model output as written")),
]


class Experiment(models.Model):
    """A designed run: apps x conditions x repeats, with a reproducibility seed."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        RUNNING = "running", _("Running")
        PAUSED = "paused", _("Paused")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        ARCHIVED = "archived", _("Archived")

    class SchedulingPolicy(models.TextChoices):
        AUTO = "auto", _("Auto")
        INTERLEAVED = "interleaved", _("Interleaved")
        BLOCKED = "blocked", _("Blocked")
        GROUPED = "grouped", _("Grouped")

    class PauseReason(models.TextChoices):
        NONE = "", _("None")
        USER = "user", _("User")
        DIGEST_CHANGED = "digest_changed", _("Digest changed")
        ENDPOINT_REMOVED = "endpoint_removed", _("Endpoint removed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    hypothesis = models.TextField(
        _("hypothesis"),
        blank=True,
        default="",
        help_text="What this experiment is meant to show, for the eventual report",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    app_requirements = models.ManyToManyField(
        AppRequirementTemplate,
        related_name="experiments",
        blank=True,
    )
    repeats = models.PositiveIntegerField(
        _("repeats"),
        default=3,
        help_text="Independent trials per (condition, app) cell",
    )
    base_seed = models.PositiveIntegerField(
        _("base seed"),
        null=True,
        blank=True,
        help_text="Set for deterministic seed derivation across launches; unset = random per run",
    )
    mode = models.CharField(
        _("generation mode"),
        max_length=20,
        choices=[("agent", _("Agent")), ("scaffolding", _("Scaffolding"))],
        default="agent",
        help_text="agent: conditions run the coding-agent harness; scaffolding: template prompts",
    )
    agent_config = models.JSONField(
        _("agent config"),
        default=dict,
        blank=True,
        help_text="Budget / network overrides for agent-mode runs (see services/agent/config.py)",
    )
    continuation_limit = models.PositiveSmallIntegerField(_("continuation limit"), default=1)
    enable_repair = models.BooleanField(_("enable repair round"), default=True)
    # Copied onto every job at launch (runtime.services.scaffolding.HealingMode).
    build_healing = models.CharField(
        _("build healing"),
        max_length=10,
        choices=BUILD_HEALING_CHOICES,
        default="full",
        help_text="How much the build repairs model output before deploying it",
    )
    llm_defaults = models.JSONField(
        _("LLM defaults"),
        default=dict,
        blank=True,
        help_text="Sampling params applied to every condition unless overridden (see llm_params.SAMPLING_KEYS)",
    )
    # Experiment-wide run settings (services/experiments.experiment_layers).
    # A blank stack (null in the API) is the legacy behaviour: each condition
    # runs on its profile's stack.
    stack_slug = models.CharField(
        _("stack slug"),
        max_length=100,
        blank=True,
        default="",
        help_text="Canonical stack every condition runs on (blank = each condition's profile stack)",
    )
    # Pinned preset versions; launch copies their values into each job.
    sampling_preset = models.ForeignKey(
        RunPreset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sampling_experiments",
    )
    budget_preset = models.ForeignKey(
        RunPreset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="budget_experiments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="experiments",
    )

    # Execution (spec §B.3 / §C). The order is frozen per job (GenerationJob.sequence) at launch.
    scheduling_policy = models.CharField(
        _("scheduling policy"),
        max_length=12,
        choices=SchedulingPolicy.choices,
        default=SchedulingPolicy.AUTO,
    )
    # -10..10; non-staff are clamped to -5..0. Orders lanes within a user's fair share.
    priority = models.SmallIntegerField(_("priority"), default=0)
    # Pre-registered baseline: defaults to the first condition at launch, frozen after launch.
    baseline_condition = models.ForeignKey(
        "ExperimentCondition",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Set on clones made by a clone_and_run schedule.
    created_by_schedule = models.ForeignKey(
        "ExperimentSchedule",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clones",
    )
    # Cap on this experiment's RUNNING jobs in all phases (NULL = no cap).
    max_concurrency = models.PositiveSmallIntegerField(_("max concurrency"), null=True, blank=True)
    infra_retries = models.PositiveSmallIntegerField(_("infra retries"), default=2)  # 0..5
    # {"warmup": bool (default true; kind=ollama only), "max_resident_models": int|null (only tightens K)}
    execution_options = models.JSONField(_("execution options"), default=dict, blank=True)
    # Keys: evaluate (bool), analysis (enabled, tool_slugs, only_passing),
    # report (enabled, exclude_truncated). Spec §B.3.
    post_run = models.JSONField(_("post-run steps"), default=dict, blank=True)
    # Pre-registered analysis plan (services/analysis_plan.py): editable while
    # draft, frozen with its hash at launch.
    analysis_plan = models.JSONField(_("analysis plan"), default=dict, blank=True)
    analysis_plan_hash = models.CharField(_("analysis plan hash"), max_length=64, blank=True, default="")
    analysis_plan_frozen_at = models.DateTimeField(_("analysis plan frozen at"), null=True, blank=True)
    pause_reason = models.CharField(
        _("pause reason"),
        max_length=32,
        choices=PauseReason.choices,
        blank=True,
        default="",
    )
    cloned_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clones",
    )
    launched_at = models.DateTimeField(_("launched at"), null=True, blank=True)
    paused_at = models.DateTimeField(_("paused at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Experiment")
        verbose_name_plural = _("Experiments")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "status", "-updated_at"], name="gen_exp_owner_status"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["created_by", "slug"],
                name="generation_experiment_owner_slug_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ExperimentCondition(models.Model):
    """One condition of an :class:`Experiment`: a model plus a profile and/or harness preset."""

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="conditions",
    )
    label = models.CharField(_("label"), max_length=200, blank=True, default="")
    # Required for scaffolding conditions (its prompts); optional in agent
    # mode, where it only contributes its stack and llm_config.
    profile = models.ForeignKey(
        GenerationProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="experiment_conditions",
    )
    model = models.ForeignKey(
        "llm_models.LLMModel",
        on_delete=models.PROTECT,
        related_name="experiment_conditions",
    )
    param_overrides = models.JSONField(
        _("param overrides"),
        default=dict,
        blank=True,
        help_text="Per-condition sampling-param overrides of the experiment llm_defaults",
    )
    harness = models.CharField(
        _("harness"),
        max_length=40,
        blank=True,
        default="",
        help_text="Agent harness for agent-mode experiments (blank = the experiment's agent_config / default)",
    )
    # The pinned harness preset (agent mode); ``harness`` then holds its
    # harness name so the legacy column stays meaningful.
    harness_preset = models.ForeignKey(
        RunPreset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="experiment_conditions",
    )
    # Set at first launch for local models; top_up/add_replicates raise
    # ``digest_drift`` when the live LLMModel.digest differs.
    pinned_digest = models.CharField(_("pinned digest"), max_length=80, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Experiment Condition")
        verbose_name_plural = _("Experiment Conditions")
        ordering = ["id"]
        constraints = [
            # NULLs compare equal: two profile-less (or preset-less) conditions
            # for the same model and harness are duplicates, not distinct rows.
            models.UniqueConstraint(
                fields=["experiment", "model", "profile", "harness", "harness_preset"],
                name="generation_experimentcondition_config_uniq",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.label or f"{self.model_id} / {self.method_label()}"

    def method_label(self) -> str:
        """What runs besides the model: the profile and the harness preset (or harness name)."""
        parts = []
        if self.profile_id is not None:
            parts.append(self.profile.slug)
        if self.harness_preset_id is not None:
            parts.append(f"{self.harness_preset.slug} v{self.harness_preset.version}")
        elif self.harness:
            parts.append(self.harness)
        return " / ".join(parts) or "default harness"

    def method_ref(self) -> str:
        """Short slicing label: ``profile@v``, ``preset@v`` (or the harness name), joined by ``/``."""
        parts = []
        if self.profile_id is not None:
            parts.append(f"{self.profile.slug}@{self.profile.version}")
        if self.harness_preset_id is not None:
            parts.append(f"{self.harness_preset.slug}@{self.harness_preset.version}")
        elif self.harness:
            parts.append(self.harness)
        return "/".join(parts)


class ExperimentSchedule(models.Model):
    """A cron trigger on an experiment: top up, add replicates, or clone and run (spec §F.5)."""

    class Action(models.TextChoices):
        TOP_UP = "top_up", _("Top up")
        ADD_REPLICATES = "add_replicates", _("Add replicates")
        CLONE_AND_RUN = "clone_and_run", _("Clone and run")

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="schedules")
    action = models.CharField(_("action"), max_length=16, choices=Action.choices)
    # croniter-valid, min interval SCHEDULE_MIN_INTERVAL_S
    cron = models.CharField(_("cron"), max_length=120)
    timezone = models.CharField(_("timezone"), max_length=64, default="UTC")
    # add_replicates {"replicates": 1..5}; clone_and_run {"name_template": "{name} {date}"}
    params = models.JSONField(_("params"), default=dict)
    skip_if_active = models.BooleanField(_("skip if active"), default=True)
    enabled = models.BooleanField(_("enabled"), default=True)
    next_run_at = models.DateTimeField(_("next run at"), null=True, blank=True)
    last_run_at = models.DateTimeField(_("last run at"), null=True, blank=True)
    # {outcome: fired|skipped|error, batch_id?, experiment_id?, jobs_created?, detail}
    last_result = models.JSONField(_("last result"), default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="experiment_schedules",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Experiment Schedule")
        verbose_name_plural = _("Experiment Schedules")
        ordering = ["id"]
        indexes = [
            models.Index(fields=["next_run_at"], condition=Q(enabled=True), name="expsched_due"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.cron} ({self.experiment_id})"


class ExperimentPostRun(models.Model):
    """One post-run (analysis, then report) of an experiment; leased by the executor (spec §F.5)."""

    class Trigger(models.TextChoices):
        COMPLETED = "completed", _("Completed")
        MANUAL = "manual", _("Manual")
        SCHEDULE = "schedule", _("Schedule")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="post_runs")
    trigger = models.CharField(_("trigger"), max_length=12, choices=Trigger.choices)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.PENDING)
    config = models.JSONField(_("config"))  # frozen copy of experiment.post_run
    analysis_run_ids = models.JSONField(_("analysis run ids"), default=list)
    report = models.ForeignKey(
        "reports.Report",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Keys: analysis_done, analysis_total, analysis_failed.
    progress = models.JSONField(_("progress"), default=dict)
    lease_owner = models.CharField(_("lease owner"), max_length=80, blank=True, default="")
    lease_expires_at = models.DateTimeField(_("lease expires at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("Experiment Post-run")
        verbose_name_plural = _("Experiment Post-runs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status"],
                condition=Q(status__in=["pending", "running"]),
                name="postrun_active",
            ),
        ]

    def __str__(self) -> str:
        return f"post-run {self.id} ({self.status})"


class GenerationBatch(models.Model):
    """Groups multiple generation jobs together."""

    class Kind(models.TextChoices):
        ADHOC = "adhoc", _("Ad hoc")
        LAUNCH = "launch", _("Launch")
        TOP_UP = "top_up", _("Top up")
        RETRY = "retry", _("Retry")
        SCHEDULE = "schedule", _("Schedule")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        PARTIAL = "partial", _("Partial")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("batch name"), max_length=200, blank=True, default="")
    mode = models.CharField(_("generation mode"), max_length=20)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # Cached from the live job rows by services/batches.refresh_batch (the
    # API derives status and counts from the jobs themselves).
    total_jobs = models.PositiveIntegerField(_("total jobs"), default=0)
    completed_jobs = models.PositiveIntegerField(_("completed jobs"), default=0)
    failed_jobs = models.PositiveIntegerField(_("failed jobs"), default=0)
    # What was asked for (base seed, run-order seed, trials, stack, preset ids,
    # app and model ids, inline overrides): the seed matrix and run order of a
    # batch stay auditable from the batch itself.
    request = models.JSONField(_("request"), default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generation_batches",
    )
    experiment = models.ForeignKey(
        Experiment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="batches",
    )
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices, default=Kind.ADHOC)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Generation Batch")
        verbose_name_plural = _("Generation Batches")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["experiment", "-created_at"], name="genbatch_experiment")]

    def __str__(self) -> str:
        return self.name or f"Batch {str(self.id)[:8]}"


class GenerationJob(models.Model):
    """A single generation request."""

    class Mode(models.TextChoices):
        # Primary: a coding agent (OpenCode) builds the app in a sandbox.
        AGENT = "agent", _("Agent")
        CUSTOM = "custom", _("Custom")
        SCAFFOLDING = "scaffolding", _("Scaffolding")
        # Legacy: in-process aider loop; kept so historical jobs still load.
        COPILOT = "copilot", _("Copilot (legacy)")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    class Phase(models.TextChoices):
        # PENDING: "". RUNNING: running | eval_queued | evaluating.
        NONE = "", _("None")
        # Legacy (thread dispatch) rows only.
        QUEUED = "queued", _("Queued")
        RUNNING = "running", _("Running")
        EVAL_QUEUED = "eval_queued", _("Waiting for evaluation")
        EVALUATING = "evaluating", _("Evaluating")

    class Lane(models.TextChoices):
        SANDBOX = "sandbox", _("Sandbox")
        LLM = "llm", _("LLM")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mode = models.CharField(
        _("generation mode"),
        max_length=20,
        choices=Mode.choices,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # Where a RUNNING agent job is: waiting for a sandbox slot, in the
    # sandbox, or being evaluated. Status is RUNNING for all three (it is set
    # before the slot wait), so this is the only persisted queued/running
    # distinction; "" whenever the job is not running.
    phase = models.CharField(
        _("phase"),
        max_length=20,
        choices=Phase.choices,
        blank=True,
        default="",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generation_jobs",
    )
    batch = models.ForeignKey(
        GenerationBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="jobs",
    )

    # Model to use
    model = models.ForeignKey(
        "llm_models.LLMModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_jobs",
    )

    # Scaffolding mode
    stack_slug = models.CharField(
        _("stack slug"),
        max_length=100,
        blank=True,
        default="",
        help_text="Canonical or alias slug from runtime/scaffolding/manifest.json",
    )
    app_requirement = models.ForeignKey(
        AppRequirementTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    profile = models.ForeignKey(
        "GenerationProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    # Historical name kept on purpose: the column and its JSON keys
    # (bundle_slug, bundle_version, ...) are frozen provenance shared with
    # every already-created job row. "Bundle" here means what the UI now
    # calls a generation profile.
    resolved_bundle = models.JSONField(
        _("resolved bundle"),
        default=dict,
        blank=True,
        help_text="Immutable snapshot of blocks, prompts, and app spec at job creation",
    )
    experiment_seed = models.PositiveIntegerField(
        _("experiment seed"),
        null=True,
        blank=True,
    )
    prompt_hash = models.CharField(
        _("prompt hash"),
        max_length=16,
        blank=True,
        default="",
        db_index=True,
        help_text="Hash of prompt material (templates + app spec + block versions); shared across repeats/models",
    )
    # Historical name (analytics slice by this column): holds
    # "<profile-slug>@<version>".
    bundle_key = models.CharField(
        _("bundle key"),
        max_length=220,
        blank=True,
        default="",
        db_index=True,
        help_text='Denormalized "profile-slug@version" slicing key',
    )

    # Experiment membership (a job is one run/cell of a designed experiment)
    experiment = models.ForeignKey(
        "Experiment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    condition = models.ForeignKey(
        "ExperimentCondition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    repeat_index = models.PositiveIntegerField(
        _("repeat index"),
        null=True,
        blank=True,
        help_text="0-based trial number within this job's (condition, app) cell",
    )

    # Custom mode
    custom_system_prompt = models.TextField(_("system prompt"), blank=True, default="")
    custom_user_prompt = models.TextField(_("user prompt"), blank=True, default="")

    # Shared LLM parameters. temperature/max_tokens/top_p are the legacy base
    # layer (kept as fallback for pre-snapshot jobs); llm_params holds the
    # per-run overrides that win over the profile's llm_config in the frozen
    # snapshot (see services/llm_params.py for the precedence chain).
    temperature = models.FloatField(_("temperature"), default=0.3)
    max_tokens = models.PositiveIntegerField(_("max tokens"), default=32000)
    top_p = models.FloatField(_("top p"), null=True, blank=True)
    llm_params = models.JSONField(
        _("LLM param overrides"),
        default=dict,
        blank=True,
    )

    # Agent mode. task_description is a free-form brief for runs without an
    # app spec (with one, it is prepended to the spec); agent_config holds
    # per-job budget / network overrides (services/agent/config.py).
    task_description = models.TextField(_("task description"), blank=True, default="")
    agent_config = models.JSONField(_("agent config overrides"), default=dict, blank=True)
    # Non-default harness options (sparse; services/agent/harnesses). Kept out
    # of agent_config on purpose: that one is merged into the fingerprinted
    # snapshot ``agent`` section, options only shape the ``harness`` section.
    harness_options = models.JSONField(_("harness options"), default=dict, blank=True)
    # Build-time repair of the output (runtime.services.scaffolding.HealingMode).
    # Not part of the snapshot: it changes how the output is built, not what
    # the model was asked, so healed and raw builds share a fingerprint.
    build_healing = models.CharField(
        _("build healing"),
        max_length=10,
        choices=BUILD_HEALING_CHOICES,
        default="full",
    )
    # Provenance only: preset values were applied into llm_params /
    # agent_config / harness_options at creation, so retries and replays
    # reproduce the configuration without reading these.
    harness_preset = models.ForeignKey(
        RunPreset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="harness_jobs",
    )
    sampling_preset = models.ForeignKey(
        RunPreset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sampling_jobs",
    )
    budget_preset = models.ForeignKey(
        RunPreset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="budget_jobs",
    )

    # Copilot mode (legacy)
    copilot_description = models.TextField(
        _("copilot description"),
        blank=True,
        default="",
    )
    copilot_max_iterations = models.PositiveIntegerField(
        _("max iterations"),
        default=5,
    )
    copilot_current_iteration = models.PositiveIntegerField(
        _("current iteration"),
        default=0,
    )
    copilot_use_open_source = models.BooleanField(
        _("prefer open-source models"),
        default=True,
    )

    # Results
    app_directory = models.CharField(
        _("app directory"),
        max_length=500,
        blank=True,
        default="",
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    duration_seconds = models.FloatField(_("duration (seconds)"), null=True, blank=True)
    error_message = models.TextField(_("error message"), blank=True, default="")
    result_data = models.JSONField(
        _("result data"),
        default=dict,
        blank=True,
        help_text="Generated code, token usage, cost, etc.",
    )
    metrics = models.JSONField(
        _("metrics"),
        default=dict,
        blank=True,
        help_text="Token usage, cost, timing",
    )

    # Execution queue (spec §B.3 / §C). Owned by backend.execution.
    # Denormalized from model.endpoint at creation; NULL = OpenRouter.
    endpoint = models.ForeignKey(
        "inference.LLMEndpoint",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="jobs",
    )
    lane = models.CharField(_("lane"), max_length=8, choices=Lane.choices, default=Lane.LLM)
    # Non-NULL means the executor owns the row; legacy (thread-dispatch) rows stay NULL.
    queued_at = models.DateTimeField(_("queued at"), null=True, blank=True)
    run_after = models.DateTimeField(_("run after"), null=True, blank=True)
    priority = models.SmallIntegerField(_("priority"), default=0)
    sequence = models.PositiveIntegerField(_("sequence"), null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(_("attempt"), default=0)
    lease_owner = models.CharField(_("lease owner"), max_length=80, blank=True, default="")
    lease_expires_at = models.DateTimeField(_("lease expires at"), null=True, blank=True)
    heartbeat_at = models.DateTimeField(_("heartbeat at"), null=True, blank=True)
    # When the job entered its current phase (eval FIFO, UI).
    stage_entered_at = models.DateTimeField(_("stage entered at"), null=True, blank=True)
    # Runtime record (policy, started_seq, queue timings, attempts, infra_errors, ...).
    # Not part of the snapshot and not fingerprinted.
    execution = models.JSONField(_("execution"), default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Generation Job")
        verbose_name_plural = _("Generation Jobs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["-priority", "queued_at"],
                condition=Q(status="pending") & Q(queued_at__isnull=False),
                name="genjob_ready",
            ),
            models.Index(
                fields=["experiment", "endpoint", "sequence", "queued_at"],
                condition=Q(status="pending") & Q(queued_at__isnull=False),
                name="genjob_lane_head",
            ),
            models.Index(fields=["lease_owner"], condition=~Q(lease_owner=""), name="genjob_owned"),
            models.Index(
                fields=["endpoint", "lane", "phase"],
                condition=Q(status="running"),
                name="genjob_running",
            ),
            models.Index(fields=["lease_expires_at"], condition=Q(status="running"), name="genjob_lease"),
            models.Index(
                fields=["experiment", "condition", "app_requirement", "repeat_index"],
                name="genjob_cell",
            ),
            models.Index(fields=["experiment", "status", "phase"], name="genjob_exp_state"),
            models.Index(fields=["batch", "status"], name="genjob_batch_status"),
        ]

    def __str__(self) -> str:
        return f"{self.mode} job {str(self.id)[:8]} ({self.status})"


class GenerationArtifact(models.Model):
    """Raw LLM request/response payload for debugging."""

    job = models.ForeignKey(
        GenerationJob,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    stage = models.CharField(
        _("stage"),
        max_length=50,
        help_text='e.g. "backend", "frontend", "copilot_iter_1"',
    )
    request_payload = models.JSONField(_("request payload"), default=dict)
    response_payload = models.JSONField(_("response payload"), default=dict)
    prompt_tokens = models.PositiveIntegerField(_("prompt tokens"), default=0)
    completion_tokens = models.PositiveIntegerField(_("completion tokens"), default=0)
    total_cost = models.FloatField(_("estimated cost"), default=0.0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Generation Artifact")
        verbose_name_plural = _("Generation Artifacts")
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Artifact {self.stage} for job {str(self.job_id)[:8]}"


class CopilotIteration(models.Model):
    """Tracks each iteration of copilot mode's agentic loop."""

    class Action(models.TextChoices):
        GENERATE = "generate", _("Generate")
        BUILD = "build", _("Build")
        FIX = "fix", _("Fix")
        VALIDATE = "validate", _("Validate")

    job = models.ForeignKey(
        GenerationJob,
        on_delete=models.CASCADE,
        related_name="copilot_iterations",
    )
    iteration_number = models.PositiveIntegerField(_("iteration"))
    action = models.CharField(
        _("action"),
        max_length=20,
        choices=Action.choices,
    )
    llm_request = models.JSONField(_("LLM request"), default=dict, blank=True)
    llm_response = models.TextField(_("LLM response"), blank=True, default="")
    build_output = models.TextField(_("build output"), blank=True, default="")
    build_success = models.BooleanField(_("build succeeded"), default=False)
    errors_detected = models.JSONField(
        _("errors detected"),
        default=list,
        blank=True,
    )
    fix_applied = models.TextField(_("fix applied"), blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Copilot Iteration")
        verbose_name_plural = _("Copilot Iterations")
        ordering = ["job", "iteration_number"]
        unique_together = [["job", "iteration_number"]]

    def __str__(self) -> str:
        job_short = str(self.job_id)[:8]
        return f"Iteration {self.iteration_number} ({self.action}) for job {job_short}"
