"""Researcher hosting endpoints (owner C): environment, task hosting summary and actions, per-app hosting actions.

Routes (on the shared researcher router, mounted at ``/api/human-eval/``):

- ``GET environment/`` → ``HumanEvalEnvironment`` (any researcher);
- ``GET/POST tasks/{task_id}/hosting/`` → ``TaskHosting`` (POST ``{action: start_all|stop_all}``);
- ``POST tasks/{task_id}/apps/{app_id}/hosting/`` ``{action: start|stop|pin|unpin|rebuild}`` →
  ``ResearcherHostingInfo``.

Tasks are owner-scoped (staff see all; others' tasks and malformed ids → 404) and the app is
looked up within the task (another task's app → 404). Container work is dispatched on commit by
``services/hosting.py``.
"""

from __future__ import annotations

from uuid import UUID

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.hosting import HeAppHostingActionSchema
from backend.human_eval.api.schemas.hosting import HeEnvironmentSchema
from backend.human_eval.api.schemas.hosting import HeResearcherHostingInfoSchema
from backend.human_eval.api.schemas.hosting import HeTaskHostingActionSchema
from backend.human_eval.api.schemas.hosting import HeTaskHostingSchema
from backend.human_eval.api.views._routers import router
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import access
from backend.human_eval.services import hosting
from backend.human_eval.services.errors import not_found

__all__ = ["router"]


@router.get("/environment/", response=HeEnvironmentSchema)
def get_environment(request):
    """Routing mode, app caps, environment warnings, and the design defaults/instruments for editors."""
    return hosting.environment_info()


@router.get("/tasks/{uuid:task_id}/hosting/", response={200: HeTaskHostingSchema, **error_responses(404)})
def get_task_hosting(request, task_id: UUID):
    """Hosting state of every app of the task, plus the task and server-wide caps."""
    task = access.researcher_task(request, task_id)
    return hosting.task_hosting(task)


@router.post("/tasks/{uuid:task_id}/hosting/", response={200: HeTaskHostingSchema, **error_responses(400, 404, 409)})
def post_task_hosting(request, task_id: UUID, payload: HeTaskHostingActionSchema):
    """``start_all`` pins and starts every active app (the rest queue); ``stop_all`` unpins and stops them."""
    task = access.researcher_task(request, task_id)
    return hosting.task_hosting_action(task, payload.action, user=request.auth)


@router.post(
    "/tasks/{uuid:task_id}/apps/{uuid:app_id}/hosting/",
    response={200: HeResearcherHostingInfoSchema, **error_responses(400, 404, 409)},
)
def post_app_hosting(request, task_id: UUID, app_id: UUID, payload: HeAppHostingActionSchema):
    """Start, stop, pin, unpin or rebuild one app of the task."""
    task = access.researcher_task(request, task_id)
    task_app = EvalTaskApp.objects.select_related("task", "job", "container").filter(task=task, pk=app_id).first()
    if task_app is None:
        msg = "App not found"
        raise not_found(msg)
    return hosting.app_hosting_action(task_app, payload.action, user=request.auth)
