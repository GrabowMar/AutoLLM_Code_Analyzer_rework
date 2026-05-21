"""API tests for rankings endpoints."""

from __future__ import annotations

import pytest

from llm_lab.llm_models.tests.factories import LLMModelFactory
from llm_lab.rankings.models import BenchmarkResult
from llm_lab.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_list_rankings(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")
    LLMModelFactory(model_id="m2", model_name="Two", provider="Google")
    BenchmarkResult.objects.create(model_id="m1", benchmark="humaneval", score=80)

    res = client.get("/api/rankings/?per_page=10")
    assert res.status_code == 200
    body = res.json()
    assert body["pagination"]["total"] == 2
    assert body["statistics"]["unique_providers"] == 2
    assert body["statistics"]["with_benchmarks"] == 1


def test_list_rankings_filters(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")
    LLMModelFactory(model_id="m2", model_name="Two", provider="Google")

    res = client.get("/api/rankings/?provider=openai")
    assert res.status_code == 200
    body = res.json()
    assert body["pagination"]["total"] == 1
    assert body["rankings"][0]["model_id"] == "m1"


def test_top_models(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One")

    res = client.get("/api/rankings/top/?count=5")
    assert res.status_code == 200
    body = res.json()
    assert "models" in body
    assert body["count"] >= 1


def test_status_endpoint(auth_client):
    client, _ = auth_client
    res = client.get("/api/rankings/status/")
    assert res.status_code == 200
    body = res.json()
    assert "benchmarks" in body
    assert "total_models" in body


def test_export_csv(auth_client):
    client, _ = auth_client
    LLMModelFactory(model_id="m1", model_name="One", provider="OpenAI")

    res = client.get("/api/rankings/export/")
    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/csv")
    body = res.content.decode()
    assert "model_id" in body
    assert "m1" in body


def test_unauthenticated(client):
    assert client.get("/api/rankings/").status_code == 401
