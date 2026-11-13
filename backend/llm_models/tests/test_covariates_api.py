"""GET /api/models/covariates/: one covariate row per visible model (JSON and formula-safe CSV)."""

from __future__ import annotations

import csv
import io
from http import HTTPStatus
from unittest.mock import patch

import pytest

from backend.generation.tests.factories import GenerationRunFactory
from backend.inference.models import LLMEndpoint
from backend.llm_models.models import LLMModel
from backend.llm_models.services.model_covariates import COLUMNS
from backend.llm_models.services.model_covariates import covariate_row
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

URL = "/api/models/covariates/"

CATALOG_META = {
    "models_dev": {"open_weights": True, "knowledge": "2025-05", "release_date": "2026-09-10"},
    "huggingface": {
        "id": "lab/Flash-One",
        "license": "mit",
        "parameters": 763_205_315_794,
        "downloads": 1_640_989,
        "downloads_all_time": None,
        "likes": 4744,
        "fetched_at": "2026-10-26T00:00:00+00:00",
        "architecture": {
            "is_moe": True,
            "moe": {"num_experts": 384, "experts_per_token": 6},
            "num_hidden_layers": 40,
            "hidden_size": 5120,
            "native_context": 65536,
            "torch_dtype": "bfloat16",
            "quantization_config": {"quant_method": "fp8"},
        },
        "generation_config": {"temperature": 1.0, "top_p": 0.95, "from_model_config": False},
    },
    "params": {
        "checkpoint_total": 763_205_315_794,
        "reported_total": None,
        "active": None,
        "active_source": None,
        "is_moe": True,
    },
    "epoch": {
        "model": "Flash One",
        "parameters": 552_000_000_000,
        "active_params_parsed": 16_000_000_000,
        "training_compute_flop": 4.32e24,
        "confidence": "Likely",
        "match": "variant",
        "fetched_at": "2026-11-02T00:00:00+00:00",
    },
    "lmarena": {
        "categories": {
            "webdev": {
                "arena_name": "Flash One (Max)",
                "rating": 1619.2,
                "rating_lower": 1608.2,
                "rating_upper": 1630.1,
                "vote_count": 3510,
                "rank": 29,
                "n_ranked": 152,
            },
            "agent": {"score": 0.0191, "score_lower": 0.0158, "score_upper": 0.0225},
        },
        "publish_date": "2026-10-28",
        "match": "variant",
        "fetched_at": "2026-11-02T00:00:00+00:00",
    },
}


def _local_endpoint(owner, **kwargs) -> LLMEndpoint:
    defaults = {
        "slug": "sub-go",
        "name": "Subscription",
        "kind": "openai_compatible",
        "connection": "direct",
        "api_flavor": "openai",
        "max_concurrency": 2,
        "owner": owner,
    }
    defaults.update(kwargs)
    return LLMEndpoint.objects.create(**defaults)


def _local_model(endpoint, name="flash-one", **kwargs) -> LLMModel:
    defaults = {
        "model_id": f"local-{endpoint.slug}/{name}",
        "canonical_slug": f"local-{endpoint.slug}_{name}",
        "provider": endpoint.slug,
        "model_name": name,
        "endpoint": endpoint,
        "served_model_name": name,
        "context_window": 0,
        "max_output_tokens": 0,
        "is_free": True,
        "metadata": {"ollama": {}},
    }
    defaults.update(kwargs)
    return LLMModel.objects.create(**defaults)


@pytest.fixture
def api_client(client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestCovariatesAPI:
    def test_requires_auth(self, client):
        assert client.get(URL).status_code == HTTPStatus.UNAUTHORIZED

    def test_rejects_unknown_format(self, api_client):
        assert api_client.get(URL, {"format": "xlsx"}).status_code == HTTPStatus.BAD_REQUEST

    def test_used_only_counts_the_callers_jobs(self, api_client, user):
        used = LLMModelFactory(model_id="lab/flash-one", metadata=CATALOG_META)
        LLMModelFactory(model_id="lab/unused")
        other_users_model = LLMModelFactory(model_id="lab/others")
        GenerationRunFactory(model=used, created_by=user, status="completed")
        GenerationRunFactory(model=used, created_by=user, status="failed")
        GenerationRunFactory(model=other_users_model, created_by=UserFactory(), status="completed")

        data = api_client.get(URL).json()
        assert data["used_only"] is True
        assert data["columns"] == list(COLUMNS)
        assert [r["model_id"] for r in data["rows"]] == ["lab/flash-one"]
        row = data["rows"][0]
        assert (row["n_jobs"], row["n_completed_jobs"]) == (2, 1)
        assert data["n_models"] == 1
        assert any("CC-BY" in (s["license"] or "") for s in data["sources"])

        everything = api_client.get(URL, {"used_only": "false"}).json()
        ids = {r["model_id"] for r in everything["rows"]}
        assert {"lab/flash-one", "lab/unused", "lab/others"} <= ids
        assert next(r for r in everything["rows"] if r["model_id"] == "lab/others")["n_jobs"] == 0

    def test_catalog_row_reads_stored_covariates(self, api_client, user):
        model = LLMModelFactory(
            model_id="lab/flash-one",
            metadata=CATALOG_META,
            capabilities_json={"knowledge_cutoff": "2025-06-01"},
            input_price_per_token=0.0000002,
            output_price_per_token=0.0000008,
        )
        GenerationRunFactory(model=model, created_by=user)
        row = api_client.get(URL).json()["rows"][0]
        assert row["open_weights"] is True
        assert row["license"] == "mit"
        assert row["knowledge_cutoff"] == "2025-05"
        assert row["knowledge_cutoff_source"] == "models.dev"
        assert row["knowledge_cutoff_conflict"] is True  # OpenRouter says 2025-06
        assert row["input_usd_per_1m"] == pytest.approx(0.2)
        assert row["params_checkpoint_total"] == 763_205_315_794
        # Reported/active fall back to Epoch with the source named.
        assert (row["params_reported_total"], row["params_reported_source"]) == (552_000_000_000, "epoch")
        assert (row["params_active"], row["params_active_source"]) == (16_000_000_000, "epoch_notes")
        assert row["arch_is_moe"] is True
        assert (row["arch_num_experts"], row["arch_experts_per_token"]) == (384, 6)
        assert row["arch_quant_method"] == "fp8"
        assert row["hf_downloads_all_time"] is None
        assert row["epoch_training_compute_flop"] == pytest.approx(4.32e24)
        assert row["lmarena_webdev_rating"] == pytest.approx(1619.2)
        assert row["lmarena_webdev_votes"] == 3510
        assert row["lmarena_agent_score"] == pytest.approx(0.0191)
        assert row["lmarena_text_coding_rating"] is None
        assert row["recommended_temperature"] == pytest.approx(1.0)
        assert row["inherited_fields"] == []
        assert row["catalog_twin"] is None
        assert (row["epoch_match"], row["lmarena_match"]) == ("variant", "variant")  # own record: as curated

    def test_missing_values_are_null_not_zero(self, api_client, user):
        model = LLMModelFactory(model_id="lab/bare", metadata={}, context_window=0, max_output_tokens=0)
        GenerationRunFactory(model=model, created_by=user)
        row = api_client.get(URL).json()["rows"][0]
        for key in (
            "context_window",
            "max_output_tokens",
            "params_checkpoint_total",
            "params_active",
            "hf_downloads_30d",
            "hf_likes",
            "epoch_training_compute_flop",
            "lmarena_webdev_rating",
            "open_weights",
            "knowledge_cutoff",
            "knowledge_cutoff_conflict",
        ):
            assert row[key] is None, key

    def test_local_model_inherits_intrinsic_covariates_from_twin(self, api_client, user):
        twin = LLMModelFactory(model_id="lab/flash-one", metadata=CATALOG_META)
        endpoint = _local_endpoint(user)
        local = _local_model(
            endpoint,
            metadata={
                "ollama": {},
                "catalog_twin": {
                    "model_id": twin.model_id,
                    "source": "models.dev:sub-go",
                    "provider_record": {"release_date": "2026-09-10", "knowledge": "2025-05"},
                },
                # Own external import wins over the twin's.
                "lmarena": {"categories": {"webdev": {"rating": 1600.0}}, "match": "assumed"},
            },
        )
        GenerationRunFactory(model=local, created_by=user)
        row = api_client.get(URL).json()["rows"][0]
        assert row["is_local"] is True
        assert row["endpoint"] == "sub-go"
        assert row["catalog_twin"] == "lab/flash-one"
        assert row["catalog_twin_source"] == "models.dev:sub-go"
        assert row["inherited_from"] == "lab/flash-one"
        assert set(row["inherited_fields"]) == {"models_dev", "huggingface", "params", "epoch"}
        assert row["license"] == "mit"
        assert row["epoch_model"] == "Flash One"
        # The twin's curated "variant" describes the twin's route; inherited, it is only assumed.
        assert row["epoch_match"] == "assumed"
        assert row["lmarena_webdev_rating"] == pytest.approx(1600.0)
        assert row["lmarena_match"] == "assumed"
        # Operational fields are the route's own, never inherited.
        assert row["context_window"] is None
        assert row["input_usd_per_1m"] is None

    def test_inherited_external_records_are_reported_as_assumed(self, api_client, user):
        # A route without its own curated entry (e.g. a direct-provider endpoint) inherits both
        # external records; the twin's "variant" must not be presented as this route's match.
        twin = LLMModelFactory(model_id="lab/flash-one", metadata=CATALOG_META)
        local = _local_model(
            _local_endpoint(user),
            metadata={"catalog_twin": {"model_id": twin.model_id, "source": "models.dev:sub-go"}},
        )
        GenerationRunFactory(model=local, created_by=user)
        row = api_client.get(URL).json()["rows"][0]
        assert {"epoch", "lmarena"} <= set(row["inherited_fields"])
        assert row["lmarena_webdev_rating"] == pytest.approx(1619.2)
        assert (row["epoch_match"], row["lmarena_match"]) == ("assumed", "assumed")

    def test_manual_twin_from_map_when_enrichment_has_not_linked(self, api_client, user):
        LLMModelFactory(model_id="lab/flash-one", metadata=CATALOG_META)
        local = _local_model(_local_endpoint(user))
        GenerationRunFactory(model=local, created_by=user)
        with patch(
            "backend.llm_models.services.model_covariates.map_twins",
            return_value={local.model_id: "lab/flash-one"},
        ):
            row = api_client.get(URL).json()["rows"][0]
        assert row["catalog_twin"] == "lab/flash-one"
        assert row["catalog_twin_source"] == "external_model_map (manual)"
        assert row["hf_id"] == "lab/Flash-One"

    def test_private_endpoint_models_are_hidden_from_other_users(self, client, user):
        owner = UserFactory()
        hidden = _local_model(_local_endpoint(owner, visibility=LLMEndpoint.Visibility.PRIVATE))
        GenerationRunFactory(model=hidden, created_by=user)  # even with a job, visibility rules apply
        client.force_login(user)
        rows = client.get(URL, {"used_only": "false"}).json()["rows"]
        assert hidden.model_id not in {r["model_id"] for r in rows}

    def test_csv_is_formula_safe(self, api_client, user):
        model = LLMModelFactory(
            model_id="lab/flash-one",
            model_name='=HYPERLINK("http://x")',
            metadata=CATALOG_META,
        )
        GenerationRunFactory(model=model, created_by=user)
        resp = api_client.get(URL, {"format": "csv"})
        assert resp.status_code == HTTPStatus.OK
        assert resp["Content-Type"].startswith("text/csv")
        assert "attachment" in resp["Content-Disposition"]
        reader = list(csv.reader(io.StringIO(resp.content.decode())))
        assert reader[0] == list(COLUMNS)
        row = dict(zip(reader[0], reader[1], strict=True))
        assert row["model_name"].startswith("'=")
        assert row["is_local"] == "false"
        assert row["arch_is_moe"] == "true"
        assert row["hf_downloads_all_time"] == ""
        assert row["inherited_fields"] == ""
        assert float(row["epoch_training_compute_flop"]) == pytest.approx(4.32e24)


@pytest.mark.django_db
def test_covariate_row_has_every_column():
    row = covariate_row(LLMModelFactory(model_id="lab/x", metadata={}))
    assert list(row) == list(COLUMNS)
