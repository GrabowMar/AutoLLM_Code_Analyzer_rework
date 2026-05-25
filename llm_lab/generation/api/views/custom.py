"""Job creation endpoints (custom, scaffolding, copilot modes).

These endpoints all live under ``/jobs/<static-segment>/`` and MUST be registered
before any ``/jobs/{job_id}/`` route — Django Ninja matches routes in
registration order, and otherwise would interpret "custom" / "scaffolding" /
"copilot" as a ``job_id``. The package ``__init__.py`` enforces this ordering.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from llm_lab.credentials.services.resolver import MissingApiKeyError
from llm_lab.credentials.services.resolver import has_resolvable_key
from llm_lab.generation.api.schema import BatchCreateResponseSchema
from llm_lab.generation.api.schema import CopilotJobCreateSchema
from llm_lab.generation.api.schema import CustomJobCreateSchema
from llm_lab.generation.api.schema import GenerationJobSchema
from llm_lab.generation.api.schema import ScaffoldingJobCreateSchema
from llm_lab.generation.api.views._router import router
from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import GenerationBatch
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.models import ScaffoldingTemplate
from llm_lab.generation.services.dispatcher import dispatch_job
from llm_lab.llm_models.models import LLMModel


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
    scaffolding = get_object_or_404(
        ScaffoldingTemplate,
        id=payload.scaffolding_template_id,
    )
    app_reqs = AppRequirementTemplate.objects.filter(
        id__in=payload.app_requirement_ids,
    )
    models_qs = LLMModel.objects.filter(id__in=payload.model_ids)

    batch = GenerationBatch.objects.create(
        name=f"Scaffolding batch - {scaffolding.name}",
        mode="scaffolding",
        total_jobs=app_reqs.count() * models_qs.count(),
        created_by=request.auth,
    )

    job_count = 0
    for app_req in app_reqs:
        for model in models_qs:
            GenerationJob.objects.create(
                mode=GenerationJob.Mode.SCAFFOLDING,
                created_by=request.auth,
                batch=batch,
                model=model,
                scaffolding_template=scaffolding,
                app_requirement=app_req,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            )
            job_count += 1

    for pending_job in batch.jobs.select_related("app_requirement", "model").all():
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
    scaffolding = None
    if payload.scaffolding_template_id:
        scaffolding = get_object_or_404(
            ScaffoldingTemplate,
            id=payload.scaffolding_template_id,
        )

    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.COPILOT,
        created_by=request.auth,
        model=model,
        scaffolding_template=scaffolding,
        copilot_description=payload.description,
        copilot_max_iterations=payload.max_iterations,
        copilot_use_open_source=payload.use_open_source,
    )
    dispatch_job(job)
    return GenerationJob.objects.get(id=job.id)
