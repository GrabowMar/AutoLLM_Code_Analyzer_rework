"""Portal hosting endpoints (owner C): heartbeat, ensure app running, HTTP console.

Routes (on the portal router, mounted at ``/api/human-eval/portal/``; ``PortalAuth``
enforces CSRF on these POSTs):

- ``POST assignments/{assignment_id}/heartbeat/`` ``HeartbeatInput`` → ``HeartbeatResult``;
- ``POST assignments/{assignment_id}/apps/{slot}/ensure/`` → ``HostingInfo`` (409 ``read_only``
  once the assignment can no longer change);
- ``POST assignments/{assignment_id}/console/`` ``ConsoleRequest`` → ``ConsoleResponse``.

Assignments are looked up for the caller's participant only (404 otherwise). Heartbeat and
ensure lock the assignment row first (lock order: assignment → hosting capacity lock → task
apps); the console does not hold the row while the request to the app is in flight.
"""

from __future__ import annotations

from uuid import UUID

from backend.human_eval.api.schemas.common import error_responses
from backend.human_eval.api.schemas.hosting import HeConsoleRequestSchema
from backend.human_eval.api.schemas.hosting import HeConsoleResponseSchema
from backend.human_eval.api.schemas.hosting import HeHeartbeatInputSchema
from backend.human_eval.api.schemas.hosting import HeHeartbeatResultSchema
from backend.human_eval.api.schemas.hosting import HeHostingInfoSchema
from backend.human_eval.api.views._routers import portal_router
from backend.human_eval.services import access
from backend.human_eval.services import console
from backend.human_eval.services import hosting

__all__ = ["portal_router"]


@portal_router.post(
    "/assignments/{uuid:assignment_id}/heartbeat/",
    response={200: HeHeartbeatResultSchema, **error_responses(404)},
)
def post_heartbeat(request, assignment_id: UUID, payload: HeHeartbeatInputSchema):
    """Liveness and timing beat; returns the assignment status and each slot's hosting."""
    assignment = access.portal_assignment(request, assignment_id, for_update=True)
    return hosting.heartbeat(
        assignment,
        active_seconds=payload.active_seconds,
        visible=payload.visible,
        viewing=payload.viewing,
    )


@portal_router.post(
    "/assignments/{uuid:assignment_id}/apps/{slot}/ensure/",
    response={200: HeHostingInfoSchema, **error_responses(404, 409)},
)
def post_ensure(request, assignment_id: UUID, slot: str):
    """Start (or queue, or reset) the slot's app for this assignment."""
    assignment = access.portal_assignment(request, assignment_id, for_update=True)
    return hosting.ensure_for_assignment(assignment, slot)


@portal_router.post(
    "/assignments/{uuid:assignment_id}/console/",
    response={200: HeConsoleResponseSchema, **error_responses(400, 404, 409, 429)},
)
def post_console(request, assignment_id: UUID, payload: HeConsoleRequestSchema):
    """Send one HTTP request to the slot's running app and return the (bounded) response."""
    assignment = access.portal_assignment(request, assignment_id)
    return console.send(assignment, payload.slot, payload.method, payload.path, payload.headers, payload.body)
