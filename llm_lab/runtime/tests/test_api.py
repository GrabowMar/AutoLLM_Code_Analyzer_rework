"""Tests for runtime API endpoints."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.test import Client

from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.tests.factories import ContainerActionFactory
from llm_lab.runtime.tests.factories import ContainerInstanceFactory
from llm_lab.users.tests.factories import UserFactory


@pytest.fixture
def auth_client(db):
    user = UserFactory()
    c = Client()
    c.force_login(user)
    return c, user


@pytest.mark.django_db
def test_list_containers_unauthenticated():
    c = Client()
    resp = c.get("/api/runtime/containers/")
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.django_db
def test_list_containers_authenticated(auth_client):
    client, _ = auth_client
    ContainerInstanceFactory.create_batch(3)
    resp = client.get("/api/runtime/containers/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "containers" in data
    assert data["pagination"]["total"] >= 3


@pytest.mark.django_db
def test_get_container_detail(auth_client):
    client, _ = auth_client
    instance = ContainerInstanceFactory()
    resp = client.get(f"/api/runtime/containers/{instance.id}/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["name"] == instance.name


@pytest.mark.django_db
def test_get_nonexistent_container_returns_404(auth_client):
    client, _ = auth_client
    resp = client.get(f"/api/runtime/containers/{uuid.uuid4()}/")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
def test_list_actions(auth_client):
    client, _ = auth_client
    ContainerActionFactory.create_batch(3)
    resp = client.get("/api/runtime/actions/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "actions" in data


@pytest.mark.django_db
def test_get_action_detail(auth_client):
    client, _ = auth_client
    action = ContainerActionFactory()
    resp = client.get(f"/api/runtime/actions/{action.action_id}/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["action_id"] == action.action_id


@pytest.mark.django_db
def test_start_container_daemon_unavailable(auth_client):
    """Returns 503 when Docker daemon is not reachable."""
    client, _ = auth_client
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.STOPPED)
    with patch(
        "llm_lab.runtime.services.docker_manager.ping",
        return_value=False,
    ):
        resp = client.post(f"/api/runtime/containers/{instance.id}/start/")
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.django_db
def test_stop_container_with_mock(auth_client):
    """Stop endpoint dispatches action when Docker is mocked."""
    client, _ = auth_client
    mock_docker = MagicMock()
    mock_docker.ping.return_value = True
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
    with (
        patch("llm_lab.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "llm_lab.runtime.services.docker_manager.client",
            return_value=mock_docker,
        ),
    ):
        resp = client.post(f"/api/runtime/containers/{instance.id}/stop/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["action_type"] == "stop"


@pytest.mark.django_db
def test_restart_container_with_mock(auth_client):
    """Restart endpoint dispatches action when Docker is mocked."""
    client, _ = auth_client
    mock_docker = MagicMock()
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
    with (
        patch("llm_lab.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "llm_lab.runtime.services.docker_manager.client",
            return_value=mock_docker,
        ),
    ):
        resp = client.post(f"/api/runtime/containers/{instance.id}/restart/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["action_type"] == "restart"


@pytest.mark.django_db
def test_logs_endpoint_with_mock(auth_client):
    """Logs endpoint returns text from docker_manager.logs."""
    client, _ = auth_client
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
    with (
        patch("llm_lab.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "llm_lab.runtime.services.docker_manager.logs",
            return_value="log line 1\nlog line 2",
        ),
    ):
        resp = client.get(f"/api/runtime/containers/{instance.id}/logs/?tail=10")
    assert resp.status_code == HTTPStatus.OK
    assert "log line" in resp.json()


@pytest.mark.django_db
def test_docker_info_daemon_unavailable(auth_client):
    """docker/info/ returns 503 when daemon not reachable."""
    client, _ = auth_client
    with patch("llm_lab.runtime.services.docker_manager.ping", return_value=False):
        resp = client.get("/api/runtime/docker/info/")
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.django_db
def test_docker_info_daemon_available(auth_client):
    """docker/info/ returns 200 when daemon is reachable."""
    client, _ = auth_client
    mock_c = MagicMock()
    mock_c.info.return_value = {"OSType": "linux", "Architecture": "x86_64"}
    mock_c.version.return_value = {"Version": "24.0.0"}
    with (
        patch("llm_lab.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "llm_lab.runtime.services.docker_manager.client",
            return_value=mock_c,
        ),
    ):
        resp = client.get("/api/runtime/docker/info/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["daemon_available"] is True
