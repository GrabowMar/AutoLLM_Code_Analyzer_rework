"""Persistence and realtime publishing helpers for generation jobs."""

from backend.generation.models import GenerationJob
from backend.realtime import events as realtime


def publish_status(job: GenerationJob) -> None:
    """Publish a status update event for a job."""
    timestamp = job.completed_at or job.started_at
    realtime.publish(
        f"generation:{job.id}",
        {
            "type": "status",
            "status": job.status,
            "updated_at": timestamp.isoformat() if timestamp else None,
        },
    )


def publish_progress(
    job: GenerationJob,
    iteration: int,
    max_iterations: int,
    timestamp_iso: str,
) -> None:
    """Publish a progress event for a copilot iteration."""
    realtime.publish(
        f"generation:{job.id}",
        {
            "type": "progress",
            "status": job.status,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "updated_at": timestamp_iso,
        },
    )


def update_batch(job: GenerationJob) -> None:
    """Update batch counters if job belongs to a batch."""
    if not job.batch:
        return
    batch = job.batch
    jobs = batch.jobs.all()
    batch.completed_jobs = jobs.filter(
        status=GenerationJob.Status.COMPLETED,
    ).count()
    batch.failed_jobs = jobs.filter(status=GenerationJob.Status.FAILED).count()
    done = batch.completed_jobs + batch.failed_jobs
    if done >= batch.total_jobs:
        if batch.failed_jobs == 0:
            batch.status = "completed"
        elif batch.completed_jobs == 0:
            batch.status = "failed"
        else:
            batch.status = "partial"
    elif done > 0:
        batch.status = "running"
    batch.save(
        update_fields=[
            "status",
            "completed_jobs",
            "failed_jobs",
            "updated_at",
        ],
    )
