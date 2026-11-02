"""Researcher task endpoints (owner A): presets, validate-design, candidate-jobs, tasks CRUD, status, duplicate,
items, preview.

Every task is looked up through ``access.researcher_task`` (owner or staff; 404 for other
users' tasks, unknown and malformed ids). Services validate before writing and run their
multi-step writes in ``transaction.atomic`` blocks, so a 4xx leaves nothing behind even
though ninja's error responses let ``ATOMIC_REQUESTS`` commit.
"""

from __future__ import annotations

import uuid

from django.http import HttpResponse  # noqa: TC002 - ninja resolves the temporal-response annotation at runtime
from ninja.responses import Status

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.common import paginated
from backend.human_eval.api.schemas.tasks import CandidateJobSchema
from backend.human_eval.api.schemas.tasks import DesignValidationIn
from backend.human_eval.api.schemas.tasks import DesignValidationSchema
from backend.human_eval.api.schemas.tasks import ItemSchema
from backend.human_eval.api.schemas.tasks import PresetSchema
from backend.human_eval.api.schemas.tasks import PreviewSessionSchema
from backend.human_eval.api.schemas.tasks import TaskCreateIn
from backend.human_eval.api.schemas.tasks import TaskDetailSchema
from backend.human_eval.api.schemas.tasks import TaskListItemSchema
from backend.human_eval.api.schemas.tasks import TaskStatusIn
from backend.human_eval.api.schemas.tasks import TaskUpdateIn
from backend.human_eval.api.views._routers import router
from backend.human_eval.services import access
from backend.human_eval.services import items as items_service
from backend.human_eval.services import participants as participants_service
from backend.human_eval.services import pool as pool_service
from backend.human_eval.services import tasks as tasks_service
from backend.human_eval.services.presets import list_presets

__all__ = ["router"]


def _detail(request, task_id) -> dict:
    return tasks_service.serialize_task_detail(access.researcher_task(request, task_id))


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("presets/", response=list[PresetSchema])
def get_presets(request):
    """Ready-made study designs (TS ``Preset[]``)."""
    return list_presets()


@router.post("validate-design/", response=DesignValidationSchema)
def validate_design(request, payload: DesignValidationIn):
    """Validate and normalize ``{mode, design, settings}`` (TS ``DesignValidation``; always 200)."""
    return tasks_service.validate_documents(payload.mode, payload.design, payload.settings)


@router.get("candidate-jobs/", response=paginated(CandidateJobSchema))
def get_candidate_jobs(
    request,
    experiment_id: str = "",
    app_slug: str = "",
    model_id: str = "",
    search: str = "",
    current_only: bool = True,
    page: int = 1,
    per_page: int = 20,
):
    """The caller's completed generation jobs that can join a task's pool (TS ``Paginated<CandidateJob>``)."""
    return pool_service.candidate_jobs(
        request.auth,
        experiment_id=experiment_id or None,
        app_slug=app_slug or None,
        model_id=model_id or None,
        search=search or None,
        current_only=current_only,
        page=page,
        per_page=per_page,
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.get("tasks/", response=paginated(TaskListItemSchema))
def get_tasks(request, status: str = "", search: str = "", page: int = 1, per_page: int = 20):
    """The caller's tasks (staff: every task), newest first (TS ``Paginated<TaskListItem>``)."""
    return tasks_service.list_tasks(
        request.auth,
        status=status or None,
        search=search or None,
        page=page,
        per_page=per_page,
    )


@router.post("tasks/", response={201: TaskDetailSchema, **error_responses(400, 404, 409)})
def post_task(request, payload: TaskCreateIn):
    """Create a draft task (TS ``TaskCreateInput`` → 201 ``TaskDetail``)."""
    task = tasks_service.create_task(request.auth, payload.model_dump())
    return Status(201, _detail(request, task.id))


@router.get("tasks/{uuid:task_id}/", response={200: TaskDetailSchema, **error_responses(404)})
def get_task(request, task_id: uuid.UUID):
    """TS ``TaskDetail``."""
    return _detail(request, task_id)


@router.patch("tasks/{uuid:task_id}/", response={200: TaskDetailSchema, **error_responses(400, 404, 409)})
def patch_task(request, task_id: uuid.UUID, payload: TaskUpdateIn):
    """Update a task (TS ``TaskUpdateInput``; frozen parts answer 409 ``design_frozen`` after open)."""
    task = access.researcher_task(request, task_id, for_update=True)
    tasks_service.update_task(task, request.auth, payload.model_dump(exclude_unset=True))
    return _detail(request, task_id)


@router.delete("tasks/{uuid:task_id}/", response={204: None, **error_responses(404)})
def delete_task(request, task_id: uuid.UUID):
    """Delete a task in any status with all its data (started apps are stopped first)."""
    tasks_service.delete_task(access.researcher_task(request, task_id, for_update=True))
    return Status(204, None)


@router.post("tasks/{uuid:task_id}/duplicate/", response={201: TaskDetailSchema, **error_responses(404)})
def duplicate_task(request, task_id: uuid.UUID):
    """A new draft copy of the task (same design, settings and apps; no participants)."""
    copy = tasks_service.duplicate_task(access.researcher_task(request, task_id), request.auth)
    return Status(201, _detail(request, copy.id))


@router.post("tasks/{uuid:task_id}/status/", response={200: TaskDetailSchema, **error_responses(400, 404, 409)})
def post_task_status(request, task_id: uuid.UUID, payload: TaskStatusIn):
    """Apply a status action (TS ``TaskAction``); the first ``open`` freezes the design and builds the items."""
    task = access.researcher_task(request, task_id, for_update=True)
    tasks_service.transition(task, request.auth, payload.action)
    return _detail(request, task_id)


@router.get("tasks/{uuid:task_id}/items/", response={200: list[ItemSchema], **error_responses(404)})
def get_items(request, task_id: uuid.UUID):
    """The task's evaluation items with progress (TS ``Item[]``; empty before the first open)."""
    return items_service.list_items(access.researcher_task(request, task_id))


@router.post("tasks/{uuid:task_id}/preview/", response={200: PreviewSessionSchema, **error_responses(404, 409)})
def post_preview(request, response: HttpResponse, task_id: uuid.UUID):
    """Start (or resume) the caller's own preview of the task in the portal (409 ``not_frozen`` on drafts).

    A participant cookie of the same task in this browser (the researcher tried their own invite link
    or code) is ended first, so the preview work never lands on that real participant.
    """
    task = access.researcher_task(request, task_id)
    session = participants_service.start_preview(task, request.auth)
    access.end_task_cookie_session(request, task.pk, response)
    return session


@router.delete("tasks/{uuid:task_id}/preview/", response={204: None, **error_responses(404)})
def delete_preview(request, task_id: uuid.UUID):
    """Discard the caller's preview participant of the task and its data."""
    participants_service.delete_preview(access.researcher_task(request, task_id), request.auth)
    return Status(204, None)
