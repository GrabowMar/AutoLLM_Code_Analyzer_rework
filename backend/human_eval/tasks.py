"""Celery tasks of the human-evaluation app (autodiscovered).

``reap_idle_apps_task`` is scheduled by ``signals.ensure_reaper_schedule``
(django-celery-beat, every 120 s): it stops task-started, unpinned apps that
idled past their task's ``hosting.idle_stop_minutes`` and starts queued ones.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def reap_idle_apps_task() -> int:
    """Periodic (Celery beat): ``services.hosting.reap_idle_apps()``; failures are logged, never raised."""
    try:
        from backend.human_eval.services import hosting

        return int(hosting.reap_idle_apps() or 0)
    except Exception:
        logger.exception("human_eval: idle app reaper failed")
        return 0
