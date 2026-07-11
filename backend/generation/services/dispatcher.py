"""Job dispatcher — schedules generation jobs for execution.

Generation jobs are dispatched in-process via daemon threads. This avoids the
need for a Celery worker for synchronous-ish background execution. Do NOT also
call ``run_generation_job.delay()`` for the same job — doing so will cause the
job to execute twice.
"""

from backend.common.threading import dispatch_in_thread
from backend.generation.models import GenerationJob


def dispatch_job(job: GenerationJob) -> None:
    """Run a generation job in a daemon thread (no Celery worker required)."""
    from backend.generation.services.orchestrator import GenerationService

    job_id = job.id

    def _run() -> None:
        service = GenerationService()
        service.execute(
            GenerationJob.objects.select_related(
                "model",
                "created_by",
                "app_requirement",
                "batch",
            ).get(id=job_id),
        )

    dispatch_in_thread(_run)
