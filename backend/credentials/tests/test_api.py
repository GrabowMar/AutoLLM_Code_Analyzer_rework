from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client

from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.credentials.models import ValidationStatus
from backend.credentials.services.validator import ValidationResult

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_get_status_no_credential(auth_client):
    resp = auth_client.get("/api/credentials/openrouter/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["key_prefix"] == ""


def test_put_invalid_key_rejected(auth_client):
    with patch(
        "backend.credentials.api.views.validate_for_provider",
        side_effect=lambda p, k: ValidationResult(ValidationStatus.INVALID, "bad key"),
    ):
        resp = auth_client.put(
            "/api/credentials/openrouter/",
            data='{"api_key": "sk-or-bad"}',
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert "bad key" in resp.json()["detail"]


def test_put_valid_key_stores_encrypted(auth_client, user):
    with patch(
        "backend.credentials.api.views.validate_for_provider",
        side_effect=lambda p, k: ValidationResult(ValidationStatus.VALID, "ok"),
    ):
        resp = auth_client.put(
            "/api/credentials/openrouter/",
            data='{"api_key": "sk-or-v1-secrettoken12345"}',
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["key_prefix"] == "sk-or-v1-sec"
    assert data["last_validation_status"] == ValidationStatus.VALID

    cred = UserApiCredential.objects.get(user=user, provider=Provider.OPENROUTER)
    # raw secret must not be in stored encrypted_secret
    assert "secrettoken" not in cred.encrypted_secret
    assert cred.get_secret() == "sk-or-v1-secrettoken12345"


def test_put_empty_key_rejected(auth_client):
    resp = auth_client.put(
        "/api/credentials/openrouter/",
        data='{"api_key": ""}',
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_delete_removes_credential(auth_client, user):
    cred = UserApiCredential(user=user, provider=Provider.OPENROUTER)
    cred.set_secret("sk-or-v1-abc")
    cred.save()
    resp = auth_client.delete("/api/credentials/openrouter/")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert not UserApiCredential.objects.filter(user=user).exists()


def test_test_endpoint_requires_credential(auth_client):
    resp = auth_client.post("/api/credentials/openrouter/test/")
    assert resp.status_code == 404


def test_test_endpoint_calls_validator(auth_client, user):
    cred = UserApiCredential(user=user, provider=Provider.OPENROUTER)
    cred.set_secret("sk-or-v1-abc")
    cred.save()
    with patch(
        "backend.credentials.api.views.validate_for_provider",
        side_effect=lambda p, k: ValidationResult(ValidationStatus.VALID, "ok"),
    ) as m:
        resp = auth_client.post("/api/credentials/openrouter/test/")
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is True
    m.assert_called_once_with("openrouter", "sk-or-v1-abc")


def test_unauthenticated_rejected():
    resp = Client().get("/api/credentials/openrouter/")
    assert resp.status_code == 401


def test_list_credentials_returns_all_providers(auth_client):
    resp = auth_client.get("/api/credentials/")
    assert resp.status_code == 200
    payload = resp.json()
    providers = {row["provider"] for row in payload}
    assert providers == {"openrouter", "huggingface"}


def test_huggingface_put_and_get(auth_client, user):
    with patch(
        "backend.credentials.api.views.validate_for_provider",
        side_effect=lambda p, k: ValidationResult(ValidationStatus.VALID, "ok"),
    ):
        resp = auth_client.put(
            "/api/credentials/huggingface/",
            data='{"api_key": "hf_secrettoken12345"}',
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "huggingface"
    assert data["configured"] is True
    assert data["key_prefix"] == "hf_secrettok"

    cred = UserApiCredential.objects.get(user=user, provider=Provider.HUGGINGFACE)
    assert cred.get_secret() == "hf_secrettoken12345"


def test_unknown_provider_404(auth_client):
    resp = auth_client.get("/api/credentials/anthropic/")
    assert resp.status_code == 404
