"""Celery tasks of the system app (autodiscovered).

``footprint_sweep_task`` is scheduled by ``signals.ensure_footprint_schedule``
(django-celery-beat, every 120 s): it stops idle apps / apps above the running
cap and runs the periodic safe prune, per ``FootprintSettings``.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def footprint_sweep_task() -> dict:
    """Periodic (Celery beat): ``services.footprint.sweep()``; failures are logged, never raised."""
    try:
        from backend.system.services import footprint

        return footprint.sweep()
    except Exception:
        logger.exception("system: footprint sweep failed")
        return {}
