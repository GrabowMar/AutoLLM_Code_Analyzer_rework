"""post_save receivers that turn terminal status changes into webhook deliveries.

Transitions written with ``QuerySet.update()`` or raw SQL (the executor's
``finish_stage`` / ``cancel_jobs`` / ``maybe_complete_experiment``) do not
fire ``post_save``; ``tasks.sweep_webhook_events`` (Celery beat, every
minute) catches those.
"""

from __future__ import annotations

from django.apps import apps
from django.db.models.signals import post_migrate
from django.db.models.signals import post_save

from backend.integrations.services import webhooks

SWEEP_TASK_NAME = "integrations: sweep webhook events"
SWEEP_TASK = "backend.integrations.tasks.sweep_webhook_events"
SWEEP_EVERY_SECONDS = 60


def _on_save(sender, instance, created=False, update_fields=None, raw=False, **kwargs):
    if raw:
        return
    if update_fields is not None and "status" not in update_fields:
        return
    if webhooks.event_type_for(instance) is None:
        return
    webhooks.emit(instance)


def ensure_sweep_schedule(sender=None, **kwargs) -> None:
    """Register the sweep as a django-celery-beat periodic task (idempotent; runs after ``migrate``)."""
    try:
        from django_celery_beat.models import IntervalSchedule
        from django_celery_beat.models import PeriodicTask
    except ImportError:  # pragma: no cover
        return
    from django.db import DatabaseError

    try:
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=SWEEP_EVERY_SECONDS,
            period=IntervalSchedule.SECONDS,
        )
        PeriodicTask.objects.get_or_create(
            name=SWEEP_TASK_NAME,
            defaults={"task": SWEEP_TASK, "interval": schedule, "enabled": True},
        )
    except DatabaseError:  # beat tables not migrated yet
        return


def connect(app_config=None) -> None:
    for key in webhooks._STATUS_EVENTS:  # noqa: SLF001
        model = apps.get_model(key)
        post_save.connect(_on_save, sender=model, dispatch_uid=f"integrations.webhooks.{key}")
    post_migrate.connect(ensure_sweep_schedule, sender=app_config, dispatch_uid="integrations.webhooks.sweep_schedule")
