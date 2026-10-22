"""Job creation endpoints (agent, custom, scaffolding modes).

These endpoints all live under ``/jobs/<static-segment>/`` and MUST be registered
before any ``/jobs/{job_id}/`` route — Django Ninja matches routes in
registration order, and otherwise would interpret "custom" / "scaffolding" /
"agent" as a ``job_id``. The package ``__init__.py`` enforces this ordering.
"""

from __future__ import annotations

import logging
import random

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import has_resolvable_key
from backend.generation.api.schema import AgentJobCreateSchema
from backend.generation.api.schema import BatchCreateResponseSchema
from backend.generation.api.schema import CustomJobCreateSchema
from backend.generation.api.schema import GenerationJobSchema
from backend.generation.api.schema import ScaffoldingJobCreateSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.harnesses import harness_choices
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import build_custom_snapshot
from backend.generation.services.profile_resolver import derive_experiment_seed
from backend.generation.services.profile_resolver import get_profile_for_app
from backend.llm_models.models import LLMModel
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import get_stack_row
from backend.runtime.services.scaffolding import is_known_stack_slug
from backend.runtime.services.stack_validation import stack_usable_by

logger = logging.getLogger(__name__)


def _preflight_api_key(request) -> tuple[int, dict] | None:
    """Return a 400 response if the user has no usable OpenRouter API key."""
    if not has_resolvable_key(request.auth):
        msg = "No OpenRouter API key is configured for your account."
        return 400, {
            "detail": msg,
            "remediation": MissingApiKeyError(msg).remediation,
            "code": "missing_api_key",
        }
    return None


def _validated_llm_params(payload) -> dict:
    """Validate the optional llm_params section of a job payload (400 on error)."""
    try:
        return validate_llm_params(payload.llm_params.as_params() if payload.llm_params else {})
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


def _usable_stack_or_error(slug: str, user) -> None:
    """403 for stacks the user may not run jobs on (unapproved/archived user stacks)."""
    row = get_stack_row(canonical_stack_slug(slug))
    if row is not None and not stack_usable_by(row, user):
        raise HttpError(403, f"Stack {row.slug!r} is not approved for use yet.")


@router.post("/jobs/custom/", response={200: GenerationJobSchema, 400: dict})
def create_custom_job(request, payload: CustomJobCreateSchema):
    """Create a custom mode generation job."""
    err = _preflight_api_key(request)
    if err is not None:
        return err
    llm_overrides = _validated_llm_params(payload)
    model = get_object_or_404(LLMModel, id=payload.model_id)
    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.CUSTOM,
        created_by=request.auth,
        model=model,
        custom_system_prompt=payload.system_prompt,
        custom_user_prompt=payload.user_prompt,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        llm_params=llm_overrides,
        experiment_seed=payload.seed,
        resolved_bundle=build_custom_snapshot(
            model=model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            llm_overrides=llm_overrides,
        ),
    )
    dispatch_job(job)
    return GenerationJob.objects.get(id=job.id)


@router.post("/jobs/scaffolding/", response={200: BatchCreateResponseSchema, 400: dict})
def create_scaffolding_jobs(request, payload: ScaffoldingJobCreateSchema):
    """Create scaffolding mode jobs (templates x models)."""
    err = _preflight_api_key(request)
    if err is not None:
        return err
    llm_overrides = _validated_llm_params(payload)
    if not is_known_stack_slug(payload.stack_slug):
        raise HttpError(404, f"Unknown stack slug: {payload.stack_slug}")
    _usable_stack_or_error(payload.stack_slug, request.auth)
    app_reqs = AppRequirementTemplate.objects.filter(
        id__in=payload.app_requirement_ids,
    )
    models_qs = LLMModel.objects.filter(id__in=payload.model_ids)

    profile = None
    if payload.profile_id:
        profile = get_object_or_404(
            GenerationProfile.objects.filter(
                Q(is_system=True) | Q(created_by=request.auth),
            ),
            id=payload.profile_id,
        )

    stack_slug = canonical_stack_slug(payload.stack_slug)

    batch = GenerationBatch.objects.create(
        name=f"Scaffolding batch - {stack_slug}",
        mode="scaffolding",
        total_jobs=app_reqs.count() * models_qs.count() * payload.trials,
        created_by=request.auth,
    )

    job_count = 0
    failed_count = 0
    for app_req in app_reqs:
        job_bundle = profile or get_profile_for_app(
            app_req,
            request.auth,
            scaffolding_slug=stack_slug,
        )
        for model in models_qs:
            for _trial in range(payload.trials):
                job = GenerationJob.objects.create(
                    mode=GenerationJob.Mode.SCAFFOLDING,
                    created_by=request.auth,
                    batch=batch,
                    model=model,
                    stack_slug=stack_slug,
                    app_requirement=app_req,
                    profile=job_bundle,
                    temperature=payload.temperature,
                    max_tokens=payload.max_tokens,
                    llm_params=llm_overrides,
                )
                job_count += 1
                try:
                    apply_snapshot_to_job(job)
                except Exception as exc:  # noqa: BLE001 — isolate one bad job from the batch
                    logger.warning("Snapshot build failed for job %s: %s", job.id, exc)
                    job.status = GenerationJob.Status.FAILED
                    job.error_message = f"Snapshot build failed: {exc}"
                    job.save(update_fields=["status", "error_message", "updated_at"])
                    failed_count += 1

    if failed_count:
        batch.failed_jobs = failed_count
        batch.save(update_fields=["failed_jobs", "updated_at"])

    dispatchable = batch.jobs.select_related(
        "app_requirement",
        "model",
        "profile",
    ).exclude(status=GenerationJob.Status.FAILED)
    for pending_job in dispatchable:
        dispatch_job(pending_job)

    return BatchCreateResponseSchema(
        batch_id=batch.id,
        job_count=job_count,
        status="pending",
    )


def _validated_agent_config(payload) -> dict:
    try:
        return validate_agent_config(payload.agent_config.as_config() if payload.agent_config else {})
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


def require_tool_capable(models, harness) -> None:
    """400 unless every model can drive *harness* (tool-calling harnesses need tool support)."""
    if not harness.requires_tool_calling:
        return
    unable = [m.model_id for m in models if not m.supports_function_calling]
    if unable:
        raise HttpError(
            400,
            f"{harness.label} needs tool-calling models; not supported by: {', '.join(unable)}",
        )


def _blocked_shuffle(jobs: list[GenerationJob], seed: int | None) -> list[GenerationJob]:
    """Trial by trial, cells shuffled within each trial (see experiments.run_order)."""
    rng = random.Random(seed)  # noqa: S311 - run-order randomisation, not crypto
    blocks: dict[int, list[GenerationJob]] = {}
    for job in jobs:
        blocks.setdefault(job.repeat_index or 0, []).append(job)
    ordered: list[GenerationJob] = []
    for trial in sorted(blocks):
        rng.shuffle(blocks[trial])
        ordered.extend(blocks[trial])
    return ordered


@router.post("/jobs/agent/", response={200: BatchCreateResponseSchema, 400: dict})
def create_agent_jobs(request, payload: AgentJobCreateSchema):
    """Create agent-mode jobs: (app specs | one free-form task) x models x harnesses x trials.

    Every cell of one (task, trial) shares a seed (common random numbers), and
    jobs run trial by trial in a seeded random order within each trial, so
    conditions are compared paired and are never confounded with run time.
    """
    err = _preflight_api_key(request)
    if err is not None:
        return err
    llm_overrides = _validated_llm_params(payload)
    agent_overrides = _validated_agent_config(payload)
    if not is_known_stack_slug(payload.stack_slug):
        raise HttpError(404, f"Unknown stack slug: {payload.stack_slug}")
    _usable_stack_or_error(payload.stack_slug, request.auth)
    stack_slug = canonical_stack_slug(payload.stack_slug)

    app_reqs = list(AppRequirementTemplate.objects.filter(id__in=payload.app_requirement_ids).order_by("id"))
    if len(app_reqs) != len(set(payload.app_requirement_ids)):
        raise HttpError(404, "Unknown app requirement id")
    if not app_reqs and not payload.description.strip():
        raise HttpError(400, "Give at least one app spec or a task description")
    models = list(LLMModel.objects.filter(id__in=payload.model_ids).order_by("id"))
    if not models or len(models) != len(set(payload.model_ids)):
        raise HttpError(404, "Unknown model id")
    harness_names = list(dict.fromkeys(payload.harnesses)) or [merge_agent_config(agent_overrides)["harness"]]
    try:
        harnesses = [get_harness(name) for name in harness_names]
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    for harness in harnesses:
        require_tool_capable(models, harness)

    profile = None
    if payload.profile_id:
        profile = get_object_or_404(
            GenerationProfile.objects.filter(Q(is_system=True) | Q(created_by=request.auth)),
            id=payload.profile_id,
        )

    tasks = app_reqs or [None]
    batch = GenerationBatch.objects.create(
        name=f"Agent batch - {stack_slug}",
        mode=GenerationJob.Mode.AGENT,
        total_jobs=len(tasks) * len(models) * len(harnesses) * payload.trials,
        created_by=request.auth,
    )
    failed = 0
    created: list[GenerationJob] = []
    for app_req in tasks:
        for trial in range(payload.trials):
            seed = None
            if payload.base_seed is not None:
                seed = derive_experiment_seed(payload.base_seed, app_req.slug if app_req else "task", trial)
            for model in models:
                for harness in harnesses:
                    job = GenerationJob.objects.create(
                        mode=GenerationJob.Mode.AGENT,
                        created_by=request.auth,
                        batch=batch,
                        model=model,
                        stack_slug=stack_slug,
                        app_requirement=app_req,
                        profile=profile,
                        task_description=payload.description,
                        max_tokens=payload.max_tokens,
                        llm_params=llm_overrides,
                        agent_config={**agent_overrides, "harness": harness.name},
                        experiment_seed=seed,
                        repeat_index=trial,
                    )
                    try:
                        apply_agent_snapshot_to_job(job)
                    except Exception as exc:  # noqa: BLE001 - isolate one bad job from the batch
                        logger.warning("Agent snapshot failed for job %s: %s", job.id, exc)
                        job.status = GenerationJob.Status.FAILED
                        job.error_message = f"Snapshot build failed: {exc}"
                        job.save(update_fields=["status", "error_message", "updated_at"])
                        failed += 1
                        continue
                    created.append(job)
    if failed:
        batch.failed_jobs = failed
        batch.save(update_fields=["failed_jobs", "updated_at"])
    order_seed = payload.base_seed if payload.base_seed is not None else random.SystemRandom().randrange(2**31)
    for job in _blocked_shuffle(created, order_seed):
        dispatch_job(job)
    return BatchCreateResponseSchema(batch_id=batch.id, job_count=batch.total_jobs, status="pending")


@router.get("/agent/harnesses/", response=list[dict])
def list_harnesses(request):
    """Available agent harnesses with their pinned releases."""
    return harness_choices()


@router.post("/jobs/copilot/", response={410: dict})
def create_copilot_job(request):
    """Retired: the in-process aider loop was replaced by sandboxed agent mode."""
    return 410, {
        "detail": "Copilot mode was retired. Use POST /generation/jobs/agent/ (sandboxed OpenCode harness).",
        "code": "mode_retired",
    }
