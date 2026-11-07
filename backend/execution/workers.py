"""Worker entry points run in the executor's pools (spec §C.1, §C.2).

Every worker registers its job in the in-process :class:`JobRegistry` as its
first statement and removes it in its ``finally`` (the heartbeat renews only
registered jobs; the lost-job check requeues claimed jobs that never got
here), and closes its DB connection on the way out, so idle threads hold no
Postgres connection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import connection

from backend.execution import transitions
from backend.execution.transitions import LeaseLost
from backend.generation.models import GenerationJob

if TYPE_CHECKING:
    from backend.execution.registry import JobRegistry
    from backend.execution.scheduler import Claimed

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerContext:
    executor_id: str
    registry: JobRegistry


def close_db() -> None:
    """Close this thread's DB connection (never inside an atomic block: tests run workers inline)."""
    if not connection.in_atomic_block:
        connection.close()


def _load(claimed: Claimed, executor_id: str) -> GenerationJob | None:
    return (
        GenerationJob.objects.select_related("model__endpoint", "created_by", "app_requirement", "batch", "profile")
        .filter(id=claimed.job_id, attempt=claimed.attempt, lease_owner=executor_id)
        .first()
    )


def run_job(ctx: WorkerContext, claimed: Claimed) -> None:
    """Run one claimed job's stage: generation (sandbox / llm lane) or evaluation (eval lane)."""
    from backend.generation.services.orchestrator import GenerationService

    entry = ctx.registry.add(claimed.job_id, claimed.attempt, claimed.lane)
    try:
        job = _load(claimed, ctx.executor_id)
        if job is None:
            return  # reclaimed or requeued meanwhile; nothing of ours to run
        if job.status != GenerationJob.Status.RUNNING:
            transitions._release(job)  # noqa: SLF001 — cancelled before the worker started
            return
        if claimed.lane == "eval":
            GenerationService().evaluate(job)
        else:
            GenerationService().execute(job, claimed=True)
    except LeaseLost:
        logger.info("Job %s attempt %s: lease lost; worker stops", claimed.job_id, claimed.attempt)
    except Exception:
        # The lease stays: the lost-job check requeues the job (executor_lost) once we leave the registry.
        logger.exception("Worker for job %s (%s) crashed", claimed.job_id, claimed.lane)
    finally:
        ctx.registry.remove(claimed.job_id, entry)  # never another stage's registration of this job
        close_db()


# Names from the spec layout (§C.1); every lane goes through run_job.
run_llm_job = run_job
run_sandbox_job = run_job
run_eval = run_job


def run_preview_build(ctx: WorkerContext, action_id) -> None:
    """Build a preview on the eval pool, retaining its lease through cleanup."""
    from backend.execution import runtime_builds
    from backend.runtime.services import container_service

    entry = ctx.registry.add(action_id, 0, "preview")
    try:
        if runtime_builds.cancelled(action_id, ctx.executor_id):
            runtime_builds.submission_failed(action_id, ctx.executor_id, "build lease expired before work started")
            return
        container_service._execute(  # noqa: SLF001 — the executor owns BUILD action dispatch
            action_id,
            claimed=True,
            cancelled=lambda: runtime_builds.cancelled(action_id, ctx.executor_id),
        )
    except Exception:
        logger.exception("Preview worker %s failed", action_id)
        runtime_builds.submission_failed(action_id, ctx.executor_id, "worker exited unexpectedly")
    finally:
        try:
            runtime_builds.release(action_id, ctx.executor_id)
        finally:
            ctx.registry.remove(action_id, entry)
            close_db()


def run_post_run(ctx: WorkerContext) -> None:
    """Claim and run one pending experiment post-run (analyses, then the report)."""
    try:
        from backend.generation.services.experiment_automation import advance_post_runs

        advance_post_runs(ctx.executor_id)
    except NotImplementedError:
        logger.debug("advance_post_runs is not implemented yet")
    except Exception:
        logger.exception("Post-run failed")
    finally:
        close_db()


def run_schedule_action(schedule_id: int) -> None:
    """Fire one due experiment schedule (top up / add replicates / clone and run)."""
    try:
        from backend.generation.services.experiment_automation import run_schedule_action as fire

        fire(schedule_id)
    except NotImplementedError:
        logger.debug("run_schedule_action is not implemented yet")
    except Exception:
        logger.exception("Schedule %s failed", schedule_id)
    finally:
        close_db()


def cleanup_sample_images() -> None:
    """Run cache GC away from the leader loop and heartbeat."""
    try:
        from backend.runtime.services.sample_builds import cleanup_sample_images as cleanup

        cleanup()
    except Exception:
        logger.exception("Sample image cache cleanup failed")
    finally:
        close_db()
