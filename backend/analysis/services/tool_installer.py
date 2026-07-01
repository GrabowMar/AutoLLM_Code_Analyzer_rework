"""Install / uninstall / verify catalog tools inside a user's workspace."""

from __future__ import annotations

import logging

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import AnalyzerWorkspace
from backend.analysis.models import InstalledTool
from backend.analysis.services import workspace_service
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)


def install(workspace: AnalyzerWorkspace, tool: AnalyzerTool) -> InstalledTool:
    """Install *tool* into *workspace* by exec'ing its install command.

    AI tools have no container footprint and are marked installed immediately.
    """
    installed, _created = InstalledTool.objects.get_or_create(
        workspace=workspace,
        tool=tool,
        defaults={"config": dict(tool.default_config or {})},
    )
    installed.status = InstalledTool.Status.INSTALLING
    installed.save(update_fields=["status", "updated_at"])

    if tool.kind == "ai" or not tool.install_cmd:
        installed.status = InstalledTool.Status.INSTALLED
        installed.installed_version = tool.version or ""
        installed.install_log = "No container install required."
        installed.save(
            update_fields=["status", "installed_version", "install_log", "updated_at"],
        )
        return installed

    try:
        container_name = workspace_service.require_ready_container(workspace)
    except RuntimeError as exc:
        installed.status = InstalledTool.Status.FAILED
        installed.install_log = str(exc)
        installed.save(update_fields=["status", "install_log", "updated_at"])
        return installed

    result = docker_manager.exec_in(
        container_name,
        tool.install_cmd,
        timeout_s=tool.install_timeout,
    )
    log = (result.get("output") or "")[-4000:]
    if result.get("exit_code") == 0:
        installed.status = InstalledTool.Status.INSTALLED
        installed.installed_version = _detect_version(container_name, tool)
    else:
        installed.status = InstalledTool.Status.FAILED
    installed.install_log = log or result.get("error", "")
    installed.save(
        update_fields=["status", "installed_version", "install_log", "updated_at"],
    )
    return installed


def _detect_version(container_name: str, tool: AnalyzerTool) -> str:
    if not tool.verify_cmd:
        return tool.version or ""
    result = docker_manager.exec_in(container_name, tool.verify_cmd, timeout_s=30)
    if result.get("exit_code") == 0:
        return (result.get("output") or "").strip().splitlines()[0][:100] if result.get("output") else ""
    return tool.version or ""


def uninstall(workspace: AnalyzerWorkspace, tool: AnalyzerTool) -> None:
    """Remove the install record. (The package stays in the container until it
    is recreated; uninstalling is cheap and the next provision is clean.)"""
    InstalledTool.objects.filter(workspace=workspace, tool=tool).delete()


def verify(workspace: AnalyzerWorkspace, tool: AnalyzerTool) -> tuple[bool, str]:
    """Run the tool's verify command and report availability."""
    if tool.kind == "ai":
        return True, "AI tool (no container dependency)"
    if not tool.verify_cmd:
        return True, "No verify command defined"
    try:
        container_name = workspace_service.require_ready_container(workspace)
    except RuntimeError as exc:
        return False, str(exc)
    result = docker_manager.exec_in(container_name, tool.verify_cmd, timeout_s=30)
    output = (result.get("output") or result.get("error", "")).strip()
    return result.get("exit_code") == 0, output[:500]
