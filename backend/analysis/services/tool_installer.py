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
        installed.installed_version = _preinstalled_version(workspace, tool)
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


def _preinstalled_version(workspace: AnalyzerWorkspace, tool: AnalyzerTool) -> str:
    """The build of a container tool that ships with the image (no install command, e.g. npm audit).

    Recorded from its verify command when the workspace is running, so results
    name the build that produced them; else the catalog pin. Never provisions.
    """
    if tool.kind == "ai" or not tool.verify_cmd or workspace.container is None:
        return tool.version or ""
    try:
        if not workspace_service.is_running(workspace):
            return tool.version or ""
        return _detect_version(workspace.container.name, tool)
    except Exception:  # noqa: BLE001 - version detection is best effort
        logger.warning("Could not detect the version of %s", tool.slug, exc_info=True)
        return tool.version or ""


def _detect_version(container_name: str, tool: AnalyzerTool) -> str:
    """The installed build as the verify command prints it.

    The first line naming a version (one with a digit): some tools print a
    banner first ("ShellCheck - shell script analysis tool" / "version: 0.10.0").
    """
    if not tool.verify_cmd:
        return tool.version or ""
    result = docker_manager.exec_in(container_name, tool.verify_cmd, timeout_s=30)
    if result.get("exit_code") != 0:
        return tool.version or ""
    return _version_line(result.get("output") or "")


def _version_line(output: str) -> str:
    """The first output line with a digit (the version), else the first line; "" for no output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    return next((line for line in lines if any(c.isdigit() for c in line)), lines[0])[:100]


def redetect_versions(workspace: AnalyzerWorkspace) -> dict[str, tuple[str, str]]:
    """Record again, from its verify command, the build of every installed container tool.

    Installs recorded before the version detection skipped banner lines keep
    the banner (shellcheck), a host tool's version (perflint) or the catalog
    pin (npm audit) as their version, and every result names that version
    until the tool is reinstalled. Run this once before re-analysing instead.
    Needs the workspace container running (never provisions it). A tool whose
    verify command fails keeps its recorded version. Returns
    ``{slug: (old, new)}`` for the versions that changed.
    """
    if workspace.container is None or not workspace_service.is_running(workspace):
        msg = "The analyzer workspace is not running; start it first."
        raise RuntimeError(msg)
    container_name = workspace.container.name
    changed: dict[str, tuple[str, str]] = {}
    installed = workspace.installed_tools.select_related("tool").filter(status=InstalledTool.Status.INSTALLED)
    with _lock_for(workspace):
        for it in installed:
            tool = it.tool
            if tool.kind == "ai" or not tool.verify_cmd:
                continue
            result = docker_manager.exec_in(container_name, tool.verify_cmd, timeout_s=30)
            version = _version_line(result.get("output") or "") if result.get("exit_code") == 0 else ""
            if version and version != it.installed_version:
                changed[tool.slug] = (it.installed_version, version)
                it.installed_version = version
                it.save(update_fields=["installed_version", "updated_at"])
    return changed


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
