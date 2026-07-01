"""Provision and manage per-user analyzer workspace containers.

A workspace is a long-lived, hardened container (built from the analyzer-base
image) into which catalog tools are installed and against which analysis runs
execute. It is created lazily on first use and reused across runs.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone

from backend.analysis.models import AnalyzerWorkspace
from backend.runtime.models import ContainerInstance
from backend.runtime.services import docker_manager

if TYPE_CHECKING:
    from backend.users.models import User

logger = logging.getLogger(__name__)

ANALYZER_IMAGE_TAG = "backend/analyzer-base:latest"
_IMAGE_DIR = Path(__file__).resolve().parent.parent / "images" / "analyzer-base"

BUILD_TIMEOUT_SECONDS = 600
START_POLL_TIMEOUT = 60
WORK_DIR = "/work"


def ensure_base_image() -> tuple[bool, str]:
    """Build the analyzer-base image if it does not already exist."""
    if docker_manager.image_exists(ANALYZER_IMAGE_TAG):
        return True, "Image present"
    if not docker_manager.ping():
        return False, "Docker daemon unavailable"
    logger.info("Building analyzer base image %s …", ANALYZER_IMAGE_TAG)
    try:
        docker_manager.build_image(str(_IMAGE_DIR), ANALYZER_IMAGE_TAG)
    except Exception as exc:  # noqa: BLE001
        detail = docker_manager.extract_build_error(exc) or str(exc)
        logger.exception("Analyzer base image build failed")
        return False, detail[-2000:]
    return True, "Image built"


def get_workspace(user: User) -> AnalyzerWorkspace:
    """Return (creating if needed) the user's workspace record."""
    workspace, _created = AnalyzerWorkspace.objects.get_or_create(user=user)
    return workspace


def _container_running(workspace: AnalyzerWorkspace) -> bool:
    container = workspace.container
    if container is None:
        return False
    info = docker_manager.inspect(container.name)
    state = (info or {}).get("State", {}) if isinstance(info, dict) else {}
    return bool(state.get("Running"))


def provision(user: User) -> AnalyzerWorkspace:
    """Ensure the user has a ready, running workspace container.

    Idempotent: if a running container already exists it is reused; a stopped
    one is restarted; otherwise a new container is created.
    """
    workspace = get_workspace(user)

    if not docker_manager.ping():
        workspace.status = AnalyzerWorkspace.Status.ERROR
        workspace.error_message = "Docker daemon unavailable"
        workspace.save(update_fields=["status", "error_message", "updated_at"])
        return workspace

    if workspace.container and _container_running(workspace):
        _mark_ready(workspace)
        return workspace

    ok, message = ensure_base_image()
    if not ok:
        workspace.status = AnalyzerWorkspace.Status.ERROR
        workspace.error_message = message
        workspace.save(update_fields=["status", "error_message", "updated_at"])
        return workspace

    workspace.status = AnalyzerWorkspace.Status.PROVISIONING
    workspace.image = ANALYZER_IMAGE_TAG
    workspace.error_message = ""
    workspace.save(update_fields=["status", "image", "error_message", "updated_at"])

    # Reuse an existing (stopped) container if we have one, else create.
    container = workspace.container
    try:
        if container is not None:
            docker_manager.start(container.name)
        else:
            container = _create_container(user)
            workspace.container = container
            workspace.save(update_fields=["container", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workspace provisioning failed for user %s", user.pk)
        workspace.status = AnalyzerWorkspace.Status.ERROR
        workspace.error_message = str(exc)[-1000:]
        workspace.save(update_fields=["status", "error_message", "updated_at"])
        return workspace

    if _wait_running(workspace):
        _mark_ready(workspace)
    else:
        workspace.status = AnalyzerWorkspace.Status.ERROR
        workspace.error_message = "Container did not reach running state"
        workspace.save(update_fields=["status", "error_message", "updated_at"])
    return workspace


def _create_container(user: User) -> ContainerInstance:
    name = f"analyzer-{uuid.uuid4().hex[:10]}"
    instance = ContainerInstance.objects.create(
        name=name,
        image=ANALYZER_IMAGE_TAG,
        status=ContainerInstance.Status.BUILDING,
        created_by=user,
        metadata={"kind": "analyzer-workspace"},
    )
    network = getattr(settings, "DOCKER_APPS_NETWORK", "") or ""
    cid = docker_manager.run_detached(
        ANALYZER_IMAGE_TAG,
        name,
        command=["sleep", "infinity"],
        container_instance_id=str(instance.id),
        network=network,
    )
    instance.container_id = cid
    instance.status = ContainerInstance.Status.RUNNING
    instance.save(update_fields=["container_id", "status"])
    return instance


def _wait_running(workspace: AnalyzerWorkspace) -> bool:
    deadline = time.monotonic() + START_POLL_TIMEOUT
    while time.monotonic() < deadline:
        if _container_running(workspace):
            return True
        time.sleep(1)
    return False


def _mark_ready(workspace: AnalyzerWorkspace) -> None:
    workspace.status = AnalyzerWorkspace.Status.READY
    workspace.error_message = ""
    workspace.last_used_at = timezone.now()
    if workspace.container:
        workspace.container.status = ContainerInstance.Status.RUNNING
        workspace.container.save(update_fields=["status"])
    workspace.save(
        update_fields=["status", "error_message", "last_used_at", "updated_at"],
    )


def touch(workspace: AnalyzerWorkspace) -> None:
    workspace.last_used_at = timezone.now()
    workspace.save(update_fields=["last_used_at", "updated_at"])


def stop(workspace: AnalyzerWorkspace) -> AnalyzerWorkspace:
    if workspace.container:
        docker_manager.stop(workspace.container.name)
        workspace.container.status = ContainerInstance.Status.STOPPED
        workspace.container.save(update_fields=["status"])
    workspace.status = AnalyzerWorkspace.Status.STOPPED
    workspace.save(update_fields=["status", "updated_at"])
    return workspace


def teardown(workspace: AnalyzerWorkspace) -> AnalyzerWorkspace:
    """Remove the container and reset the workspace to absent."""
    container = workspace.container
    if container:
        docker_manager.remove(container.name)
        container.status = ContainerInstance.Status.REMOVED
        container.save(update_fields=["status"])
    workspace.installed_tools.all().delete()
    workspace.container = None
    workspace.status = AnalyzerWorkspace.Status.ABSENT
    workspace.save(update_fields=["container", "status", "updated_at"])
    return workspace


def require_ready_container(workspace: AnalyzerWorkspace) -> str:
    """Return the container name of a ready workspace, provisioning if needed.

    Raises ``RuntimeError`` if a running container cannot be obtained.
    """
    if not (workspace.container and _container_running(workspace)):
        workspace = provision(workspace.user)
    if not (workspace.container and _container_running(workspace)):
        msg = workspace.error_message or "Analyzer workspace is not available"
        raise RuntimeError(msg)
    touch(workspace)
    return workspace.container.name
