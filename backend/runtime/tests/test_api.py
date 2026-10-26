"""Tests for runtime API endpoints."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.test import Client
from django.test import RequestFactory

from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerActionFactory
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.users.tests.factories import UserFactory


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
    client, user = auth_client
    ContainerInstanceFactory.create_batch(3, created_by=user)
    resp = client.get("/api/runtime/containers/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "containers" in data
    assert data["pagination"]["total"] >= 3


@pytest.mark.django_db
def test_list_containers_filter_by_model(auth_client):
    from backend.generation.tests.factories import GenerationJobFactory
    from backend.llm_models.tests.factories import LLMModelFactory

    client, user = auth_client
    job = GenerationJobFactory(model=LLMModelFactory(), created_by=user)
    mine = ContainerInstanceFactory(generation_job=job, created_by=user)
    ContainerInstanceFactory(
        generation_job=GenerationJobFactory(model=LLMModelFactory(), created_by=user),
        created_by=user,
    )
    resp = client.get("/api/runtime/containers/", {"model_id": job.model.model_id})
    assert resp.status_code == HTTPStatus.OK
    assert [c["id"] for c in resp.json()["containers"]] == [str(mine.id)]


@pytest.mark.django_db
def test_get_container_detail(auth_client):
    client, user = auth_client
    instance = ContainerInstanceFactory(created_by=user)
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
    client, user = auth_client
    ContainerActionFactory.create_batch(3, container__created_by=user, triggered_by=user)
    resp = client.get("/api/runtime/actions/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert "actions" in data


@pytest.mark.django_db
def test_get_action_detail(auth_client):
    client, user = auth_client
    action = ContainerActionFactory(container__created_by=user, triggered_by=user)
    resp = client.get(f"/api/runtime/actions/{action.action_id}/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["action_id"] == action.action_id


@pytest.mark.django_db
def test_start_container_daemon_unavailable(auth_client):
    """Returns 503 when Docker daemon is not reachable."""
    client, user = auth_client
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.STOPPED, created_by=user)
    with patch(
        "backend.runtime.services.docker_manager.ping",
        return_value=False,
    ):
        resp = client.post(f"/api/runtime/containers/{instance.id}/start/")
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.django_db
def test_stop_container_with_mock(auth_client):
    """Stop endpoint dispatches action when Docker is mocked."""
    client, user = auth_client
    mock_docker = MagicMock()
    mock_docker.ping.return_value = True
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, created_by=user)
    with (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "backend.runtime.services.docker_manager.client",
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
    client, user = auth_client
    mock_docker = MagicMock()
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, created_by=user)
    with (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "backend.runtime.services.docker_manager.client",
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
    client, user = auth_client
    instance = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, created_by=user)
    with (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "backend.runtime.services.docker_manager.logs",
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
    with patch("backend.runtime.services.docker_manager.ping", return_value=False):
        resp = client.get("/api/runtime/docker/info/")
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.django_db
def test_docker_info_daemon_available(auth_client):
    """docker/info/ returns 200 when daemon is reachable."""
    client, _ = auth_client
    mock_c = MagicMock()
    mock_c.info.return_value = {
        "OSType": "linux",
        "Architecture": "x86_64",
        "ContainersRunning": 2,
        "ContainersStopped": 1,
        "Images": 7,
    }
    mock_c.version.return_value = {"Version": "24.0.0"}
    with (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch(
            "backend.runtime.services.docker_manager.client",
            return_value=mock_c,
        ),
    ):
        resp = client.get("/api/runtime/docker/info/")
    assert resp.status_code == HTTPStatus.OK
    data = resp.json()
    assert data["daemon_available"] is True
    assert data["containers_running"] == 2
    assert data["containers_stopped"] == 1
    assert data["images"] == 7


# ---- ownership scoping (other users' containers/jobs answer 404) ----


def _docker_up():
    return (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch("backend.runtime.services.docker_manager.client", return_value=MagicMock()),
    )


@pytest.mark.django_db
def test_list_containers_only_shows_own(auth_client):
    from backend.generation.tests.factories import GenerationJobFactory

    client, user = auth_client
    built = ContainerInstanceFactory(created_by=user)
    # Built by someone else (e.g. the executor) for a job the caller owns.
    via_job = ContainerInstanceFactory(generation_job=GenerationJobFactory(created_by=user))
    ContainerInstanceFactory()  # another user's
    ContainerInstanceFactory(created_by=None)  # ownerless (internal)
    resp = client.get("/api/runtime/containers/")
    assert resp.status_code == HTTPStatus.OK
    assert {c["id"] for c in resp.json()["containers"]} == {str(built.id), str(via_job.id)}


@pytest.mark.django_db
def test_staff_sees_all_containers():
    staff = UserFactory(is_staff=True)
    c = Client()
    c.force_login(staff)
    ContainerInstanceFactory.create_batch(2)
    resp = c.get("/api/runtime/containers/")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["pagination"]["total"] == 2
    other = ContainerInstanceFactory()
    assert c.get(f"/api/runtime/containers/{other.id}/").status_code == HTTPStatus.OK


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", ""),
        ("get", "logs/"),
        ("get", "health/"),
        ("get", "inspect/"),
        ("post", "start/"),
        ("post", "stop/"),
        ("post", "restart/"),
        ("post", "remove/"),
    ],
)
def test_other_users_container_is_404(auth_client, method, suffix):
    client, _ = auth_client
    other = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
    ping, cli = _docker_up()
    with ping, cli, patch("backend.runtime.services.container_service.stop_instance") as stop:
        resp = getattr(client, method)(f"/api/runtime/containers/{other.id}/{suffix}")
    assert resp.status_code == HTTPStatus.NOT_FOUND
    stop.assert_not_called()


@pytest.mark.django_db
def test_exec_on_other_users_container_is_404(auth_client):
    client, _ = auth_client
    other = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
    ping, cli = _docker_up()
    with ping, cli, patch("backend.runtime.services.docker_manager.exec_in") as exec_in:
        resp = client.post(
            f"/api/runtime/containers/{other.id}/exec/",
            {"action": "environment"},
            content_type="application/json",
        )
    assert resp.status_code == HTTPStatus.NOT_FOUND
    exec_in.assert_not_called()


@pytest.mark.django_db
def test_actions_scoped_to_own_containers(auth_client):
    client, user = auth_client
    mine = ContainerActionFactory(container__created_by=user, triggered_by=user)
    theirs = ContainerActionFactory()
    resp = client.get("/api/runtime/actions/")
    assert resp.status_code == HTTPStatus.OK
    assert [a["action_id"] for a in resp.json()["actions"]] == [mine.action_id]
    assert client.get(f"/api/runtime/actions/{theirs.action_id}/").status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
@pytest.mark.parametrize("suffix", ["build/", "smoke/"])
def test_build_and_smoke_reject_other_users_job(auth_client, suffix):
    from backend.generation.tests.factories import GenerationJobFactory

    client, _ = auth_client
    job = GenerationJobFactory()  # owned by someone else
    with (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch("backend.runtime.services.container_service.build_for_job") as build,
    ):
        resp = client.post(f"/api/runtime/jobs/{job.id}/{suffix}")
    assert resp.status_code == HTTPStatus.NOT_FOUND
    build.assert_not_called()


@pytest.mark.django_db
def test_build_own_job_dispatches(auth_client):
    from backend.generation.tests.factories import GenerationJobFactory

    client, user = auth_client
    job = GenerationJobFactory(created_by=user)
    instance = ContainerInstanceFactory(generation_job=job, created_by=user)
    with (
        patch("backend.runtime.services.docker_manager.ping", return_value=True),
        patch("backend.runtime.services.container_service.build_for_job", return_value=instance) as build,
    ):
        resp = client.post(f"/api/runtime/jobs/{job.id}/build/")
    assert resp.status_code == HTTPStatus.OK
    build.assert_called_once()


@pytest.mark.django_db
def test_bearer_token_is_scoped_too():
    from backend.tokens.services import generate_token

    user = UserFactory()
    raw, _ = generate_token(user=user, name="t")
    mine = ContainerInstanceFactory(created_by=user)
    other = ContainerInstanceFactory()
    c = Client(headers={"authorization": f"Bearer {raw}"})
    resp = c.get("/api/runtime/containers/")
    assert [row["id"] for row in resp.json()["containers"]] == [str(mine.id)]
    assert c.get(f"/api/runtime/containers/{other.id}/").status_code == HTTPStatus.NOT_FOUND


# ---- path-mode /apps/<name>/ proxy ----


@pytest.mark.django_db
def test_app_proxy_other_users_app_is_404(auth_client):
    client, _ = auth_client
    other = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING)
    with patch("backend.runtime.proxy._proxy") as proxy:
        resp = client.get(f"/apps/{other.name}/")
    assert resp.status_code == HTTPStatus.NOT_FOUND
    proxy.assert_not_called()


@pytest.mark.django_db
def test_app_proxy_own_app_is_proxied(auth_client):
    from django.http import HttpResponse

    client, user = auth_client
    mine = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, created_by=user)
    with patch("backend.runtime.proxy._proxy", return_value=HttpResponse("ok")) as proxy:
        resp = client.get(f"/apps/{mine.name}/")
    assert resp.status_code == HTTPStatus.OK
    proxy.assert_called_once()


# ---- subdomain mode: <name>.<APPS_DOMAIN> ----


@pytest.fixture
def subdomain_mode(settings):
    settings.APPS_SUBDOMAIN_PROXY = True
    settings.APPS_DOMAIN = "apps.example.org"
    return settings


@pytest.mark.django_db
def test_app_tls_check_allows_only_running_app_subdomains(subdomain_mode):
    running = ContainerInstanceFactory(name="llm-aaaa1111", status=ContainerInstance.Status.RUNNING)
    ContainerInstanceFactory(name="llm-bbbb2222", status=ContainerInstance.Status.STOPPED)
    client = Client()

    def check(domain):
        return client.get("/api/runtime/app-tls-check/", {"domain": domain}).status_code

    assert check(f"{running.name}.apps.example.org") == HTTPStatus.OK
    assert check("llm-bbbb2222.apps.example.org") == HTTPStatus.NOT_FOUND
    assert check("llm-cccc3333.apps.example.org") == HTTPStatus.NOT_FOUND
    assert check(f"{running.name}.evil.example.net") == HTTPStatus.NOT_FOUND
    assert check(f"x.{running.name}.apps.example.org") == HTTPStatus.NOT_FOUND
    assert check("") == HTTPStatus.NOT_FOUND
    subdomain_mode.APPS_SUBDOMAIN_PROXY = False
    assert check(f"{running.name}.apps.example.org") == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
def test_subdomain_proxy_forwards_app_cookies(subdomain_mode):
    from backend.runtime.proxy import proxy_app_subdomain

    app = ContainerInstanceFactory(name="llm-dddd4444", status=ContainerInstance.Status.RUNNING)
    upstream = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    upstream.iter_content.return_value = [b"<html></html>"]
    request = RequestFactory().get(
        "/assets/x.js",
        HTTP_HOST=f"{app.name}.apps.example.org",
        HTTP_COOKIE="app_session=1",
    )
    with patch("backend.runtime.proxy.requests.request", return_value=upstream) as req:
        resp = proxy_app_subdomain(request, app.name)
    assert resp.status_code == HTTPStatus.OK
    assert req.call_args.args[1].endswith("/assets/x.js")
    assert req.call_args.kwargs["headers"]["Cookie"] == "app_session=1"


@pytest.mark.django_db
def test_path_proxy_strips_platform_cookies(auth_client):
    client, user = auth_client
    mine = ContainerInstanceFactory(status=ContainerInstance.Status.RUNNING, created_by=user)
    upstream = MagicMock(status_code=200, headers={})
    upstream.iter_content.return_value = [b""]
    with patch("backend.runtime.proxy.requests.request", return_value=upstream) as req:
        client.get(f"/apps/{mine.name}/")
    assert "Cookie" not in req.call_args.kwargs["headers"]
