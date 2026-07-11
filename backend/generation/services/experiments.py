"""Expand, preview, launch, and report on :class:`Experiment` runs.

An experiment's matrix is ``conditions x app_requirements x repeats``. Each
cell becomes one :class:`GenerationJob` (mode=scaffolding), reusing the same
snapshot/dispatch machinery as an ad-hoc scaffolding batch
(``api/views/custom.py:create_scaffolding_jobs``) so nothing downstream
(analysis, runtime, reports) needs to know an experiment was involved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import derive_experiment_seed
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.runtime.services.scaffolding import canonical_stack_slug

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from backend.generation.models import AppRequirementTemplate
    from backend.generation.models import ExperimentCondition

logger = logging.getLogger(__name__)

# Rough heuristic for a pre-generation cost estimate: chars/token ratio for
# prompt sizing, and a typical completion length assumption. Neither is
# billing-accurate — this is a "does this look reasonable" preview, not
# an invoice.
_CHARS_PER_TOKEN_ESTIMATE = 4
_TYPICAL_COMPLETION_TOKENS = 2500
_STAGES_PER_JOB = 2  # backend + frontend


@dataclass(frozen=True)
class MatrixCell:
    condition: ExperimentCondition
    app_requirement: AppRequirementTemplate
    repeat_index: int


def expand_matrix(experiment: Experiment) -> list[MatrixCell]:
    """Every (condition, app, repeat) cell the experiment's config implies."""
    conditions = list(experiment.conditions.select_related("profile", "model"))
    apps = list(experiment.app_requirements.all())
    return [
        MatrixCell(condition=condition, app_requirement=app, repeat_index=repeat)
        for condition in conditions
        for app in apps
        for repeat in range(experiment.repeats)
    ]


def _condition_param(condition: ExperimentCondition, experiment: Experiment, key: str) -> Any:
    overrides = condition.param_overrides or {}
    if key in overrides:
        return overrides[key]
    return getattr(experiment, key)


def _estimate_cell_cost(cell: MatrixCell) -> float:
    model = cell.condition.model
    if not model.input_price_per_token and not model.output_price_per_token:
        return 0.0
    # Rough prompt-size proxy: app spec size scales with requirement count.
    app = cell.app_requirement
    requirement_chars = sum(
        len(r)
        for r in (app.backend_requirements or []) + (app.frontend_requirements or []) + (app.admin_requirements or [])
    )
    prompt_tokens = max(500, requirement_chars // _CHARS_PER_TOKEN_ESTIMATE) * _STAGES_PER_JOB
    completion_tokens = _TYPICAL_COMPLETION_TOKENS * _STAGES_PER_JOB
    return OpenRouterClient.estimate_cost(
        prompt_tokens,
        completion_tokens,
        model.input_price_per_token,
        model.output_price_per_token,
    )


def preview_experiment(experiment: Experiment) -> dict[str, Any]:
    """Matrix size + rough cost estimate, without creating any jobs."""
    cells = expand_matrix(experiment)
    conditions = list(experiment.conditions.select_related("profile", "model"))
    apps = list(experiment.app_requirements.all())

    per_condition: list[dict[str, Any]] = []
    total_cost = 0.0
    for condition in conditions:
        cost = sum(_estimate_cell_cost(c) for c in cells if c.condition == condition)
        total_cost += cost
        per_condition.append(
            {
                "condition_id": condition.id,
                "label": condition.label or f"{condition.model.model_name} / {condition.profile.slug}",
                "model": condition.model.model_name,
                "bundle_slug": condition.profile.slug,
                "bundle_version": condition.profile.version,
                "jobs": len(apps) * experiment.repeats,
                "estimated_cost": round(cost, 4),
            },
        )

    return {
        "total_jobs": len(cells),
        "conditions": len(conditions),
        "apps": len(apps),
        "repeats": experiment.repeats,
        "estimated_cost": round(total_cost, 4),
        "per_condition": per_condition,
    }


def _existing_cell_job_ids(experiment: Experiment) -> set[tuple[int, int, int]]:
    """(condition_id, app_id, repeat_index) already covered by a non-failed job."""
    rows = experiment.jobs.exclude(status=GenerationJob.Status.FAILED).values_list(
        "condition_id",
        "app_requirement_id",
        "repeat_index",
    )
    return {(c, a, r) for c, a, r in rows if c is not None and a is not None and r is not None}


def launch_experiment(experiment: Experiment, user: AbstractUser) -> GenerationBatch:
    """Create jobs for every matrix cell lacking a non-failed job, and dispatch them.

    Idempotent/resumable: re-invoking after partial failures only creates
    jobs for the cells that still need one, so retrying a failed launch (or
    topping up after a failure) never duplicates completed work.
    """
    cells = expand_matrix(experiment)
    covered = _existing_cell_job_ids(experiment)
    pending_cells = [c for c in cells if (c.condition.id, c.app_requirement.id, c.repeat_index) not in covered]

    batch = GenerationBatch.objects.create(
        name=f"Experiment: {experiment.name}",
        mode="scaffolding",
        total_jobs=len(cells),
        created_by=user,
    )

    created_jobs: list[GenerationJob] = []
    failed_count = 0
    for cell in pending_cells:
        condition = cell.condition
        app_req = cell.app_requirement
        temperature = _condition_param(condition, experiment, "temperature")
        max_tokens = _condition_param(condition, experiment, "max_tokens")
        top_p = _condition_param(condition, experiment, "top_p")

        job = GenerationJob.objects.create(
            mode=GenerationJob.Mode.SCAFFOLDING,
            created_by=user,
            batch=batch,
            experiment=experiment,
            condition=condition,
            repeat_index=cell.repeat_index,
            model=condition.model,
            stack_slug=canonical_stack_slug(condition.profile.scaffolding_slug),
            app_requirement=app_req,
            profile=condition.profile,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )
        if experiment.base_seed is not None:
            job.experiment_seed = derive_experiment_seed(
                experiment.base_seed,
                condition.id,
                app_req.id,
                cell.repeat_index,
            )

        try:
            apply_snapshot_to_job(job)
        except Exception as exc:  # noqa: BLE001 — isolate one bad cell from the launch
            logger.warning("Snapshot build failed for experiment job %s: %s", job.id, exc)
            job.status = GenerationJob.Status.FAILED
            job.error_message = f"Snapshot build failed: {exc}"
            job.save(update_fields=["status", "error_message", "updated_at"])
            failed_count += 1
            continue
        created_jobs.append(job)

    if failed_count:
        batch.failed_jobs = failed_count
        batch.save(update_fields=["failed_jobs", "updated_at"])

    if experiment.status == Experiment.Status.DRAFT:
        experiment.status = Experiment.Status.RUNNING
        experiment.save(update_fields=["status", "updated_at"])

    for job in created_jobs:
        dispatch_job(job)

    return batch


def experiment_status(experiment: Experiment) -> dict[str, Any]:
    """Progress grid (per condition x app) and running cost."""
    jobs = list(
        experiment.jobs.select_related("condition", "app_requirement").exclude(condition__isnull=True),
    )
    total_cells = len(expand_matrix(experiment))

    by_status: dict[str, int] = {}
    running_cost = 0.0
    for job in jobs:
        by_status[job.status] = by_status.get(job.status, 0) + 1
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        running_cost += float(metrics.get("cost") or 0.0)

    grid: dict[str, dict[str, Any]] = {}
    for job in jobs:
        key = f"{job.condition_id}:{job.app_requirement_id}"
        cell = grid.setdefault(key, {"condition_id": job.condition_id, "app_id": job.app_requirement_id, "runs": []})
        cell["runs"].append(
            {
                "job_id": str(job.id),
                "repeat_index": job.repeat_index,
                "status": job.status,
            },
        )

    return {
        "status": experiment.status,
        "total_cells": total_cells,
        "jobs_created": len(jobs),
        "by_status": by_status,
        "running_cost": round(running_cost, 4),
        "grid": list(grid.values()),
    }


def export_experiment(experiment: Experiment) -> dict[str, Any]:
    """Research export: experiment config + per-job summaries."""
    jobs = list(
        experiment.jobs.select_related(
            "condition",
            "condition__model",
            "condition__profile",
            "app_requirement",
        ),
    )
    return {
        "experiment": {
            "id": str(experiment.id),
            "name": experiment.name,
            "slug": experiment.slug,
            "description": experiment.description,
            "hypothesis": experiment.hypothesis,
            "status": experiment.status,
            "repeats": experiment.repeats,
            "base_seed": experiment.base_seed,
            "temperature": experiment.temperature,
            "max_tokens": experiment.max_tokens,
            "top_p": experiment.top_p,
            "continuation_limit": experiment.continuation_limit,
            "enable_repair": experiment.enable_repair,
            "created_at": experiment.created_at.isoformat(),
        },
        "conditions": [
            {
                "id": c.id,
                "label": c.label,
                "model": c.model.model_id,
                "bundle_slug": c.profile.slug,
                "bundle_version": c.profile.version,
                "param_overrides": c.param_overrides,
            }
            for c in experiment.conditions.select_related("model", "profile")
        ],
        "app_requirements": [a.slug for a in experiment.app_requirements.all()],
        "jobs": [
            {
                "id": str(job.id),
                "status": job.status,
                "condition_id": job.condition_id,
                "app_requirement_slug": job.app_requirement.slug if job.app_requirement else None,
                "repeat_index": job.repeat_index,
                "experiment_seed": job.experiment_seed,
                "prompt_hash": job.prompt_hash,
                "bundle_key": job.bundle_key,
                "metrics": job.metrics,
            }
            for job in jobs
        ],
    }
