"""Expand, preview, launch, and report on :class:`Experiment` runs.

An experiment's matrix is ``conditions x app_requirements x repeats``. Each
cell becomes one :class:`GenerationJob` in the experiment's mode -- ``agent``
(the default: the OpenCode harness builds the app in a sandbox) or
``scaffolding`` (template prompts) -- reusing the same snapshot/dispatch
machinery as ad-hoc batches (``api/views/custom.py``) so nothing downstream
(analysis, runtime, reports) needs to know an experiment was involved.

In agent mode a condition's profile contributes only its stack and
``llm_config``; its prompt blocks do not apply (the brief is rendered from the
app spec and is identical for every condition). A condition may also name a
``harness``, so (model x harness) designs are first-class.

Run order is a randomised complete block design: all cells of repeat 0 run
before any cell of repeat 1, and within a repeat the cells are shuffled with
a seed derived from the experiment. Without this, a condition-major order
would confound conditions with time (provider load and routing, alias
repointing, rate limits all drift over hours). The FIFO run queue keeps
dispatch order = execution order.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.models import Experiment
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.agent.config import default_provider_routing
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.snapshot import DEFAULT_MAX_OUTPUT_TOKENS
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import gateway_policy
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import derive_experiment_seed
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


def _condition_llm_params(condition: ExperimentCondition, experiment: Experiment) -> dict[str, Any]:
    """Experiment defaults + condition overrides, collapsed into one per-run layer."""
    return merge_llm_params(experiment.llm_defaults, condition.param_overrides)


def _condition_agent_config(condition: ExperimentCondition, experiment: Experiment) -> dict[str, Any]:
    """Experiment agent_config + the condition's harness choice."""
    config = dict(experiment.agent_config or {})
    if condition.harness:
        config["harness"] = condition.harness
    return config


def run_order(jobs: list[GenerationJob], experiment: Experiment) -> list[GenerationJob]:
    """Randomised complete blocks: repeat by repeat, cells shuffled within each repeat."""
    seed = experiment.base_seed if experiment.base_seed is not None else int(experiment.id.int % 2_147_483_647)
    rng = random.Random(seed)  # noqa: S311 - run-order randomisation, not crypto
    blocks: dict[int, list[GenerationJob]] = {}
    for job in sorted(jobs, key=lambda j: (j.repeat_index or 0, j.condition_id or 0, j.app_requirement_id or 0)):
        blocks.setdefault(job.repeat_index or 0, []).append(job)
    ordered: list[GenerationJob] = []
    for repeat in sorted(blocks):
        block = blocks[repeat]
        rng.shuffle(block)
        ordered.extend(block)
    return ordered


def comparability(experiment: Experiment) -> dict[str, Any]:
    """What each agent condition will actually run with, and where conditions differ.

    Differences are not errors (an ablation varies something on purpose), but
    unintended ones -- a param one model cannot take, a harness a model
    cannot drive -- must be visible before any money is spent.
    """
    rows: list[dict[str, Any]] = []
    for condition in experiment.conditions.select_related("profile", "model"):
        agent_config = merge_agent_config(_condition_agent_config(condition, experiment))
        harness = get_harness(agent_config["harness"])
        llm = {
            **(condition.profile.llm_config or {}),
            **_condition_llm_params(condition, experiment),
        }
        llm.setdefault("provider", default_provider_routing())
        cap = min(int(llm.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS), condition.model.max_output_tokens or 10**9)
        policy = gateway_policy(
            llm,
            experiment.base_seed if experiment.base_seed is not None else 0,
            condition.model,
            sampling_policy=agent_config["sampling_policy"],
            max_output_tokens=cap,
        )
        forced = {k: v for k, v in policy["forced_params"].items() if k not in ("seed", "provider")}
        problems = []
        if harness.requires_tool_calling and not condition.model.supports_function_calling:
            problems.append(f"{harness.label} needs tool calling; {condition.model.model_id} has none")
        problems += [f"{k} not applied: {why}" for k, why in policy["dropped_params"].items()]
        rows.append(
            {
                "condition_id": condition.id,
                "model": condition.model.model_id,
                "harness": harness.name,
                "harness_version": harness.version(),
                "sampling_policy": agent_config["sampling_policy"],
                "forced_params": forced,
                "seed_forwarded": "seed" in policy["forced_params"],
                "dropped_params": policy["dropped_params"],
                "problems": problems,
            },
        )
    differs = sorted(
        key
        for key in ("harness", "sampling_policy", "forced_params", "seed_forwarded")
        if len({str(r[key]) for r in rows}) > 1
    )
    return {"conditions": rows, "differs_on": differs, "has_problems": any(r["problems"] for r in rows)}


def _estimate_cell_cost(cell: MatrixCell, experiment: Experiment | None = None) -> float:
    if experiment is not None and experiment.mode == GenerationJob.Mode.AGENT:
        # Agent runs vary too much for a token model; the budget cap is the
        # honest number (an upper bound per job).
        return float(merge_agent_config(experiment.agent_config)["max_cost_usd"])
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
        cost = sum(_estimate_cell_cost(c, experiment) for c in cells if c.condition == condition)
        total_cost += cost
        per_condition.append(
            {
                "condition_id": condition.id,
                "label": condition.label
                or f"{condition.model.model_name} / {condition.harness or condition.profile.slug}",
                "harness": condition.harness,
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
        "cost_estimate_kind": "budget_upper_bound" if experiment.mode == GenerationJob.Mode.AGENT else "heuristic",
        "mode": experiment.mode,
        "per_condition": per_condition,
        "comparability": comparability(experiment) if experiment.mode == GenerationJob.Mode.AGENT else None,
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

    is_agent = experiment.mode == GenerationJob.Mode.AGENT
    batch = GenerationBatch.objects.create(
        name=f"Experiment: {experiment.name}",
        mode=experiment.mode,
        total_jobs=len(cells),
        created_by=user,
    )

    created_jobs: list[GenerationJob] = []
    failed_count = 0
    for cell in pending_cells:
        condition = cell.condition
        app_req = cell.app_requirement
        llm_params = _condition_llm_params(condition, experiment)

        job = GenerationJob.objects.create(
            mode=GenerationJob.Mode.AGENT if is_agent else GenerationJob.Mode.SCAFFOLDING,
            created_by=user,
            batch=batch,
            experiment=experiment,
            condition=condition,
            repeat_index=cell.repeat_index,
            model=condition.model,
            stack_slug=canonical_stack_slug(condition.profile.scaffolding_slug),
            app_requirement=app_req,
            profile=condition.profile,
            llm_params=llm_params,
            # Legacy columns mirror the collapsed layer so pre-snapshot code
            # paths (and the admin) see the effective values.
            temperature=llm_params.get("temperature", 0.3),
            max_tokens=llm_params.get("max_tokens", 32000),
            top_p=llm_params.get("top_p"),
            agent_config=_condition_agent_config(condition, experiment) if is_agent else {},
        )
        if experiment.base_seed is not None:
            # Agent mode uses common random numbers: every condition of an
            # (app, repeat) cell gets the same seed, so paired comparisons
            # differ only in the condition. (Scaffolding keeps its historical
            # per-condition derivation so old launches stay reproducible.)
            parts = (app_req.id, cell.repeat_index) if is_agent else (condition.id, app_req.id, cell.repeat_index)
            job.experiment_seed = derive_experiment_seed(experiment.base_seed, *parts)

        try:
            if is_agent:
                apply_agent_snapshot_to_job(job)
            else:
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

    for job in run_order(created_jobs, experiment):
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
            "llm_defaults": experiment.llm_defaults,
            "mode": experiment.mode,
            "agent_config": merge_agent_config(experiment.agent_config) if experiment.mode == "agent" else None,
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
                "harness": c.harness,
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
                "run_fingerprint": (job.resolved_bundle or {}).get("run_fingerprint"),
                "termination": ((job.result_data or {}).get("agent") or {}).get("termination"),
                "providers": ((job.result_data or {}).get("agent") or {}).get("providers"),
                "metrics": job.metrics,
            }
            for job in jobs
        ],
    }
