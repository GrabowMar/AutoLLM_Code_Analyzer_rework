"""Installed-tool management: install / uninstall / configure / test."""

from __future__ import annotations

from typing import Any

from django.db import connection
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from llm_lab.analysis.api.schema import InstalledToolSchema
from llm_lab.analysis.api.schema import InstallToolSchema
from llm_lab.analysis.api.schema import TestResultSchema
from llm_lab.analysis.api.schema import TestToolSchema
from llm_lab.analysis.api.schema import ToolConfigSchema
from llm_lab.analysis.api.views._router import analyzers_router
from llm_lab.analysis.models import AnalyzerTool
from llm_lab.analysis.models import InstalledTool
from llm_lab.analysis.services import tool_installer
from llm_lab.analysis.services import tool_tester
from llm_lab.analysis.services import workspace_service
from llm_lab.common.threading import dispatch_in_thread


def serialize_installed(it: InstalledTool) -> dict[str, Any]:
    return {
        "id": str(it.id),
        "tool_slug": it.tool.slug,
        "tool_name": it.tool.name,
        "category": it.tool.category,
        "status": it.status,
        "installed_version": it.installed_version,
        "config": it.config or {},
        "install_log": it.install_log,
    }


def _install_async(workspace_id, tool_id) -> None:
    try:
        from llm_lab.analysis.models import AnalyzerWorkspace

        workspace = AnalyzerWorkspace.objects.get(id=workspace_id)
        tool = AnalyzerTool.objects.get(id=tool_id)
        tool_installer.install(workspace, tool)
    finally:
        connection.close()


@analyzers_router.get("/workspace/tools/", response=list[InstalledToolSchema])
def list_installed(request):
    """List tools installed in the user's workspace."""
    ws = workspace_service.get_workspace(request.auth)
    return [serialize_installed(it) for it in ws.installed_tools.select_related("tool")]


@analyzers_router.post("/workspace/tools/", response=InstalledToolSchema)
def install_tool(request, payload: InstallToolSchema):
    """Install a catalog tool into the workspace (runs in background)."""
    ws = workspace_service.get_workspace(request.auth)
    tool = get_object_or_404(AnalyzerTool, slug=payload.tool_slug, is_enabled=True)
    installed, _created = InstalledTool.objects.get_or_create(
        workspace=ws,
        tool=tool,
        defaults={"config": dict(tool.default_config or {})},
    )
    installed.status = InstalledTool.Status.INSTALLING
    installed.save(update_fields=["status", "updated_at"])
    dispatch_in_thread(_install_async, ws.id, tool.id)
    return serialize_installed(installed)


@analyzers_router.put("/workspace/tools/{slug}/", response=InstalledToolSchema)
def update_tool_config(request, slug: str, payload: ToolConfigSchema):
    """Update per-user config for an installed tool."""
    ws = workspace_service.get_workspace(request.auth)
    it = get_object_or_404(ws.installed_tools.select_related("tool"), tool__slug=slug)
    it.config = payload.config
    it.save(update_fields=["config", "updated_at"])
    return serialize_installed(it)


@analyzers_router.delete("/workspace/tools/{slug}/", response={200: dict})
def uninstall_tool(request, slug: str):
    """Uninstall a tool from the workspace."""
    ws = workspace_service.get_workspace(request.auth)
    tool = get_object_or_404(AnalyzerTool, slug=slug)
    tool_installer.uninstall(ws, tool)
    return 200, {"status": "uninstalled", "tool_slug": slug}


@analyzers_router.post("/workspace/tools/{slug}/test/", response=TestResultSchema)
def test_tool(request, slug: str, payload: TestToolSchema):
    """Verify availability and run the tool against a sample snippet."""
    ws = workspace_service.get_workspace(request.auth)
    tool = get_object_or_404(AnalyzerTool, slug=slug)
    try:
        return tool_tester.test_tool(ws, tool, payload.config)
    except Exception as exc:  # noqa: BLE001
        raise HttpError(500, f"Test failed: {exc}") from exc
