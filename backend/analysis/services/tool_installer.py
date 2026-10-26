"""Install / update / uninstall / verify catalog tools inside a user's workspace."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import AnalyzerWorkspace
from backend.analysis.models import InstalledTool
from backend.analysis.services import workspace_service
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

# Package managers in one container don't tolerate concurrent installs
# (pip/npm lock files, half-written site-packages), so exec'ing install,
# update and uninstall commands is serialized per workspace.
_workspace_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def _lock_for(workspace: AnalyzerWorkspace) -> threading.Lock:
    return _workspace_locks[str(workspace.id)]


def install(workspace: AnalyzerWorkspace, tool: AnalyzerTool, *, update: bool = False) -> InstalledTool:
    """Install *tool* into *workspace* by exec'ing its install command.

    With ``update=True`` the existing install is re-run (pip installs use
    ``--upgrade``, so this picks up new releases within the catalog pin). A
    failed update leaves a still-working previous version marked installed.

    AI tools have no container footprint and are marked installed immediately.
    """
    installed, _created = InstalledTool.objects.get_or_create(
        workspace=workspace,
        tool=tool,
        defaults={"config": dict(tool.default_config or {})},
    )
    previous_version = installed.installed_version
    installed.status = InstalledTool.Status.UPDATING if update else InstalledTool.Status.INSTALLING
    installed.save(update_fields=["status", "updated_at"])

    if tool.kind == "ai" or not tool.install_cmd:
        installed.status = InstalledTool.Status.INSTALLED
        installed.installed_version = tool.version or ""
        installed.catalog_version = tool.version or ""
        installed.install_log = "No container install required."
        installed.save(
            update_fields=["status", "installed_version", "catalog_version", "install_log", "updated_at"],
        )
        return installed

    try:
        container_name = workspace_service.require_ready_container(workspace)
    except RuntimeError as exc:
        installed.status = (
            InstalledTool.Status.INSTALLED if update and previous_version else InstalledTool.Status.FAILED
        )
        installed.install_log = str(exc)
        installed.save(update_fields=["status", "install_log", "updated_at"])
        return installed

    with _lock_for(workspace):
        result = docker_manager.exec_in(
            container_name,
            tool.install_cmd,
            timeout_s=tool.install_timeout,
        )
        log = (result.get("output") or "")[-4000:] or result.get("error", "")
        if result.get("exit_code") == 0:
            installed.status = InstalledTool.Status.INSTALLED
            installed.installed_version = _detect_version(container_name, tool)
            installed.catalog_version = tool.version or ""
        elif update and verify_in(container_name, tool)[0]:
            # The upgrade failed but the old version still runs: keep it usable
            # (catalog_version unchanged, so the update stays on offer).
            installed.status = InstalledTool.Status.INSTALLED
            log = f"Update failed; keeping {previous_version or 'the previous version'}.\n\n{log}"
        else:
            installed.status = InstalledTool.Status.FAILED
    installed.install_log = log
    installed.save(
        update_fields=["status", "installed_version", "catalog_version", "install_log", "updated_at"],
    )
    return installed


def install_many(workspace: AnalyzerWorkspace, tools: list[AnalyzerTool], *, update: bool = False) -> None:
    """Install (or update) *tools* one after another; one failure doesn't stop the rest."""
    for tool in tools:
        try:
            install(workspace, tool, update=update)
        except Exception:
            logger.exception("Installing %s into workspace %s failed", tool.slug, workspace.id)
            InstalledTool.objects.filter(workspace=workspace, tool=tool).exclude(
                status=InstalledTool.Status.INSTALLED,
            ).update(status=InstalledTool.Status.FAILED, install_log="Unexpected error during install.")


def _detect_version(container_name: str, tool: AnalyzerTool) -> str:
    if not tool.verify_cmd:
        return tool.version or ""
    result = docker_manager.exec_in(container_name, tool.verify_cmd, timeout_s=30)
    if result.get("exit_code") == 0:
        return (result.get("output") or "").strip().splitlines()[0][:100] if result.get("output") else ""
    return tool.version or ""


def uninstall(workspace: AnalyzerWorkspace, tool: AnalyzerTool) -> str:
    """Remove the install record and, if the workspace is running, the package.

    Package removal is best-effort: a stopped workspace keeps the files until it
    is recreated, which is harmless since runs only use recorded installs.
    Returns a short note on what happened to the package.
    """
    InstalledTool.objects.filter(workspace=workspace, tool=tool).delete()
    if tool.kind == "ai" or not tool.uninstall_cmd:
        return ""
    if not (workspace.container and workspace_service.is_running(workspace)):
        return "Workspace not running; package files will be removed when it is recreated."
    with _lock_for(workspace):
        result = docker_manager.exec_in(workspace.container.name, tool.uninstall_cmd, timeout_s=120)
    if result.get("exit_code") != 0:
        logger.warning("Uninstall command for %s failed: %s", tool.slug, result.get("output") or result.get("error"))
        return "Package removal failed; it is no longer used by runs."
    return "Package removed."


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
    return verify_in(container_name, tool)


def verify_in(container_name: str, tool: AnalyzerTool) -> tuple[bool, str]:
    if not tool.verify_cmd:
        return True, "No verify command defined"
    result = docker_manager.exec_in(container_name, tool.verify_cmd, timeout_s=30)
    output = (result.get("output") or result.get("error", "")).strip()
    return result.get("exit_code") == 0, output[:500]
