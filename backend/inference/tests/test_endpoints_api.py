"""/api/inference/ endpoints API (spec §F.3): permissions, redaction, IDOR scoping, tokens, install."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from backend.generation.tests.factories import GenerationJobFactory
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.inference.tests.conftest import UNUSED_HASH
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.llm_models.models import LLMModel
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
API = "/api/inference/endpoints/"
OPERATIONAL = ("api_key_prefix", "base_url", "last_error")


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(client, url, payload=None):
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json")


def _patch(client, url, payload):
    return client.patch(url, data=json.dumps(payload), content_type="application/json")


def _session(endpoint):
    token = RelayToken.objects.create(endpoint=endpoint, prefix=f"llr_s{endpoint.pk:07d}", secret_hash=UNUSED_HASH)
    return RelaySession.objects.create(
        endpoint=endpoint,
        token=token,
        relay_version="1.0.0",
        upstream_kind="ollama",
        hostname="secret-host",
        client_ip="203.0.113.9",
        last_seen_at=timezone.now(),
    )


class TestCreate:
    def test_relay_endpoint_returns_token_once(self, owner):
        client = _client(owner)
        resp = _post(client, API, {"name": "GPU Box 1", "kind": "ollama", "max_concurrency": 2})
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["endpoint"]["slug"] == "gpu-box-1"
        assert data["endpoint"]["status"] == "offline"
        assert data["token"]["secret_once"].startswith(data["token"]["prefix"] + ".")
        tokens = client.get(f"{API}{data['endpoint']['id']}/tokens/").json()
        assert "secret_once" not in json.dumps(tokens)
        assert "secret_hash" not in json.dumps(tokens)

    def test_non_staff_cannot_create_direct_or_shared(self, owner):
        client = _client(owner)
        direct = {"name": "d", "kind": "ollama", "connection": "direct", "base_url": "http://100.64.1.2:11434"}
        assert _post(client, API, direct).status_code == 403
        shared = {"name": "s", "kind": "ollama", "visibility": "shared"}
        assert _post(client, API, shared).status_code == 400
        assert _post(client, API, {"name": "o", "kind": "openrouter"}).status_code == 422
        assert _post(client, API, {"name": "r", "kind": "ollama", "slug": "openrouter"}).status_code == 400

    def test_staff_direct_endpoint_validates_and_hides_key(self, staff, settings):
        settings.INFERENCE_DIRECT_ALLOWED_TARGETS = ["100.64.0.0/10:11434"]
        client = _client(staff)
        with mock.patch("backend.inference.security.resolve", return_value=("100.64.1.2",)):
            resp = _post(
                client,
                API,
                {
                    "name": "tail",
                    "kind": "openai_compatible",
                    "connection": "direct",
                    "base_url": "http://gpu.tail:11434",
                    "api_key": "sk-supersecret",
                },
            )
            bad = _post(
                client,
                API,
                {"name": "bad", "kind": "ollama", "connection": "direct", "base_url": "http://gpu.tail:22"},
            )
        assert resp.status_code == 201, resp.content
        assert bad.status_code == 400
        body = resp.content.decode()
        assert "sk-supersecret" not in body
        assert resp.json()["endpoint"]["api_key_prefix"] == "sk-super"
        assert resp.json()["endpoint"]["api_flavor"] == "openai"
        assert resp.json()["token"] is None


class TestVisibilityAndRedaction:
    def test_private_endpoints_are_404_for_others(self, owner, endpoint):
        stranger = _client(UserFactory())
        assert stranger.get(f"{API}{endpoint.pk}/").status_code == 404
        assert endpoint.pk not in [e["id"] for e in stranger.get(API).json()]

    def test_shared_endpoint_is_redacted_for_non_owners(self, owner, staff):
        endpoint = make_endpoint(owner, visibility="shared", last_error="boom at 10.0.0.1")
        _session(endpoint)
        stranger = _client(UserFactory())
        data = stranger.get(f"{API}{endpoint.pk}/").json()
        for key in OPERATIONAL:
            assert data[key] is None
        assert data["live"]["sessions"] is None
        assert data["sessions_recent"] is None
        assert data["live"]["session_count"] == 1
        assert "secret-host" not in json.dumps(data)
        assert "203.0.113.9" not in json.dumps(data)
        assert stranger.get(f"{API}{endpoint.pk}/sessions/").status_code == 403
        mine = _client(owner).get(f"{API}{endpoint.pk}/").json()
        assert mine["live"]["sessions"][0]["hostname"] == "secret-host"
        assert mine["last_error"] == "boom at 10.0.0.1"
        assert _client(staff).get(f"{API}{endpoint.pk}/").json()["live"]["sessions"][0]["client_ip"] == "203.0.113.9"

    def test_owner_still_sees_a_disabled_endpoint(self, owner, endpoint):
        client = _client(owner)
        assert _patch(client, f"{API}{endpoint.pk}/", {"enabled": False}).json()["status"] == "disabled"
        assert client.get(f"{API}{endpoint.pk}/").status_code == 200
        assert _patch(client, f"{API}{endpoint.pk}/", {"enabled": True}).json()["enabled"] is True

    def test_system_row(self, owner, staff):
        client = _client(owner)
        system = next(e for e in client.get(API).json() if e["is_system"])
        assert system["status"] == "online"
        assert _patch(client, f"{API}{system['id']}/", {"max_concurrency": 10}).status_code == 403
        staff_client = _client(staff)
        assert _patch(staff_client, f"{API}{system['id']}/", {"name": "x"}).status_code == 400
        assert _patch(staff_client, f"{API}{system['id']}/", {"max_concurrency": 10}).json()["max_concurrency"] == 10


class TestScoping:
    def test_nested_objects_are_scoped_to_the_endpoint(self, owner):
        mine = make_endpoint(owner)
        other = make_endpoint(UserFactory())
        other_model = make_local_model(other)
        other_token = RelayToken.objects.create(endpoint=other, prefix="llr_other001", secret_hash=UNUSED_HASH)
        catalog = LLMModelFactory(model_id="vendor/catalog")
        client = _client(owner)
        assert _patch(client, f"{API}{mine.pk}/models/{other_model.pk}/", {"is_hidden": True}).status_code == 404
        assert _patch(client, f"{API}{mine.pk}/models/{catalog.pk}/", {"is_hidden": True}).status_code == 404
        assert _post(client, f"{API}{mine.pk}/tokens/{other_token.pk}/revoke/").status_code == 404
        assert _post(client, f"{API}{mine.pk}/unload/", {"model_pk": other_model.pk}).status_code == 404
        probe = LLMRequest.objects.create(
            endpoint=other,
            source="probe",
            path="/api/version",
            method="GET",
            deadline_at=timezone.now(),
        )
        assert client.get(f"{API}{mine.pk}/test/{probe.pk}/").status_code == 404
        assert client.get(f"{API}{other.pk}/models/").status_code == 404

    def test_slug_is_immutable(self, owner, endpoint):
        resp = _patch(_client(owner), f"{API}{endpoint.pk}/", {"slug": "renamed", "name": "New"})
        assert resp.status_code == 200
        endpoint.refresh_from_db()
        assert (endpoint.slug, endpoint.name) != ("renamed", "New")
        assert endpoint.slug != "renamed"


class TestModelsTokensProbes:
    def test_models_patch_and_hidden(self, owner, endpoint, local_model):
        client = _client(owner)
        resp = _patch(
            client,
            f"{API}{endpoint.pk}/models/{local_model.pk}/",
            {"num_ctx_override": 4096, "is_hidden": True},
        )
        assert resp.json()["num_ctx_effective"] == 4096
        assert client.get(f"{API}{endpoint.pk}/models/").json() == []
        assert len(client.get(f"{API}{endpoint.pk}/models/?include_hidden=true").json()) == 1

    def test_rotate_and_revoke(self, owner, endpoint):
        client = _client(owner)
        created = _post(client, f"{API}{endpoint.pk}/tokens/", {"label": "box"}).json()
        rotated = _post(client, f"{API}{endpoint.pk}/tokens/{created['id']}/rotate/", {"grace_minutes": 30}).json()
        assert rotated["new"]["secret_once"]
        assert rotated["old_expires_at"]
        revoked = _post(client, f"{API}{endpoint.pk}/tokens/{rotated['new']['id']}/revoke/").json()
        assert revoked["sessions_ended"] == 0
        assert RelayToken.objects.get(pk=rotated["new"]["id"]).revoked_at is not None
        assert (
            _post(client, f"{API}{endpoint.pk}/tokens/{created['id']}/rotate/", {"grace_minutes": 99999}).status_code
            == 422
        )

    def test_probe_request_lifecycle(self, owner, endpoint, local_model):
        client = _client(owner)
        rid = _post(client, f"{API}{endpoint.pk}/test/", {"model_id": local_model.pk}).json()["request_id"]
        row = LLMRequest.objects.get(pk=rid)
        assert (row.source, row.path, row.body["options"]["num_predict"]) == ("probe", "/api/chat", 1)
        assert client.get(f"{API}{endpoint.pk}/test/{rid}/").json()["status"] == "queued"
        LLMRequest.objects.filter(pk=rid).update(
            status="done",
            response_status=200,
            response={"message": {"content": "ok"}},
            completed_at=timezone.now(),
        )
        result = client.get(f"{API}{endpoint.pk}/test/{rid}/").json()
        assert (result["status"], result["sample"]) == ("done", "ok")
        version = _post(client, f"{API}{endpoint.pk}/test/", {}).json()["request_id"]
        assert LLMRequest.objects.get(pk=version).path == "/api/version"
        # A malformed id is a client error, not a crash.
        assert client.get(f"{API}{endpoint.pk}/test/not-a-uuid/").status_code in (404, 422)

    def test_refresh_sets_resync(self, owner, endpoint):
        assert _post(_client(owner), f"{API}{endpoint.pk}/refresh/").status_code == 202
        endpoint.refresh_from_db()
        assert endpoint.inventory_resync_requested

    def test_metrics_shape(self, owner, endpoint):
        now = timezone.now()
        LLMRequest.objects.create(
            endpoint=endpoint,
            source="scaffolding",
            path="/api/chat",
            status="done",
            leased_at=now,
            completed_at=now,
            prompt_tokens=10,
            completion_tokens=5,
            deadline_at=now,
        )
        points = _client(owner).get(f"{API}{endpoint.pk}/metrics/?minutes=5").json()["points"]
        assert len(points) == 5
        assert sum(p["completed"] for p in points) == 1
        assert sum(p["tokens"] for p in points) == 15


class TestInstallAndDelete:
    def test_install_never_contains_the_token(self, owner, endpoint):
        RelayToken.objects.create(endpoint=endpoint, prefix="llr_abcdefgh", secret_hash=UNUSED_HASH)
        data = _client(owner).get(f"{API}{endpoint.pk}/install/").json()
        assert len(data["script_sha256"]) == 64
        assert data["relay_version"] == "1.0.0"
        assert f'echo "{data["script_sha256"]}  llmlab_relay.py" | sha256sum -c -' in data["commands"]["python"]
        assert "install -m600 /dev/stdin" in data["commands"]["python"]
        assert "llr_" not in json.dumps(data)
        assert data["ollama_env"]["OLLAMA_NUM_PARALLEL"] == str(endpoint.max_concurrency)
        # The relay (uid 65534) must own its state volume to persist its relay id.
        assert "chown 65534 /state /state/token" in data["commands"]["docker"]
        # The systemd unit is downloadable and verified like the script.
        unit_sha = hashlib.sha256((Path(settings.BASE_DIR) / "relay" / "llmlab-relay.service").read_bytes())
        assert "/api/relay/v1/llmlab-relay.service" in data["commands"]["systemd"]
        assert f'echo "{unit_sha.hexdigest()}  llmlab-relay.service" | sha256sum -c -' in data["commands"]["systemd"]

    def test_delete_in_use_is_409(self, owner, endpoint, local_model):
        GenerationJobFactory(model=local_model, created_by=owner)
        resp = _client(owner).delete(f"{API}{endpoint.pk}/")
        assert resp.status_code == 409

    def test_delete_unused(self, owner, endpoint, local_model):
        resp = _client(owner).delete(f"{API}{endpoint.pk}/")
        assert resp.json() == {"success": True}
        assert not LLMEndpoint.objects.filter(pk=endpoint.pk).exists()
        assert not LLMModel.objects.filter(pk=local_model.pk).exists()


class TestModelsCatalogVisibility:
    def test_invisible_local_models_are_hidden_from_models_api(self, owner, endpoint, local_model):
        catalog = LLMModelFactory(model_id="vendor/public", canonical_slug="vendor_public")
        stranger = _client(UserFactory())
        ids = [m["id"] for m in stranger.get("/api/models/?per_page=200").json()["items"]]
        assert catalog.pk in ids
        assert local_model.pk not in ids
        assert stranger.get(f"/api/models/detail/{local_model.canonical_slug}/").status_code == 404
        mine = _client(owner).get(f"/api/models/?endpoint={endpoint.slug}").json()["items"]
        assert [m["id"] for m in mine] == [local_model.pk]
        assert mine[0]["is_local"] is True
        assert mine[0]["endpoint"] == {
            "id": endpoint.pk,
            "slug": endpoint.slug,
            "name": endpoint.name,
            "kind": "ollama",
            "online": False,
        }
        openrouter = _client(owner).get("/api/models/?endpoint=openrouter").json()["items"]
        assert local_model.pk not in [m["id"] for m in openrouter]

    def test_unavailable_models_need_the_flag(self, owner, endpoint, local_model):
        LLMModel.objects.filter(pk=local_model.pk).update(is_available=False)
        client = _client(owner)
        assert client.get("/api/models/?endpoint=local").json()["total"] == 0
        assert client.get("/api/models/?endpoint=local&include_unavailable=true").json()["total"] == 1


class TestAbuseCaps:
    def test_non_staff_endpoint_cap(self, owner, settings):
        settings.RELAY_MAX_ENDPOINTS_PER_USER = 2
        client = _client(owner)
        for i in range(2):
            assert _post(client, API, {"name": f"box {i}", "kind": "ollama"}).status_code == 201
        assert _post(client, API, {"name": "box 3", "kind": "ollama"}).status_code == 400

    def test_staff_is_exempt_from_the_endpoint_cap(self, staff, settings):
        settings.RELAY_MAX_ENDPOINTS_PER_USER = 1
        client = _client(staff)
        for i in range(2):
            assert _post(client, API, {"name": f"box {i}", "kind": "ollama"}).status_code == 201

    def test_non_staff_live_token_cap(self, owner, endpoint, settings):
        settings.RELAY_MAX_TOKENS_PER_ENDPOINT = 2
        client = _client(owner)
        live = RelayToken.objects.filter(endpoint=endpoint, revoked_at__isnull=True).count()
        created = []
        for i in range(max(0, 2 - live)):
            resp = _post(client, f"{API}{endpoint.pk}/tokens/", {"label": f"t{i}"})
            assert resp.status_code == 201
            created.append(resp.json()["id"])
        assert _post(client, f"{API}{endpoint.pk}/tokens/", {"label": "over"}).status_code == 400
        # Revoked tokens do not count against the cap.
        token_id = created[0] if created else RelayToken.objects.filter(endpoint=endpoint).first().pk
        assert _post(client, f"{API}{endpoint.pk}/tokens/{token_id}/revoke/").status_code == 200
        assert _post(client, f"{API}{endpoint.pk}/tokens/", {"label": "again"}).status_code == 201


class TestServerUrl:
    def test_falls_back_to_the_public_frontend_origin(self, owner, endpoint, settings):
        settings.LLMLAB_PUBLIC_URL = ""
        settings.FRONTEND_PUBLIC_ORIGIN = "https://lab.example.org"
        data = _client(owner).get(f"{API}{endpoint.pk}/install/").json()
        assert data["server_url"] == "https://lab.example.org"
        assert "testserver" not in json.dumps(data["commands"])

    def test_explicit_public_url_wins(self, owner, endpoint, settings):
        settings.LLMLAB_PUBLIC_URL = "https://relay.example.org/"
        settings.FRONTEND_PUBLIC_ORIGIN = "https://lab.example.org"
        data = _client(owner).get(f"{API}{endpoint.pk}/install/").json()
        assert data["server_url"] == "https://relay.example.org"
