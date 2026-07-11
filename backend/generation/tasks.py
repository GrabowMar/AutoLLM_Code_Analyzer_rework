"""Celery tasks for generation jobs.

These tasks are available when a Celery worker is running but are NOT required.
The API views dispatch jobs via background threads (see ``dispatch_job`` in
``backend.generation.services.dispatcher``) so generation works without Celery.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, soft_time_limit=600, time_limit=660)
def run_generation_job(self, job_id: str) -> dict:
    """Execute a single generation job (any mode)."""
    from backend.generation.models import GenerationJob
    from backend.generation.services.generation_service import GenerationService

    logger.info("Starting generation job %s", job_id)
    try:
        job = GenerationJob.objects.select_related(
            "model",
            "app_requirement",
            "batch",
        ).get(id=job_id)
    except GenerationJob.DoesNotExist:
        logger.exception("Job %s not found", job_id)
        return {"error": f"Job {job_id} not found"}

    service = GenerationService()
    service.execute(job)

    return {"job_id": str(job.id), "status": job.status}


@shared_task(bind=True)
def run_generation_batch(self, batch_id: str) -> dict:
    """Orchestrate a batch of generation jobs via Celery."""
    from backend.generation.models import GenerationBatch

    logger.info("Starting generation batch %s", batch_id)
    try:
        batch = GenerationBatch.objects.get(id=batch_id)
    except GenerationBatch.DoesNotExist:
        return {"error": f"Batch {batch_id} not found"}

    batch.status = "running"
    batch.save(update_fields=["status"])

    for job in batch.jobs.filter(status="pending"):
        run_generation_job.delay(str(job.id))

    return {"batch_id": batch_id, "status": "dispatched"}
