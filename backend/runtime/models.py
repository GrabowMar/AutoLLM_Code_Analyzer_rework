"""Models for Docker runtime container management."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ContainerInstance(models.Model):
    """Tracks a containerised application instance."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        BUILDING = "building", _("Building")
        RUNNING = "running", _("Running")
        STOPPED = "stopped", _("Stopped")
        FAILED = "failed", _("Failed")
        REMOVED = "removed", _("Removed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation_job = models.ForeignKey(
        "generation.GenerationJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="container_instances",
    )
    name = models.SlugField(_("name"), max_length=200, unique=True)
    image = models.CharField(_("image"), max_length=500, blank=True, default="")
    container_id = models.CharField(
        _("container id"),
        max_length=200,
        blank=True,
        default="",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    app_port = models.IntegerField(_("app port"), null=True, blank=True)
    health_status = models.CharField(
        _("health status"),
        max_length=100,
        blank=True,
        default="",
    )
    last_health_check = models.DateTimeField(
        _("last health check"),
        null=True,
        blank=True,
    )
    config = models.JSONField(_("config"), default=dict, blank=True)
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="container_instances",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Container Instance")
        verbose_name_plural = _("Container Instances")
        ordering = ["-created_at"]
        unique_together = []

    def __str__(self) -> str:
        return self.name


class ContainerAction(models.Model):
    """Tracks a single operation performed on a container."""

    class ActionType(models.TextChoices):
        BUILD = "build", _("Build")
        START = "start", _("Start")
        STOP = "stop", _("Stop")
        RESTART = "restart", _("Restart")
        REMOVE = "remove", _("Remove")
        LOGS = "logs", _("Logs")
        HEALTH = "health", _("Health")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action_id = models.CharField(
        _("action id"),
        max_length=100,
        unique=True,
        db_index=True,
    )
    container = models.ForeignKey(
        ContainerInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actions",
    )
    action_type = models.CharField(
        _("action type"),
        max_length=20,
        choices=ActionType.choices,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    progress_percent = models.IntegerField(_("progress percent"), default=0)
    output = models.TextField(_("output"), blank=True, default="")
    error_message = models.TextField(_("error message"), blank=True, default="")
    exit_code = models.IntegerField(_("exit code"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="container_actions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Container Action")
        verbose_name_plural = _("Container Actions")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action_id} ({self.action_type})"

    def mark_running(self) -> None:
        from django.utils import timezone

        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_completed(self, output: str = "", exit_code: int = 0) -> None:
        from django.utils import timezone

        self.status = self.Status.COMPLETED
        self.output = output
        self.exit_code = exit_code
        self.progress_percent = 100
        self.completed_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "output",
                "exit_code",
                "progress_percent",
                "completed_at",
            ],
        )

    def mark_failed(self, error: str) -> None:
        from django.utils import timezone

        self.status = self.Status.FAILED
        self.error_message = error
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "error_message", "completed_at"])

    def append_output(self, chunk: str) -> None:
        self.output = (self.output or "") + chunk
        self.save(update_fields=["output"])

    def update_progress(self, percent: int) -> None:
        self.progress_percent = min(100, max(0, percent))
        self.save(update_fields=["progress_percent"])


class PortAllocation(models.Model):
    """Tracks allocated host port pairs for container instances."""

    app_port = models.IntegerField(_("app port"), unique=True)
    container = models.OneToOneField(
        ContainerInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="port_allocation",
    )
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Port Allocation")
        verbose_name_plural = _("Port Allocations")

    def __str__(self) -> str:
        return f"app:{self.app_port}"

    @classmethod
    def allocate(cls) -> PortAllocation:
        """Find the next free app port and allocate it atomically."""
        import socket

        from django.db import transaction

        base_port = 9001
        max_retries = 5

        def _is_port_free(port: int) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", port))
                except OSError:
                    return False
                else:
                    return True

        for _attempt in range(max_retries):
            with transaction.atomic():
                used_ports = set(cls.objects.values_list("app_port", flat=True))
                candidate = base_port
                while candidate in used_ports or not _is_port_free(candidate):
                    candidate += 1

                try:
                    return cls.objects.create(app_port=candidate)
                except Exception:  # noqa: BLE001, S112
                    continue  # race condition on creation — retry

        msg = "Could not allocate port after retries"
        raise RuntimeError(msg)


class Stack(models.Model):
    """A runnable code skeleton (Dockerfile, deps, frontend shell) for scaffolding jobs.

    slug+version rows are immutable, mirroring ContentBlock/GenerationProfile:
    job snapshots pin an exact version so historical runs keep provisioning the
    skeleton they were generated against. Builtin rows are seeded from
    ``runtime/scaffolding/manifest.json`` + the skeleton directories; the
    seeder bumps the version when the on-disk content changes.
    """

    class DockerfileMode(models.TextChoices):
        # Dockerfile ships inside ``files`` (builtin stacks).
        BUNDLED = "bundled", _("Bundled")
        # Dockerfile is generated server-side from a pinned template
        # (user-authored stacks; see Stage 6).
        GENERATED = "generated", _("Generated")

    slug = models.SlugField(_("slug"), max_length=100)
    version = models.PositiveIntegerField(
        _("version"),
        default=1,
        help_text="Versions are immutable: content changes create version+1",
    )
    name = models.CharField(_("name"), max_length=200, blank=True, default="")
    description = models.TextField(_("description"), blank=True, default="")
    is_builtin = models.BooleanField(_("builtin"), default=False)
    is_archived = models.BooleanField(_("archived"), default=False)
    is_approved = models.BooleanField(
        _("approved"),
        default=True,
        help_text="Gate for user stacks when STACK_REQUIRE_APPROVAL is on",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stacks",
    )

    # Runtime config (mirrors the manifest entry shape)
    has_frontend = models.BooleanField(_("has frontend"), default=False)
    default_port = models.PositiveIntegerField(_("default port"), default=8000)
    patch_profile = models.CharField(
        _("patch profile"),
        max_length=20,
        choices=[("flask", "Flask"), ("none", "None")],
        default="none",
    )
    frontend_component = models.CharField(_("frontend component"), max_length=100, blank=True, default="")
    backend_filename = models.CharField(_("backend filename"), max_length=100, default="app.py")
    aliases = models.JSONField(_("aliases"), default=list, blank=True)

    # Content
    files = models.JSONField(
        _("files"),
        default=dict,
        blank=True,
        help_text="Skeleton tree as {relative_path: text}",
    )
    content_hash = models.CharField(_("content hash"), max_length=64, blank=True, default="")
    dockerfile_mode = models.CharField(
        _("dockerfile mode"),
        max_length=20,
        choices=DockerfileMode.choices,
        default=DockerfileMode.BUNDLED,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Stack")
        verbose_name_plural = _("Stacks")
        ordering = ["slug", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "version"],
                name="runtime_stack_slug_version_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.slug} v{self.version}"
