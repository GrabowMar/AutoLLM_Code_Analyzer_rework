"""Contracts the web process uses to hand work to the executor (spec §C.1, §C.9).

Frozen contract (Foundation) → Track A. Implemented: :func:`wake`,
:func:`enqueue_jobs`, :func:`request_reeval`. :func:`cancel_jobs` delegates to
``execution.transitions.cancel_jobs`` (Track A).

The web process never executes jobs: it writes rows and rings the
``executor:wake`` doorbell after commit. Redis carries doorbells only, so a
Redis outage costs at most one executor tick of latency.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import F
from django.db.models import Func
from django.db.models import JSONField
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone

if TYPE_CHECKING:
    import redis

    from backend.generation.models import GenerationJob

logger = logging.getLogger(__name__)

WAKE_KEY = "executor:wake"


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    import redis
    from django.conf import settings

    return redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


def wake() -> None:
    """Ring the executor doorbell: ``LPUSH executor:wake 1`` + ``LTRIM 0 0`` + ``EXPIRE 60``. Never raises."""
    try:
        pipe = _redis().pipeline()
        pipe.lpush(WAKE_KEY, 1)
        pipe.ltrim(WAKE_KEY, 0, 0)
        pipe.expire(WAKE_KEY, 60)
        pipe.execute()
    except Exception:  # noqa: BLE001 — the executor's 2 s tick still runs
        logger.warning("executor wake failed (Redis unavailable?)", exc_info=True)


def _lane_for(job: GenerationJob) -> str:
    from backend.generation.models import GenerationJob

    return GenerationJob.Lane.SANDBOX if job.mode == GenerationJob.Mode.AGENT else GenerationJob.Lane.LLM


def enqueue_jobs(jobs: Iterable[GenerationJob]) -> int:
    """Hand saved PENDING jobs to the executor; idempotent. Returns the number of rows updated.

    Sets ``queued_at`` (only if NULL), ``lane`` (agent → sandbox, else llm),
    ``endpoint`` (denormalized from ``model.endpoint``; NULL = OpenRouter),
    ``priority`` (the experiment's, when the job has one and none is set) and
    ``sequence`` (kept when already set by launch; ad-hoc jobs get their index
    order) in one bulk UPDATE, then ``transaction.on_commit(wake)``.
    """
    from backend.generation.models import GenerationJob

    jobs = list(jobs)
    if not jobs:
        return 0
    now = timezone.now()
    model_endpoints = dict(
        GenerationJob.objects.filter(pk__in=[j.pk for j in jobs], model__isnull=False).values_list(
            "pk",
            "model__endpoint_id",
        ),
    )
    experiment_priority: dict[Any, int] = {}
    experiment_ids = {j.experiment_id for j in jobs if j.experiment_id}
    if experiment_ids:
        from backend.generation.models import Experiment

        experiment_priority = dict(Experiment.objects.filter(pk__in=experiment_ids).values_list("pk", "priority"))

    for index, job in enumerate(jobs):
        if job.queued_at is None:
            job.queued_at = now
        job.lane = _lane_for(job)
        job.endpoint_id = model_endpoints.get(job.pk)
        if job.experiment_id and not job.priority:
            job.priority = experiment_priority.get(job.experiment_id, 0)
        if job.sequence is None:
            job.sequence = index
    GenerationJob.objects.bulk_update(
        jobs,
        ["queued_at", "lane", "endpoint", "priority", "sequence"],
        batch_size=500,
    )
    transaction.on_commit(wake)
    return len(jobs)


def request_reeval(job: GenerationJob) -> bool:
    """Queue a manual re-evaluation: ``RUNNING/eval_queued`` with ``execution.reeval``.

    Sets ``queued_at = coalesce(queued_at, now())`` (so legacy jobs are
    counted), stores ``execution.reeval = true`` and
    ``execution.reeval_prev_status``, and ``stage_entered_at = now``. A
    re-evaluation ends in the previous status, never FAILED (§C.4 step 2).
    Returns False when the job is active (pending/running) and nothing changed.
    """
    from backend.generation.models import GenerationJob

    now = timezone.now()
    prev_status = job.status
    patch = {"reeval": True, "reeval_prev_status": prev_status}
    json_field = JSONField()
    updated = (
        GenerationJob.objects.filter(pk=job.pk)
        .exclude(status__in=[GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING])
        .update(
            status=GenerationJob.Status.RUNNING,
            phase=GenerationJob.Phase.EVAL_QUEUED,
            queued_at=Coalesce(F("queued_at"), Value(now)),
            stage_entered_at=now,
            lease_owner="",
            lease_expires_at=None,
            execution=Func(
                Coalesce(F("execution"), Value({}, output_field=json_field)),
                Value(patch, output_field=json_field),
                template="(%(expressions)s)",
                arg_joiner=" || ",
                output_field=json_field,
            ),
        )
    )
    if not updated:
        return False
    job.refresh_from_db()
    transaction.on_commit(wake)
    return True


def cancel_jobs(qs, scope: str = "all") -> dict[str, int]:
    """Cancel jobs (spec §C.8): see ``execution.transitions.cancel_jobs``."""
    from backend.execution.transitions import cancel_jobs as _cancel_jobs

    return _cancel_jobs(qs, scope=scope)
