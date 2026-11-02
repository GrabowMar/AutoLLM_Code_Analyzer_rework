"""Portal workbench endpoints (owner B2).

Assignment view, responses, submit, skip, observations, attachments, finding verdicts, source viewer.

All operations sit on the shared portal router (auth = ``PortalAuth``: CSRF on
unsafe methods, cookie participant and/or session user; 401 without either).
Every object is looked up through ``services.access`` so it must belong to the
caller's participant (404 otherwise); mutations of read-only assignments answer
409 ``read_only``. Services raise ``HumanEvalServiceError`` (rendered as
``{detail, code, errors}``) and keep refused mutations from leaving rows behind.

Participants never receive model names/ids, condition labels, job ids,
experiment ids, task-app ids or container details: payloads are built from the
blinded label, the display slot, ``hosting.participant_hosting_info`` and the
frozen context (hosting provenance withheld, see ``context.portal_context``).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from typing import Any

from ninja import File
from ninja.files import UploadedFile  # noqa: TC002 - ninja resolves the annotation at runtime
from ninja.responses import Status

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.portal_work import WorkAnswersInput
from backend.human_eval.api.schemas.portal_work import WorkAttachment
from backend.human_eval.api.schemas.portal_work import WorkFindingVerdict
from backend.human_eval.api.schemas.portal_work import WorkFindingVerdictInput
from backend.human_eval.api.schemas.portal_work import WorkObservation
from backend.human_eval.api.schemas.portal_work import WorkObservationInput
from backend.human_eval.api.schemas.portal_work import WorkObservationPatch
from backend.human_eval.api.schemas.portal_work import WorkPortalAssignment
from backend.human_eval.api.schemas.portal_work import WorkResponseSaved
from backend.human_eval.api.schemas.portal_work import WorkSkipInput
from backend.human_eval.api.schemas.portal_work import WorkSourceFile
from backend.human_eval.api.schemas.portal_work import WorkSourceTree
from backend.human_eval.api.schemas.portal_work import WorkSubmitResult
from backend.human_eval.api.views._routers import portal_router
from backend.human_eval.services import access
from backend.human_eval.services import context as context_service
from backend.human_eval.services import design as design_service
from backend.human_eval.services import observations as observation_service
from backend.human_eval.services import responses as response_service
from backend.human_eval.services import source as source_service
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import not_found

if TYPE_CHECKING:
    from backend.human_eval.models import Assignment

__all__ = ["portal_router"]


# ---------------------------------------------------------------------------
# Assignment view
# ---------------------------------------------------------------------------


def _jobs(assignment: Assignment) -> dict[Any, Any]:
    """The assignment's jobs by id, without the (possibly multi-MB) code until the source viewer needs it."""
    from backend.generation.models import GenerationJob

    job_ids = [app.job_id for _, app in context_service.slot_apps(assignment) if app.job_id is not None]
    return {job.pk: job for job in GenerationJob.objects.filter(pk__in=job_ids).defer("result_data", "metrics")}


def _portal_app(
    assignment: Assignment,
    slot: str,
    task_app: Any,
    settings: dict[str, Any],
    jobs: dict[Any, Any],
) -> dict[str, Any]:
    from backend.generation.services.evaluation.contract import ADMIN_PW_DEFAULT
    from backend.generation.services.evaluation.contract import ADMIN_USERNAME
    from backend.human_eval.services import hosting

    blinding = settings["blinding"]
    job = jobs.get(task_app.job_id)
    credentials = {"username": ADMIN_USERNAME, "password": ADMIN_PW_DEFAULT}
    return {
        "slot": slot,
        "label": task_app.label,
        "hosting": hosting.participant_hosting_info(task_app, assignment=assignment),
        # The spec frozen at first task open: the one the requirement items were derived from.
        "spec": context_service.app_spec_view(task_app, job) if blinding.get("show_spec") else None,
        "admin_credentials": credentials if blinding.get("show_admin_credentials") else None,
        "has_source": bool(blinding.get("show_source_code")) and source_service.has_source(job),
    }


def assignment_payload(assignment: Assignment) -> dict[str, Any]:
    """TS ``PortalAssignment`` for the assignment's own participant."""
    task = assignment.task
    design = task.design or {}
    settings = design_service.merged_settings(task.settings)
    jobs = _jobs(assignment)
    apps = [_portal_app(assignment, slot, app, settings, jobs) for slot, app in context_service.slot_apps(assignment)]
    return {
        "id": str(assignment.id),
        "task": {"id": str(task.id), "title": task.title, "mode": task.mode, "instructions": task.instructions},
        "status": assignment.status,
        "position": assignment.position,
        "started_at": assignment.started_at.isoformat() if assignment.started_at else None,
        "submitted_at": assignment.submitted_at.isoformat() if assignment.submitted_at else None,
        "active_seconds": assignment.active_seconds,
        "read_only": access.assignment_read_only(assignment),
        "apps": apps,
        "blocks": design_service.participant_blocks(design),
        "context": context_service.portal_context(assignment),
        "responses": response_service.responses_payload(assignment),
        "observations": observation_service.list_observations(assignment),
        "finding_verdicts": response_service.verdicts_payload(assignment),
        "allow_skip": bool(settings["allocation"].get("allow_skip")),
        "http_console": design_service.has_http_console(design),
    }


@portal_router.get(
    "assignments/{uuid:assignment_id}/",
    response={200: WorkPortalAssignment, **error_responses(404)},
    summary="Open an assignment in the workbench",
)
def get_assignment(request, assignment_id: uuid.UUID):
    """The assignment as the participant sees it; the first open starts it and freezes its context."""
    assignment = access.portal_assignment(request, assignment_id)
    context_service.open_assignment(assignment)
    return Status(200, assignment_payload(assignment))


# ---------------------------------------------------------------------------
# Responses, submit, skip
# ---------------------------------------------------------------------------


@portal_router.put(
    "assignments/{uuid:assignment_id}/responses/{block_id}/",
    response={200: WorkResponseSaved, **error_responses(400, 404, 409)},
    summary="Autosave one block's answers",
)
def put_response(request, assignment_id: uuid.UUID, block_id: str, payload: WorkAnswersInput):
    assignment = access.portal_assignment(request, assignment_id)
    response = response_service.save_response(assignment, block_id, payload.answers)
    return Status(200, response_service.response_payload(response))


@portal_router.post(
    "assignments/{uuid:assignment_id}/submit/",
    response={200: WorkSubmitResult, **error_responses(400, 404, 409)},
    summary="Submit the assignment (400 incomplete lists every missing answer)",
)
def submit_assignment(request, assignment_id: uuid.UUID):
    assignment = access.portal_assignment(request, assignment_id)
    errors = response_service.submit(assignment)
    if errors:
        msg = "Some answers are missing or not valid"
        raise HumanEvalServiceError(400, msg, "incomplete", errors)
    return Status(200, {"status": assignment.status, "next": response_service.next_after(assignment.participant)})


@portal_router.post(
    "assignments/{uuid:assignment_id}/skip/",
    response={200: WorkSubmitResult, **error_responses(400, 404, 409)},
    summary="Skip the assignment",
)
def skip_assignment(request, assignment_id: uuid.UUID, payload: WorkSkipInput):
    assignment = access.portal_assignment(request, assignment_id)
    response_service.skip(assignment, payload.reason, payload.note)
    return Status(200, {"status": assignment.status, "next": response_service.next_after(assignment.participant)})


# ---------------------------------------------------------------------------
# Observations and attachments
# ---------------------------------------------------------------------------


@portal_router.get(
    "assignments/{uuid:assignment_id}/observations/",
    response={200: list[WorkObservation], **error_responses(404)},
    summary="List the assignment's reports",
)
def list_observations(request, assignment_id: uuid.UUID):
    assignment = access.portal_assignment(request, assignment_id)
    return Status(200, observation_service.list_observations(assignment))


@portal_router.post(
    "assignments/{uuid:assignment_id}/observations/",
    response={201: WorkObservation, **error_responses(400, 404, 409)},
    summary="Log a bug, vulnerability or note",
)
def create_observation(request, assignment_id: uuid.UUID, payload: WorkObservationInput):
    assignment = access.portal_assignment(request, assignment_id)
    observation = observation_service.create_observation(assignment, payload.model_dump())
    return Status(201, observation_service.serialize_observation(observation))


@portal_router.patch(
    "observations/{uuid:observation_id}/",
    response={200: WorkObservation, **error_responses(400, 404, 409)},
    summary="Edit a report (its app slot cannot change)",
)
def update_observation(request, observation_id: uuid.UUID, payload: WorkObservationPatch):
    observation = access.portal_observation(request, observation_id)
    observation = observation_service.update_observation(observation, payload.model_dump(exclude_unset=True))
    return Status(200, observation_service.serialize_observation(observation))


@portal_router.delete(
    "observations/{uuid:observation_id}/",
    response={204: None, **error_responses(404, 409)},
    summary="Delete a report and its files",
)
def delete_observation(request, observation_id: uuid.UUID):
    observation = access.portal_observation(request, observation_id)
    observation_service.delete_observation(observation)
    return Status(204, None)


@portal_router.post(
    "observations/{uuid:observation_id}/attachments/",
    response={201: WorkAttachment, **error_responses(400, 404, 409)},
    summary="Attach a screenshot (PNG, JPEG, GIF or WEBP)",
)
def upload_attachment(
    request,
    observation_id: uuid.UUID,
    file: UploadedFile = File(...),  # noqa: B008 - ninja's parameter declaration
):
    observation = access.portal_observation(request, observation_id)
    attachment = observation_service.add_attachment(observation, file)
    return Status(201, observation_service.serialize_attachment(attachment))


@portal_router.get("attachments/{uuid:attachment_id}/", summary="Download an attachment (image bytes)")
def get_attachment(request, attachment_id: uuid.UUID):
    attachment = access.portal_attachment(request, attachment_id)
    return observation_service.file_response(attachment)


@portal_router.delete(
    "attachments/{uuid:attachment_id}/",
    response={204: None, **error_responses(404, 409)},
    summary="Delete an attachment",
)
def delete_attachment(request, attachment_id: uuid.UUID):
    attachment = access.portal_attachment(request, attachment_id)
    observation_service.delete_attachment(attachment)
    return Status(204, None)


# ---------------------------------------------------------------------------
# Finding verdicts
# ---------------------------------------------------------------------------


@portal_router.put(
    "assignments/{uuid:assignment_id}/finding-verdicts/{uuid:finding_id}/",
    response={200: WorkFindingVerdict, **error_responses(400, 404, 409)},
    summary="Record a true/false positive verdict on a sampled finding",
)
def put_finding_verdict(
    request,
    assignment_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: WorkFindingVerdictInput,
):
    assignment = access.portal_assignment(request, assignment_id)
    record = response_service.set_finding_verdict(assignment, str(finding_id), payload.verdict, payload.note)
    return Status(200, {"finding_key": record.finding_key, "verdict": record.verdict, "note": record.note})


# ---------------------------------------------------------------------------
# Source viewer
# ---------------------------------------------------------------------------


def _source_job(request, assignment_id: uuid.UUID, slot: str) -> Any:
    assignment = access.portal_assignment(request, assignment_id)
    settings = design_service.merged_settings(assignment.task.settings)
    if not settings["blinding"].get("show_source_code"):
        msg = "Source code is not shown in this task"
        raise not_found(msg)
    return context_service.app_for_slot(assignment, slot).job


@portal_router.get(
    "assignments/{uuid:assignment_id}/source/",
    response={200: WorkSourceTree, **error_responses(404)},
    summary="The file tree of an app's generated code",
)
def get_source_tree(request, assignment_id: uuid.UUID, slot: str = ""):
    return Status(200, source_service.source_tree(_source_job(request, assignment_id, slot)))


@portal_router.get(
    "assignments/{uuid:assignment_id}/source/file/",
    response={200: WorkSourceFile, **error_responses(404)},
    summary="One file of an app's generated code (capped at 512 KB)",
)
def get_source_file(request, assignment_id: uuid.UUID, slot: str = "", path: str = ""):
    return Status(200, source_service.source_file(_source_job(request, assignment_id, slot), path))
