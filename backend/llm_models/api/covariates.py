"""Model covariate endpoints (per-model research covariates: architecture, size, openness, external sources)."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from django.http import HttpResponse
from django.utils import timezone
from ninja import Query
from ninja.errors import HttpError

from backend.export.services.csv_safe import safe_row
from backend.llm_models.api.views import router
from backend.llm_models.services.model_covariates import model_covariates


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value)
    return value


def _csv_response(data: dict[str, Any]) -> HttpResponse:
    buffer = StringIO()
    writer = csv.writer(buffer)
    columns = data["columns"]
    writer.writerow(columns)
    for row in data["rows"]:
        writer.writerow(safe_row([_csv_cell(row.get(column)) for column in columns]))
    stamp = timezone.now().strftime("%Y%m%d")
    scope = "used" if data["used_only"] else "all"
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="model_covariates_{scope}_{stamp}.csv"'
    return response


@router.get("/covariates/")
def get_model_covariates(
    request,
    used_only: bool = Query(True),  # noqa: FBT003
    format: str = Query("json"),
):
    """One row of research covariates per model the caller can see.

    ``used_only`` keeps models with at least one of the caller's generation
    jobs. ``format=csv`` downloads the same rows (formula-safe cells); JSON
    adds the column order, sources with licenses, and reading notes.
    """
    export_format = (format or "json").lower()
    if export_format not in {"json", "csv"}:
        raise HttpError(400, "format must be json or csv.")
    data = model_covariates(request.auth, used_only=used_only)
    if export_format == "csv":
        return _csv_response(data)
    return data
