from __future__ import annotations

from http import HTTPStatus
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from django.core.cache import cache

from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.services.enrichment import enrich_model
from backend.llm_models.services.enrichment import fetch_provider_endpoints
from backend.llm_models.services.enrichment import models_dev_entry
from backend.llm_models.services.enrichment import normalize_hf_info
from backend.llm_models.services.sync import upsert_openrouter_models
from backend.llm_models.tests.factories import LLMModelFactory

MODELS_DEV = {
    "openrouter": {
        "models": {
            "acme/coder-1": {
                "id": "acme/coder-1",
                "family": "coder",
                "knowledge": "2025-08-31",
                "release_date": "2026-02-17",
                "open_weights": True,
                "limit": {"context": 128000, "output": 8192},
                "cost": {"input": 1, "output": 2},
                "unexpected": "dropped",
            },
        },
    },
}

HF_RECORD = {
    "id": "acme/Coder-1",
    "author": "acme",
    "downloads": 1234,
    "likes": 56,
    "pipeline_tag": "text-generation",
    "tags": ["transformers", "license:apache-2.0", "arxiv:2412.19437"],
    "cardData": {"base_model": "acme/Coder-1-Base", "language": ["en"]},
    "config": {"architectures": ["CoderForCausalLM"], "model_type": "coder"},
    "safetensors": {"total": 7_000_000_000, "parameters": {"BF16": 7_000_000_000}},
    "gated": "False",
}


def _response(payload):
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_models_dev_entry_keeps_known_fields_and_falls_back_from_variant():
    catalog = MODELS_DEV["openrouter"]["models"]
    entry = models_dev_entry(catalog, "acme/coder-1:free")
    assert entry is not None
    assert entry["knowledge"] == "2025-08-31"
    assert entry["open_weights"] is True
    assert "unexpected" not in entry
    assert models_dev_entry(catalog, "acme/other") is None


def test_normalize_hf_info_reads_license_params_and_papers():
    info = normalize_hf_info(HF_RECORD)
    assert info["license"] == "apache-2.0"
    assert info["parameters"] == 7_000_000_000
    assert info["arxiv"] == ["2412.19437"]
    assert info["base_models"] == ["acme/Coder-1-Base"]
    assert info["architectures"] == ["CoderForCausalLM"]
    assert info["gated"] is False


@pytest.mark.django_db
@patch("backend.llm_models.services.enrichment.requests.get")
def test_enrich_model_stores_models_dev_and_hf(mock_get):
    model = LLMModelFactory(model_id="acme/coder-1", capabilities_json={"hugging_face_id": "acme/Coder-1"})
    mock_get.side_effect = lambda url, **_: _response(MODELS_DEV if "models.dev" in url else HF_RECORD)

    written = enrich_model(model)

    assert written == {"models_dev": True, "huggingface": True}
    model.refresh_from_db()
    assert model.metadata["models_dev"]["release_date"] == "2026-02-17"
    assert model.metadata["huggingface"]["downloads"] == 1234


@pytest.mark.django_db
@patch("backend.llm_models.services.enrichment.requests.get")
def test_enrich_model_survives_unreachable_sources(mock_get):
    import requests

    model = LLMModelFactory(model_id="acme/coder-1", capabilities_json={"hugging_face_id": "acme/Coder-1"})
    mock_get.side_effect = requests.ConnectionError("offline")

    assert enrich_model(model) == {"models_dev": False, "huggingface": False}


@pytest.mark.django_db
def test_catalog_resync_keeps_enrichment():
    model = LLMModelFactory(
        model_id="acme/coder-1",
        metadata={"models_dev": {"knowledge": "2025-08-31"}, "huggingface": {"likes": 3}, "stale": 1},
    )
    with patch("backend.llm_models.services.enrichment.requests.get"):
        upsert_openrouter_models([{"id": "acme/coder-1", "name": "Acme: Coder 1", "pricing": {}}])

    model.refresh_from_db()
    assert model.metadata["models_dev"] == {"knowledge": "2025-08-31"}
    assert model.metadata["huggingface"] == {"likes": 3}
    assert "stale" not in model.metadata
    assert model.metadata["openrouter_name"] == "Acme: Coder 1"


@pytest.mark.django_db
@patch("backend.llm_models.services.enrichment.requests.get")
def test_provider_endpoints_use_details_link_and_normalize(mock_get):
    model = LLMModelFactory(
        model_id="acme/coder-1",
        capabilities_json={"links": {"details": "/api/v1/models/acme/coder-1-20260101/endpoints"}},
    )
    mock_get.return_value = _response(
        {
            "data": {
                "endpoints": [
                    {
                        "provider_name": "Acme Cloud",
                        "quantization": "fp8",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        "uptime_last_30m": 99.5,
                        "native_tools": {"openrouter:web_search": {}},
                    },
                ],
            },
        },
    )

    result = fetch_provider_endpoints(model)

    assert mock_get.call_args.args[0].endswith("/models/acme/coder-1-20260101/endpoints")
    assert result["available"] is True
    endpoint = result["endpoints"][0]
    assert endpoint["provider_name"] == "Acme Cloud"
    assert endpoint["input_price_per_million"] == pytest.approx(1.0)
    assert endpoint["native_tools"] == ["openrouter:web_search"]
    # Second call is served from the cache.
    fetch_provider_endpoints(model)
    assert mock_get.call_count == 1


@pytest.mark.django_db
class TestModelInsightsAPI:
    def test_insights_summarize_own_jobs_only(self, client, user):
        client.force_login(user)
        model = LLMModelFactory(model_id="acme/coder-1", canonical_slug="acme_coder-1")
        metrics = {
            "cost": 0.5,
            "prompt_tokens": 1000,
            "cached_tokens": 500,
            "completion_tokens": 200,
            "lines_of_code": 2000,
            "engine": "opencode@1.0",
            "functional": {"passed": True, "pass_rate": 1.0, "status": "passed"},
        }
        GenerationJobFactory(created_by=user, model=model, status="completed", metrics=metrics, duration_seconds=100)
        GenerationJobFactory(created_by=user, model=model, status="failed")
        GenerationJobFactory(model=model, status="completed", metrics=metrics)  # another user's job

        resp = client.get("/api/models/detail/acme_coder-1/insights/")

        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        gen = data["generation"]
        assert gen["jobs_total"] == 2
        assert gen["success"]["rate"] == pytest.approx(0.5)
        assert gen["functional"]["successes"] == 1
        assert gen["resources"]["cache_hit_ratio"] == pytest.approx(0.5)
        assert gen["resources"]["cost_per_kloc"] == pytest.approx(0.25)
        assert gen["engines"] == {"opencode": 1}
        assert data["ranking"]["positions"]["composite"]["rank"] >= 1
        assert data["catalog"]["catalog_size"] == 1

    def test_insights_requires_auth(self, client):
        LLMModelFactory(model_id="acme/coder-1", canonical_slug="acme_coder-1")
        assert client.get("/api/models/detail/acme_coder-1/insights/").status_code == HTTPStatus.UNAUTHORIZED

    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_endpoints_route(self, mock_get, client, user):
        client.force_login(user)
        LLMModelFactory(model_id="acme/coder-1", canonical_slug="acme_coder-1")
        mock_get.return_value = _response({"data": {"endpoints": []}})

        resp = client.get("/api/models/detail/acme_coder-1/endpoints/")

        assert resp.status_code == HTTPStatus.OK
        assert resp.json()["available"] is True
