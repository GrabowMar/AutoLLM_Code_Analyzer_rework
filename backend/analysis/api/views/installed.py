"""Installed-tool management: install / update / uninstall / configure / test."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import connection
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja.errors import HttpError

from backend.analysis.api.schema import BulkInstallSchema
from backend.analysis.api.schema import InstalledToolSchema
from backend.analysis.api.schema import InstallToolSchema
from backend.analysis.api.schema import TestResultSchema
from backend.analysis.api.schema import TestToolSchema
from backend.analysis.api.schema import ToolConfigSchema
from backend.analysis.api.schema import UninstallResultSchema
from backend.analysis.api.schema import UpdateAllSchema
from backend.analysis.api.views._router import analyzers_router
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import InstalledTool
from backend.analysis.services import tool_installer
from backend.analysis.services import tool_tester
from backend.analysis.services import workspace_service
from backend.common.threading import dispatch_in_thread


def serialize_installed(it: InstalledTool) -> dict[str, Any]:
    return {
        "id": str(it.id),
        "tool_slug": it.tool.slug,
        "tool_name": it.tool.name,
        "category": it.tool.category,
        "status": it.status,
        "installed_version": it.installed_version,
        "catalog_version": it.catalog_version,
        "latest_version": it.tool.version,
        "update_available": it.update_available,
        "config": it.config or {},
        "install_log": it.install_log,
        "updated_at": it.updated_at.isoformat() if it.updated_at else None,
    }


_BUSY = (InstalledTool.Status.INSTALLING, InstalledTool.Status.UPDATING)
# Grace beyond a tool's install timeout before a busy row counts as abandoned
# (e.g. the install thread died with a server reload) and may be retried.
_STALE_GRACE = timedelta(minutes=2)


def _is_busy(it: InstalledTool) -> bool:
    if it.status not in _BUSY:
        return False
    deadline = it.updated_at + timedelta(seconds=it.tool.install_timeout) + _STALE_GRACE
    return timezone.now() < deadline


def _install_async(workspace_id, tool_ids: list, update: bool) -> None:
    try:
        from backend.analysis.models import AnalyzerWorkspace

        workspace = AnalyzerWorkspace.objects.get(id=workspace_id)
        by_id = AnalyzerTool.objects.in_bulk(tool_ids)
        tool_installer.install_many(workspace, [by_id[i] for i in tool_ids if i in by_id], update=update)
    finally:
        connection.close()


def _queue(ws, tools: list[AnalyzerTool], *, update: bool) -> list[InstalledTool]:
    """Mark *tools* busy and install them sequentially on one background thread."""
    status = InstalledTool.Status.UPDATING if update else InstalledTool.Status.INSTALLING
    queued = []
    for tool in tools:
        it, created = InstalledTool.objects.get_or_create(
            workspace=ws,
            tool=tool,
            defaults={"config": dict(tool.default_config or {})},
        )
        if not created and _is_busy(it):
            continue
        it.status = status
        it.save(update_fields=["status", "updated_at"])
        queued.append(it)
    if queued:
        dispatch_in_thread(_install_async, ws.id, [it.tool_id for it in queued], update)
    return queued


@analyzers_router.get("/workspace/tools/", response=list[InstalledToolSchema])
def list_installed(request):
    """List tools installed in the user's workspace."""
    ws = workspace_service.get_workspace(request.auth)
    return [serialize_installed(it) for it in ws.installed_tools.select_related("tool")]


@analyzers_router.post("/workspace/tools/", response=InstalledToolSchema)
def install_tool(request, payload: InstallToolSchema):
    """Install a catalog tool into the workspace (runs in background).

    Re-posting an installed or failed tool reinstalls it.
    """
    ws = workspace_service.get_workspace(request.auth)
    tool = get_object_or_404(AnalyzerTool, slug=payload.tool_slug, is_enabled=True)
    _queue(ws, [tool], update=False)
    return serialize_installed(ws.installed_tools.select_related("tool").get(tool=tool))


@analyzers_router.post("/workspace/tools/bulk/", response=list[InstalledToolSchema])
def bulk_install(request, payload: BulkInstallSchema):
    """Install several catalog tools, one after another, in the background."""
    ws = workspace_service.get_workspace(request.auth)
    tools = list(AnalyzerTool.objects.filter(slug__in=payload.tool_slugs, is_enabled=True))
    if not tools:
        raise HttpError(404, "No matching tools")
    return [serialize_installed(it) for it in _queue(ws, tools, update=False)]


@analyzers_router.post("/workspace/tools/update-all/", response=list[InstalledToolSchema])
def update_all(request, payload: UpdateAllSchema):
    """Update installed tools (only outdated ones by default) in the background."""
    ws = workspace_service.get_workspace(request.auth)
    candidates = [
        it
        for it in ws.installed_tools.select_related("tool").filter(tool__is_enabled=True)
        if not _is_busy(it) and (it.update_available or not payload.only_outdated)
    ]
    return [serialize_installed(it) for it in _queue(ws, [it.tool for it in candidates], update=True)]


@analyzers_router.post("/workspace/tools/{slug}/update/", response=InstalledToolSchema)
def update_tool(request, slug: str):
    """Re-run a tool's install to upgrade it (keeps its config)."""
    ws = workspace_service.get_workspace(request.auth)
    it = get_object_or_404(ws.installed_tools.select_related("tool"), tool__slug=slug)
    if _is_busy(it):
        raise HttpError(409, f"{it.tool.name} is already being installed")
    _queue(ws, [it.tool], update=it.status == InstalledTool.Status.INSTALLED)
    it.refresh_from_db()
    return serialize_installed(it)


@analyzers_router.put("/workspace/tools/{slug}/", response=InstalledToolSchema)
def update_tool_config(request, slug: str, payload: ToolConfigSchema):
    """Update per-user config for an installed tool."""
    ws = workspace_service.get_workspace(request.auth)
    it = get_object_or_404(ws.installed_tools.select_related("tool"), tool__slug=slug)
    it.config = payload.config
    it.save(update_fields=["config", "updated_at"])
    return serialize_installed(it)


@analyzers_router.delete("/workspace/tools/{slug}/", response=UninstallResultSchema)
def uninstall_tool(request, slug: str):
    """Uninstall a tool from the workspace and remove its package if possible."""
    ws = workspace_service.get_workspace(request.auth)
    tool = get_object_or_404(AnalyzerTool, slug=slug)
    if any(_is_busy(it) for it in ws.installed_tools.select_related("tool").filter(tool=tool)):
        raise HttpError(409, f"{tool.name} is being installed; try again when it finishes")
    message = tool_installer.uninstall(ws, tool)
    return {"status": "uninstalled", "tool_slug": slug, "message": message}


@analyzers_router.post("/workspace/tools/{slug}/test/", response=TestResultSchema)
def test_tool(request, slug: str, payload: TestToolSchema):
    """Verify availability and run the tool against a sample snippet."""
    ws = workspace_service.get_workspace(request.auth)
    tool = get_object_or_404(AnalyzerTool, slug=slug)
    try:
        return tool_tester.test_tool(ws, tool, payload.config)
    except Exception as exc:
        raise HttpError(500, f"Test failed: {exc}") from exc
