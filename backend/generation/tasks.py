"""Celery tasks of the generation app (autodiscovered).

``fetch_call_stats_task`` fetches OpenRouter's per-call generation stats for
one job (``services.openrouter_stats``). The orchestrator queues it after a
generation stage with a countdown, because OpenRouter's record can lag the
completion; a call still unknown then is retried later, and marked
``unavailable`` only on the last try. It never touches the job's status.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

# Countdown between tries while some records are still missing (404).
PENDING_RETRY_S = 600


@shared_task(bind=True, ignore_result=True, max_retries=2, soft_time_limit=900, time_limit=1200)
def fetch_call_stats_task(self, job_id: str) -> dict:
    """Store ``telemetry["openrouter"]`` for ``job_id``'s ``gen-`` calls; failures are logged, never raised."""
    from backend.generation.services import openrouter_stats

    final = self.request.retries >= self.max_retries
    try:
        summary = openrouter_stats.fetch_for_job(job_id, final=final)
    except Exception:
        logger.exception("OpenRouter stats for job %s failed", job_id)
        return {"error": True}
    if not final and (summary.get("pending") or summary.get("error")):
        raise self.retry(countdown=PENDING_RETRY_S)
    return summary
