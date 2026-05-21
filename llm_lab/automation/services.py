"""Automation business logic services."""

from __future__ import annotations

import copy
import logging
import threading
from datetime import UTC
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from llm_lab.automation.models import Pipeline
    from llm_lab.automation.models import PipelineRun
    from llm_lab.users.models import User

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _celery_available() -> bool:
    """Probe the Celery broker once at startup and cache the result."""
    try:
        from celery import current_app

        result = current_app.control.ping(timeout=1)
        return bool(result)
    except Exception:  # noqa: BLE001
        logger.warning("Celery broker unavailable — falling back to daemon threads")
        return False


_REQUIRED_STEP_FIELDS: dict[str, list[str]] = {
    "generate": ["model_id", "template_slug"],
    "analyze": ["job_ids"],
    "report": ["report_type"],
    "wait": ["seconds"],
    "notify": ["channel"],
    "script": ["code"],
}


def validate_pipeline_dsl(config: dict[str, Any]) -> list[str]:
    """Validate a pipeline DSL config. Returns a list of error strings."""
    errors: list[str] = []
    steps = config.get("steps", [])
    if not isinstance(steps, list):
        errors.append("'steps' must be a list")
        return errors
    step_ids: set[str] = set()
    for i, step in enumerate(steps):
        prefix = f"Step {i}"
        if not isinstance(step, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        if not step.get("name"):
            errors.append(f"{prefix}: 'name' is required")
        kind = step.get("kind")
        if not kind:
            errors.append(f"{prefix}: 'kind' is required")
        else:
            required = _REQUIRED_STEP_FIELDS.get(kind, [])
            step_config = step.get("config", {})
            errors.extend(  # PERF401
                f"{prefix} (kind={kind}): config.{field} is required"
                for field in required
                if not step_config.get(field)
            )
        step_id = step.get("id")
        if step_id:
            if step_id in step_ids:
                errors.append(f"{prefix}: duplicate step id '{step_id}'")
            step_ids.add(step_id)
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        errors.extend(  # PERF401
            f"Step {i}: depends_on references unknown step id '{dep}'"
            for dep in step.get("depends_on", [])
            if dep not in step_ids
        )
    return errors


def clone_pipeline(pipeline: Pipeline, new_name: str) -> Pipeline:
    """Deep-copy a pipeline and all its steps under a new name."""
    from llm_lab.automation.models import Pipeline as PipelineModel
    from llm_lab.automation.models import PipelineStep

    new_pipeline = PipelineModel.objects.create(
        owner=pipeline.owner,
        name=new_name,
        description=pipeline.description,
        version=1,
        status=PipelineModel.Status.DRAFT,
        config=copy.deepcopy(pipeline.config),
        tags=copy.deepcopy(pipeline.tags),
    )
    for step in pipeline.steps.order_by("order"):
        PipelineStep.objects.create(
            pipeline=new_pipeline,
            order=step.order,
            name=step.name,
            kind=step.kind,
            config=copy.deepcopy(step.config),
            depends_on=copy.deepcopy(step.depends_on),
        )
    return new_pipeline


def next_cron_time(expr: str, after: datetime | None = None) -> datetime:
    """Return the next scheduled datetime for a cron expression."""
    from croniter import croniter

    base = after if after is not None else datetime.now(UTC)
    return croniter(expr, base).get_next(datetime)


def trigger_run(pipeline: Pipeline, params: dict[str, Any], user: User) -> PipelineRun:
    """Create a pending PipelineRun and dispatch its execution.

    Dispatch strategy:
    1. Try Celery (``run_pipeline_task.delay``).
    2. Fall back to a daemon thread if broker is unreachable.
    """
    from llm_lab.automation.models import PipelineRun
    from llm_lab.automation.models import PipelineStep
    from llm_lab.automation.models import PipelineStepRun

    run = PipelineRun.objects.create(
        pipeline=pipeline,
        triggered_by=user,
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

    run_id_str = str(run.id)

    if _celery_available():
        from llm_lab.automation.tasks import run_pipeline_task

        run_pipeline_task.delay(run_id_str)
        logger.info("Dispatched run %s via Celery", run_id_str)
    else:
        import uuid

        from llm_lab.automation.engine.runner import execute_run

        t = threading.Thread(
            target=execute_run,
            args=(uuid.UUID(run_id_str),),
            daemon=True,
            name=f"pipeline-run-{run_id_str}",
        )
        t.start()
        logger.info(
            "Dispatched run %s via daemon thread (Celery unavailable)",
            run_id_str,
        )

    return run
