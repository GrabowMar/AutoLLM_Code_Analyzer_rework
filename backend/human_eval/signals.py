"""Signal wiring of the human-evaluation app (connected from ``HumanEvalConfig.ready``).

- ``post_migrate`` → :func:`ensure_reaper_schedule` registers the idle-app reaper
  as a django-celery-beat periodic task (``tasks.reap_idle_apps_task`` every
  120 s), like ``backend/integrations/signals.py``. It never raises.
- ``post_delete`` on :class:`~backend.human_eval.models.Attachment` deletes the
  stored file once the transaction commits (covers cascades from observation,
  assignment, participant and task deletion).
- ``post_save`` on :class:`~backend.human_eval.models.EvaluatorProfile` with
  ``is_restricted`` revokes the user's API tokens: restricted evaluators must not
  reach the REST API through a Bearer token. Staff users are never restricted.
"""

from __future__ import annotations

import logging

from django.db import DEFAULT_DB_ALIAS
from django.db import transaction
from django.db.models.signals import post_delete
from django.db.models.signals import post_migrate
from django.db.models.signals import post_save
from django.utils import timezone

logger = logging.getLogger(__name__)

REAPER_TASK_NAME = "human_eval: reap idle apps"
REAPER_TASK = "backend.human_eval.tasks.reap_idle_apps_task"
REAPER_EVERY_SECONDS = 120


def ensure_reaper_schedule(sender=None, using: str = DEFAULT_DB_ALIAS, **kwargs) -> None:
    """Register the idle-app reaper as a periodic task (idempotent; runs after ``migrate``; never raises)."""
    try:
        from django_celery_beat.models import IntervalSchedule
        from django_celery_beat.models import PeriodicTask
    except ImportError:  # pragma: no cover - django_celery_beat is installed
        return
    try:
        schedules = IntervalSchedule.objects.using(using)
        schedule = schedules.filter(every=REAPER_EVERY_SECONDS, period=IntervalSchedule.SECONDS).first()
        if schedule is None:
            schedule = schedules.create(every=REAPER_EVERY_SECONDS, period=IntervalSchedule.SECONDS)
        PeriodicTask.objects.using(using).get_or_create(
            name=REAPER_TASK_NAME,
            defaults={"task": REAPER_TASK, "interval": schedule, "enabled": True},
        )
    except Exception:
        # Beat tables not migrated yet, or a broken schedule row: never break `migrate`.
        logger.exception("Could not register the %s periodic task", REAPER_TASK_NAME)


def _delete_file(storage, name: str) -> None:
    try:
        storage.delete(name)
    except Exception:
        logger.exception("Could not delete human-eval attachment file %s", name)


def delete_attachment_file(sender, instance, using: str = DEFAULT_DB_ALIAS, **kwargs) -> None:
    """``post_delete`` on Attachment: remove the stored file after the deleting transaction commits."""
    name = getattr(instance.file, "name", "") or ""
    if not name:
        return
    storage = instance.file.storage
    transaction.on_commit(lambda: _delete_file(storage, name), using=using)


def revoke_restricted_tokens(sender, instance, created: bool = False, raw: bool = False, **kwargs) -> None:
    """``post_save`` on EvaluatorProfile: a restricted (non-staff) evaluator's API tokens are revoked."""
    if raw or not instance.is_restricted:
        return
    from backend.tokens.models import ApiToken
    from backend.users.models import User

    user = User.objects.filter(pk=instance.user_id).only("is_staff", "is_superuser").first()
    if user is None or user.is_staff or user.is_superuser:
        return
    now = timezone.now()
    ApiToken.objects.filter(user_id=instance.user_id, revoked_at__isnull=True).update(revoked_at=now, updated_at=now)


def connect(app_config=None) -> None:
    """Connect every receiver of the app (called once from ``HumanEvalConfig.ready``)."""
    from backend.human_eval.models import Attachment
    from backend.human_eval.models import EvaluatorProfile

    post_migrate.connect(ensure_reaper_schedule, sender=app_config, dispatch_uid="human_eval.reaper_schedule")
    post_delete.connect(delete_attachment_file, sender=Attachment, dispatch_uid="human_eval.attachment_file_cleanup")
    post_save.connect(
        revoke_restricted_tokens,
        sender=EvaluatorProfile,
        dispatch_uid="human_eval.evaluator_token_revocation",
    )
