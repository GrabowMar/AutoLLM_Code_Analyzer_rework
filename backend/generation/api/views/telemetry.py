"""Per-call LLM telemetry endpoints (provider performance, per-call dataset export).

* ``GET /models/{model_id}/call-telemetry/`` -- descriptive per-provider call
  statistics (TTFT, decode/end-to-end speed, duration, cache hits, reasoning
  share, retry/error rates, OpenRouter-stats coverage) over the caller's jobs
  of that model.
* ``GET /calls.csv?experiment=&job=&model=`` -- one row per LLM call
  (``services.call_telemetry.CSV_COLUMNS``; never message content), streamed,
  spreadsheet-safe.

Both are owner-scoped: only the caller's own jobs, of a model they may see.
"""

from __future__ import annotations

import csv
import uuid

from django.http import Http404
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError

from backend.export.services.csv_safe import safe_row
from backend.generation.api.views._router import router
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.services import call_telemetry
from backend.llm_models.models import LLMModel

_MODES = {choice for choice, _ in GenerationJob.Mode.choices}


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:  # a malformed id is a missing object, not a 500
        raise Http404 from exc


@router.get("/models/{model_id}/call-telemetry/", response=dict)
def model_call_telemetry(request, model_id: int, mode: str = Query("")):
    """Per upstream provider call statistics for ``model_id`` over the caller's jobs (descriptive)."""
    model = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model_id)
    if mode and mode not in _MODES:
        raise HttpError(400, f"unknown mode {mode!r}")
    jobs = GenerationJob.objects.filter(created_by=request.auth, model=model)
    return {
        "model_id": model.pk,
        "model": model.model_id,
        **call_telemetry.provider_performance(jobs, mode=mode or None),
    }


class _Echo:
    """A file-like object whose ``write`` returns the line (for streaming ``csv.writer``)."""

    def write(self, value: str) -> str:
        return value


@router.get("/calls.csv", response=None)
def calls_csv(
    request,
    experiment: str = Query(""),
    job: str = Query(""),
    model: int | None = Query(None),
    spreadsheet_safe: bool = True,
):
    """Per-call telemetry rows for an experiment, a job or a model (filters combine; one is required)."""
    if not (experiment or job or model is not None):
        raise HttpError(400, "pass experiment, job or model")
    jobs = GenerationJob.objects.filter(created_by=request.auth)
    name = "calls"
    if experiment:
        exp = get_object_or_404(Experiment, id=_uuid_or_404(experiment), created_by=request.auth)
        jobs = jobs.filter(experiment=exp)
        name += f"_experiment_{str(exp.pk)[:8]}"
    if job:
        owned = get_object_or_404(GenerationJob, id=_uuid_or_404(job), created_by=request.auth)
        jobs = jobs.filter(pk=owned.pk)
        name += f"_job_{str(owned.pk)[:8]}"
    if model is not None:
        llm = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model)
        jobs = jobs.filter(model=llm)
        name += f"_model_{llm.pk}"

    writer = csv.writer(_Echo())
    rows = (writer.writerow(safe_row(row, enabled=spreadsheet_safe)) for row in call_telemetry.csv_rows(jobs))
    response = StreamingHttpResponse(rows, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return response
