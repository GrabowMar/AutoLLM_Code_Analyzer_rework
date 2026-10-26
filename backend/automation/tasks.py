"""Celery tasks for the automation execution engine.

Workers are configured in ``docker-compose.local.yml``.
Verify a worker is running: ``docker ps | grep celery``.

If the Celery broker is unreachable, ``trigger_run`` in ``services.py``
falls back to a daemon thread automatically.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

logger = logging.getLogger(__name__)


# A pipeline blocks on its steps: an agent generation (hours), an analysis
# run (the analyze step waits up to its own timeout, 4 h by default) and
# reports. The global Celery limits (60 s soft / 300 s hard) would kill it
# mid-step, so this task carries its own, sized above the longest step chain.
PIPELINE_SOFT_TIME_LIMIT = int(getattr(settings, "AUTOMATION_PIPELINE_SOFT_TIME_LIMIT", 12 * 3600))
PIPELINE_TIME_LIMIT = int(getattr(settings, "AUTOMATION_PIPELINE_TIME_LIMIT", PIPELINE_SOFT_TIME_LIMIT + 600))


@shared_task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=PIPELINE_SOFT_TIME_LIMIT,
    time_limit=PIPELINE_TIME_LIMIT,
)
def run_pipeline_task(self, run_id: str) -> dict:
    """Execute a pipeline run.  Wraps ``engine.runner.execute_run``."""
    from backend.automation.engine.runner import execute_run

    logger.info("Celery run_pipeline_task: run_id=%s", run_id)
    try:
        execute_run(uuid.UUID(run_id))
    except SoftTimeLimitExceeded:
        # Retrying would only restart the same over-long pipeline.
        logger.exception("run_pipeline_task hit its time limit for %s", run_id)
        return {"run_id": run_id, "status": "time_limit"}
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
