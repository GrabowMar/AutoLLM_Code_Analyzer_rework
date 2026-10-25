"""Executor process bookkeeping (spec §B.4).

``ExecutorInstance`` is one ``run_executor`` process; ``ExecutorSettings`` is
the staff-editable singleton holding slot counts and the drain flag.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ExecutorInstance(models.Model):
    """A running (or stopped) executor process; its id is the lease owner on jobs it claims."""

    # Format: hostname:pid:<8 hex chars of a uuid4>
    id = models.CharField(primary_key=True, max_length=80)
    hostname = models.CharField(max_length=120)
    pid = models.PositiveIntegerField()
    # sha1 over (path, mtime_ns, size) of backend/**/*.py
    code_version = models.CharField(max_length=40)
    is_leader = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(db_index=True)
    # set on clean SIGTERM; a stopped or stale (>90 s) instance's leases count as expired
    stopped_at = models.DateTimeField(null=True, blank=True)
    # effective slots, memory math
    config = models.JSONField(default=dict)

    class Meta:
        verbose_name = _("executor instance")
        verbose_name_plural = _("executor instances")
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return self.id


def _initial_sandbox_slots() -> int:
    return int(getattr(settings, "AGENT_MAX_CONCURRENT_RUNS", 1) or 1)


class ExecutorSettings(models.Model):
    """Singleton (pk=1), staff-editable at runtime; re-read by the executor every tick."""

    sandbox_slots = models.PositiveSmallIntegerField(default=1)  # clamped by the memory rule (§C.10)
    eval_slots = models.PositiveSmallIntegerField(default=1)
    llm_job_threads = models.PositiveSmallIntegerField(default=16)  # <= 32
    post_run_slots = models.PositiveSmallIntegerField(default=1)  # analysis /work collision -> keep 1
    draining = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = _("executor settings")
        verbose_name_plural = _("executor settings")

    def __str__(self) -> str:
        return "Executor settings"

    @classmethod
    def get(cls) -> ExecutorSettings:
        """The singleton row; created on demand so flushed test databases still work."""
        obj, _created = cls.objects.get_or_create(
            pk=1,
            defaults={"sandbox_slots": _initial_sandbox_slots()},
        )
        return obj
