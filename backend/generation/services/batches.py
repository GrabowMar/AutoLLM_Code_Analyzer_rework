"""Batch status, counts and summaries, derived from the batch's live job rows.

A batch's stored ``status`` / ``total_jobs`` / ``completed_jobs`` /
``failed_jobs`` columns are a cache (:func:`refresh_batch`); what the API
reports is recomputed from the jobs, so retries, deletions, cancellations
and experiment top-ups can never leave a batch stuck "pending":

* counts: ``pending``, ``queued`` (RUNNING, waiting for a sandbox slot),
  ``running`` (RUNNING otherwise, evaluation included), ``completed``,
  ``failed``, ``cancelled``;
* terminal = nothing pending, queued or running;
* status: ``pending`` (nothing started) / ``running`` while active; once
  terminal ``completed`` (all completed, or no jobs), ``cancelled`` (all
  cancelled), ``failed`` (nothing completed) or ``partial``.

Summaries for a page of batches come from one grouped query over their jobs
(plus one lookup each for the harness presets and experiments named).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from typing import Any

from django.contrib.postgres.aggregates import ArrayAgg
from django.db import transaction
from django.db.models import Avg
from django.db.models import Count
from django.db.models import Exists
from django.db.models import Max
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Sum
from django.utils import timezone

from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import RunPreset
from backend.generation.services.job_listing import cost_expression
from backend.generation.services.job_listing import harness_expression
from backend.generation.services.job_listing import pass_rate_expression

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from django.db.models import QuerySet

COUNT_KEYS = ("pending", "queued", "running", "completed", "failed", "cancelled")
ACTIVE_KEYS = ("pending", "queued", "running")
ACTIVE_JOB_STATUSES = (GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING)

_COUNT_FILTERS = {
    "pending": Q(status=GenerationJob.Status.PENDING),
    "queued": Q(status=GenerationJob.Status.RUNNING, phase=GenerationJob.Phase.QUEUED),
    "running": Q(status=GenerationJob.Status.RUNNING) & ~Q(phase=GenerationJob.Phase.QUEUED),
    "completed": Q(status=GenerationJob.Status.COMPLETED),
    "failed": Q(status=GenerationJob.Status.FAILED),
    "cancelled": Q(status=GenerationJob.Status.CANCELLED),
}


def empty_counts() -> dict[str, int]:
    return dict.fromkeys(COUNT_KEYS, 0)


def is_terminal(counts: dict[str, int]) -> bool:
    return not any(counts[key] for key in ACTIVE_KEYS)


def derive_status(counts: dict[str, int]) -> str:
    total = sum(counts[key] for key in COUNT_KEYS)
    if not is_terminal(counts):
        started = total - counts["pending"]
        return GenerationBatch.Status.RUNNING if started else GenerationBatch.Status.PENDING
    if counts["completed"] == total:
        return GenerationBatch.Status.COMPLETED
    if counts["cancelled"] == total:
        return GenerationBatch.Status.CANCELLED
    if counts["completed"] == 0:
        return GenerationBatch.Status.FAILED
    return GenerationBatch.Status.PARTIAL


def _jobs_of(batch_ids: Iterable[UUID]) -> QuerySet[GenerationJob]:
    # order_by(): no Meta.ordering column may leak into the GROUP BY.
    return GenerationJob.objects.filter(batch_id__in=list(batch_ids)).order_by().values("batch_id")


def _counts(row: dict[str, Any]) -> dict[str, int]:
    return {key: int(row.get(key) or 0) for key in COUNT_KEYS}


def batch_job_counts(batch_ids: Iterable[UUID]) -> dict[UUID, dict[str, int]]:
    rows = _jobs_of(batch_ids).annotate(**{k: Count("id", filter=f) for k, f in _COUNT_FILTERS.items()})
    return {row["batch_id"]: _counts(row) for row in rows}


def refresh_batch(batch: GenerationBatch) -> dict[str, int]:
    """Recompute *batch*'s cached columns from its jobs (row-locked, so concurrent job ends serialize)."""
    with transaction.atomic():
        GenerationBatch.objects.select_for_update().filter(id=batch.id).values_list("id", flat=True).first()
        counts = batch_job_counts([batch.id]).get(batch.id, empty_counts())
        batch.status = derive_status(counts)
        batch.total_jobs = sum(counts.values())
        batch.completed_jobs = counts["completed"]
        batch.failed_jobs = counts["failed"]
        batch.updated_at = timezone.now()
        GenerationBatch.objects.filter(id=batch.id).update(
            status=batch.status,
            total_jobs=batch.total_jobs,
            completed_jobs=batch.completed_jobs,
            failed_jobs=batch.failed_jobs,
            updated_at=batch.updated_at,
        )
    return counts


def cancel_batch(batch: GenerationBatch) -> list[GenerationJob]:
    """Cancel every pending / queued / running job of *batch*; returns the jobs cancelled.

    Runs notice the cancel themselves (queued agent runs within seconds,
    sandboxed runs at the next heartbeat; template-mode LLM calls finish
    first but keep the cancelled status).
    """
    now = timezone.now()
    with transaction.atomic():
        jobs = list(
            batch.jobs.select_for_update()
            .filter(status__in=ACTIVE_JOB_STATUSES)
            .only("id", "status", "phase", "started_at", "completed_at"),
        )
        GenerationJob.objects.filter(id__in=[job.id for job in jobs]).update(
            status=GenerationJob.Status.CANCELLED,
            phase=GenerationJob.Phase.NONE,
            updated_at=now,
        )
    for job in jobs:
        job.status = GenerationJob.Status.CANCELLED
        job.phase = GenerationJob.Phase.NONE
    refresh_batch(batch)
    return jobs


# ── summaries ─────────────────────────────────────────────────────────


def experiment_batch_q() -> Q:
    """Batches launched from an experiment (its jobs say so; ``request.experiment_id`` covers empty launches)."""
    has_experiment_job = Exists(
        GenerationJob.objects.filter(batch_id=OuterRef("pk"), experiment_id__isnull=False),
    )
    return Q(has_experiment_job) | Q(request__has_key="experiment_id")


def _aggregates(batch_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    rows = _jobs_of(batch_ids).annotate(
        **{k: Count("id", filter=f) for k, f in _COUNT_FILTERS.items()},
        model_count=Count("model_id", distinct=True),
        app_count=Count("app_requirement_id", distinct=True),
        max_repeat=Max("repeat_index"),
        cost_usd=Sum(cost_expression()),
        mean_pass_rate=Avg(pass_rate_expression()),
        harnesses=ArrayAgg(harness_expression(), distinct=True, filter=Q(mode=GenerationJob.Mode.AGENT)),
        stacks=ArrayAgg("stack_slug", distinct=True),
        harness_preset_ids=ArrayAgg("harness_preset_id", distinct=True, filter=Q(harness_preset_id__isnull=False)),
        experiment_ids=ArrayAgg("experiment_id", distinct=True, filter=Q(experiment_id__isnull=False)),
    )
    return {row["batch_id"]: row for row in rows}


def _ordered(values: Iterable[Any], preferred: Iterable[Any]) -> list[Any]:
    """*values* in the order of *preferred* (what the request listed), the rest sorted after."""
    values = set(values)
    head = [v for v in dict.fromkeys(preferred) if v in values]
    return head + sorted(values - set(head), key=str)


def _summary(
    batch: GenerationBatch,
    agg: dict[str, Any] | None,
    presets: dict[int, dict[str, Any]],
    experiments: dict[UUID, Experiment],
) -> dict[str, Any]:
    agg = agg or {}
    request = batch.request if isinstance(batch.request, dict) else {}
    counts = _counts(agg)

    stacks = sorted({s for s in agg.get("stacks") or [] if s})
    stack_slug = request.get("stack_slug") or (stacks[0] if len(stacks) == 1 else None)

    harnesses = _ordered((h for h in agg.get("harnesses") or [] if h), request.get("harnesses") or [])
    preset_ids = _ordered(
        (pid for pid in agg.get("harness_preset_ids") or [] if pid in presets),
        request.get("harness_preset_ids") or [],
    )

    candidates = [_request_experiment_id(batch), *(agg.get("experiment_ids") or [])]
    experiment = next((experiments[e] for e in candidates if e in experiments), None)

    trials = request.get("trials")
    if trials is None and agg.get("max_repeat") is not None:
        trials = agg["max_repeat"] + 1
    base_seed = request.get("base_seed")
    if base_seed is None and experiment is not None:
        base_seed = experiment.base_seed
    mean = agg.get("mean_pass_rate")

    return {
        "id": batch.id,
        "name": batch.name,
        "mode": batch.mode,
        "status": derive_status(counts),
        "total_jobs": sum(counts.values()),
        "counts": counts,
        "is_terminal": is_terminal(counts),
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "stack_slug": stack_slug,
        "harnesses": harnesses,
        "harness_presets": [presets[pid] for pid in preset_ids],
        "model_count": int(agg.get("model_count") or 0),
        "app_count": int(agg.get("app_count") or 0),
        "trials": trials,
        "base_seed": base_seed,
        "cost_usd": round(float(agg.get("cost_usd") or 0.0), 6),
        # Only app specs carry the hidden conformance suite.
        "graded": bool(agg.get("app_count")),
        "mean_pass_rate": round(float(mean), 4) if mean is not None else None,
        "experiment": {"id": experiment.id, "name": experiment.name} if experiment is not None else None,
        # What the batch was created from (seed, presets, apps, models, overrides):
        # lets a client set up the same grid again.
        "request": request,
    }


def _request_experiment_id(batch: GenerationBatch) -> UUID | None:
    request = batch.request if isinstance(batch.request, dict) else {}
    try:
        return uuid.UUID(str(request["experiment_id"])) if request.get("experiment_id") else None
    except ValueError:
        return None


def batch_summaries(batches: Iterable[GenerationBatch]) -> list[dict[str, Any]]:
    """``BatchSummary`` dicts for *batches* (one grouped job query for all of them)."""
    batches = list(batches)
    aggregates = _aggregates([b.id for b in batches])

    preset_ids = {pid for agg in aggregates.values() for pid in agg.get("harness_preset_ids") or []}
    presets = {row["id"]: row for row in RunPreset.objects.filter(id__in=preset_ids).values("id", "slug", "version")}

    experiment_ids = {eid for agg in aggregates.values() for eid in agg.get("experiment_ids") or []}
    experiment_ids |= {eid for b in batches if (eid := _request_experiment_id(b)) is not None}
    experiments = {
        row.id: row for row in Experiment.objects.filter(id__in=experiment_ids).only("id", "name", "base_seed")
    }
    return [_summary(b, aggregates.get(b.id), presets, experiments) for b in batches]


def batch_summary(batch: GenerationBatch) -> dict[str, Any]:
    return batch_summaries([batch])[0]
