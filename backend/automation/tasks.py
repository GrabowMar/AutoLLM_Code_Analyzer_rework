"""Celery tasks of the retired automation pipelines (legacy, kept for existing data).

The automation API router is unmounted and nothing enqueues or schedules these
tasks any more: experiment schedules and post-run steps now run inside the
executor (see docs/BACKGROUND_SERVICES.md). They remain registered with the
legacy models. ``trigger_run`` in
``services.py`` (no longer reachable from the API) still falls back to a
daemon thread when the broker is unreachable.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, acks_late=True, max_retries=3, default_retry_delay=10)
def run_pipeline_task(self, run_id: str) -> dict:
    """Execute a pipeline run.  Wraps ``engine.runner.execute_run``."""
    from backend.automation.engine.runner import execute_run

    logger.info("Celery run_pipeline_task: run_id=%s", run_id)
    try:
        execute_run(uuid.UUID(run_id))
    except Exception as exc:
        logger.exception("run_pipeline_task failed for %s", run_id)
        raise self.retry(exc=exc) from exc
    else:
        return {"run_id": run_id, "status": "dispatched"}


@shared_task
def run_scheduler_tick() -> dict:
    """Trigger all due schedules. Intended for use as a Celery beat task."""
    from backend.automation.engine.scheduler import tick

    fired = tick()
    logger.info("Scheduler tick: %d schedules fired", fired)
    return {"fired": fired}
