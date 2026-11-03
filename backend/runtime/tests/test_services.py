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
        patch("backend.execution.api.wake_preview_build") as wake,
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
        assert action.status == ContainerAction.Status.PENDING
        wake.assert_called_once_with(action.id)
        # The executor, rather than an unbounded web thread, owns cache misses.
        container_service._execute(action.id)

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


def test_new_client_uses_apps_daemon_over_tls(settings):
    from backend.runtime.services import docker_manager

    settings.APPS_DOCKER_HOST = "tcp://apps-dind:2376"
    settings.APPS_DOCKER_CERT_PATH = "/apps-dind-certs"
    with patch("docker.from_env") as from_env:
        docker_manager.new_client()
    from_env.assert_called_once_with(
        environment={
            "DOCKER_HOST": "tcp://apps-dind:2376",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/apps-dind-certs",
        },
    )


def test_new_client_defaults_to_process_environment(settings):
    from backend.runtime.services import docker_manager

    settings.APPS_DOCKER_HOST = ""
    with patch("docker.from_env") as from_env:
        docker_manager.new_client()
    from_env.assert_called_once_with()


def _run_remove(container) -> ContainerAction:
    action = container_service.create_action(container, ContainerAction.ActionType.REMOVE, UserFactory())
    deadline = time.time() + 10
    while time.time() < deadline:
        action.refresh_from_db()
        if action.status in (ContainerAction.Status.COMPLETED, ContainerAction.Status.FAILED):
            break
        time.sleep(0.1)
    return action


@pytest.mark.django_db(transaction=True)
def test_remove_action_drops_per_app_image():
    mock_client = _mock_docker()
    with patch("backend.runtime.services.docker_manager.client", return_value=mock_client):
        container = ContainerInstanceFactory(status=ContainerInstance.Status.STOPPED, name="llm-abc12345")
        container.image = "backend/llm-abc12345:latest"
        container.save(update_fields=["image"])
        action = _run_remove(container)

    assert action.status == ContainerAction.Status.COMPLETED
    mock_client.images.remove.assert_called_once_with("backend/llm-abc12345:latest", force=True)


@pytest.mark.django_db(transaction=True)
def test_remove_action_keeps_shared_image():
    mock_client = _mock_docker()
    with patch("backend.runtime.services.docker_manager.client", return_value=mock_client):
        container = ContainerInstanceFactory(status=ContainerInstance.Status.STOPPED, name="analyzer-0123456789")
        container.image = "backend/analyzer-base:latest"
        container.save(update_fields=["image"])
        action = _run_remove(container)

    assert action.status == ContainerAction.Status.COMPLETED
    mock_client.images.remove.assert_not_called()


# -- hosted-app credentials (spec 8.6; U1 human_eval ask) ---------------------------------------------


def _host_run(monkeypatch, credentials):
    """Run ``_do_build`` for a run-backed container with a cached image; returns (container, env passed)."""
    from types import SimpleNamespace

    from backend.generation import evaluation
    from backend.generation.testing import factories as gen_factories

    if isinstance(credentials, BaseException):

        def fake_credentials(run):
            raise credentials
    else:

        def fake_credentials(run):
            return credentials

    monkeypatch.setattr(evaluation, "credentials", fake_credentials)
    run = gen_factories.GenerationRunFactory()
    container = ContainerInstanceFactory(
        status=ContainerInstance.Status.PENDING,
        generation_job=run,
        metadata={"runtime_env": {"source": "evaluation.credentials", "admin_password_sha256": "stale"}},
    )
    action = ContainerActionFactory(container=container, action_type=ContainerAction.ActionType.BUILD)
    started = {}

    def run_container(image, name, ports, env, *args, **kwargs):
        started["env"] = dict(env)
        return "cid-1"

    cached = SimpleNamespace(image_tag="llmlab/sample:t", image_id="sha256:x", log="", metrics=dict)
    monkeypatch.setattr(container_service.docker_manager, "ping", lambda: True)
    monkeypatch.setattr(container_service.docker_manager, "run_container", run_container)
    monkeypatch.setattr(container_service, "_verify_running", lambda *a, **k: (True, ""))
    monkeypatch.setattr(container_service, "publish_container_status", lambda *a, **k: None)
    monkeypatch.setattr(container_service.traefik_router, "write_route", lambda *a, **k: None)
    monkeypatch.setattr("backend.runtime.services.smoke.run_smoke", lambda *a, **k: {"passed": True})
    container_service._do_build(action, container, cached_build=cached)
    container.refresh_from_db()
    return container, started.get("env")


@pytest.mark.django_db
def test_hosted_run_gets_record_credentials_and_a_hash_stamp(monkeypatch):
    from backend.human_eval.services.credentials import container_env_matches

    record = {"admin_username": "admin", "admin_password": "pw-123", "secret_key": "sk-456"}
    container, env = _host_run(monkeypatch, record)
    assert env == {"ADMIN_PASSWORD": "pw-123", "SECRET_KEY": "sk-456"}
    stamp = container.metadata["runtime_env"]
    assert stamp["source"] == "evaluation.credentials"
    assert "pw-123" not in str(container.metadata)
    assert "sk-456" not in str(container.metadata)
    assert container_env_matches(container, "pw-123")


@pytest.mark.django_db
@pytest.mark.parametrize("error", [NotImplementedError("stub"), LookupError("no record"), RuntimeError("boom")])
def test_hosted_run_without_a_record_starts_without_credentials_or_stamp(monkeypatch, error):
    container, env = _host_run(monkeypatch, error)
    assert env == {}
    assert "runtime_env" not in container.metadata  # a stale stamp never vouches for the new env
