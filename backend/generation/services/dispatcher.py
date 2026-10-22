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
    if job.mode == GenerationJob.Mode.AGENT:
        # Claim the FIFO position now, in dispatch order: threads start in an
        # arbitrary order, and experiments rely on dispatch order = run order.
        from backend.generation.services.agent.runner import run_queue

        run_queue().reserve(str(job_id))

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


def dispatch_evaluation(job: GenerationJob) -> None:
    """(Re-)evaluate a finished job on a daemon thread, through the sandbox queue.

    Evaluation builds and runs the app on the apps daemon, so it takes a run
    slot like an agent run does instead of competing with one for memory.
    """
    from backend.generation.services.agent.runner import run_queue
    from backend.generation.services.evaluation.runner import evaluate_job

    key = f"{job.id}:evaluate"
    queue = run_queue()
    queue.reserve(key)
    job_id = job.id

    def _run() -> None:
        queue.enter(key, lambda: False)
        try:
            evaluate_job(GenerationJob.objects.select_related("app_requirement").get(id=job_id))
        finally:
            queue.leave(key)

    dispatch_in_thread(_run)
