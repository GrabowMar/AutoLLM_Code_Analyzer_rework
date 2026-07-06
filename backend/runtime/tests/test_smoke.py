"""Tests for the functional smoke test (health + declared endpoint probing)."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.services import smoke
from backend.runtime.tests.factories import ContainerInstanceFactory

pytestmark = pytest.mark.django_db


def _job_with_endpoints(user=None, **kwargs):
    app_req = AppRequirementTemplateFactory(
        api_endpoints=[
            {"method": "GET", "path": "/api/urls", "description": "list"},
            {"method": "GET", "path": "/api/urls/:id", "description": "detail"},
            {"method": "POST", "path": "/api/shorten", "description": "create"},
        ],
        admin_api_endpoints=[
            {"method": "GET", "path": "/api/admin/urls", "description": "admin list"},
        ],
    )
    extra = {"created_by": user} if user else {}
    return GenerationJobFactory(app_requirement=app_req, **extra, **kwargs)


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
    assert job.metrics["functional"]["passed"] is True
    assert "checks" not in job.metrics["functional"]  # job copy stays lean
    assert container.metadata["smoke"]["checks"]


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
    assert by_path["/api/urls/:id"]["ok"] is True  # record 1 may just not exist
    assert by_path["/api/urls/:id"]["probed"] == "/api/urls/1"
    # 2/3 ok is below the 0.8 gate.
    assert result["passed"] is False


def test_smoke_no_routing_configured(user):
    job = _job_with_endpoints(user)
    container = ContainerInstanceFactory(generation_job=job, created_by=user, app_port=None)

    result = smoke.run_smoke(job, container)
    assert result["passed"] is False
    assert "error" in result
