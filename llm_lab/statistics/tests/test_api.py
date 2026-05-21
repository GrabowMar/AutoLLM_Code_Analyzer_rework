"""API integration tests for the statistics endpoints."""

from __future__ import annotations

import pytest

from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_dashboard_endpoint(auth_client):
    client, user = auth_client
    GenerationJobFactory(created_by=user)
    AnalysisTaskFactory(created_by=user)

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
