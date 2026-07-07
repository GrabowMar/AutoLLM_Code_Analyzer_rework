"""API tests for rankings endpoints."""

from __future__ import annotations

import pytest

from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.models import BenchmarkResult
from backend.users.tests.factories import UserFactory

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
    header = body.splitlines()[0]
    for column in (
        "empirical_quality_score",
        "composite_score",
        "smoke_pass_rate",
        "n_trials",
        "empirical_density_stdev",
        "findings_total_static",
        "ai_findings_total",
    ):
        assert column in header


def test_sensitivity_endpoint(auth_client):
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory
    from backend.generation.models import GenerationJob
    from backend.generation.tests.factories import GenerationJobFactory

    client, user = auth_client
    model = LLMModelFactory(model_id="m1", model_name="One")
    job = GenerationJobFactory(
        created_by=user,
        model=model,
        status=GenerationJob.Status.COMPLETED,
        metrics={"lines_of_code": 1000},
    )
    run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=run, tool_slug="bandit")
    Finding.objects.create(result=result, severity="high", title="f")

    res = client.get("/api/rankings/sensitivity/")
    assert res.status_code == 200
    body = res.json()
    assert body["models_evaluated"] == 1
    assert body["baseline_ranking"][0]["model_id"] == "m1"
    schemes = {s["scheme"] for s in body["schemes"]}
    assert schemes == {"security_heavy", "flat", "info_included"}
    for scheme in body["schemes"]:
        assert -1.0 <= scheme["kendall_tau"] <= 1.0
        assert isinstance(scheme["adjacent_swaps"], list)


def test_unauthenticated(client):
    assert client.get("/api/rankings/").status_code == 401
    assert client.get("/api/rankings/sensitivity/").status_code == 401
