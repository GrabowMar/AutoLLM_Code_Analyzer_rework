"""Job creation endpoints (custom, scaffolding, copilot modes).

These endpoints all live under ``/jobs/<static-segment>/`` and MUST be registered
before any ``/jobs/{job_id}/`` route — Django Ninja matches routes in
registration order, and otherwise would interpret "custom" / "scaffolding" /
"copilot" as a ``job_id``. The package ``__init__.py`` enforces this ordering.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import has_resolvable_key
from backend.generation.api.schema import BatchCreateResponseSchema
from backend.generation.api.schema import CopilotJobCreateSchema
from backend.generation.api.schema import CustomJobCreateSchema
from backend.generation.api.schema import GenerationJobSchema
from backend.generation.api.schema import ScaffoldingJobCreateSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services.dispatcher import dispatch_job
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import get_profile_for_app
from backend.llm_models.models import LLMModel
from backend.runtime.services.scaffolding import canonical_stack_slug
from backend.runtime.services.scaffolding import is_known_stack_slug

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


@router.post("/jobs/custom/", response={200: GenerationJobSchema, 400: dict})
def create_custom_job(request, payload: CustomJobCreateSchema):
    """Create a custom mode generation job."""
    err = _preflight_api_key(request)
    if err is not None:
        return err
    model = get_object_or_404(LLMModel, id=payload.model_id)
    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.CUSTOM,
        created_by=request.auth,
        model=model,
        custom_system_prompt=payload.system_prompt,
        custom_user_prompt=payload.user_prompt,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    dispatch_job(job)
    return GenerationJob.objects.get(id=job.id)


@router.post("/jobs/scaffolding/", response={200: BatchCreateResponseSchema, 400: dict})
def create_scaffolding_jobs(request, payload: ScaffoldingJobCreateSchema):
    """Create scaffolding mode jobs (templates x models)."""
    err = _preflight_api_key(request)
    if err is not None:
        return err
    if not is_known_stack_slug(payload.stack_slug):
        raise HttpError(404, f"Unknown stack slug: {payload.stack_slug}")
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


@router.post("/jobs/copilot/", response={200: GenerationJobSchema, 400: dict})
def create_copilot_job(request, payload: CopilotJobCreateSchema):
    """Create a copilot mode generation job."""
    err = _preflight_api_key(request)
    if err is not None:
        return err
    model = None
    if payload.model_id:
        model = get_object_or_404(LLMModel, id=payload.model_id)
    stack_slug = ""
    if payload.stack_slug:
        if not is_known_stack_slug(payload.stack_slug):
            raise HttpError(404, f"Unknown stack slug: {payload.stack_slug}")
        stack_slug = canonical_stack_slug(payload.stack_slug)

    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.COPILOT,
        created_by=request.auth,
        model=model,
        stack_slug=stack_slug,
        copilot_description=payload.description,
        copilot_max_iterations=payload.max_iterations,
        copilot_use_open_source=payload.use_open_source,
    )
    dispatch_job(job)
    return GenerationJob.objects.get(id=job.id)
