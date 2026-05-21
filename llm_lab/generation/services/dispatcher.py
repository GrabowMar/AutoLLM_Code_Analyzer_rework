"""Job dispatcher — schedules generation jobs for execution.

Generation jobs are dispatched in-process via daemon threads. This avoids the
need for a Celery worker for synchronous-ish background execution. Do NOT also
call ``run_generation_job.delay()`` for the same job — doing so will cause the
job to execute twice.
"""

from llm_lab.common.threading import dispatch_in_thread
from llm_lab.generation.models import GenerationJob


def dispatch_job(job: GenerationJob) -> None:
    """Run a generation job in a daemon thread (no Celery worker required)."""
    from llm_lab.generation.services.orchestrator import GenerationService

    job_id = job.id

    def _run() -> None:
        service = GenerationService()
        service.execute(
            GenerationJob.objects.select_related(
                "model",
                "app_requirement",
                "scaffolding_template",
                "backend_prompt_template",
                "frontend_prompt_template",
                "batch",
            ).get(id=job_id),
        )

    dispatch_in_thread(_run)
