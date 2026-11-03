"""Contracts the web process uses to hand work to the executor (spec §C.1, §C.9; v3 sections 3.7 and D6).

:func:`wake`, :func:`enqueue_runs`, :func:`enqueue_evaluation`, :func:`request_reeval` and
:func:`cancel_runs` (delegates to ``execution.transitions.cancel_runs``).

The web process never executes runs: it writes rows and rings the ``executor:wake`` doorbell after
commit. Redis carries doorbells only, so a Redis outage costs at most one executor tick of latency.
Callers enqueue in ``transaction.on_commit`` (launch, top-up, retry, evaluate, cancel).
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

    from backend.generation.models import GenerationRun

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


def wake_preview_build(action_id=None) -> None:
    """A BUILD ContainerAction is the durable queue; Redis is only its doorbell."""
    transaction.on_commit(wake)


def lane_for(run: GenerationRun) -> str:
    """The generation lane of ``run``, from its kind (section 3.7): agentic kinds use ``sandbox``, else ``llm``."""
    from backend.generation.models.choices import Lane
    from backend.generation.models.run import LANE_BY_KIND

    return LANE_BY_KIND.get(run.kind, Lane.LLM)


def _as_runs(runs: Iterable[GenerationRun | Any]) -> list[GenerationRun]:
    """Run objects for a mix of runs and ids (ids are loaded in one query; unknown ids are dropped)."""
    from backend.generation.models import GenerationRun

    items = list(runs)
    if all(isinstance(item, GenerationRun) for item in items):
        return items
    ids = [str(getattr(item, "pk", item)) for item in items]
    by_id = {str(run.pk): run for run in GenerationRun.objects.filter(pk__in=ids)}
    return [by_id[i] for i in ids if i in by_id]


def enqueue_runs(runs: Iterable[GenerationRun | Any]) -> int:
    """Hand saved PENDING runs (objects or ids) to the executor; idempotent. Returns the number of rows updated.

    Sets ``queued_at`` (only if NULL), ``lane`` (from ``run.kind``), ``endpoint`` (denormalized from
    ``model.endpoint``; NULL = OpenRouter), ``priority`` (the study's, when the run has one and none is
    set) and ``sequence`` (kept when set by launch; ad-hoc runs get their index order) in one bulk
    UPDATE, then ``transaction.on_commit(wake)``.
    """
    from backend.generation.models import GenerationRun
    from backend.generation.models import Study

    items = _as_runs(runs)
    if not items:
        return 0
    now = timezone.now()
    model_endpoints = dict(
        GenerationRun.objects.filter(pk__in=[r.pk for r in items], model__isnull=False).values_list(
            "pk",
            "model__endpoint_id",
        ),
    )
    study_ids = {r.study_id for r in items if r.study_id}
    study_priority: dict[Any, int] = {}
    if study_ids:
        study_priority = dict(Study.objects.filter(pk__in=study_ids).values_list("pk", "priority"))

    for index, run in enumerate(items):
        if run.queued_at is None:
            run.queued_at = now
        run.lane = lane_for(run)
        run.endpoint_id = model_endpoints.get(run.pk)
        if run.study_id and not run.priority:
            run.priority = study_priority.get(run.study_id, 0)
        if run.sequence is None:
            run.sequence = index
    GenerationRun.objects.bulk_update(
        items,
        ["queued_at", "lane", "endpoint", "priority", "sequence"],
        batch_size=500,
    )
    transaction.on_commit(wake)
    return len(items)


def enqueue_evaluation(runs: Iterable[GenerationRun | Any]) -> int:
    """Make saved runs (objects or ids) claimable evaluation work: ``status=running, phase=eval_queued``.

    For ``reference`` runs, which have no generation stage (D6, section 3.7): ``_EVAL_CLAIM_SQL`` claims
    them by phase like any other evaluation. Rows already in the generation stage or being evaluated,
    and rows a worker still holds, are left alone. Returns the number of rows updated.
    """
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import RunPhase
    from backend.generation.models.choices import RunStatus

    ids = [str(getattr(item, "pk", item)) for item in runs]
    if not ids:
        return 0
    now = timezone.now()
    updated = (
        GenerationRun.objects.filter(pk__in=ids, lease_owner="", status__in=[RunStatus.PENDING, RunStatus.RUNNING])
        .exclude(status=RunStatus.RUNNING, phase__in=[RunPhase.RUNNING, RunPhase.EVALUATING])
        .update(
            status=RunStatus.RUNNING,
            phase=RunPhase.EVAL_QUEUED,
            queued_at=Coalesce(F("queued_at"), Value(now)),
            stage_entered_at=Coalesce(F("stage_entered_at"), Value(now)),
            updated_at=now,
        )
    )
    if updated:
        transaction.on_commit(wake)
    return updated


def request_reeval(run: GenerationRun, *, force_rebuild: bool = False) -> bool:
    """Queue a manual re-evaluation: ``RUNNING/eval_queued`` with ``execution.reeval``.

    Sets ``queued_at = coalesce(queued_at, now())``, stores ``execution.reeval = true`` and
    ``execution.reeval_prev_status``, and ``stage_entered_at = now``. A re-evaluation ends in the
    previous status, never FAILED by itself (§C.4 step 2). Returns False when the run is active
    (pending/running) and nothing changed.
    """
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import RunPhase
    from backend.generation.models.choices import RunStatus

    now = timezone.now()
    patch = {"reeval": True, "reeval_prev_status": run.status, "force_rebuild": force_rebuild}
    json_field = JSONField()
    updated = (
        GenerationRun.objects.filter(pk=run.pk)
        .exclude(status__in=[RunStatus.PENDING, RunStatus.RUNNING])
        .update(
            status=RunStatus.RUNNING,
            phase=RunPhase.EVAL_QUEUED,
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
    run.refresh_from_db()
    transaction.on_commit(wake)
    return True


def cancel_runs(qs, scope: str = "all", *, study=None) -> dict[str, int]:
    """Cancel runs (spec §C.8): see ``execution.transitions.cancel_runs``."""
    from backend.execution.transitions import cancel_runs as _cancel_runs

    return _cancel_runs(qs, scope=scope, study=study)
