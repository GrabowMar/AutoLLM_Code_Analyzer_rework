"""Tests for container service (uses mocked Docker SDK)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from backend.runtime.models import ContainerAction
from backend.runtime.models import ContainerInstance
from backend.runtime.services import container_service
from backend.runtime.tests.factories import ContainerActionFactory
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory


def _mock_docker():
    """Return a MagicMock that passes ping() and provides container operations."""
    mock = MagicMock()
    mock.ping.return_value = True
    mock.containers.get.return_value.status = "running"
    mock.containers.get.return_value.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
    }
    mock.containers.get.return_value.logs.return_value = b"log line 1\nlog line 2\n"
    mock.containers.run.return_value.id = "deadbeef001"
    mock.images.build.return_value = (MagicMock(), iter([]))
    return mock


@pytest.mark.django_db(transaction=True)
def test_build_action_completes():
    """build_for_job dispatches a BUILD action that transitions to completed."""
    mock_client = _mock_docker()
    with (
        patch("backend.runtime.services.docker_manager._docker_client", mock_client),
        patch(
            "backend.runtime.services.docker_manager.client",
            return_value=mock_client,
        ),
    ):
        user = UserFactory()
        container = ContainerInstanceFactory(
            status=ContainerInstance.Status.PENDING,
            app_port=8001,
        )
        action = container_service.create_action(
            container,
            ContainerAction.ActionType.BUILD,
            user,
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            action.refresh_from_db()
            if action.status in (
                ContainerAction.Status.COMPLETED,
                ContainerAction.Status.FAILED,
            ):
                break
            time.sleep(0.1)

        action.refresh_from_db()
        assert action.status == ContainerAction.Status.COMPLETED


@pytest.mark.django_db(transaction=True)
def test_stop_action_completes():
    """Stop action transitions container to STOPPED."""
    mock_client = _mock_docker()
    with (
        patch("backend.runtime.services.docker_manager._docker_client", mock_client),
        patch(
            "backend.runtime.services.docker_manager.client",
            return_value=mock_client,
        ),
    ):
        user = UserFactory()
        container = ContainerInstanceFactory(
            status=ContainerInstance.Status.RUNNING,
        )
        action = container_service.create_action(
            container,
            ContainerAction.ActionType.STOP,
            user,
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            action.refresh_from_db()
            if action.status in (
                ContainerAction.Status.COMPLETED,
                ContainerAction.Status.FAILED,
            ):
                break
            time.sleep(0.1)

        action.refresh_from_db()
        assert action.status == ContainerAction.Status.COMPLETED
        container.refresh_from_db()
        assert container.status == ContainerInstance.Status.STOPPED


@pytest.mark.django_db(transaction=True)
def test_start_action_completes():
    """Start action transitions container to RUNNING."""
    mock_client = _mock_docker()
    with (
        patch("backend.runtime.services.docker_manager._docker_client", mock_client),
        patch(
            "backend.runtime.services.docker_manager.client",
            return_value=mock_client,
        ),
    ):
        user = UserFactory()
        container = ContainerInstanceFactory(
            status=ContainerInstance.Status.STOPPED,
        )
        action = container_service.create_action(
            container,
            ContainerAction.ActionType.START,
            user,
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            action.refresh_from_db()
            if action.status in (
                ContainerAction.Status.COMPLETED,
                ContainerAction.Status.FAILED,
            ):
                break
            time.sleep(0.1)

        action.refresh_from_db()
        assert action.status == ContainerAction.Status.COMPLETED
        container.refresh_from_db()
        assert container.status == ContainerInstance.Status.RUNNING


@pytest.mark.django_db(transaction=True)
def test_restart_action_completes():
    """Restart action keeps container RUNNING."""
    mock_client = _mock_docker()
    with (
        patch("backend.runtime.services.docker_manager._docker_client", mock_client),
        patch(
            "backend.runtime.services.docker_manager.client",
            return_value=mock_client,
        ),
    ):
        user = UserFactory()
        container = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
        action = container_service.create_action(
            container,
            ContainerAction.ActionType.RESTART,
            user,
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            action.refresh_from_db()
            if action.status in (
                ContainerAction.Status.COMPLETED,
                ContainerAction.Status.FAILED,
            ):
                break
            time.sleep(0.1)

        action.refresh_from_db()
        assert action.status == ContainerAction.Status.COMPLETED


@pytest.mark.django_db(transaction=True)
def test_daemon_unavailable_marks_action_failed():
    """When Docker daemon is unavailable, action should be marked failed."""
    with (
        patch("backend.runtime.services.docker_manager._docker_client", None),
        patch("backend.runtime.services.docker_manager.client", return_value=None),
    ):
        user = UserFactory()
        container = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
        action = container_service.create_action(
            container,
            ContainerAction.ActionType.STOP,
            user,
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            action.refresh_from_db()
            if action.status in (
                ContainerAction.Status.COMPLETED,
                ContainerAction.Status.FAILED,
            ):
                break
            time.sleep(0.1)

        action.refresh_from_db()
        assert action.status == ContainerAction.Status.FAILED


@pytest.mark.django_db(transaction=True)
def test_idempotency_guard_skips_non_pending():
    """An action already in RUNNING state should be skipped by _execute."""
    action = ContainerActionFactory(status=ContainerAction.Status.RUNNING)
    original_started_at = action.started_at
    container_service._execute(action.id)
    action.refresh_from_db()
    assert action.status == ContainerAction.Status.RUNNING
    assert action.started_at == original_started_at
