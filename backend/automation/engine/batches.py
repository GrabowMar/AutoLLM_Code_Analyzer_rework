"""Batch fan-out utilities.

``expand_batch(batch_id)``  — creates one PipelineRun + BatchItem per
matrix combination.
``update_batch_status(batch_id)`` — recomputes aggregate from item statuses.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from backend.automation.engine.runner import execute_run

logger = logging.getLogger(__name__)


def expand_batch(batch_id: Any) -> list[Any]:
    """Create PipelineRun + BatchItem for every matrix combination.

    The Batch.config must contain::

        {
            "pipeline_id": "<uuid>",
            "matrix": {
                "models": ["gpt-4", "claude-3"],
                "templates": ["todo-app", "chat-app"],
                ...
            },
            "repeats": 3,   # optional; independent runs per combination
        }

    With ``repeats`` > 1 each combination is expanded that many times and the
    runs get a 1-based ``trial`` param so they stay distinguishable.

    Returns list of PipelineRun IDs created.
    """
    import threading

    from backend.automation.models import Batch
    from backend.automation.models import BatchItem
    from backend.automation.models import Pipeline
    from backend.automation.models import PipelineRun
    from backend.automation.models import PipelineStep
    from backend.automation.models import PipelineStepRun

    batch = Batch.objects.get(id=batch_id)
    config = batch.config
    pipeline_id = config.get("pipeline_id")
    matrix = config.get("matrix", {})

    if not pipeline_id:
        msg = f"Batch {batch_id} has no pipeline_id in config"
        raise ValueError(msg)

    pipeline = Pipeline.objects.get(id=pipeline_id)

    # Build cartesian product
    keys = list(matrix.keys())
    values = [matrix[k] if isinstance(matrix[k], list) else [matrix[k]] for k in keys]
    combinations = list(itertools.product(*values))
    repeats = max(1, int(config.get("repeats", 1) or 1))

    run_ids: list[Any] = []
    batch.status = Batch.Status.RUNNING
    batch.save(update_fields=["status"])

    for combo, trial in itertools.product(combinations, range(1, repeats + 1)):
        params = dict(zip(keys, combo, strict=False))
        if repeats > 1:
            # Only with actual repetition, so single-run batches and existing
            # {{params.*}} references are untouched.
            params["trial"] = trial

        run = PipelineRun.objects.create(
            pipeline=pipeline,
            triggered_by=batch.owner,
            status="pending",
            params=params,
        )
        for step in PipelineStep.objects.filter(pipeline=pipeline).order_by("order"):
            PipelineStepRun.objects.create(
                run=run,
                step=step,
                status="pending",
                retries_remaining=step.config.get("max_retries", 0),
            )

        BatchItem.objects.create(
            batch=batch,
            pipeline_run=run,
            status=BatchItem.Status.PENDING,
            params=params,
        )
        run_ids.append(run.id)

    # Trigger execute_run for each run in a daemon thread
    for run_id in run_ids:
        t = threading.Thread(target=execute_run, args=(run_id,), daemon=True)
        t.start()

    logger.info("Batch %s expanded: %d runs launched", batch_id, len(run_ids))
    return run_ids


def update_batch_status(batch_id: Any) -> str:
    """Recompute Batch.status from its BatchItems. Returns new status string."""
    from backend.automation.models import Batch
    from backend.automation.models import BatchItem

    batch = Batch.objects.get(id=batch_id)
    items = list(BatchItem.objects.filter(batch=batch))

    if not items:
        new_status = Batch.Status.PENDING
    elif all(i.status == BatchItem.Status.SUCCEEDED for i in items):
        new_status = Batch.Status.SUCCEEDED
    elif any(i.status == BatchItem.Status.FAILED for i in items):
        new_status = Batch.Status.FAILED
    elif any(i.status == BatchItem.Status.RUNNING for i in items):
        new_status = Batch.Status.RUNNING
    elif all(i.status == BatchItem.Status.CANCELLED for i in items):
        new_status = Batch.Status.CANCELLED
    else:
        new_status = Batch.Status.RUNNING

    batch.status = new_status
    batch.save(update_fields=["status"])
    return new_status
