"""Per-user analyzer workspace (container) lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from django.db import connection

from llm_lab.analysis.api.schema import WorkspaceSchema
from llm_lab.analysis.api.views._router import analyzers_router
from llm_lab.analysis.models import AnalyzerWorkspace
from llm_lab.analysis.services import workspace_service
from llm_lab.common.threading import dispatch_in_thread


def serialize_workspace(ws: AnalyzerWorkspace) -> dict[str, Any]:
    return {
        "id": str(ws.id),
        "status": ws.status,
        "image": ws.image,
        "error_message": ws.error_message,
        "container_name": ws.container_name,
        "last_used_at": ws.last_used_at.isoformat() if ws.last_used_at else None,
        "installed_count": ws.installed_tools.count(),
    }


def _provision_async(user_id) -> None:
    from llm_lab.users.models import User

    try:
        user = User.objects.get(pk=user_id)
        workspace_service.provision(user)
    finally:
        connection.close()


@analyzers_router.get("/workspace/", response=WorkspaceSchema)
def get_workspace(request):
    """Return the current user's workspace status."""
    ws = workspace_service.get_workspace(request.auth)
    return serialize_workspace(ws)


@analyzers_router.post("/workspace/provision/", response=WorkspaceSchema)
def provision_workspace(request):
    """Provision (build/start) the workspace container in the background."""
    ws = workspace_service.get_workspace(request.auth)
    if ws.status != AnalyzerWorkspace.Status.READY:
        ws.status = AnalyzerWorkspace.Status.PROVISIONING
        ws.error_message = ""
        ws.save(update_fields=["status", "error_message", "updated_at"])
    dispatch_in_thread(_provision_async, request.auth.id)
    return serialize_workspace(ws)


@analyzers_router.post("/workspace/start/", response=WorkspaceSchema)
def start_workspace(request):
    """Alias for provision (start a stopped workspace)."""
    return provision_workspace(request)


@analyzers_router.post("/workspace/stop/", response=WorkspaceSchema)
def stop_workspace(request):
    """Stop the workspace container (keeps installed tools)."""
    ws = workspace_service.get_workspace(request.auth)
    ws = workspace_service.stop(ws)
    return serialize_workspace(ws)


@analyzers_router.delete("/workspace/", response=WorkspaceSchema)
def delete_workspace(request):
    """Tear down the workspace container and clear installed tools."""
    ws = workspace_service.get_workspace(request.auth)
    ws = workspace_service.teardown(ws)
    return serialize_workspace(ws)
