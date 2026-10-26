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
    header = body.splitlines()[0].split(",")
    # Legacy columns keep their names and order; new ones are appended.
    assert header[:22] == [
        "model_id",
        "model_name",
        "provider",
        "is_free",
        "context_length",
        "price_per_million_input",
        "price_per_million_output",
        "mss_score",
        "adoption_score",
        "benchmark_score",
        "cost_efficiency_score",
        "accessibility_score",
        "composite_score",
        "empirical_quality_score",
        "smoke_pass_rate",
        "n_trials",
        "empirical_density_stdev",
        "smoke_pass_rate_stdev",
        "findings_total_static",
        "ai_findings_total",
        "apps",
        "apps_completed",
    ]
    for column in ("issues_total_static", "measured_sloc", "plan_hash", "scoring_version"):
        assert column in header


def _measured_job(user, model_id, **measure):
    from backend.generation.models import GenerationJob
    from backend.generation.tests.factories import GenerationJobFactory
    from backend.rankings.tests.measured import make_run

    model = LLMModelFactory(model_id=model_id, model_name=model_id)
    job = GenerationJobFactory(created_by=user, model=model, status=GenerationJob.Status.COMPLETED)
    make_run(job, **measure)
    return job


def test_export_csv_honours_filters_and_leaves_unmeasured_cells_empty(auth_client):
    from backend.generation.tests.factories import ExperimentFactory

    client, user = auth_client
    job = _measured_job(user, "m/measured", issues={"high": 2}, findings={"high": 2})
    experiment = ExperimentFactory(created_by=user)
    job.experiment = experiment
    job.save(update_fields=["experiment"])
    LLMModelFactory(model_id="m/unmeasured")

    res = client.get(f"/api/rankings/export/?experiment_id={experiment.id}&search=m/")
    import csv
    import io

    rows = {r["model_id"]: r for r in csv.DictReader(io.StringIO(res.content.decode()))}
    assert rows["m/measured"]["findings_total_static"] == "2"
    assert rows["m/measured"]["n_trials"] == "1"
    assert rows["m/unmeasured"]["findings_total_static"] == ""
    assert rows["m/unmeasured"]["empirical_quality_score"] == ""
    assert res["X-Analysis-Plan-Hash"]


def test_list_rankings_reports_comparability(auth_client):
    client, user = auth_client
    _measured_job(user, "m/one", issues={"high": 1})

    body = client.get("/api/rankings/?search=m/one").json()

    comparability = body["comparability"]
    assert comparability["measured"] == 1
    assert comparability["plan_hash"]
    assert comparability["scoring_version"]
    assert body["statistics"]["measured_models"] == 1
    assert body["filters_applied"]["search"] == "m/one"
    assert body["rankings"][0]["n_trials"] == 1


def test_list_rankings_only_counts_my_jobs(auth_client):
    client, _ = auth_client
    other = UserFactory()
    _measured_job(other, "m/theirs", issues={"high": 1})

    body = client.get("/api/rankings/?search=m/theirs").json()

    assert body["rankings"][0]["apps"] == 0
    assert body["rankings"][0]["empirical_quality_score"] is None


def test_sensitivity_endpoint(auth_client):
    client, user = auth_client
    _measured_job(user, "m1", issues={"high": 1})
    _measured_job(user, "m2", issues={"low": 30})

    res = client.get("/api/rankings/sensitivity/")
    assert res.status_code == 200
    body = res.json()
    assert body["models_evaluated"] == 2
    assert body["defined"] is True
    assert body["comparability"]["measured"] == 2
    assert {r["model_id"] for r in body["baseline_ranking"]} == {"m1", "m2"}
    schemes = {s["scheme"] for s in body["schemes"]}
    assert schemes == {"security_heavy", "flat", "info_included"}
    for scheme in body["schemes"]:
        assert -1.0 <= scheme["kendall_tau"] <= 1.0
        assert isinstance(scheme["adjacent_swaps"], list)


def test_sensitivity_endpoint_honours_filters(auth_client):
    client, user = auth_client
    _measured_job(user, "m1", issues={"high": 1})
    _measured_job(user, "m2", issues={"low": 30})

    body = client.get("/api/rankings/sensitivity/?search=m1").json()

    assert body["models_evaluated"] == 1
    assert body["defined"] is False


def test_unauthenticated(client):
    assert client.get("/api/rankings/").status_code == 401
    assert client.get("/api/rankings/sensitivity/").status_code == 401


@pytest.mark.parametrize("path", ["/api/rankings/", "/api/rankings/sensitivity/", "/api/rankings/export/"])
def test_malformed_experiment_id_is_a_422_not_a_500(auth_client, path):
    client, _ = auth_client
    client.raise_request_exception = False
    LLMModelFactory(model_id="m1")

    assert client.get(f"{path}?experiment_id=abc").status_code == 422
    # A well-formed id of no experiment, or an empty value, is simply an empty scope.
    assert client.get(f"{path}?experiment_id=00000000-0000-0000-0000-000000000000").status_code == 200
    assert client.get(f"{path}?experiment_id=").status_code == 200
