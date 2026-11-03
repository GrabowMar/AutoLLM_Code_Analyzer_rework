"""Run-to-run variability endpoints (study repeatability, same-setup replicates, model variability).

* ``GET /studies/{study_id}/variability/`` -- the Results tab's "Run-to-run variation" section, computed on
  the same current runs as ``/studies/{id}/results/`` (same query options); ``?format=csv`` gives one row per
  (condition, task) cell.
* ``GET /runs/{run_id}/replicates/`` -- every visible run sharing the run's ``config_fingerprint``.
* ``GET /models/{model_id}/variability/`` -- within-group spread over the model's same-setup groups of the
  caller's runs.

Owner-scoped like the study and run routes (a foreign or malformed id is a 404); models resolve through
``LLMModel.objects.visible_to``. Everything is descriptive (no p-values); the computation is
:mod:`backend.statistics.services.variability` (WP-02b), imported per request so the URLconf never depends
on the statistics stack.
"""

from __future__ import annotations

import uuid

from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.responses import Status

from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.llm_models.models import LLMModel

router = Router(tags=["generation"])

FINGERPRINT_CHOICES = ("all", "majority")


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:  # a malformed id is a missing object, not a 500
        raise Http404 from exc


def _svc():
    from backend.statistics.services import variability

    return variability


def _owned_study(user, study_id: str) -> Study:
    return get_object_or_404(Study, id=_uuid_or_404(study_id), created_by=user)


def _owned_run(user, run_id: str) -> GenerationRun:
    return get_object_or_404(GenerationRun, id=_uuid_or_404(run_id), created_by=user)


@router.get("/studies/{study_id}/variability/", response={200: dict, 400: dict})
def study_variability(
    request,
    study_id: str,
    fingerprint: str = "majority",
    sensitivity: str | None = None,
    format: str = "json",
):
    """Within-cell dispersion, variance components, ICC, pass@k, similarity, finding overlap, ranks, planner."""
    study = _owned_study(request.auth, study_id)
    if format not in ("json", "csv"):
        return Status(400, {"detail": "format must be json or csv", "code": "invalid_format"})
    if fingerprint not in FINGERPRINT_CHOICES:
        return Status(400, {"detail": "fingerprint must be all or majority", "code": "invalid_fingerprint"})
    try:
        payload = _svc().study_variability(study, fingerprint=fingerprint, sensitivity=sensitivity)
    except ValueError as exc:
        return Status(400, {"detail": str(exc), "code": "invalid_variability_query"})
    if format == "csv":
        response = HttpResponse(_svc().study_variability_csv(payload), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{study.slug}-variability.csv"'
        return response
    return Status(200, payload)


@router.get("/runs/{run_id}/replicates/", response=dict)
def run_replicates(request, run_id: str):
    """The run's same-setup group (replicates and retries) with metrics and similarity."""
    run = _owned_run(request.auth, run_id)
    return _svc().run_replicates(run, request.auth)


@router.get("/models/{model_id}/variability/", response=dict)
def model_variability(request, model_id: int):
    """Run-to-run spread within each same-setup group of this model's runs (the caller's runs)."""
    model = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model_id)
    return _svc().model_variability(model, request.auth)
