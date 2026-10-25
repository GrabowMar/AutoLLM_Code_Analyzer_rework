"""Inventory upserts (spec §D.7)."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.inference.inventory import InventoryError
from backend.inference.inventory import apply_inventory
from backend.inference.inventory import local_model_id
from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession
from backend.inference.models import RelayToken
from backend.inference.tests.conftest import UNUSED_HASH
from backend.inference.tests.conftest import make_endpoint
from backend.llm_models.models import LLMModel
from backend.llm_models.services.sync import upsert_openrouter_models
from backend.llm_models.tests.factories import LLMModelFactory

pytestmark = pytest.mark.django_db
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def _payload(*models, show=None, loaded=None):
    return {
        "version": "0.12.3",
        "models": [
            {"name": n, "digest": d, "size": 1, "family": "qwen2", "parameter_size": "7B", "quantization": "Q4_K_M"}
            for n, d in models
        ],
        "show": show or {},
        "loaded": loaded or [],
    }


def test_upsert_identity_and_need_show(endpoint):
    result = apply_inventory(endpoint, None, _payload(("Qwen2.5-Coder:7B", D1), ("llama3:8b", D2)))
    assert result.accepted_models == 2
    assert sorted(result.need_show) == ["Qwen2.5-Coder:7B", "llama3:8b"]
    row = LLMModel.objects.get(endpoint=endpoint, served_model_name="Qwen2.5-Coder:7B")
    assert row.model_id == f"local-{endpoint.slug}/qwen2.5-coder-7b"
    assert (row.provider, row.is_free, row.input_price_per_token, row.quantization) == (
        endpoint.slug,
        True,
        0.0,
        "Q4_K_M",
    )
    assert row.supports_function_calling is False  # unknown until /api/show
    show = {"Qwen2.5-Coder:7B": {"context_length": 32768, "capabilities": ["completion", "tools", "bogus"]}}
    result = apply_inventory(endpoint, None, _payload(("Qwen2.5-Coder:7B", D1), ("llama3:8b", D2), show=show))
    assert result.need_show == ["llama3:8b"]
    row.refresh_from_db()
    assert row.supports_function_calling is True
    assert row.context_window == 32768
    assert row.metadata["ollama"]["capabilities"] == ["completion", "tools"]
    endpoint.refresh_from_db()
    assert endpoint.server_version == "0.12.3"
    assert endpoint.last_inventory_at is not None


def test_sanitised_id_collision_gets_digest_suffix(endpoint):
    apply_inventory(endpoint, None, _payload(("a:b", D1), ("a-b", D2)))
    ids = set(LLMModel.objects.filter(endpoint=endpoint).values_list("model_id", flat=True))
    assert ids == {local_model_id(endpoint, "a:b"), f"{local_model_id(endpoint, 'a-b')}-{D2[7:13]}"}


def test_missing_models_become_unavailable(endpoint):
    apply_inventory(endpoint, None, _payload(("a:1", D1), ("b:1", D2)))
    result = apply_inventory(endpoint, None, _payload(("a:1", D1)))
    assert result.removed == 1
    assert not LLMModel.objects.get(endpoint=endpoint, served_model_name="b:1").is_available
    apply_inventory(endpoint, None, _payload(("a:1", D1), ("b:1", D2)))
    assert LLMModel.objects.get(endpoint=endpoint, served_model_name="b:1").is_available


def test_digest_change_records_history_and_pauses(endpoint, django_capture_on_commit_callbacks):
    apply_inventory(endpoint, None, _payload(("a:1", D1)))
    with (
        mock.patch("backend.generation.services.experiment_automation.pause_for_digest_drift") as pause,
        django_capture_on_commit_callbacks(execute=True),
    ):
        result = apply_inventory(endpoint, None, _payload(("a:1", D2)))
    row = LLMModel.objects.get(endpoint=endpoint, served_model_name="a:1")
    assert result.digest_changed == [row.pk]
    assert row.digest == D2
    assert row.metadata["digest_history"][0]["digest"] == D1
    pause.assert_called_once()
    assert pause.call_args.args[0].pk == row.pk


def test_standby_session_only_updates_its_models(endpoint):
    token = RelayToken.objects.create(endpoint=endpoint, prefix="llr_standby1", secret_hash=UNUSED_HASH)
    from django.utils import timezone

    session = RelaySession.objects.create(
        endpoint=endpoint,
        token=token,
        relay_version="1.0.0",
        upstream_kind="ollama",
        role="standby",
        last_seen_at=timezone.now(),
    )
    apply_inventory(endpoint, session, _payload(("a:1", D1)))
    assert not LLMModel.objects.filter(endpoint=endpoint).exists()
    session.refresh_from_db()
    assert session.models == [{"name": "a:1", "digest": D1}]


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"models": [{"name": "x"}] * 3}, 413),
        ({"models": [{"name": "bad name"}]}, 422),
        ({"models": [{"name": "a:1", "digest": "x" * 81}]}, 422),
        ({"models": [], "show": {"a:1": {"capabilities": ["tools"] * 17}}}, 422),
        ({"models": [], "loaded": [{"name": "a:1"}] * 65}, 413),
        ({"models": [{"name": "a:1", "size": -1}]}, 422),
    ],
)
def test_caps_reject_without_applying(endpoint, settings, payload, status):
    settings.RELAY_INVENTORY_MAX_MODELS = 2
    with pytest.raises(InventoryError) as exc:
        apply_inventory(endpoint, None, payload)
    assert exc.value.status == status
    assert not LLMModel.objects.filter(endpoint=endpoint).exists()


def test_openai_compatible_uses_assume_tools(owner):
    endpoint = make_endpoint(owner, kind="openai_compatible", api_flavor="openai", assume_tools=True)
    result = apply_inventory(endpoint, None, {"models": [{"name": "Qwen/Qwen2.5-7B", "context_length": 16384}]})
    assert result.need_show == []
    row = LLMModel.objects.get(endpoint=endpoint)
    assert (row.supports_function_calling, row.context_window) == (True, 16384)


def test_openrouter_sync_never_touches_local_rows(endpoint):
    apply_inventory(endpoint, None, _payload(("a:1", D1)))
    local = LLMModel.objects.get(endpoint=endpoint)
    catalog = LLMModelFactory(model_id="vendor/x", canonical_slug="vendor_x")
    upserted = upsert_openrouter_models(
        [
            {"id": local.model_id, "name": "hijack", "canonical_slug": "zzz"},
            {"id": "other/y", "name": "Y", "canonical_slug": local.canonical_slug},
            {"id": "vendor/x", "name": "Renamed X"},
        ],
    )
    assert upserted == 1
    local.refresh_from_db()
    assert (local.model_name, local.endpoint_id) == ("a:1", endpoint.pk)
    catalog.refresh_from_db()
    assert catalog.model_name == "Renamed X"
    assert LLMEndpoint.objects.filter(pk=endpoint.pk).exists()
