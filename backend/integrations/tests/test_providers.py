"""One-click cloud provider connections: presets, key checks, connect flow and the URL join for Gemini."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.test import Client

from backend.inference import upstream
from backend.inference.access import _decrypt_api_key
from backend.inference.models import LLMEndpoint
from backend.integrations.services import providers as svc
from backend.llm_models.models import LLMModel
from backend.users.tests.factories import UserFactory

API = "/api/integrations/providers/"
KEY = "sk-ant-api03-SECRETSECRETSECRET"
ANTHROPIC_MODELS = ["claude-haiku-5-5", "claude-sonnet-5", "claude-opus-5-5"]
OPENAI_MODELS = [
    "gpt-5",
    "gpt-5-mini",
    "o4-mini",
    "text-embedding-3-large",
    "whisper-1",
    "tts-1",
    "dall-e-3",
    "gpt-image-1",
    "omni-moderation-latest",
    "gpt-4o-realtime-preview",
    "gpt-3.5-turbo-instruct",
    "davinci-002",
]


def _models_response(ids, status=200):
    payload = {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}
    return upstream.UpstreamResponse(status=status, payload=payload, text=json.dumps(payload), elapsed_ms=5)


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def staff(db):
    return UserFactory(is_staff=True)


@pytest.fixture
def user(db):
    return UserFactory()


# ── upstream._join ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("base_path", "path", "expected"),
    [
        ("", "/v1/models", "/v1/models"),
        ("/v1", "/v1/chat/completions", "/v1/chat/completions"),
        ("/openai/v1", "/v1/models", "/openai/v1/models"),
        ("/inference/v1", "/v1/chat/completions", "/inference/v1/chat/completions"),
        ("/v1beta/openai", "/v1/chat/completions", "/v1beta/openai/chat/completions"),
        ("/v1beta/openai", "/v1/models", "/v1beta/openai/models"),
        ("/openai", "/api/chat", "/openai/api/chat"),
    ],
)
def test_join(base_path, path, expected):
    assert upstream._join(base_path, path) == expected


# ── Presets ─────────────────────────────────────────────────────────────


def test_presets_are_unique_and_well_formed():
    ids = [p.id for p in svc.PRESETS]
    assert len(ids) == len(set(ids))
    assert {"anthropic", "openai", "gemini", "mistral", "groq", "deepseek", "xai", "nvidia"} <= set(ids)
    for p in svc.PRESETS:
        assert p.base_url.startswith("https://")
        assert not p.base_url.endswith("/")
        assert 1 <= p.max_concurrency <= 256
        assert 30 <= p.request_timeout_s <= 7200


def test_openai_filter_keeps_only_chat_models():
    preset = svc.get_preset("openai")
    assert preset.chat_models(OPENAI_MODELS) == ["gpt-5", "gpt-5-mini", "o4-mini"]


@pytest.mark.parametrize(
    ("preset_id", "kept", "dropped"),
    [
        ("anthropic", ["claude-haiku-5-5"], []),
        (
            "gemini",
            ["models/gemini-2.5-pro", "models/gemma-3-27b-it"],
            ["models/text-embedding-004", "models/imagen-4.0-generate-001", "models/gemini-2.0-flash-live-001"],
        ),
        (
            "mistral",
            ["codestral-latest", "devstral-medium-latest"],
            ["mistral-embed", "mistral-moderation-latest", "mistral-ocr-latest"],
        ),
        (
            "groq",
            ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"],
            ["whisper-large-v3", "playai-tts", "meta-llama/llama-guard-4-12b"],
        ),
        (
            "together",
            ["Qwen/Qwen2.5-Coder-32B-Instruct"],
            ["BAAI/bge-large-en-v1.5", "black-forest-labs/FLUX.1-schnell", "Salesforce/Llama-Rank-V1-rerank"],
        ),
        (
            "nvidia",
            ["meta/llama-3.3-70b-instruct"],
            ["nvidia/nv-embedqa-e5-v5", "nvidia/llama-3.1-nemotron-70b-reward"],
        ),
    ],
)
def test_preset_filters(preset_id, kept, dropped):
    preset = svc.get_preset(preset_id)
    assert all(preset.is_chat_model(m) for m in kept)
    assert not any(preset.is_chat_model(m) for m in dropped)


def test_unknown_preset():
    with pytest.raises(svc.NotFoundError):
        svc.get_preset("nope")


# ── validate_key ────────────────────────────────────────────────────────


def test_validate_key_calls_models_through_upstream():
    preset = svc.get_preset("anthropic")
    with mock.patch.object(upstream, "call", return_value=_models_response(ANTHROPIC_MODELS)) as call:
        ok, message, ids = svc.validate_key(preset, f"  {KEY}\n")
    assert ok
    assert ids == ANTHROPIC_MODELS
    assert "3 chat models" in message
    args, kwargs = call.call_args
    assert args[:3] == ("https://api.anthropic.com/v1", "GET", "/v1/models")
    assert kwargs["api_key"] == KEY


def test_validate_key_rejected():
    preset = svc.get_preset("openai")
    resp = upstream.UpstreamResponse(status=401, payload={"error": {}}, text=f"bad key {KEY}", elapsed_ms=1)
    with mock.patch.object(upstream, "call", return_value=resp):
        ok, message, ids = svc.validate_key(preset, KEY)
    assert not ok
    assert ids == []
    assert "rejected" in message
    assert KEY not in message


def test_validate_key_error_text_is_redacted():
    preset = svc.get_preset("openai")
    resp = upstream.UpstreamResponse(status=500, payload=None, text=f"oops {KEY}", elapsed_ms=1)
    with mock.patch.object(upstream, "call", return_value=resp):
        ok, message, _ids = svc.validate_key(preset, KEY)
    assert not ok
    assert "HTTP 500" in message
    assert KEY not in message


def test_validate_key_unreachable_and_bad_payload():
    preset = svc.get_preset("groq")
    with mock.patch.object(upstream, "call", side_effect=upstream.UpstreamError("connect", "refused")):
        ok, message, _ids = svc.validate_key(preset, KEY)
    assert not ok
    assert "refused" in message
    bad = upstream.UpstreamResponse(status=200, payload={"models": []}, text="{}", elapsed_ms=1)
    with mock.patch.object(upstream, "call", return_value=bad):
        ok, _message, _ids = svc.validate_key(preset, KEY)
    assert not ok


@pytest.mark.parametrize("key", ["", "   ", "sk with space", "sk-\r\nX-Injected: 1"])
def test_validate_key_rejects_malformed_keys_without_calling(key):
    with mock.patch.object(upstream, "call") as call, pytest.raises(svc.ValidationFailed):
        svc.validate_key(svc.get_preset("openai"), key)
    call.assert_not_called()


# ── API ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestApi:
    def test_list_for_regular_user(self, user):
        resp = _client(user).get(API)
        assert resp.status_code == 200, resp.content
        data = resp.json()
        anthropic = next(p for p in data if p["id"] == "anthropic")
        assert anthropic["can_connect"] is False
        assert anthropic["connected_endpoints"] == []
        assert anthropic["base_url"] == "https://api.anthropic.com/v1"
        assert anthropic["keys_url"].startswith("https://")

    def test_requires_auth(self, db):
        assert Client().get(API).status_code == 401

    def test_non_staff_cannot_test_or_connect(self, user):
        client = _client(user)
        with mock.patch.object(upstream, "call") as call:
            assert _post(client, f"{API}anthropic/test/", {"api_key": KEY}).status_code == 403
            assert _post(client, f"{API}anthropic/connect/", {"api_key": KEY}).status_code == 403
        call.assert_not_called()
        assert not LLMEndpoint.objects.filter(owner=user).exists()

    def test_unknown_preset_is_404(self, staff):
        assert _post(_client(staff), f"{API}nope/test/", {"api_key": KEY}).status_code == 404

    def test_test_key(self, staff):
        with mock.patch.object(upstream, "call", return_value=_models_response(OPENAI_MODELS)):
            resp = _post(_client(staff), f"{API}openai/test/", {"api_key": KEY})
        assert resp.status_code == 200, resp.content
        assert resp.json() == {
            "ok": True,
            "message": "The key works: 3 chat models available.",
            "model_count": 3,
            "total_models": len(OPENAI_MODELS),
            "sample_model_ids": ["gpt-5", "gpt-5-mini", "o4-mini"],
        }
        assert KEY not in resp.content.decode()

    def test_test_key_rejected_is_200_with_ok_false(self, staff):
        resp401 = upstream.UpstreamResponse(status=401, payload=None, text="", elapsed_ms=1)
        with mock.patch.object(upstream, "call", return_value=resp401):
            resp = _post(_client(staff), f"{API}openai/test/", {"api_key": KEY})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert resp.json()["model_count"] == 0

    def test_connect_creates_direct_endpoint_and_hides_non_chat_models(self, staff):
        client = _client(staff)
        with mock.patch.object(upstream, "call", return_value=_models_response(OPENAI_MODELS)) as call:
            resp = _post(client, f"{API}openai/connect/", {"api_key": KEY})
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert KEY not in resp.content.decode()
        assert data["slug"] == "openai"
        assert data["name"] == "OpenAI"
        assert data["model_count"] == 3
        assert data["hidden_count"] == len(OPENAI_MODELS) - 3
        assert data["total_models"] == len(OPENAI_MODELS)
        assert data["inventory_error"] == ""
        # validation + inventory both went through upstream.call with the key
        assert call.call_count == 2
        assert all(c.kwargs["api_key"] == KEY for c in call.call_args_list)

        endpoint = LLMEndpoint.objects.get(pk=data["endpoint_id"])
        assert endpoint.kind == "openai_compatible"
        assert endpoint.connection == "direct"
        assert endpoint.api_flavor == "openai"
        assert endpoint.assume_tools is True
        assert endpoint.visibility == "private"
        assert endpoint.owner == staff
        assert endpoint.base_url == "https://api.openai.com/v1"
        assert endpoint.encrypted_api_key
        assert KEY not in endpoint.encrypted_api_key
        assert endpoint.api_key_prefix == KEY[:8]
        assert _decrypt_api_key(endpoint) == KEY

        visible = set(
            LLMModel.objects.filter(endpoint=endpoint, is_hidden=False).values_list("served_model_name", flat=True),
        )
        assert visible == {"gpt-5", "gpt-5-mini", "o4-mini"}
        assert LLMModel.objects.get(endpoint=endpoint, served_model_name="gpt-5").supports_function_calling

        listed = next(p for p in client.get(API).json() if p["id"] == "openai")
        assert listed["can_connect"] is True
        assert listed["connected_endpoints"] == [
            {
                "id": endpoint.pk,
                "slug": "openai",
                "name": "OpenAI",
                "enabled": True,
                "model_count": 3,
                "last_error": "",
            },
        ]
        other = next(p for p in client.get(API).json() if p["id"] == "anthropic")
        assert other["connected_endpoints"] == []

    def test_second_connection_gets_a_unique_slug_and_custom_name(self, staff):
        client = _client(staff)
        with mock.patch.object(upstream, "call", return_value=_models_response(ANTHROPIC_MODELS)):
            first = _post(client, f"{API}anthropic/connect/", {"api_key": KEY})
            second = _post(client, f"{API}anthropic/connect/", {"api_key": KEY, "name": "Claude (team)"})
        assert first.json()["slug"] == "anthropic"
        assert second.status_code == 201
        assert second.json()["slug"] == "anthropic-2"
        assert second.json()["name"] == "Claude (team)"
        listed = next(p for p in client.get(API).json() if p["id"] == "anthropic")
        assert [e["slug"] for e in listed["connected_endpoints"]] == ["anthropic", "anthropic-2"]

    def test_connected_endpoints_are_scoped_to_the_user(self, staff):
        other_staff = UserFactory(is_staff=True)
        with mock.patch.object(upstream, "call", return_value=_models_response(ANTHROPIC_MODELS)):
            _post(_client(other_staff), f"{API}anthropic/connect/", {"api_key": KEY})
        listed = next(p for p in _client(staff).get(API).json() if p["id"] == "anthropic")
        assert listed["connected_endpoints"] == []

    def test_connect_with_invalid_key_is_400_and_creates_nothing(self, staff):
        resp401 = upstream.UpstreamResponse(status=401, payload=None, text="", elapsed_ms=1)
        with mock.patch.object(upstream, "call", return_value=resp401):
            resp = _post(_client(staff), f"{API}anthropic/connect/", {"api_key": KEY})
        assert resp.status_code == 400
        assert "rejected" in resp.json()["detail"]
        assert not LLMEndpoint.objects.filter(owner=staff).exists()

    def test_connect_with_malformed_key_is_400(self, staff):
        with mock.patch.object(upstream, "call") as call:
            resp = _post(_client(staff), f"{API}anthropic/connect/", {"api_key": "two words"})
        assert resp.status_code == 400
        call.assert_not_called()

    def test_inventory_failure_still_connects_and_reports_it(self, staff):
        ok = _models_response(ANTHROPIC_MODELS)
        fail = upstream.UpstreamError("timeout", "no answer within 30s")
        with mock.patch.object(upstream, "call", side_effect=[ok, fail]):
            resp = _post(_client(staff), f"{API}anthropic/connect/", {"api_key": KEY})
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["model_count"] == 0
        assert "no answer" in data["inventory_error"]
