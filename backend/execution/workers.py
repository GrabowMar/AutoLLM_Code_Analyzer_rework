"""Worker entry points run in the executor's pools (spec §C.1, §C.2; v3 D6).

Every worker registers its run in the in-process :class:`JobRegistry` as its
first statement and removes it in its ``finally`` (the heartbeat renews only
registered runs; the lost-run check requeues claimed runs that never got
here), and closes its DB connection on the way out, so idle threads hold no
Postgres connection.

The generation stage of every kind goes through ``agentic.dispatch.run_stage``
(WP-08) and evaluation through ``evaluation.suite.run_eval_stage`` (WP-09); both
end their stage with ``execution.transitions.finish_stage`` / ``infra_retry``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.execution import transitions
from backend.execution.transitions import LeaseLost
from backend.generation.models import GenerationRun
from backend.generation.models import StudyPostRun
from backend.generation.models.choices import PostRunTrigger
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import WorkStatus

if TYPE_CHECKING:
    from backend.execution.registry import JobRegistry
    from backend.execution.scheduler import Claimed

logger = logging.getLogger(__name__)

# A post-run triggered by completion waits until its study has finished.
_FINISHED_STUDY = ("completed", "stopped")


@dataclass(frozen=True)
class WorkerContext:
    executor_id: str
    registry: JobRegistry


def close_db() -> None:
    """Close this thread's DB connection (never inside an atomic block: tests run workers inline)."""
    if not connection.in_atomic_block:
        connection.close()


def _load(claimed: Claimed, executor_id: str) -> GenerationRun | None:
    return (
        GenerationRun.objects.select_related("model__endpoint", "created_by", "study", "cell", "task_version")
        .filter(id=claimed.run_id, attempt=claimed.attempt, lease_owner=executor_id)
        .first()
    )


def run_generation_stage(run: GenerationRun) -> None:
    """The generation stage of ``run`` (every generation kind): ``agentic.dispatch.run_stage``."""
    from backend.generation.agentic.dispatch import run_stage

    run_stage(run)


def run_evaluation_stage(run: GenerationRun) -> None:
    """The evaluation stage of ``run`` (claimed by phase ``eval_queued``): ``evaluation.suite.run_eval_stage``."""
    from backend.generation.evaluation.suite import run_eval_stage

    run_eval_stage(run)


def run_job(ctx: WorkerContext, claimed: Claimed) -> None:
    """Run one claimed run's stage: generation (sandbox / llm lane) or evaluation (eval lane)."""
    entry = ctx.registry.add(claimed.run_id, claimed.attempt, claimed.lane)
    try:
        run = _load(claimed, ctx.executor_id)
        if run is None:
            return  # reclaimed or requeued meanwhile; nothing of ours to run
        if run.status != RunStatus.RUNNING:
            transitions._release(run)  # noqa: SLF001 — cancelled before the worker started
            return
        if claimed.lane == "eval":
            run_evaluation_stage(run)
        else:
            run_generation_stage(run)
    except LeaseLost:
        logger.info("Run %s attempt %s: lease lost; worker stops", claimed.run_id, claimed.attempt)
    except Exception:
        # The lease stays: the lost-run check requeues the run (executor_lost) once we leave the registry.
        logger.exception("Worker for run %s (%s) crashed", claimed.run_id, claimed.lane)
    finally:
        ctx.registry.remove(claimed.run_id, entry)  # never another stage's registration of this run
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


# ── post-runs (analysis then report, or pattern analysis) ─────────────


def claimable_post_runs():
    """Post-runs the post-run pool may start: pending ones that are due, and running ones whose lease expired.

    A ``completed``-triggered post-run (attached at launch) is due once its study is completed or
    stopped; manual and scheduled ones are due at once. An expired lease means an executor restart
    interrupted the post-run.
    """
    now = timezone.now()
    due = Q(status=WorkStatus.PENDING) & (~Q(trigger=PostRunTrigger.COMPLETED) | Q(study__status__in=_FINISHED_STUDY))
    expired = Q(status=WorkStatus.RUNNING) & (Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True))
    return StudyPostRun.objects.filter(due | expired)


def claim_post_run(executor_id: str) -> StudyPostRun | None:
    """Lease the oldest claimable post-run to ``executor_id`` (``FOR UPDATE SKIP LOCKED``)."""
    now = timezone.now()
    lease = timedelta(seconds=settings.EXECUTOR_LEASE_SECONDS)
    with transaction.atomic():
        post_run = (
            claimable_post_runs().select_for_update(skip_locked=True, of=("self",)).order_by("created_at").first()
        )
        if post_run is None:
            return None
        post_run.status = WorkStatus.RUNNING
        post_run.lease_owner = executor_id
        post_run.lease_expires_at = now + lease
        post_run.started_at = post_run.started_at or now
        post_run.save(update_fields=["status", "lease_owner", "lease_expires_at", "started_at"])
    return post_run


def _unclaim_post_run(post_run: StudyPostRun, executor_id: str) -> None:
    StudyPostRun.objects.filter(pk=post_run.pk, lease_owner=executor_id, status=WorkStatus.RUNNING).update(
        status=WorkStatus.PENDING,
        lease_owner="",
        lease_expires_at=None,
    )


def run_post_run(ctx: WorkerContext) -> None:
    """Claim and run one study post-run (``study.automation.run_post_run``, WP-10)."""
    from backend.generation.study.automation import run_post_run as execute

    post_run = None
    try:
        post_run = claim_post_run(ctx.executor_id)
        if post_run is None:
            return
        execute(post_run)
    except NotImplementedError:
        logger.debug("study.automation.run_post_run is not implemented yet")
        if post_run is not None:
            _unclaim_post_run(post_run, ctx.executor_id)
    except Exception:
        logger.exception("Post-run %s failed", getattr(post_run, "pk", None))
    finally:
        close_db()


def run_due_schedules() -> None:
    """Fire the due study schedules (``study.automation.fire_due_schedules``, WP-10) on the schedule pool."""
    try:
        from backend.generation.study.automation import fire_due_schedules

        fire_due_schedules(timezone.now())
    except NotImplementedError:
        logger.debug("study.automation.fire_due_schedules is not implemented yet")
    except Exception:
        logger.exception("Firing due schedules failed")
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
