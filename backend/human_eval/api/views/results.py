"""Researcher results endpoints (owner D2): progress, responses browser, attachments, HTTP log, results, exports.

Every route looks the task up with ``access.researcher_task`` (owner or staff;
404 for other users' tasks and malformed ids) and every nested object with
``task=task`` (404 on a mismatch). Routes, relative to ``/api/human-eval/``:

- ``GET tasks/{id}/progress/`` → ``TaskProgress``
- ``GET tasks/{id}/assignments/`` (``participant_id, app_id, status, page, per_page``) →
  ``Paginated<AssignmentSummary>``; ``GET`` / ``PATCH tasks/{id}/assignments/{aid}/`` →
  ``AssignmentDetail`` / ``AssignmentSummary`` (manual exclusion)
- ``GET tasks/{id}/observations/`` (``kind, app_id, participant_id, page, per_page``) →
  ``Paginated<ResearcherObservation>``
- ``GET tasks/{id}/attachments/{attachment_id}/`` → the screenshot bytes
- ``GET tasks/{id}/http-log/`` (``participant_id, app_id, assignment_id, page, per_page``) →
  ``Paginated<HttpExchangeRecord>``
- ``GET tasks/{id}/results/`` (``group_by, include_excluded``) → ``TaskResults``
- ``GET tasks/{id}/export/bundle.zip``, ``.../export/results.json`` (``group_by``,
  ``include_excluded``), ``.../export/{kind}.csv`` (``include_excluded`` is accepted
  but CSVs always hold every row; spec R1)

Results are validated against :class:`TaskResultsSchema` (the TS contract): a
computation failure or a payload that breaks the contract is a 500 with the
detail, never an empty or partial result.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
from typing import Any

from django.http import FileResponse
from django.http import HttpResponse
from pydantic import ValidationError

from backend.human_eval.api.schemas.common import HumanEvalErrorSchema
from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.common import paginate
from backend.human_eval.api.schemas.common import paginated
from backend.human_eval.api.schemas.results import AssignmentDetailSchema
from backend.human_eval.api.schemas.results import AssignmentExclusionInputSchema
from backend.human_eval.api.schemas.results import AssignmentSummarySchema
from backend.human_eval.api.schemas.results import HttpExchangeRecordSchema
from backend.human_eval.api.schemas.results import ResearcherObservationSchema
from backend.human_eval.api.schemas.results import TaskProgressSchema
from backend.human_eval.api.schemas.results import TaskResultsSchema
from backend.human_eval.api.views._routers import router
from backend.human_eval.services import access
from backend.human_eval.services import export
from backend.human_eval.services import progress
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from backend.human_eval.models import Attachment
    from backend.human_eval.models import EvalTask

logger = logging.getLogger(__name__)

__all__ = ["router"]

#: Sniffed upload types (services/observations.py) → file extension.
ATTACHMENT_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_MAX_REPORTED_PROBLEMS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validated_results(task: EvalTask, *, group_by: str, include_excluded: bool) -> TaskResultsSchema:
    """``export.task_results`` checked against the TS ``TaskResults`` contract (500 ``results_invalid`` if not)."""
    data = export.task_results(task, group_by=group_by, include_excluded=include_excluded)
    try:
        return TaskResultsSchema.model_validate(data)
    except ValidationError as exc:
        logger.exception("human_eval: results of task %s do not match the TaskResults contract", task.pk)
        errors = [
            {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()[:_MAX_REPORTED_PROBLEMS]
        ]
        summary = "; ".join(f"{error['path']}: {error['message']}" for error in errors)
        msg = f"Results do not match the TaskResults contract ({exc.error_count()} problems): {summary}"
        raise HumanEvalServiceError(500, msg, "results_invalid", errors) from exc


def results_document(task: EvalTask, *, group_by: str = "condition", include_excluded: bool = False) -> dict[str, Any]:
    """The validated results as a JSON-ready dict (what ``results/`` returns)."""
    return validated_results(task, group_by=group_by, include_excluded=include_excluded).model_dump(
        mode="json",
        exclude_unset=True,
    )


def attachment_response(attachment: Attachment) -> FileResponse:
    """The attachment file with safe headers (spec §5.9, R2): sniffed type, inline, nosniff, CSP sandbox."""
    extension = ATTACHMENT_TYPES.get(attachment.content_type)
    try:
        handle = attachment.file.open("rb")
    except (OSError, ValueError) as exc:
        msg = "Attachment file not found"
        raise not_found(msg) from exc
    filename = f"{attachment.pk}.{extension or 'bin'}"
    response = FileResponse(
        handle,
        content_type=attachment.content_type if extension else "application/octet-stream",
        as_attachment=extension is None,
        filename=filename,
    )
    response["Content-Disposition"] = f'{"inline" if extension else "attachment"}; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=0"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


def _download(response: HttpResponse | FileResponse) -> HttpResponse | FileResponse:
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


# ---------------------------------------------------------------------------
# Progress and the responses browser
# ---------------------------------------------------------------------------


@router.get("/tasks/{uuid:task_id}/progress/", response={200: TaskProgressSchema, **error_responses(404)})
def get_progress(request, task_id: uuid.UUID):
    task = access.researcher_task(request, task_id)
    return progress.task_progress(task)


@router.get(
    "/tasks/{uuid:task_id}/assignments/",
    response={200: paginated(AssignmentSummarySchema), **error_responses(400, 404)},
)
def list_assignments(
    request,
    task_id: uuid.UUID,
    participant_id: str | None = None,
    app_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    task = access.researcher_task(request, task_id)
    rows = progress.assignment_rows(task, participant_id=participant_id, app_id=app_id, status=status)
    flags = progress.assignment_flags(task)
    return paginate(
        rows,
        page,
        per_page,
        serialize=lambda assignment: progress.serialize_assignment_summary(assignment, flags=flags),
    )


@router.get(
    "/tasks/{uuid:task_id}/assignments/{uuid:assignment_id}/",
    response={200: AssignmentDetailSchema, **error_responses(404)},
)
def get_assignment(request, task_id: uuid.UUID, assignment_id: uuid.UUID):
    task = access.researcher_task(request, task_id)
    assignment = progress.researcher_assignment(task, assignment_id)
    return progress.assignment_detail(task, assignment)


@router.patch(
    "/tasks/{uuid:task_id}/assignments/{uuid:assignment_id}/",
    response={200: AssignmentSummarySchema, **error_responses(400, 404)},
)
def update_assignment(
    request,
    task_id: uuid.UUID,
    assignment_id: uuid.UUID,
    payload: AssignmentExclusionInputSchema,
):
    task = access.researcher_task(request, task_id)
    assignment = progress.set_assignment_exclusion(
        task,
        assignment_id,
        excluded=payload.excluded,
        reason=payload.exclusion_reason,
        user=request.auth,
    )
    return progress.serialize_assignment_summary(assignment, flags=progress.assignment_flags(task))


@router.get(
    "/tasks/{uuid:task_id}/observations/",
    response={200: paginated(ResearcherObservationSchema), **error_responses(400, 404)},
)
def list_observations(
    request,
    task_id: uuid.UUID,
    kind: str | None = None,
    app_id: str | None = None,
    participant_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    task = access.researcher_task(request, task_id)
    rows = progress.observation_rows(task, kind=kind, app_id=app_id, participant_id=participant_id)
    return paginate(rows, page, per_page, serialize=progress.serialize_researcher_observation)


@router.get("/tasks/{uuid:task_id}/attachments/{uuid:attachment_id}/", response={404: HumanEvalErrorSchema})
def download_attachment(request, task_id: uuid.UUID, attachment_id: uuid.UUID):
    task = access.researcher_task(request, task_id)
    return attachment_response(progress.researcher_attachment(task, attachment_id))


@router.get(
    "/tasks/{uuid:task_id}/http-log/",
    response={200: paginated(HttpExchangeRecordSchema), **error_responses(404)},
)
def list_http_log(
    request,
    task_id: uuid.UUID,
    participant_id: str | None = None,
    app_id: str | None = None,
    assignment_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    task = access.researcher_task(request, task_id)
    rows = progress.http_exchange_rows(task, participant_id=participant_id, app_id=app_id, assignment_id=assignment_id)
    return paginate(rows, page, per_page, serialize=progress.serialize_http_exchange)


# ---------------------------------------------------------------------------
# Results and exports
# ---------------------------------------------------------------------------


@router.get(
    "/tasks/{uuid:task_id}/results/",
    response={200: TaskResultsSchema, **error_responses(400, 404, 500)},
    exclude_unset=True,
)
def get_results(request, task_id: uuid.UUID, group_by: str = "condition", include_excluded: bool = False):
    task = access.researcher_task(request, task_id)
    return validated_results(task, group_by=group_by, include_excluded=include_excluded)


# Literal export routes are registered before the parameterised ``{kind}.csv`` (spec R3.9).
@router.get("/tasks/{uuid:task_id}/export/bundle.zip", response=error_responses(404, 500))
def export_bundle(request, task_id: uuid.UUID):
    task = access.researcher_task(request, task_id)
    results = results_document(task)
    response = FileResponse(
        export.bundle_file(task, results),
        content_type="application/zip",
        as_attachment=True,
        filename=export.export_filename(task, "bundle", "zip"),
    )
    return _download(response)


@router.get("/tasks/{uuid:task_id}/export/results.json", response=error_responses(400, 404, 500))
def export_results_json(
    request,
    task_id: uuid.UUID,
    group_by: str = "condition",
    include_excluded: bool = False,
):
    task = access.researcher_task(request, task_id)
    body = export.results_json_bytes(results_document(task, group_by=group_by, include_excluded=include_excluded))
    response = HttpResponse(body, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{export.export_filename(task, "results", "json")}"'
    return _download(response)


@router.get("/tasks/{uuid:task_id}/export/{kind}.csv", response=error_responses(404))
def export_csv(request, task_id: uuid.UUID, kind: str, include_excluded: bool = False):
    # include_excluded is accepted for symmetry with results/ but ignored: CSVs always hold every row.
    task = access.researcher_task(request, task_id)
    export.check_kind(kind)
    response = FileResponse(
        export.csv_file(task, kind),
        content_type="text/csv; charset=utf-8",
        as_attachment=True,
        filename=export.export_filename(task, kind),
    )
    return _download(response)
