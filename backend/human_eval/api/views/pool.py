"""Researcher app-pool endpoints (owner A): list / add / patch / delete task apps.

The task comes from ``access.researcher_task`` and each app is looked up within that
task (404 for another task's app). Adding and removing apps is only possible while the
task is a draft (409 ``design_frozen``); ``is_active`` can change at any time.
"""

from __future__ import annotations

import uuid

from ninja.responses import Status

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.tasks import TaskAppsAddIn
from backend.human_eval.api.schemas.tasks import TaskAppSchema
from backend.human_eval.api.schemas.tasks import TaskAppUpdateIn
from backend.human_eval.api.views._routers import router
from backend.human_eval.services import access
from backend.human_eval.services import counts
from backend.human_eval.services import pool as pool_service

__all__ = ["router"]


@router.get("tasks/{uuid:task_id}/apps/", response={200: list[TaskAppSchema], **error_responses(404)})
def get_task_apps(request, task_id: uuid.UUID):
    """The task's app pool, de-blinded, with hosting state and progress (TS ``TaskApp[]``)."""
    return pool_service.list_task_apps(access.researcher_task(request, task_id))


@router.post("tasks/{uuid:task_id}/apps/", response={200: list[TaskAppSchema], **error_responses(400, 404, 409)})
def post_task_apps(request, task_id: uuid.UUID, payload: TaskAppsAddIn):
    """Add jobs and/or an experiment's current attempts to a draft task; returns the whole pool."""
    task = access.researcher_task(request, task_id, for_update=True)
    pool_service.add_jobs(
        task,
        request.auth,
        job_ids=payload.job_ids,
        experiment_id=payload.experiment_id,
        current_only=payload.current_only,
    )
    return pool_service.list_task_apps(task)


@router.patch(
    "tasks/{uuid:task_id}/apps/{uuid:app_id}/",
    response={200: TaskAppSchema, **error_responses(400, 404, 409)},
)
def patch_task_app(request, task_id: uuid.UUID, app_id: uuid.UUID, payload: TaskAppUpdateIn):
    """Activate / deactivate an app (any time) or rename it (drafts only)."""
    task = access.researcher_task(request, task_id, for_update=True)
    app = pool_service.task_app_or_404(task, app_id)
    pool_service.update_task_app(task, app, payload.model_dump(exclude_unset=True))
    return pool_service.serialize_task_app(app, counts.app_stats(task))


@router.delete("tasks/{uuid:task_id}/apps/{uuid:app_id}/", response={204: None, **error_responses(404, 409)})
def delete_task_app(request, task_id: uuid.UUID, app_id: uuid.UUID):
    """Remove an app from a draft task (409 ``design_frozen`` once opened: deactivate it instead)."""
    task = access.researcher_task(request, task_id, for_update=True)
    pool_service.delete_task_app(task, pool_service.task_app_or_404(task, app_id))
    return Status(204, None)
