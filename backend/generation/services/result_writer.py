"""Persistence and realtime publishing helpers for generation jobs."""

from backend.generation.models import GenerationJob
from backend.generation.services.batches import refresh_batch
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


def publish_phase(job: GenerationJob, phase: str) -> None:
    """Persist and publish an agent-run phase change (``queued`` → ``running`` → ``evaluating``).

    Persisted so lists and batch counts can tell queued from running after
    a reload; only while the job is still RUNNING, so a cancel is never
    overwritten. The orchestrator clears the phase when the run ends.
    """
    job.phase = phase
    GenerationJob.objects.filter(id=job.id, status=GenerationJob.Status.RUNNING).update(phase=phase)
    realtime.publish(
        f"generation:{job.id}",
        {"type": "progress", "status": job.status, "phase": phase},
    )


def update_batch(job: GenerationJob) -> None:
    """Refresh the cached counters and status of the job's batch, if any."""
    if job.batch_id:
        refresh_batch(job.batch)
