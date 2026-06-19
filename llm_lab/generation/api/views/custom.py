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
from llm_lab.generation.models import TemplateBundle
from llm_lab.generation.services.bundle_resolver import apply_snapshot_to_job
from llm_lab.generation.services.bundle_resolver import get_bundle_for_app
from llm_lab.generation.services.dispatcher import dispatch_job
from llm_lab.llm_models.models import LLMModel
from llm_lab.runtime.services.scaffolding import canonical_stack_slug

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
    scaffolding = get_object_or_404(
        ScaffoldingTemplate,
        id=payload.scaffolding_template_id,
    )
    app_reqs = AppRequirementTemplate.objects.filter(
        id__in=payload.app_requirement_ids,
    )
    models_qs = LLMModel.objects.filter(id__in=payload.model_ids)

    template_bundle = None
    if payload.template_bundle_id:
        template_bundle = get_object_or_404(
            TemplateBundle.objects.filter(
                Q(is_system=True) | Q(created_by=request.auth),
            ),
            id=payload.template_bundle_id,
        )

    batch = GenerationBatch.objects.create(
        name=f"Scaffolding batch - {scaffolding.name}",
        mode="scaffolding",
        total_jobs=app_reqs.count() * models_qs.count(),
        created_by=request.auth,
    )

    stack_slug = canonical_stack_slug(scaffolding.slug)

    job_count = 0
    failed_count = 0
    for app_req in app_reqs:
        job_bundle = template_bundle or get_bundle_for_app(
            app_req,
            request.auth,
            scaffolding_slug=stack_slug,
        )
        for model in models_qs:
            job = GenerationJob.objects.create(
                mode=GenerationJob.Mode.SCAFFOLDING,
                created_by=request.auth,
                batch=batch,
                model=model,
                scaffolding_template=scaffolding,
                app_requirement=app_req,
                template_bundle=job_bundle,
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
        "app_requirement", "model", "template_bundle",
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
