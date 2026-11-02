"""API integration tests for the statistics endpoints."""

from __future__ import annotations

import pytest

from backend.analysis.tests.factories import AnalysisRunFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_dashboard_endpoint(auth_client):
    client, user = auth_client
    GenerationJobFactory(created_by=user)
    AnalysisRunFactory(created_by=user)

    res = client.get("/api/statistics/dashboard/")

    assert res.status_code == 200
    payload = res.json()
    assert payload["overview"]["total_apps"] == 1
    assert payload["overview"]["total_analyses"] == 1


def test_overview_endpoint(auth_client):
    client, _ = auth_client
    res = client.get("/api/statistics/overview/")
    assert res.status_code == 200
    body = res.json()
    assert "total_apps" in body
    assert "apps_success_rate" in body


def test_trends_endpoint_validates_days(auth_client):
    client, _ = auth_client
    res = client.get("/api/statistics/trends/?days=14")
    assert res.status_code == 200
    body = res.json()
    assert body["days"] == 14
    assert len(body["series"]) == 14


def test_unauthenticated_blocked(client):
    res = client.get("/api/statistics/dashboard/")
    assert res.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/statistics/models/?experiment_id=not-a-uuid",
        "/api/statistics/study/breakdowns/?experiment_id=not-a-uuid",
        "/api/statistics/study/healing/?batch_id=not-a-uuid",
        "/api/statistics/study/accounting/?batch_id=not-a-uuid",
    ],
)
def test_malformed_scope_ids_are_a_bad_request(auth_client, path):
    client, _ = auth_client
    res = client.get(path)
    assert res.status_code == 400
    assert "must be a UUID" in res.json()["detail"]


def test_well_formed_scope_ids_are_accepted(auth_client):
    client, _ = auth_client
    scope = "experiment_id=6f1c1d5e-9a43-4a8b-b6a4-3c3f4f0b9d2e"
    assert client.get(f"/api/statistics/models/?{scope}").status_code == 200
    assert client.get(f"/api/statistics/study/accounting/?{scope}").status_code == 200


def test_tools_endpoint_reports_unmeasured_executions(auth_client):
    from backend.analysis.models import ToolResult
    from backend.analysis.tests.factories import ToolResultFactory

    client, user = auth_client
    run = AnalysisRunFactory(created_by=user)
    ToolResultFactory(run=run, tool_slug="mypy", status=ToolResult.Status.FAILED)

    res = client.get("/api/statistics/tools/")

    assert res.status_code == 200
    row = next(t for t in res.json() if t["name"] == "mypy")
    assert (row["scans"], row["not_measured"], row["avg_per_scan"]) == (0, 1, None)


def test_top_findings_endpoint_returns_the_tools_label_not_the_normalized_level(auth_client):
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    client, user = auth_client
    result = ToolResultFactory(run=AnalysisRunFactory(created_by=user), tool_slug="bandit")
    FindingFactory.create_batch(2, result=result, title="Use of assert", severity="low", tool_severity="LOW")

    for path in ("/api/statistics/top-findings/", "/api/statistics/dashboard/"):
        body = client.get(path).json()
        rows = body if isinstance(body, list) else body["top_findings"]
        assert rows == [
            {
                "title": "Use of assert",
                "tool": "bandit",
                "tool_severity": "LOW",
                "count": 2,
                "dependency_advisory": False,
            },
        ]


def test_overview_duration_is_null_before_an_analysis_completed(auth_client):
    client, user = auth_client
    AnalysisRunFactory(created_by=user, status="failed", duration_seconds=3)

    body = client.get("/api/statistics/overview/").json()

    assert body["avg_analysis_duration_seconds"] is None
