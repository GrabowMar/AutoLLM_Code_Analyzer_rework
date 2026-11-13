"""Tests for the preview smoke probe (health + the task's declared GET endpoints).

The probe result lives on the container only; ``GenerationRun.metrics`` is never written (Appendix A #7).
"""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.testing.content import task_content
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import TaskSpecVersionFactory
from backend.runtime.services import smoke
from backend.runtime.tests.factories import ContainerInstanceFactory

pytestmark = pytest.mark.django_db


def _job_with_endpoints(user=None, **kwargs):
    content = task_content(title="URL shortener")
    content["endpoints"] = [
        {"id": "urls.list", "method": "GET", "path": "/api/urls", "auth": "user"},
        {"id": "urls.detail", "method": "GET", "path": "/api/urls/{id}", "auth": "user"},
        {"id": "urls.create", "method": "POST", "path": "/api/shorten", "auth": "user", "success_status": 201},
        {"id": "admin.urls", "method": "GET", "path": "/api/admin/urls", "auth": "admin"},
        {"id": "auth.register", "method": "POST", "path": "/api/auth/register", "auth": "public"},
        {"id": "auth.login", "method": "POST", "path": "/api/auth/login", "auth": "public"},
    ]
    content["ui"] = []
    content["acceptance"] = []
    content["probes"] = []
    extra = {"created_by": user} if user else {}
    return GenerationRunFactory(task_version=TaskSpecVersionFactory(content=content), **extra, **kwargs)


def _status_map(mapping: dict[str, int]):
    """Fake requests.get returning a canned status per probed path."""

    def fake_get(url, **kwargs):
        resp = mock.Mock()
        path = "/" + url.split("/", 3)[3]
        resp.status_code = mapping.get(path, 200)
        return resp

    return fake_get


def test_smoke_all_ok_passes(user):
    job = _job_with_endpoints(user)
    container = ContainerInstanceFactory(generation_job=job, created_by=user)

    with mock.patch.object(smoke.requests, "get", side_effect=_status_map({})):
        result = smoke.run_smoke(job, container)

    assert result["passed"] is True
    assert result["health_ok"] is True
    # Only the 3 GET endpoints are probed (POST skipped), health reported separately.
    assert result["endpoints_checked"] == 3
    assert result["endpoints_ok"] == 3

    job.refresh_from_db()
    container.refresh_from_db()
    assert container.metadata["smoke"]["passed"] is True
    assert container.metadata["smoke"]["checks"]
    assert job.metrics == {}  # a preview probe is never a measurement


def test_smoke_health_down_fails_without_probing(user):
    job = _job_with_endpoints(user)
    container = ContainerInstanceFactory(generation_job=job, created_by=user)

    with (
        mock.patch.object(smoke.requests, "get", side_effect=_status_map({"/api/health": 500})),
        mock.patch.object(smoke.time, "sleep"),
    ):
        result = smoke.run_smoke(job, container)

    assert result["passed"] is False
    assert result["health_ok"] is False
    assert result["endpoints_checked"] == 0


def test_smoke_auth_protected_route_counts_ok(user):
    job = _job_with_endpoints(user)
    container = ContainerInstanceFactory(generation_job=job, created_by=user)

    with mock.patch.object(smoke.requests, "get", side_effect=_status_map({"/api/admin/urls": 401})):
        result = smoke.run_smoke(job, container)

    assert result["passed"] is True
    admin_check = next(c for c in result["checks"] if c.get("admin"))
    assert admin_check["ok"] is True


def test_smoke_missing_static_route_fails_parametrized_404_tolerated(user):
    job = _job_with_endpoints(user)
    container = ContainerInstanceFactory(generation_job=job, created_by=user)

    statuses = {"/api/urls": 404, "/api/urls/1": 404}
    with mock.patch.object(smoke.requests, "get", side_effect=_status_map(statuses)):
        result = smoke.run_smoke(job, container)

    by_path = {c["path"]: c for c in result["checks"]}
    assert by_path["/api/urls"]["ok"] is False  # declared route missing
    assert by_path["/api/urls/{id}"]["ok"] is True  # record 1 may just not exist
    assert by_path["/api/urls/{id}"]["probed"] == "/api/urls/1"
    # 2/3 ok is below the 0.8 gate.
    assert result["passed"] is False


def test_smoke_no_routing_configured(user):
    job = _job_with_endpoints(user)
    container = ContainerInstanceFactory(generation_job=job, created_by=user, app_port=None)

    result = smoke.run_smoke(job, container)
    assert result["passed"] is False
    assert "error" in result


def test_smoke_never_touches_run_metrics(user):
    graded = {
        "eval": {
            "eval_suite": "eval@1",
            "eval_level": "functional",
            "status": "measured",
            "measured": True,
            "agent_pass_rate": 0.4375,
        },
    }
    job = _job_with_endpoints(user, metrics=graded)
    job.refresh_from_db()
    before = dict(job.metrics)
    container = ContainerInstanceFactory(generation_job=job, created_by=user)

    with mock.patch.object(smoke.requests, "get", side_effect=_status_map({})):
        smoke.run_smoke(job, container)

    job.refresh_from_db()
    assert job.metrics == before
    assert before["eval"]["agent_pass_rate"] == 0.4375
    container.refresh_from_db()
    assert container.metadata["smoke"]["pass_rate"] == 1.0
