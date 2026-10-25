"""Persistence and realtime publishing helpers for generation jobs."""

from backend.generation.models import GenerationJob
from backend.generation.services.batches import refresh_batch
from backend.realtime import events as realtime


def publish_status(job: GenerationJob) -> None:
    """Publish a status update event for a job.

    ``generation:<id>`` gets the status event; a job of an experiment also
    emits the ``job`` event on ``experiment:<id>`` (spec §F.6).
    """
    timestamp = job.completed_at or job.started_at
    realtime.publish(
        f"generation:{job.id}",
        {
            "type": "status",
            "status": job.status,
            "updated_at": timestamp.isoformat() if timestamp else None,
        },
    )
    _publish_experiment_job(job)


def _publish_experiment_job(job: GenerationJob) -> None:
    if not getattr(job, "experiment_id", None):
        return
    from backend.execution.transitions import job_event

    realtime.publish_experiment_event(job.experiment_id, job_event(job))


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
    """Persist and publish a phase change of a running job (``running`` → ``evaluating``).

    The write is guarded (``execution.transitions.guarded_save``): it only
    lands while this worker still owns the job's current attempt, so a
    cancel or a requeue is never overwritten. A lost lease is ignored here;
    the worker notices it at its next guarded write or when it finishes.
    """
    from backend.execution.transitions import LeaseLost
    from backend.execution.transitions import guarded_save

    job.phase = phase
    try:
        guarded_save(job, ["phase"])
    except LeaseLost:
        return
    realtime.publish(
        f"generation:{job.id}",
        {"type": "progress", "status": job.status, "phase": phase},
    )
    _publish_experiment_job(job)


def update_batch(job: GenerationJob) -> None:
    """Refresh the cached counters and status of the job's batch, if any."""
    if job.batch_id:
        refresh_batch(job.batch)
