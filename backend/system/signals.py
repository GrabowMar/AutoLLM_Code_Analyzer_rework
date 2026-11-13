"""Signal wiring of the system app (connected from ``SystemConfig.ready``).

``post_migrate`` registers the footprint sweep as a django-celery-beat periodic
task (``tasks.footprint_sweep_task`` every 120 s). It never raises.
"""

from __future__ import annotations

import logging

from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

SWEEP_TASK_NAME = "system: app footprint sweep"
SWEEP_TASK = "backend.system.tasks.footprint_sweep_task"
SWEEP_EVERY_SECONDS = 120


def ensure_footprint_schedule(sender=None, using: str = DEFAULT_DB_ALIAS, **kwargs) -> None:
    """Register the footprint sweep as a periodic task (idempotent; runs after ``migrate``; never raises)."""
    try:
        from django_celery_beat.models import IntervalSchedule
        from django_celery_beat.models import PeriodicTask
    except ImportError:  # pragma: no cover - django_celery_beat is installed
        return
    try:
        schedules = IntervalSchedule.objects.using(using)
        schedule = schedules.filter(every=SWEEP_EVERY_SECONDS, period=IntervalSchedule.SECONDS).first()
        if schedule is None:
            schedule = schedules.create(every=SWEEP_EVERY_SECONDS, period=IntervalSchedule.SECONDS)
        PeriodicTask.objects.using(using).get_or_create(
            name=SWEEP_TASK_NAME,
            defaults={"task": SWEEP_TASK, "interval": schedule, "enabled": True},
        )
    except Exception:
        # Beat tables not migrated yet, or a broken schedule row: never break `migrate`.
        logger.exception("Could not register the %s periodic task", SWEEP_TASK_NAME)


def connect(app_config=None) -> None:
    post_migrate.connect(ensure_footprint_schedule, sender=app_config, dispatch_uid="system.footprint_schedule")
