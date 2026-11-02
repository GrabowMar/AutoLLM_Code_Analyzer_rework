"""Run-to-run variability endpoints (experiment repeatability, same-setup replicates, model variability).

* ``GET /experiments/{experiment_id}/variability/`` — the Results tab's
  "Run-to-run variation" section, computed on the same kept cells as
  ``/results/`` (same query options); ``?format=csv`` gives one row per
  (condition, app) cell.
* ``GET /jobs/{job_id}/replicates/`` — every visible job sharing the job's
  configuration fingerprint (contract C3).
* ``GET /models/{model_id}/variability/`` — within-group spread over the
  model's same-setup groups of the caller's jobs.

Owner-scoped like the experiment and job routes (a foreign or malformed id
is a 404); models resolve through ``LLMModel.objects.visible_to``.
Everything is descriptive (no p-values); see
:mod:`backend.statistics.services.variability`.
"""

from __future__ import annotations

import uuid

from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError
from ninja.responses import Status

from backend.generation.api.views._router import router
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.services.experiment_results import ResultFilters
from backend.statistics.services import variability as svc


def _owned_experiment(user, experiment_id: str) -> Experiment:
    try:
        uuid.UUID(str(experiment_id))
    except ValueError as exc:  # a malformed id is a missing experiment, not a 500
        raise Http404 from exc
    return get_object_or_404(Experiment, id=experiment_id, created_by=user)


def _owned_job(user, job_id: str) -> GenerationJob:
    try:
        job_uuid = uuid.UUID(str(job_id))
    except ValueError as exc:
        raise HttpError(404, "Not Found") from exc
    return get_object_or_404(GenerationJob.objects.defer("result_data"), id=job_uuid, created_by=user)


@router.get("/experiments/{experiment_id}/variability/", response={200: dict, 400: dict})
def experiment_variability(
    request,
    experiment_id: str,
    exclude_invalid: bool = False,
    exclude_truncated: bool | None = None,
    common_cells: bool = True,
    fingerprint: str = "all",
    planned_only: bool = True,
    format: str = "json",
):
    """Within-cell dispersion, variance components, ICC, pass@k, similarity, finding overlap, ranks, planner."""
    experiment = _owned_experiment(request.auth, experiment_id)
    if format not in ("json", "csv"):
        return Status(400, {"detail": "format must be json or csv", "code": "invalid_format"})
    filters = ResultFilters(
        exclude_invalid=exclude_invalid,
        exclude_truncated=exclude_truncated,
        common_cells=common_cells,
        fingerprint=fingerprint,
        planned_only=planned_only,
    )
    try:
        payload = svc.experiment_variability(experiment, filters)
    except ValueError as exc:
        return Status(400, {"detail": str(exc), "code": "invalid_variability_query"})
    if format == "csv":
        response = HttpResponse(svc.experiment_variability_csv(payload), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{experiment.slug}-variability.csv"'
        return response
    return Status(200, payload)


@router.get("/jobs/{job_id}/replicates/", response=dict)
def job_replicates(request, job_id: str):
    """Contract C3: the job's same-setup group (replicates, replays, reruns) with metrics and similarity."""
    job = _owned_job(request.auth, job_id)
    return svc.job_replicates(job, request.auth)


@router.get("/models/{model_id}/variability/", response=dict)
def model_variability(request, model_id: int):
    """Run-to-run spread within each same-setup group of this model's runs (the caller's jobs)."""
    from backend.llm_models.models import LLMModel

    model = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model_id)
    return svc.model_variability(model, request.auth)
