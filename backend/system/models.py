"""Staff-editable runtime settings of the System page."""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

APP_MEM_LIMIT_MIN_MB = 64
APP_MEM_LIMIT_MAX_MB = 8192


class FootprintSettings(models.Model):
    """Singleton (pk=1): how hard the platform keeps preview apps' footprint down.

    Re-read by the footprint sweep (``backend.system.tasks.footprint_sweep_task``)
    on every tick and by ``docker_manager.run_container`` on every app start.
    """

    idle_stop_enabled = models.BooleanField(
        _("stop idle apps"),
        default=False,
        help_text="Stop preview apps nobody opened for idle_stop_minutes",
    )
    idle_stop_minutes = models.PositiveIntegerField(
        _("idle minutes"),
        default=60,
        validators=[MinValueValidator(5), MaxValueValidator(7 * 24 * 60)],
    )
    max_running_apps = models.PositiveIntegerField(
        _("max running apps"),
        default=0,
        validators=[MaxValueValidator(500)],
        help_text="0 = unlimited; above it the least recently used apps are stopped",
    )
    app_mem_limit_mb = models.PositiveIntegerField(
        _("memory limit per app (MB)"),
        default=512,
        validators=[MinValueValidator(APP_MEM_LIMIT_MIN_MB), MaxValueValidator(APP_MEM_LIMIT_MAX_MB)],
    )
    auto_prune_enabled = models.BooleanField(
        _("prune unused images automatically"),
        default=False,
        help_text="Safe prune of the apps daemon (old tool image versions, orphan app images, dangling, build cache)",
    )
    auto_prune_interval_hours = models.PositiveIntegerField(
        _("auto prune every (hours)"),
        default=24,
        validators=[MinValueValidator(1), MaxValueValidator(24 * 30)],
    )
    last_auto_prune_at = models.DateTimeField(null=True, blank=True)
    last_sweep_at = models.DateTimeField(null=True, blank=True)
    last_sweep_result = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = _("footprint settings")
        verbose_name_plural = _("footprint settings")

    def __str__(self) -> str:
        return "Footprint settings"

    @classmethod
    def get(cls) -> FootprintSettings:
        """The singleton row; created on demand so flushed test databases still work."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
