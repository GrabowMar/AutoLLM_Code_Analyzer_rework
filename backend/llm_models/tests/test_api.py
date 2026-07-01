import json
from http import HTTPStatus
from unittest.mock import patch

import pytest
from django.test import override_settings

from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.llm_models.models import LLMModel
from backend.llm_models.tests.factories import LLMModelFactory


@pytest.fixture
def api_client(client, user):
    """Authenticated test client."""
    client.force_login(user)
    return client


def _set_user_openrouter_key(user, raw: str) -> UserApiCredential:
    cred = UserApiCredential(user=user, provider=Provider.OPENROUTER)
    cred.set_secret(raw)
    cred.save()
    return cred


@pytest.mark.django_db
class TestModelsListAPI:
    def test_list_requires_auth(self, client):
        resp = client.get("/api/models/")
        assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_list_empty(self, api_client):
        resp = api_client.get("/api/models/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_with_models(self, api_client):
        LLMModelFactory.create_batch(3)
        resp = api_client.get("/api/models/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        batch_size = 3
        assert data["total"] == batch_size
        assert len(data["items"]) == batch_size

    def test_search(self, api_client):
        LLMModelFactory(
            model_name="GPT-4o",
            model_id="openai/gpt-4o",
            canonical_slug="openai_gpt-4o",
        )
        LLMModelFactory(
            model_name="Claude",
            model_id="anthropic/claude",
            canonical_slug="anthropic_claude",
        )
        resp = api_client.get("/api/models/?search=GPT")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["model_name"] == "GPT-4o"

    def test_filter_by_provider(self, api_client):
        LLMModelFactory(
            provider="openai",
            model_id="openai/m1",
            canonical_slug="openai_m1",
        )
        LLMModelFactory(
            provider="anthropic",
            model_id="anthropic/m2",
            canonical_slug="anthropic_m2",
        )
        resp = api_client.get("/api/models/?provider=openai")
        data = resp.json()
        assert data["total"] == 1

    def test_pagination(self, api_client):
        total_models = 5
        page_size = 2
        expected_pages = 3
        for i in range(total_models):
            LLMModelFactory(model_id=f"p/m{i}", canonical_slug=f"p_m{i}")
        resp = api_client.get("/api/models/?per_page=2&page=1")
        data = resp.json()
        assert data["total"] == total_models
        assert len(data["items"]) == page_size
        assert data["pages"] == expected_pages


@pytest.mark.django_db
class TestModelDetailAPI:
    def test_get_model(self, api_client):
        m = LLMModelFactory(
            canonical_slug="openai_gpt-4o",
            model_id="openai/gpt-4o",
        )
        resp = api_client.get(f"/api/models/detail/{m.canonical_slug}/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["canonical_slug"] == "openai_gpt-4o"

    def test_not_found(self, api_client):
        resp = api_client.get("/api/models/detail/nonexistent/")
        assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestModelStatsAPI:
    def test_stats_empty(self, api_client):
        resp = api_client.get("/api/models/stats/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["total"] == 0

    def test_stats_with_data(self, api_client):
        LLMModelFactory(
            provider="openai",
            model_id="o/m1",
            canonical_slug="o_m1",
            is_free=True,
        )
        LLMModelFactory(
            provider="anthropic",
            model_id="a/m2",
            canonical_slug="a_m2",
            is_free=False,
        )
        resp = api_client.get("/api/models/stats/")
        data = resp.json()
        expected_count = 2
        assert data["total"] == expected_count
        assert data["providers"] == expected_count
        assert data["free"] == 1


@pytest.mark.django_db
class TestProvidersAPI:
    def test_list_providers(self, api_client):
        LLMModelFactory(provider="openai", model_id="o/m1", canonical_slug="o_m1")
        LLMModelFactory(provider="anthropic", model_id="a/m2", canonical_slug="a_m2")
        resp = api_client.get("/api/models/providers/")
        assert resp.status_code == HTTPStatus.OK
        providers = resp.json()
        assert "openai" in providers
        assert "anthropic" in providers


@pytest.mark.django_db
class TestSyncAPI:
    @patch("backend.llm_models.api.views.sync_models_from_openrouter")
    def test_sync(self, mock_sync, api_client, user):
        _set_user_openrouter_key(user, "user-key")
        expected_fetched = 100
        expected_upserted = 95
        mock_sync.return_value = {
            "fetched": expected_fetched,
            "upserted": expected_upserted,
        }
        resp = api_client.post("/api/models/sync/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["fetched"] == expected_fetched
        assert data["upserted"] == expected_upserted
        mock_sync.assert_called_once_with("user-key")

    @override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
    @patch("backend.llm_models.api.views.sync_models_from_openrouter")
    def test_sync_requires_user_key_even_when_global_fallback_exists(self, mock_sync, api_client):
        resp = api_client.post("/api/models/sync/")

        assert resp.status_code == HTTPStatus.FORBIDDEN
        data = resp.json()
        assert data["detail"] == "No OpenRouter API key is configured for this account."
        assert "Settings" in data["remediation"]
        mock_sync.assert_not_called()


@pytest.mark.django_db
class TestImportAPI:
    def test_import_openrouter_payload(self, api_client):
        payload = {
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "pricing": {
                        "prompt": "0.0000025",
                        "completion": "0.00001",
                    },
                    "top_provider": {
                        "context_length": 128000,
                        "max_completion_tokens": 4096,
                    },
                    "supported_parameters": ["tools", "stream", "response_format"],
                },
            ],
        }

        resp = api_client.post(
            "/api/models/import/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        assert resp.status_code == HTTPStatus.OK, resp.content
        assert resp.json() == {"count": 1, "imported": 1}
        assert LLMModel.objects.filter(canonical_slug="openai_gpt-4o").exists()

    def test_import_exported_payload(self, api_client):
        payload = {
            "models": [
                {
                    "model_id": "anthropic/claude-3-5-sonnet",
                    "canonical_slug": "anthropic_claude-3-5-sonnet",
                    "provider": "anthropic",
                    "model_name": "Claude 3.5 Sonnet",
                    "description": "Imported from export.",
                    "context_window": 200000,
                    "max_output_tokens": 8192,
                    "input_price_per_token": 0.000003,
                    "output_price_per_token": 0.000015,
                    "supports_function_calling": True,
                    "supports_vision": True,
                    "supports_streaming": True,
                    "supports_json_mode": True,
                    "metadata": {"source": "test"},
                    "capabilities_json": {"supported_parameters": ["tools"]},
                },
            ],
        }

        resp = api_client.post(
            "/api/models/import/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        assert resp.status_code == HTTPStatus.OK, resp.content
        assert resp.json() == {"count": 1, "imported": 1}
        model = LLMModel.objects.get(canonical_slug="anthropic_claude-3-5-sonnet")
        assert model.model_name == "Claude 3.5 Sonnet"
        assert model.metadata == {"source": "test"}


@pytest.mark.django_db
class TestExportAPI:
    def test_export_csv(self, api_client):
        LLMModelFactory(
            provider="openai",
            model_name="GPT-4o",
            model_id="openai/gpt-4o",
            canonical_slug="openai_gpt-4o",
        )

        resp = api_client.get("/api/models/export/?format=csv")

        assert resp.status_code == HTTPStatus.OK
        assert resp["Content-Disposition"] == 'attachment; filename="models_export.csv"'
        content = resp.content.decode("utf-8")
        assert "canonical_slug" in content
        assert "openai_gpt-4o" in content

    def test_export_json(self, api_client):
        LLMModelFactory(
            provider="openai",
            model_name="GPT-4o",
            model_id="openai/gpt-4o",
            canonical_slug="openai_gpt-4o",
        )

        resp = api_client.get("/api/models/export/?format=json")

        assert resp.status_code == HTTPStatus.OK
        payload = resp.json()
        assert payload["format"] == "json"
        assert payload["count"] == 1
        assert payload["models"][0]["canonical_slug"] == "openai_gpt-4o"


@pytest.mark.django_db
class TestComparisonAPI:
    def test_compare_models_preserves_order(self, api_client):
        first = LLMModelFactory(
            model_id="openai/gpt-4o",
            canonical_slug="openai_gpt-4o",
            model_name="GPT-4o",
        )
        second = LLMModelFactory(
            model_id="anthropic/claude-3-5-sonnet",
            canonical_slug="anthropic_claude-3-5-sonnet",
            model_name="Claude 3.5 Sonnet",
        )

        resp = api_client.get(
            f"/api/models/comparison/?models={second.canonical_slug},{first.canonical_slug}",
        )

        assert resp.status_code == HTTPStatus.OK
        payload = resp.json()
        assert [item["canonical_slug"] for item in payload["items"]] == [
            second.canonical_slug,
            first.canonical_slug,
        ]
        assert payload["missing"] == []


@pytest.mark.django_db
class TestRefreshAPI:
    @patch("backend.llm_models.api.views.refresh_model_from_openrouter")
    def test_refresh_model(self, mock_refresh, api_client, user):
        _set_user_openrouter_key(user, "user-key")
        model = LLMModelFactory(
            model_id="openai/gpt-4o",
            canonical_slug="openai_gpt-4o",
            model_name="Old Name",
        )

        def refresh_side_effect(instance, api_key):
            assert api_key == "user-key"
            instance.model_name = "New Name"
            instance.save(update_fields=["model_name", "updated_at"])
            return True

        mock_refresh.side_effect = refresh_side_effect

        resp = api_client.post(f"/api/models/detail/{model.canonical_slug}/refresh/")

        assert resp.status_code == HTTPStatus.OK, resp.content
        assert resp.json()["model_name"] == "New Name"
        mock_refresh.assert_called_once()

    @override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
    @patch("backend.llm_models.api.views.refresh_model_from_openrouter")
    def test_refresh_requires_user_key_even_when_global_fallback_exists(self, mock_refresh, api_client):
        model = LLMModelFactory(
            model_id="openai/gpt-4o",
            canonical_slug="openai_gpt-4o",
        )

        resp = api_client.post(f"/api/models/detail/{model.canonical_slug}/refresh/")

        assert resp.status_code == HTTPStatus.FORBIDDEN
        data = resp.json()
        assert data["detail"] == "No OpenRouter API key is configured for this account."
        assert "Settings" in data["remediation"]
        mock_refresh.assert_not_called()


@pytest.mark.django_db
class TestDeleteAPI:
    def test_delete(self, api_client):
        m = LLMModelFactory(
            model_id="del/test",
            canonical_slug="del_test",
        )
        resp = api_client.delete(f"/api/models/detail/{m.canonical_slug}/")
        assert resp.status_code == HTTPStatus.OK
        assert not LLMModel.objects.filter(canonical_slug="del_test").exists()
