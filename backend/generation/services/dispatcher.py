"""Job dispatch: hand saved jobs to the executor (spec §C.9).

The web process never runs jobs. ``dispatch_job`` / ``enqueue_jobs`` mark
rows as executor-owned (``queued_at``, lane, endpoint, priority, sequence)
and ring the executor doorbell after the surrounding transaction commits;
the executor (``manage.py run_executor``) claims them in lane order.

The names stay: views and tests patch ``custom.dispatch_job`` and
``jobs.dispatch_job`` / ``jobs.dispatch_evaluation``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.execution import api as execution_api

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.generation.models import GenerationJob


def dispatch_job(job: GenerationJob) -> None:
    """Queue one saved job for the executor (idempotent; wakes it after commit)."""
    execution_api.enqueue_jobs([job])


def enqueue_jobs(jobs: Iterable[GenerationJob]) -> int:
    """Queue saved jobs in this order (ad-hoc jobs get their index as ``sequence``); returns the count."""
    return execution_api.enqueue_jobs(jobs)


def dispatch_evaluation(job: GenerationJob) -> bool:
    """Queue a manual (re-)evaluation of a finished job; it ends in the job's previous status."""
    return execution_api.request_reeval(job)
