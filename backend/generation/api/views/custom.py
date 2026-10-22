"""Job creation endpoints (agent, custom, scaffolding modes).

These endpoints all live under ``/jobs/<static-segment>/`` and MUST be registered
before any ``/jobs/{job_id}/`` route — Django Ninja matches routes in
registration order, and otherwise would interpret "custom" / "scaffolding" /
"agent" as a ``job_id``. The package ``__init__.py`` enforces this ordering.
"""

from __future__ import annotations

import logging
import random

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import has_resolvable_key
from backend.generation.api.schema import AgentJobCreateSchema
from backend.generation.api.schema import BatchCreateResponseSchema
from backend.generation.api.schema import CustomJobCreateSchema
from backend.generation.api.schema import GenerationJobSchema
from backend.generation.api.schema import LLMPreviewSchema
from backend.generation.api.schema import ScaffoldingJobCreateSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services.agent.config import NETWORK_POLICIES
from backend.generation.services.agent.config import SAMPLING_POLICIES
from backend.generation.services.agent.config import default_agent_config
from backend.generation.services.agent.config import default_provider_routing
from backend.generation.services.agent.config import limits_schema
from backend.generation.services.agent.harnesses import harness_choices
from backend.generation.services.agent.harnesses import harness_details
from backend.generation.services.agent.request import Issue
from backend.generation.services.agent.request import RequestError
from backend.generation.services.agent.request import plan_agent_request
from backend.generation.services.agent.request import preview_payload
from backend.generation.services.agent.request import usable_app_specs
from backend.generation.services.agent.snapshot import DEFAULT_MAX_OUTPUT_TOKENS
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.batches import refresh_batch
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.llm_params import merge_llm_params
from backend.generation.services.llm_params import validate_llm_params
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import batch_cell_seed
from backend.generation.services.profile_resolver import build_custom_snapshot
from backend.generation.services.profile_resolver import get_profile_for_app
from backend.generation.services.sampling import SamplingPresetError
from backend.generation.services.sampling import layered_llm_params
from backend.generation.services.sampling import mode_default_params
from backend.generation.services.sampling import sampling_preset_params
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


def _sampling_preset(user, preset_id: int | None):
    try:
        return sampling_preset_params(user, preset_id)
    except SamplingPresetError as exc:
        raise HttpError(exc.status, exc.detail) from exc


def _visible_profile(user, profile_id: int) -> GenerationProfile:
    return get_object_or_404(
        GenerationProfile.objects.filter(Q(is_system=True) | Q(created_by=user)),
        id=profile_id,
    )


def _scaffolding_stack(requested: str | None, profile: GenerationProfile | None, user) -> str:
    """The batch's canonical stack: explicit -> profile's -> settings default (404 unknown, 403 unusable).

    A profile's prompts are written for its own stack, so an explicit stack
    that differs from the chosen profile's is refused rather than rendering
    prompts for one stack and building on another.
    """
    raw = requested or (profile.scaffolding_slug if profile else "") or settings.GENERATION_DEFAULT_STACK
    if not is_known_stack_slug(raw):
        raise HttpError(404, f"Unknown stack slug: {raw}")
    slug = canonical_stack_slug(raw)
    _usable_stack_or_error(slug, user)
    if profile is not None and canonical_stack_slug(profile.scaffolding_slug) != slug:
        raise HttpError(
            400,
            f"stack_slug {slug!r} does not match the stack of profile {profile.slug} "
            f"({profile.scaffolding_slug!r}); omit stack_slug to use the profile's stack",
        )
    return slug


@router.post("/jobs/custom/", response={200: GenerationJobSchema, 400: dict})
def create_custom_job(request, payload: CustomJobCreateSchema):
    """Create a custom mode generation job."""
    err = _preflight_api_key(request)
    if err is not None:
        return err
    llm_overrides = _validated_llm_params(payload)
    model = get_object_or_404(LLMModel, id=payload.model_id)
    preset, preset_llm = _sampling_preset(request.auth, payload.sampling_preset_id)
    job_llm_params = merge_llm_params(preset_llm, llm_overrides)
    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.CUSTOM,
        created_by=request.auth,
        model=model,
        custom_system_prompt=payload.system_prompt,
        custom_user_prompt=payload.user_prompt,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        llm_params=job_llm_params,
        sampling_preset=preset,
        experiment_seed=payload.seed,
        resolved_bundle=build_custom_snapshot(
            model=model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            llm_overrides=job_llm_params,
            sampling_preset=preset,
        ),
    )
    dispatch_job(job)
    return GenerationJob.objects.select_related("model", "sampling_preset").get(id=job.id)


@router.post("/jobs/scaffolding/", response={200: BatchCreateResponseSchema, 400: dict})
def create_scaffolding_jobs(request, payload: ScaffoldingJobCreateSchema):
    """Create scaffolding mode jobs: app specs x models x trials.

    Everything is resolved before the first row is written (stack, profiles,
    preset), so a refused request creates nothing.
    """
    err = _preflight_api_key(request)
    if err is not None:
        return err
    llm_overrides = _validated_llm_params(payload)
    if not payload.app_requirement_ids:
        raise HttpError(400, "Pick at least one app spec")
    if not payload.model_ids:
        raise HttpError(400, "Pick at least one model")
    profile = _visible_profile(request.auth, payload.profile_id) if payload.profile_id is not None else None
    stack_slug = _scaffolding_stack(payload.stack_slug, profile, request.auth)

    # Another user's private spec is unknown (services/agent/request.py).
    app_reqs = list(usable_app_specs(request.auth).filter(id__in=payload.app_requirement_ids).order_by("id"))
    if len(app_reqs) != len(set(payload.app_requirement_ids)):
        raise HttpError(404, "Unknown app requirement id")
    models = list(LLMModel.objects.filter(id__in=payload.model_ids).order_by("id"))
    if len(models) != len(set(payload.model_ids)):
        raise HttpError(404, "Unknown model id")
    preset, preset_llm = _sampling_preset(request.auth, payload.sampling_preset_id)
    job_llm_params = merge_llm_params(preset_llm, llm_overrides)

    app_profiles: list[tuple[AppRequirementTemplate, GenerationProfile | None]] = []
    for app_req in app_reqs:
        job_profile = profile or get_profile_for_app(app_req, request.auth, scaffolding_slug=stack_slug)
        # The automatic choice falls back to the global default profile when
        # no profile targets the stack; its prompts would describe another stack.
        if job_profile is not None and canonical_stack_slug(job_profile.scaffolding_slug) != stack_slug:
            raise HttpError(
                400,
                f"No generation profile targets stack {stack_slug!r} (the default, {job_profile.slug}, is "
                f"for {job_profile.scaffolding_slug!r}); pick a profile for this stack",
            )
        app_profiles.append((app_req, job_profile))

    batch = GenerationBatch.objects.create(
        name=f"Scaffolding batch - {stack_slug}",
        mode=GenerationJob.Mode.SCAFFOLDING,
        total_jobs=len(app_reqs) * len(models) * payload.trials,
        created_by=request.auth,
        request={
            "mode": GenerationJob.Mode.SCAFFOLDING,
            "trials": payload.trials,
            "stack_slug": stack_slug,
            "app_requirement_ids": [a.id for a in app_reqs],
            "model_ids": [m.id for m in models],
            "profile_id": profile.id if profile else None,
            "sampling_preset_id": preset.id if preset else None,
            "temperature": payload.temperature,
            "max_tokens": payload.max_tokens,
            "llm_params": llm_overrides,
        },
    )

    created: list[GenerationJob] = []
    for app_req, job_profile in app_profiles:
        for model in models:
            for trial in range(payload.trials):
                job = GenerationJob.objects.create(
                    mode=GenerationJob.Mode.SCAFFOLDING,
                    created_by=request.auth,
                    batch=batch,
                    model=model,
                    stack_slug=stack_slug,
                    app_requirement=app_req,
                    profile=job_profile,
                    temperature=payload.temperature,
                    max_tokens=payload.max_tokens,
                    llm_params=dict(job_llm_params),
                    sampling_preset=preset,
                    repeat_index=trial,
                )
                try:
                    apply_snapshot_to_job(job)
                except Exception as exc:  # noqa: BLE001 — isolate one bad job from the batch
                    logger.warning("Snapshot build failed for job %s: %s", job.id, exc)
                    job.status = GenerationJob.Status.FAILED
                    job.error_message = f"Snapshot build failed: {exc}"
                    job.save(update_fields=["status", "error_message", "updated_at"])
                    continue
                created.append(job)

    # Every snapshot failed -> the batch is failed now, not pending forever.
    refresh_batch(batch)
    for job in created:
        dispatch_job(job)

    return BatchCreateResponseSchema(batch_id=batch.id, job_count=batch.total_jobs, status=batch.status)


@router.post("/jobs/llm-preview/", response={200: dict, 400: dict})
def preview_llm_params(request, payload: LLMPreviewSchema):
    """Effective llm params of a scaffolding / single-prompt job, with the layer each value came from.

    Sources: ``mode_default`` (temperature 0.3, max_tokens 32000), ``profile``
    (scaffolding only; an automatically chosen profile is not known here),
    ``preset`` (sampling preset ``llm_params``), ``inline``.
    """
    inline = _validated_llm_params(payload)
    profile = None
    if payload.profile_id is not None:
        if payload.mode == GenerationJob.Mode.CUSTOM:
            raise HttpError(400, "Single-prompt jobs take no profile")
        profile = _visible_profile(request.auth, payload.profile_id)
    _preset, preset_llm = _sampling_preset(request.auth, payload.sampling_preset_id)
    effective, sources = layered_llm_params(
        [
            ("mode_default", mode_default_params()),
            ("profile", dict(profile.llm_config or {}) if profile else {}),
            ("preset", preset_llm),
            ("inline", inline),
        ],
    )
    return {"effective": effective, "sources": sources}


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


def _agent_plan(request, payload: AgentJobCreateSchema):
    """Resolve the request (presets, layers, stack) or raise the matching HTTP error."""
    try:
        return plan_agent_request(payload.to_request(), request.auth)
    except RequestError as exc:
        raise HttpError(exc.status, exc.detail) from exc


@router.post("/jobs/agent/preview/", response={200: dict, 400: dict})
def preview_agent_jobs(request, payload: AgentJobCreateSchema):
    """What ``POST /jobs/agent/`` would create, resolved per model and harness preset (no writes)."""
    plan = _agent_plan(request, payload)
    extra: list[Issue] = []
    if not has_resolvable_key(request.auth):
        extra.append(Issue("missing_api_key", "error", "No OpenRouter API key is configured for your account."))
    return preview_payload(plan, extra_issues=extra)


@router.post("/jobs/agent/", response={200: BatchCreateResponseSchema, 400: dict})
def create_agent_jobs(request, payload: AgentJobCreateSchema):
    """Create agent-mode jobs: (app specs | one free-form task) x models x harness presets x trials.

    Preset values are applied into each job's own fields (llm_params,
    agent_config, harness_options); the preset FKs are provenance. With a
    base seed every cell of one (task, trial) shares a seed (common random
    numbers), and jobs run trial by trial in a seeded random order within
    each trial, so conditions are compared paired and are never confounded
    with run time.
    """
    err = _preflight_api_key(request)
    if err is not None:
        return err
    plan = _agent_plan(request, payload)
    if plan.errors:
        return 400, {
            "detail": "; ".join(issue.detail for issue in plan.errors),
            "code": plan.errors[0].code,
            "errors": [issue.to_dict() for issue in plan.errors],
        }

    order_seed = payload.base_seed if payload.base_seed is not None else random.SystemRandom().randrange(2**31)
    batch = GenerationBatch.objects.create(
        name=f"Agent batch - {plan.stack_slug}",
        mode=GenerationJob.Mode.AGENT,
        total_jobs=plan.run_count,
        created_by=request.auth,
        request=plan.batch_request(order_seed=order_seed),
    )
    failed = 0
    created: list[GenerationJob] = []
    for app_req in plan.tasks:
        for trial in range(payload.trials):
            seed = None
            if payload.base_seed is not None:
                seed = batch_cell_seed(payload.base_seed, app_req.slug if app_req else None, trial)
            for model in plan.models:
                for choice in plan.choices:
                    job = GenerationJob.objects.create(
                        mode=GenerationJob.Mode.AGENT,
                        created_by=request.auth,
                        batch=batch,
                        model=model,
                        stack_slug=plan.stack_slug,
                        app_requirement=app_req,
                        profile=plan.profile,
                        task_description=plan.description,
                        max_tokens=plan.job_max_tokens,
                        llm_params=dict(plan.job_llm_params),
                        agent_config={**plan.job_agent_config, "harness": choice.harness.name},
                        harness_options=dict(choice.options),
                        harness_preset=choice.preset,
                        sampling_preset=plan.sampling_preset,
                        budget_preset=plan.budget_preset,
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
        # Counts and status from the rows (every snapshot failed -> failed).
        refresh_batch(batch)
    for job in _blocked_shuffle(created, order_seed):
        dispatch_job(job)
    return BatchCreateResponseSchema(batch_id=batch.id, job_count=batch.total_jobs, status=batch.status)


@router.get("/agent/harnesses/", response=list[dict])
def list_harnesses(request):
    """Available agent harnesses with their pinned releases."""
    return harness_choices()


@router.get("/agent/config/", response=dict)
def agent_run_config(request):
    """Defaults, limits, policies and harness option schemas the agent composer renders from."""
    defaults = default_agent_config()
    return {
        "defaults": {
            key: defaults[key]
            for key in (
                "harness",
                "sampling_policy",
                "wall_clock_seconds",
                "max_llm_requests",
                "max_total_tokens",
                "max_cost_usd",
                "network_policy",
            )
        },
        "limits": limits_schema(),
        "network_policies": list(NETWORK_POLICIES),
        "sampling_policies": list(SAMPLING_POLICIES),
        "default_provider_routing": default_provider_routing(),
        "default_stack_slug": settings.GENERATION_DEFAULT_STACK,
        "default_max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "harnesses": harness_details(),
    }


@router.post("/jobs/copilot/", response={410: dict})
def create_copilot_job(request):
    """Retired: the in-process aider loop was replaced by sandboxed agent mode."""
    return 410, {
        "detail": "Copilot mode was retired. Use POST /generation/jobs/agent/ (sandboxed OpenCode harness).",
        "code": "mode_retired",
    }
