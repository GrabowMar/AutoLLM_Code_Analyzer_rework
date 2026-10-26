"""AI review on the platform's inference path: local models (relay / direct endpoints) and access resolution.

Ports the v1 ``ai_runner`` local-routing tests (inline outside a
transaction, own connection inside one, bounded inline wait) to the v2
reviewer client, and covers freezing, capabilities and a whole single-shot
replicate on a local model. ``inference.chat.chat`` is mocked at its public
boundary; the access rules are the real ``inference.access`` ones.
"""

from __future__ import annotations

import threading
from unittest import mock

import pytest
from django.db import connection
from django.db import transaction
from django.test import override_settings

from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.reviewer import client
from backend.analysis.engine.reviewer import config
from backend.analysis.engine.reviewer import single_shot
from backend.analysis.tests.reviewer_support import SQLI
from backend.analysis.tests.reviewer_support import answer
from backend.analysis.tests.reviewer_support import app_sample
from backend.analysis.tests.reviewer_support import patch_normalize
from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.inference.access import LLMAccessError
from backend.inference.access import resolve_access
from backend.inference.chat import ChatResult
from backend.inference.ollama_native import from_native_response
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)
MESSAGES = [{"role": "user", "content": "review"}]
CHAT = "backend.inference.chat.chat"
BOTH = ["backend/app.py", "frontend/src/App.jsx"]


@pytest.fixture
def owner():
    return UserFactory()


def _local_model(owner, *, thinking=False, flavor="native", kind="ollama", **kw):
    endpoint = make_endpoint(owner, api_flavor=flavor, kind=kind)
    caps = ["completion", "tools"] + (["thinking"] if thinking else [])
    return make_local_model(
        endpoint,
        metadata={"ollama": {"capabilities": caps, "show_digest": "sha256:" + "ab" * 32}},
        **kw,
    )


def _local_access(owner):
    return resolve_access(owner, _local_model(owner))


def _native(content, *, done_reason="stop", prompt_tokens=900, eval_count=120, rid="0f" * 16):
    """A translated Ollama answer, as inference.chat returns it."""
    payload = {
        "message": {"role": "assistant", "content": content},
        "done_reason": done_reason,
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_count,
    }
    return ChatResult(
        response=from_native_response(payload, request_id=rid, served_model="qwen2.5-coder:7b@abab", provider="gpu"),
        served={"endpoint": "gpu", "request_id": rid},
    )


# --------------------------------------------------------------------------
# Transaction rules (ported from inference/tests/test_ai_runner_local.py)
# --------------------------------------------------------------------------


def test_outside_a_transaction_the_call_runs_inline(owner):
    access = _local_access(owner)
    seen = {}

    def fake_chat(*_args, **kwargs):
        seen["thread"] = threading.get_ident()
        seen["source"] = kwargs["source"]
        return ChatResult(response={"choices": []})

    with mock.patch(CHAT, side_effect=fake_chat):
        client._dispatch_local(access, MESSAGES, {"max_tokens": 100}, None)
    assert seen == {"thread": threading.get_ident(), "source": "analysis"}


def test_inside_a_request_transaction_the_call_runs_on_its_own_connection(owner):
    access = _local_access(owner)
    seen = {}

    def fake_chat(*_args, **kwargs):
        # The queued request must be committed for a relay or pump to claim it.
        seen["atomic"] = connection.in_atomic_block
        seen["thread"] = threading.get_ident()
        seen["bounded"] = kwargs.get("cancel_check") is not None
        return ChatResult(response={"choices": [{"message": {"content": "[]"}}]})

    with transaction.atomic(), mock.patch(CHAT, side_effect=fake_chat):
        result = client._dispatch_local(access, MESSAGES, {"max_tokens": 100}, None)
    assert result.response["choices"][0]["message"]["content"] == "[]"
    assert seen["atomic"] is False
    assert seen["thread"] != threading.get_ident()
    assert seen["bounded"] is True


def test_inline_call_past_its_bound_is_a_timeout(owner):
    access = _local_access(owner)

    def fake_chat(*_args, **kwargs):
        assert kwargs["cancel_check"]()  # the bound has already passed
        raise LLMAccessError(code="cancelled", message="cancelled")

    with (
        transaction.atomic(),
        mock.patch.object(client, "INLINE_LOCAL_TIMEOUT_S", -1),
        mock.patch(CHAT, side_effect=fake_chat),
        pytest.raises(LLMAccessError) as excinfo,
    ):
        client._dispatch_local(access, MESSAGES, {"max_tokens": 100}, None)
    assert excinfo.value.code == "timeout"


def test_inside_a_transaction_the_callers_cancel_still_cancels(owner):
    access = _local_access(owner)

    def fake_chat(*_args, **kwargs):
        assert kwargs["cancel_check"]()
        raise LLMAccessError(code="cancelled", message="cancelled")

    with (
        transaction.atomic(),
        mock.patch(CHAT, side_effect=fake_chat),
        pytest.raises(LLMAccessError) as excinfo,
    ):
        client._dispatch_local(access, MESSAGES, {}, lambda: True)
    assert excinfo.value.code == "cancelled"  # not a timeout: the bound had not passed


def test_a_worker_thread_closes_its_connection_after_the_call(owner):
    access = _local_access(owner)
    closed = []

    def worker():
        with (
            mock.patch(CHAT, return_value=ChatResult(response={})),
            mock.patch("backend.inference.db.close_db", side_effect=lambda: closed.append(True)),
        ):
            client._dispatch_local(access, MESSAGES, {}, None)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert closed == [True]


# --------------------------------------------------------------------------
# chat() with a local Access
# --------------------------------------------------------------------------


def test_chat_with_a_local_access_goes_through_the_inference_queue(owner):
    access = _local_access(owner)
    sent = {}

    def fake_chat(acc, **kwargs):
        sent.update(kwargs, access=acc)
        return _native('{"findings": []}')

    with mock.patch(CHAT, side_effect=fake_chat):
        call = client.chat(
            access,
            model="ignored-for-local",
            messages=MESSAGES,
            params={"temperature": 0.2, "max_tokens": 4000, "seed": 7, "reasoning": None},
            response_format={"type": "json_object"},
            provider={"require_parameters": True},
        )
    assert call.ok, call.error
    assert call.content == '{"findings": []}'
    assert sent["access"] is access
    assert sent["source"] == "analysis"
    # Params pass as given (None dropped); provider routing is OpenRouter-only and never sent.
    assert sent["params"] == {
        "temperature": 0.2,
        "max_tokens": 4000,
        "seed": 7,
        "response_format": {"type": "json_object"},
    }
    # Recorded request: the exact upstream body (Ollama native, JSON mode, num_ctx and num_predict).
    assert call.request["endpoint"] == access.endpoint.slug
    assert call.request["path"] == "/api/chat"
    body = call.request["body"]
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["format"] == "json"
    assert body["options"]["num_predict"] == 4000
    assert body["options"]["seed"] == 7
    assert "provider" not in body
    # Local compute is not billed: cost is a known 0.0, tokens are the server's counts.
    assert call.usage == {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020, "cost": 0.0}
    assert call.provider == "gpu"
    assert call.finish_reason == "stop"
    assert len(call.attempts) == 1
    assert call.attempts[0]["served"]["request_id"]


def test_chat_local_length_stop_is_reported_not_retried(owner):
    access = _local_access(owner)
    with mock.patch(CHAT, return_value=_native("", done_reason="length")) as fake:
        call = client.chat(access, model="", messages=MESSAGES, params={}, max_retries=3, sleep=lambda s: None)
    assert not call.ok
    assert call.finish_reason == "length"
    assert fake.call_count == 1
    # Every attempt's usage is kept even when it failed.
    assert call.usage["cost"] == 0.0


@pytest.mark.parametrize(
    ("code", "status", "kind"),
    [
        ("endpoint_offline", None, "unavailable"),
        ("unavailable", None, "unavailable"),
        ("lease_expired", None, "unavailable"),
        ("timeout", None, "timeout"),
        ("cancelled", None, "cancelled"),
        ("model_unavailable", None, "not_found"),
        ("endpoint_forbidden", 403, "auth"),
        ("upstream_oom", 500, "bad_request"),
        ("too_large", None, "bad_request"),
        ("upstream_error", 400, "bad_request"),
        ("upstream_error", None, "provider_error"),
    ],
)
def test_local_access_errors_are_classified_and_not_retried(owner, code, status, kind):
    access = _local_access(owner)
    error = LLMAccessError(code=code, message=f"{code} happened", status_code=status)
    with mock.patch(CHAT, side_effect=error) as fake:
        call = client.chat(access, model="", messages=MESSAGES, params={}, max_retries=3, sleep=lambda s: None)
    assert not call.ok
    assert call.error_kind == kind
    assert code in call.error
    # The queue already retried deliveries: one logical call, no paid-for repeats.
    assert fake.call_count == 1
    assert call.usage == {}


def test_local_upstream_5xx_is_retried_then_succeeds(owner):
    access = _local_access(owner)
    answers = [LLMAccessError(code="upstream_error", message="boom", status_code=503), _native('{"findings": []}')]
    delays = []
    with mock.patch(CHAT, side_effect=answers):
        call = client.chat(access, model="", messages=MESSAGES, params={}, max_retries=2, sleep=delays.append)
    assert call.ok
    assert call.retries == 1
    assert [a["error_kind"] for a in call.attempts] == ["server", ""]
    assert len(delays) == 1


def test_local_retries_stop_once_the_wait_is_abandoned(owner):
    access = _local_access(owner)
    with mock.patch(CHAT, side_effect=LLMAccessError(code="upstream_error", message="x", status_code=502)) as fake:
        call = client.chat(
            access,
            model="",
            messages=MESSAGES,
            params={},
            max_retries=3,
            sleep=lambda s: None,
            cancel_check=lambda: True,
        )
    assert not call.ok
    assert fake.call_count == 1


def test_an_openrouter_access_uses_the_http_client_with_its_key(owner):
    model = LLMModelFactory(model_id="vendor/or-model")
    with override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True):
        access = resolve_access(owner, model)
    seen = {}

    class Session:
        def post(self, url, json=None, headers=None, timeout=None):
            seen.update(url=url, auth=headers["Authorization"], model=json["model"])
            msg = "offline"
            raise client.requests.exceptions.ConnectionError(msg)

    with mock.patch(CHAT) as inference_chat:
        call = client.chat(access, model="vendor/or-model", messages=MESSAGES, session=Session(), max_retries=0)
    inference_chat.assert_not_called()
    assert seen == {"url": client.OPENROUTER_COMPLETIONS_URL, "auth": "Bearer global-key", "model": "vendor/or-model"}
    assert call.error_kind == "network"


# --------------------------------------------------------------------------
# resolve_review_access
# --------------------------------------------------------------------------


def _frozen_model(model, **local):
    section = {"slug": model.model_id, "id": model.pk}
    if model.is_local:
        section["local"] = {"endpoint": model.endpoint.slug, "digest": model.digest, **local}
    return section


def test_resolve_a_local_model_for_its_owner(owner):
    model = _local_model(owner)
    access = client.resolve_review_access(owner, _frozen_model(model))
    assert not access.is_openrouter
    assert access.served_model_name == "qwen2.5-coder:7b"
    assert access.endpoint == model.endpoint


def test_resolve_a_private_local_model_of_someone_else_fails(owner):
    model = _local_model(owner)
    with pytest.raises(LLMAccessError) as exc:
        client.resolve_review_access(UserFactory(), _frozen_model(model))
    assert exc.value.code == "model_unavailable"


def test_resolve_a_shared_local_model_of_someone_else(owner):
    model = _local_model(owner)
    model.endpoint.visibility = "shared"
    model.endpoint.save(update_fields=["visibility"])
    access = client.resolve_review_access(UserFactory(), _frozen_model(model))
    assert access.endpoint == model.endpoint


def test_resolve_refuses_other_weights_than_the_plan_froze(owner):
    model = _local_model(owner)
    frozen = _frozen_model(model)
    model.digest = "sha256:" + "cd" * 32
    model.save(update_fields=["digest"])
    with pytest.raises(LLMAccessError) as exc:
        client.resolve_review_access(owner, frozen)
    assert exc.value.code == "model_unavailable"
    assert "other weights" in str(exc.value)


def test_resolve_a_model_no_longer_served(owner):
    model = _local_model(owner)
    model.is_available = False
    model.save(update_fields=["is_available"])
    with pytest.raises(LLMAccessError) as exc:
        client.resolve_review_access(owner, _frozen_model(model))
    assert exc.value.code == "model_unavailable"


@override_settings(OPENROUTER_API_KEY="", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_resolve_an_openrouter_model_needs_a_key(owner):
    model = LLMModelFactory(model_id="vendor/or-model")
    with pytest.raises(LLMAccessError) as exc:
        client.resolve_review_access(owner, _frozen_model(model))
    assert exc.value.code == "missing_api_key"
    cred = UserApiCredential(user=owner, provider=Provider.OPENROUTER)
    cred.set_secret("user-key")
    cred.save()
    access = client.resolve_review_access(owner, _frozen_model(model))
    assert access.is_openrouter
    assert access.api_key == "user-key"
    assert access.served_model_name == "vendor/or-model"


@override_settings(OPENROUTER_API_KEY="global-key", OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True)
def test_resolve_an_openrouter_model_removed_from_the_catalog_uses_the_frozen_slug(owner):
    access = client.resolve_review_access(owner, {"slug": "vendor/gone", "id": 999_999})
    assert access.is_openrouter
    assert access.served_model_name == "vendor/gone"


# --------------------------------------------------------------------------
# Freezing and capabilities
# --------------------------------------------------------------------------


def _freeze(model, user=config._ANY_USER, **cfg):
    return config.freeze_reviewer({"model": model.model_id, "max_output_tokens": 4000, **cfg}, user=user)


def test_freeze_a_local_model(owner):
    model = _local_model(owner)
    frozen = _freeze(model)
    section = frozen["model"]
    assert section["slug"] == model.model_id
    assert section["local"] == {
        "endpoint": model.endpoint.slug,
        "endpoint_id": model.endpoint.pk,
        "kind": "ollama",
        "api_flavor": "native",
        "served_model_name": "qwen2.5-coder:7b",
        "digest": model.digest,
        "num_ctx": section["context_window"],
    }
    assert section["context_window_source"] == "num_ctx"
    assert section["pricing"]["input_per_token"] == 0.0
    assert section["pricing"]["output_per_token"] == 0.0
    assert frozen["provider"] is None
    assert frozen["provider_routing_source"] == "local"
    assert frozen["output_mode"] == "json_object"  # Ollama JSON mode, schema validated locally
    assert frozen["params"]["temperature"] == 0.2
    assert frozen["params"]["seed_forwarded"] is True
    assert any("not billed" in note for note in frozen["notes"])
    estimate = config.estimate_cost(frozen, chars=40_000, files=4)
    assert estimate["expected"] == estimate["high"] == 0.0


def test_an_openrouter_freeze_has_no_local_section():
    LLMModelFactory(model_id="vendor/or-model", context_window=128_000)
    frozen = config.freeze_reviewer({"model": "vendor/or-model"})
    assert "local" not in frozen["model"]
    assert frozen["provider"]["require_parameters"] is True


def test_freeze_a_local_openai_compatible_model_uses_prompt_mode(owner):
    model = _local_model(owner, flavor="openai", kind="openai_compatible")
    frozen = _freeze(model)
    assert frozen["output_mode"] == "prompt"
    assert frozen["model"]["local"]["api_flavor"] == "openai"


def test_freeze_a_local_model_refuses_the_agent_engine_and_provider_routing(owner):
    model = _local_model(owner)
    with pytest.raises(ConfigError) as exc:
        _freeze(model, engine="agent", harness="opencode")
    assert "reviewer.engine" in exc.value.field_errors
    with pytest.raises(ConfigError) as exc:
        _freeze(model, provider={"order": ["x"]})
    assert "reviewer.provider" in exc.value.field_errors


def test_freeze_a_local_thinking_model_offers_only_thinking_off(owner):
    model = _local_model(owner, thinking=True)
    assert _freeze(model, reasoning_effort="none")["params"]["reasoning"] == {"effort": "none"}
    assert _freeze(model, reasoning_effort=None)["params"]["reasoning"] is None
    with pytest.raises(ConfigError) as exc:
        _freeze(model, reasoning_effort="low")
    assert "reviewer.reasoning_effort" in exc.value.field_errors


def test_freeze_an_effort_on_a_local_model_that_does_not_think_is_dropped(owner):
    model = _local_model(owner)
    frozen = _freeze(model, reasoning_effort="low")
    assert frozen["params"]["reasoning"] is None
    assert "endpoint" in frozen["params"]["dropped"]["reasoning_effort"]


def test_freeze_with_a_user_needs_a_visible_local_model(owner):
    model = _local_model(owner)
    assert _freeze(model, user=owner)["model"]["local"]
    with pytest.raises(ConfigError):
        config.freeze_reviewer({"model": model.model_id, "max_output_tokens": 4000}, user=UserFactory())


def test_model_capabilities_of_a_local_model(owner):
    model = _local_model(owner, thinking=True)
    caps = config.model_capabilities(model.model_id)
    assert caps["in_catalog"] is False
    assert caps["resolvable"] is True
    assert caps["local"]["endpoint"] == model.endpoint.slug
    assert caps["local"]["available"] is True
    assert caps["context_window"] == caps["local"]["num_ctx"]
    assert caps["structured_outputs"] is False
    assert caps["response_format"] is True
    assert caps["tools"] is True
    assert caps["output_mode"] == "json_object"
    assert caps["reasoning"]["supported"] is True
    assert caps["reasoning"]["effort_choices"] == ["none"]
    assert caps["pricing"] == {
        "input_per_token": 0.0,
        "output_per_token": 0.0,
        "cache_read_per_token": None,
        "is_free": True,
    }
    # Another user cannot see the private endpoint's model.
    hidden = config.model_capabilities(model.model_id, user=UserFactory())
    assert hidden["resolvable"] is False
    assert hidden["model_id"] is None
    # Not served any more: known, but not resolvable.
    model.is_available = False
    model.save(update_fields=["is_available"])
    assert config.model_capabilities(model.model_id)["resolvable"] is False


def test_model_capabilities_of_a_catalog_model_is_resolvable_and_not_local():
    LLMModelFactory(model_id="vendor/or-model")
    caps = config.model_capabilities("vendor/or-model")
    assert caps["in_catalog"] is True
    assert caps["resolvable"] is True
    assert caps["local"] is None


# --------------------------------------------------------------------------
# A whole single-shot replicate on a local model
# --------------------------------------------------------------------------


def test_single_shot_replicate_on_a_local_model(owner, monkeypatch):
    patch_normalize(monkeypatch)
    model = _local_model(owner)
    frozen = _freeze(model, samples=1)
    access = client.resolve_review_access(owner, frozen["model"])
    content = answer([SQLI], BOTH)
    with mock.patch(CHAT, return_value=_native(content)) as fake:
        out = single_shot.run_sample(
            frozen=frozen,
            sample=app_sample(),
            index=0,
            api_key=access,
            is_cancelled=lambda: False,
            heartbeat=lambda: None,
        )
    assert out.status == "succeeded", out.error_message
    assert fake.call_args.kwargs["source"] == "analysis"
    assert out.usage["cost_usd"] == 0.0
    assert out.usage.get("cost_complete", True) is True
    assert len(out.findings) == 1


def test_an_abandoned_wait_cancels_the_queued_local_request(owner, monkeypatch):
    patch_normalize(monkeypatch)
    model = _local_model(owner)
    frozen = _freeze(model, samples=1)
    access = client.resolve_review_access(owner, frozen["model"])
    released = threading.Event()
    seen = {}

    def slow_chat(_access, **kwargs):
        # Wait like inference.requests.wait_result: until the cancel check fires.
        for _ in range(200):
            if kwargs["cancel_check"]():
                seen["cancelled"] = True
                released.set()
                raise LLMAccessError(code="cancelled", message="cancelled")
            released.wait(0.05)
        pytest.fail("never cancelled")

    checks = iter([False, False, True])
    monkeypatch.setattr(single_shot, "HEARTBEAT_INTERVAL_S", 0.05)
    with mock.patch(CHAT, side_effect=slow_chat):
        out = single_shot.run_sample(
            frozen=frozen,
            sample=app_sample(),
            index=0,
            api_key=access,
            is_cancelled=lambda: next(checks, True),
            heartbeat=lambda: None,
        )
        assert released.wait(5)
    assert out.status == "cancelled"
    assert seen == {"cancelled": True}
