"""Per-call LLM telemetry endpoints (provider performance, per-call dataset export).

* ``GET /models/{model_id}/call-telemetry/?kind=`` -- descriptive per-provider call statistics (TTFT,
  decode/end-to-end speed, duration, cache hits, reasoning share, retry/error rates, OpenRouter-stats
  coverage) over the caller's runs of that model.
* ``GET /calls.csv?study=&run=&model=`` -- one row per LLM call (``services.call_telemetry.CSV_COLUMNS``;
  never message content), streamed, spreadsheet-safe.

Both are owner-scoped: only the caller's own runs, of a model they may see. A run's per-call list with the
gateway's protocol fields is ``GET /runs/{id}/calls/`` (``runs/api.py``).
"""

from __future__ import annotations

import csv
import uuid

from django.http import Http404
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja import Router
from ninja.errors import HttpError

from backend.export.services.csv_safe import safe_row
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models.choices import RunKind
from backend.generation.services import call_telemetry
from backend.llm_models.models import LLMModel

router = Router(tags=["generation"])

_KINDS = {choice for choice, _ in RunKind.choices}


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:  # a malformed id is a missing object, not a 500
        raise Http404 from exc


@router.get("/models/{model_id}/call-telemetry/", response=dict)
def model_call_telemetry(request, model_id: int, kind: str = Query("")):
    """Per upstream provider call statistics for ``model_id`` over the caller's runs (descriptive)."""
    model = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model_id)
    if kind and kind not in _KINDS:
        raise HttpError(400, f"unknown run kind {kind!r}")
    runs = GenerationRun.objects.filter(created_by=request.auth, model=model)
    return {
        "model_id": model.pk,
        "model": model.model_id,
        **call_telemetry.provider_performance(runs, kind=kind or None),
    }


class _Echo:
    """A file-like object whose ``write`` returns the line (for streaming ``csv.writer``)."""

    def write(self, value: str) -> str:
        return value


@router.get("/calls.csv", response=None)
def calls_csv(
    request,
    study: str = Query(""),
    run: str = Query(""),
    model: int | None = Query(None),
    spreadsheet_safe: bool = True,
):
    """Per-call telemetry rows for a study, a run or a model (filters combine; one is required)."""
    if not (study or run or model is not None):
        raise HttpError(400, "pass study, run or model")
    runs = GenerationRun.objects.filter(created_by=request.auth)
    name = "calls"
    if study:
        owned_study = get_object_or_404(Study, id=_uuid_or_404(study), created_by=request.auth)
        runs = runs.filter(study=owned_study)
        name += f"_study_{str(owned_study.pk)[:8]}"
    if run:
        owned = get_object_or_404(GenerationRun, id=_uuid_or_404(run), created_by=request.auth)
        runs = runs.filter(pk=owned.pk)
        name += f"_run_{str(owned.pk)[:8]}"
    if model is not None:
        llm = get_object_or_404(LLMModel.objects.visible_to(request.auth), pk=model)
        runs = runs.filter(model=llm)
        name += f"_model_{llm.pk}"

    writer = csv.writer(_Echo())
    rows = (writer.writerow(safe_row(row, enabled=spreadsheet_safe)) for row in call_telemetry.csv_rows(runs))
    response = StreamingHttpResponse(rows, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return response
