"""Sample generation models for scaffolding templates, prompts, jobs, and artifacts."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ScaffoldingTemplate(models.Model):
    """Docker infrastructure template (e.g., react-flask, vue-fastapi)."""

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200, unique=True)
    description = models.TextField(_("description"), blank=True, default="")
    tech_stack = models.JSONField(
        _("tech stack"),
        default=dict,
        blank=True,
        help_text='e.g. {"frontend": "React 18", "backend": "Flask 3.x"}',
    )
    template_archive = models.FileField(
        _("template archive"),
        upload_to="scaffolding/",
        blank=True,
        help_text=".tar.gz of the scaffolding directory",
    )
    substitution_vars = models.JSONField(
        _("substitution variables"),
        default=list,
        blank=True,
        help_text="List of {{var}} placeholders available in the template",
    )
    is_default = models.BooleanField(_("system default"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scaffolding_templates",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Scaffolding Template")
        verbose_name_plural = _("Scaffolding Templates")
        ordering = ["-is_default", "name"]

    def __str__(self) -> str:
        return self.name


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


class PromptTemplate(models.Model):
    """Jinja2 prompt template for system/user prompts."""

    class Stage(models.TextChoices):
        BACKEND = "backend", _("Backend")
        FRONTEND = "frontend", _("Frontend")

    class Role(models.TextChoices):
        SYSTEM = "system", _("System")
        USER = "user", _("User")

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200, unique=True)
    stage = models.CharField(_("stage"), max_length=20, choices=Stage.choices)
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    content = models.TextField(_("template content"))
    description = models.TextField(_("description"), blank=True, default="")
    is_default = models.BooleanField(_("system default"), default=False)
    version = models.PositiveIntegerField(_("version"), default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prompt_templates",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Prompt Template")
        verbose_name_plural = _("Prompt Templates")
        ordering = ["-is_default", "stage", "role", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.stage}/{self.role})"


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


class TemplateBundle(models.Model):
    """Ordered set of content blocks + scaffold slug for reproducible runs."""

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200, unique=True)
    description = models.TextField(_("description"), blank=True, default="")
    scaffolding_slug = models.SlugField(
        _("scaffolding slug"),
        max_length=200,
        default="flask-react",
        help_text="Canonical stack slug from runtime/scaffolding/manifest.json",
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
    is_system = models.BooleanField(_("system bundle"), default=False)
    is_default = models.BooleanField(_("default bundle"), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="template_bundles",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Template Bundle")
        verbose_name_plural = _("Template Bundles")
        ordering = ["-is_default", "-is_system", "name"]

    def __str__(self) -> str:
        return self.name


class GenerationBatch(models.Model):
    """Groups multiple generation jobs together."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        PARTIAL = "partial", _("Partial")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("batch name"), max_length=200, blank=True, default="")
    mode = models.CharField(_("generation mode"), max_length=20)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    total_jobs = models.PositiveIntegerField(_("total jobs"), default=0)
    completed_jobs = models.PositiveIntegerField(_("completed jobs"), default=0)
    failed_jobs = models.PositiveIntegerField(_("failed jobs"), default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generation_batches",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Generation Batch")
        verbose_name_plural = _("Generation Batches")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name or f"Batch {str(self.id)[:8]}"


class GenerationJob(models.Model):
    """A single generation request."""

    class Mode(models.TextChoices):
        CUSTOM = "custom", _("Custom")
        SCAFFOLDING = "scaffolding", _("Scaffolding")
        COPILOT = "copilot", _("Copilot")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

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
    scaffolding_template = models.ForeignKey(
        ScaffoldingTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    app_requirement = models.ForeignKey(
        AppRequirementTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
    backend_prompt_template = models.ForeignKey(
        PromptTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backend_jobs",
    )
    frontend_prompt_template = models.ForeignKey(
        PromptTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="frontend_jobs",
    )
    template_bundle = models.ForeignKey(
        "TemplateBundle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
    )
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
    bundle_key = models.CharField(
        _("bundle key"),
        max_length=220,
        blank=True,
        default="",
        db_index=True,
        help_text='Denormalized "bundle-slug@version" slicing key',
    )

    # Custom mode
    custom_system_prompt = models.TextField(_("system prompt"), blank=True, default="")
    custom_user_prompt = models.TextField(_("user prompt"), blank=True, default="")

    # Shared LLM parameters
    temperature = models.FloatField(_("temperature"), default=0.3)
    max_tokens = models.PositiveIntegerField(_("max tokens"), default=32000)

    # Copilot mode
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Generation Job")
        verbose_name_plural = _("Generation Jobs")
        ordering = ["-created_at"]

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
