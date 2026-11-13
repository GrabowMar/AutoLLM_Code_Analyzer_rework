"""The native Anthropic route end to end against a fake Messages server: ``upstream.call``, the pump, the
inventory (list prices), the proxy, the budgets and the endpoint flavor (migration 0005, constraint)."""

from __future__ import annotations

import importlib
import json
import threading
import time
from unittest import mock

import pytest
from django.db import IntegrityError
from django.db import transaction

from backend.inference import anthropic_native as an
from backend.inference import proxy_server
from backend.inference import pump
from backend.inference import requests as q
from backend.inference import security
from backend.inference import upstream
from backend.inference.access import access_from_snapshot
from backend.inference.chat import build_upstream_body
from backend.inference.gateway_wiring import proxy_token
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.tests.conftest import make_agent_run
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.inference.tests.fake_anthropic_stream import OUTPUT_TOKENS
from backend.inference.tests.fake_anthropic_stream import USAGE
from backend.inference.tests.fake_anthropic_stream import FakeAnthropic
from backend.llm_models.models import LLMModel

MESSAGES = "/v1/messages"
PROMPT = USAGE["input_tokens"] + USAGE["cache_creation_input_tokens"] + USAGE["cache_read_input_tokens"]
TOOLS = [{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}]


@pytest.fixture
def fake():
    server = FakeAnthropic().start()
    yield server
    server.stop()


@pytest.fixture
def target(fake):
    tgt = security.Target(
        scheme="http",
        host="api.anthropic.com",
        port=fake.port,
        base_path="/v1",
        addresses=("127.0.0.1",),
    )
    with mock.patch("backend.inference.upstream.validate_direct_base_url", return_value=tgt):
        yield tgt


def _body(model: str, **params) -> dict:
    return an.to_messages_request(
        model,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "build it"}],
        {"max_tokens": 1000, "temperature": 0.3, **params},
        tools=TOOLS,
        tool_choice="required",
    )


def _call(model: str, limits: upstream.StreamLimits | None = None, **params):
    return upstream.call(
        "https://api.anthropic.com/v1",
        "POST",
        MESSAGES,
        _body(model, **params),
        timeout=10.0,
        api_key="sk-ant-test",
        stream_limits=limits,
    )


# =====================================================================================
# upstream.call
# =====================================================================================


def test_messages_call_sends_the_native_request_and_returns_a_chat_completion(fake, target):
    resp = _call("normal")
    assert fake.paths[-1] == "/v1/messages"
    headers = fake.headers[-1]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == an.ANTHROPIC_VERSION
    assert "authorization" not in headers
    assert headers["accept"] == "text/event-stream"
    sent = fake.received[-1]
    assert an.META_KEY not in sent
    assert sent["stream"] is True
    assert "temperature" not in sent
    assert sent["output_config"] == {"effort": "high"}
    assert sent["thinking"] == {"type": "adaptive"}
    assert resp.status == 200
    payload = resp.payload
    assert payload["object"] == "chat.completion"
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"] == {"role": "assistant", "content": "Hello, world"}
    usage = payload["usage"]
    assert usage["prompt_tokens"] == PROMPT
    assert usage["completion_tokens"] == OUTPUT_TOKENS
    assert usage["prompt_tokens_details"] == {
        "cached_tokens": USAGE["cache_read_input_tokens"],
        "cache_write_tokens": USAGE["cache_creation_input_tokens"],
    }
    assert "cost" not in usage
    assert "llmlab_estimated" not in usage
    request = usage["anthropic"]["request"]
    assert request["effort"] == "high"
    assert request["effort_source"] == "llmlab_default"
    assert request["stripped"] == ["temperature"]
    assert usage["anthropic"]["thinking_blocks"] == 1


def test_tool_use_streams_into_tool_calls(fake, target):
    payload = _call("tools").payload
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == "Writing it."
    assert choice["message"]["tool_calls"] == [
        {
            "id": "toolu_01",
            "type": "function",
            "function": {"name": "write", "arguments": '{"path": "a.txt", "text": "hi"}'},
        },
    ]
    # tool_choice reached the upstream body (required -> auto on an unknown, conservative model).
    assert fake.received[-1]["tool_choice"] == {"type": "auto"}


def test_wall_clock_cut_keeps_the_exact_prompt_split(fake, target):
    limits = upstream.StreamLimits(max_output_tokens=1_000_000, wall_clock_s=0.6, prompt_tokens_estimate=9)
    started = time.monotonic()
    resp = _call("slow", limits)
    assert time.monotonic() - started < 3
    payload = resp.payload
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "length"
    assert choice["native_finish_reason"] == "llmlab_cut:wall_clock"
    assert payload["usage"]["prompt_tokens"] == PROMPT
    assert payload["usage"]["llmlab_estimated"] is True
    assert payload["usage"]["completion_tokens"] > 0


def test_no_content_block_output_past_the_wall_clock_is_a_timeout(fake, target):
    """Only an empty text block and pings (no thinking block started): an infrastructure timeout."""
    limits = upstream.StreamLimits(max_output_tokens=1000, wall_clock_s=0.5)
    with pytest.raises(upstream.UpstreamError) as err:
        _call("thinking-only", limits)
    assert err.value.kind == "timeout"


def test_hidden_thinking_past_the_wall_clock_is_a_length_cut_booked_at_max_tokens(fake, target):
    """A started thinking block is output (as streamed reasoning is on the OpenAI route): the call is a
    model-attributed ``length`` cut with the exact input split and the hidden thinking bounded by
    ``max_tokens``, never a $0 infrastructure timeout."""
    limits = upstream.StreamLimits(max_output_tokens=None, wall_clock_s=0.5)
    resp = _call("hidden-thinking", limits)
    assert resp.status == 200
    choice = resp.payload["choices"][0]
    assert choice["finish_reason"] == "length"
    assert choice["native_finish_reason"] == "llmlab_cut:wall_clock"
    usage = resp.payload["usage"]
    assert usage["prompt_tokens"] == PROMPT
    assert usage["prompt_tokens_details"]["cached_tokens"] == USAGE["cache_read_input_tokens"]
    assert usage["completion_tokens"] == 1000  # the request's max_tokens
    assert usage["llmlab_estimated"] is True
    assert usage["llmlab_estimate_basis"] == "max_tokens (hidden thinking)"


@pytest.mark.parametrize(
    ("model", "status", "code"),
    [
        ("http-529", 529, "overloaded_error"),
        ("http-429", 429, "rate_limit_error"),
        ("http-400-too-long", 400, "context_length_exceeded"),
    ],
)
def test_error_answers_keep_their_status_in_the_openai_shape(fake, target, model, status, code):
    resp = _call(model)
    assert resp.status == status
    assert resp.payload["error"]["code"] == code
    assert resp.payload["anthropic_error"]["type"] == "error"


def test_an_in_stream_error_is_a_502(fake, target):
    resp = _call("error-event")
    assert resp.status == 502
    assert resp.payload["llmlab_stream_error"] is True
    assert resp.payload["error"]["type"] == "overloaded_error"


def test_a_json_message_answer_is_converted_too(fake, target):
    resp = _call("json")
    assert resp.status == 200
    assert resp.payload["choices"][0]["message"]["content"] == "plain"
    assert resp.payload["usage"]["prompt_tokens"] == PROMPT
    assert resp.payload["usage"]["anthropic"]["request"]["effort"] == "high"


def test_messages_path_is_direct_only_and_chat_paths_keep_bearer_auth(fake, target):
    assert upstream.path_allowed("POST", MESSAGES, {})
    assert not upstream.path_allowed("GET", MESSAGES, None)
    assert ("POST", MESSAGES) not in upstream.ALLOWED_PATHS  # never part of the relay allowlist
    upstream.call("https://api.anthropic.com/v1", "GET", "/v1/models", timeout=5.0, api_key="sk-ant-test")
    headers = fake.headers[-1]
    assert headers["authorization"] == "Bearer sk-ant-test"
    assert headers["anthropic-version"] == upstream.ANTHROPIC_VERSION


# =====================================================================================
# Pump, inventory and list prices (database)
# =====================================================================================


@pytest.fixture
def anthropic_endpoint(staff, target):
    return make_endpoint(
        staff,
        slug="anthropic-test",
        kind="openai_compatible",
        connection="direct",
        api_flavor="anthropic",
        base_url="https://api.anthropic.com/v1",
        cost_basis="billed",
        max_concurrency=2,
    )


@pytest.mark.django_db(transaction=True)
def test_pump_runs_a_native_request_and_stores_an_openai_answer(fake, anthropic_endpoint):
    model = make_local_model(anthropic_endpoint, name="tools", digest="")
    access = access_from_snapshot(
        {"kind": "openai_compatible", "api_flavor": "anthropic", "served_model_name": "tools"},
        anthropic_endpoint,
    )
    path, body = build_upstream_body(
        access,
        [{"role": "user", "content": "write a.txt"}],
        {"max_tokens": 2000},
        11,
        tools=TOOLS,
        tool_choice="auto",
    )
    assert path == MESSAGES
    rid = q.enqueue(access, path, body, source="agent_proxy")
    row = LLMRequest.objects.get(pk=rid)
    limits = pump.stream_limits(row, anthropic_endpoint)
    assert limits is not None
    assert limits.max_output_tokens == 2000
    assert limits.prompt_tokens_estimate > 0
    assert pump.run_one(anthropic_endpoint.pk, "exec-test") is True
    row.refresh_from_db()
    assert row.status == LLMRequest.Status.DONE
    assert row.response_status == 200
    assert row.response["choices"][0]["message"]["tool_calls"][0]["id"] == "toolu_01"
    assert row.prompt_tokens == PROMPT
    assert row.completion_tokens == OUTPUT_TOKENS
    assert fake.received[-1]["model"] == model.served_model_name
    assert an.META_KEY not in fake.received[-1]


@pytest.mark.django_db(transaction=True)
def test_pump_stores_a_429_for_the_gateway_to_retry(fake, anthropic_endpoint):
    from backend.inference.access import LLMAccessError
    from backend.inference.chat import raise_for_request

    make_local_model(anthropic_endpoint, name="http-429", digest="")
    access = access_from_snapshot({"served_model_name": "http-429"}, anthropic_endpoint)
    path, body = build_upstream_body(access, [{"role": "user", "content": "x"}], {"max_tokens": 10}, None)
    rid = q.enqueue(access, path, body, source="agent_proxy")
    pump.run_one(anthropic_endpoint.pk, "exec-test")
    row = LLMRequest.objects.select_related("endpoint").get(pk=rid)
    assert row.response_status == 429
    with pytest.raises(LLMAccessError) as exc:
        raise_for_request(row)
    assert exc.value.status_code == 429
    assert exc.value.retryable is False  # the proxy passes the 429 through; the gateway retries it


@pytest.mark.django_db(transaction=True)
def test_inventory_uses_max_input_tokens_and_prices_the_rows(fake, anthropic_endpoint):
    pump.refresh_inventory(anthropic_endpoint)
    rows = {m.served_model_name: m for m in LLMModel.objects.filter(endpoint=anthropic_endpoint)}
    assert set(rows) == set(fake.models)
    opus = rows["claude-opus-5-5"]
    assert opus.context_window == 1_000_000
    assert not opus.is_free
    assert opus.input_price_per_token == pytest.approx(4e-6)
    assert opus.output_price_per_token == pytest.approx(20e-6)
    assert opus.capabilities_json["pricing"] == {
        "prompt": "0.000004",
        "completion": "0.00002",
        "input_cache_read": "0.0000002",
        "input_cache_write": "0.000005",
    }
    assert opus.capabilities_json["pricing_source"]["kind"] == "anthropic_list_price"
    assert opus.metadata["supports_reasoning"] is True
    assert rows["claude-haiku-4-5-20251001"].metadata["supports_reasoning"] is False
    # Unknown served names (no list price, no twin) stay as the inventory made them.
    assert rows["normal"].is_free
    # Idempotent: a second refresh changes nothing.
    stamp = opus.capabilities_json["pricing_source"]["set_at"]
    pump.refresh_inventory(anthropic_endpoint)
    opus.refresh_from_db()
    assert opus.capabilities_json["pricing_source"]["set_at"] == stamp


@pytest.mark.django_db
def test_model_facts_of_a_priced_row(staff):
    from backend.generation.budget.model_facts import model_facts
    from backend.inference.anthropic_prices import apply_prices

    endpoint = make_endpoint(
        staff,
        kind="openai_compatible",
        connection="direct",
        api_flavor="anthropic",
        base_url="https://api.anthropic.com/v1",
    )
    sonnet = make_local_model(endpoint, name="claude-sonnet-5-5", digest="")
    assert apply_prices(sonnet) is True
    assert apply_prices(sonnet) is False
    facts = model_facts(LLMModel.objects.get(pk=sonnet.pk))
    assert facts.input_per_mtok == pytest.approx(2.0)
    assert facts.output_per_mtok == pytest.approx(10.0)
    assert facts.cached_input_per_mtok == pytest.approx(0.2)  # official price, not the catalog's 0.10
    assert facts.cache_write_per_mtok == pytest.approx(2.5)
    assert facts.supports_reasoning is True


@pytest.mark.django_db
def test_an_unlisted_model_takes_its_catalog_twins_prices(staff):
    from backend.inference.anthropic_prices import apply_prices

    LLMModel.objects.create(
        model_id="anthropic/claude-sonnet-9",
        canonical_slug="anthropic_claude-sonnet-9",
        provider="anthropic",
        model_name="Claude Sonnet 9",
        input_price_per_token=3e-6,
        output_price_per_token=15e-6,
        capabilities_json={
            "pricing": {"prompt": "0.000003", "completion": "0.000015", "input_cache_read": "0.0000003"},
        },
    )
    endpoint = make_endpoint(
        staff,
        kind="openai_compatible",
        connection="direct",
        api_flavor="anthropic",
        base_url="https://api.anthropic.com/v1",
    )
    row = make_local_model(
        endpoint,
        name="claude-sonnet-9",
        digest="",
        metadata={"catalog_twin": {"model_id": "anthropic/claude-sonnet-9"}},
    )
    assert apply_prices(row) is True
    row.refresh_from_db()
    assert row.input_price_per_token == pytest.approx(3e-6)
    assert row.capabilities_json["pricing"]["input_cache_read"] == "0.0000003"
    assert row.capabilities_json["pricing_source"] == {
        **row.capabilities_json["pricing_source"],
        "kind": "catalog_twin",
        "twin": "anthropic/claude-sonnet-9",
    }


# =====================================================================================
# Proxy and budgets
# =====================================================================================


@pytest.fixture
def proxy():
    proxy_server._auth_cache._jobs.clear()
    server = proxy_server.make_server("127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class _Pump:
    """Runs ``pump.run_one`` for one endpoint in a thread (the executor's slot loop)."""

    def __init__(self, endpoint_id: int) -> None:
        self.endpoint_id = endpoint_id
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        from django.db import connection

        try:
            while not self.stop.is_set():
                if not pump.run_one(self.endpoint_id, "exec-test"):
                    time.sleep(0.05)
        finally:
            connection.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=10)


def _post(base: str, body: dict, token: str) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(  # noqa: S310
        base + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _agent_run(owner, endpoint, served: str, *, caching: dict | None = None):
    model = make_local_model(endpoint, name=served, digest="")
    section = {
        "id": endpoint.pk,
        "kind": "openai_compatible",
        "served_model_name": served,
        "digest": "",
        "cost_basis": "billed",
        "api_flavor": "anthropic",
    }
    if caching is None:
        return make_agent_run(model, owner, endpoint_section=section)
    # RunManifest is write-once: build the run with its caching policy in the frozen manifest.
    from datetime import timedelta

    from django.utils import timezone

    from backend.generation.models.choices import RunPhase
    from backend.generation.models.choices import RunStatus
    from backend.generation.tests.factories import GenerationRunFactory
    from backend.generation.tests.factories import RunManifestFactory
    from backend.generation.tests.factories import run_manifest_content

    run = GenerationRunFactory(
        model=model,
        created_by=owner,
        status=RunStatus.RUNNING,
        phase=RunPhase.RUNNING,
        attempt=1,
        priority=3,
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )
    manifest = run_manifest_content(run)
    manifest["model"]["endpoint"] = section
    manifest["resources"]["effective"] = {"content": {"time": {"wall_clock_s": 600}, "caching": caching}}
    RunManifestFactory(run=run, manifest=manifest)
    return run


@pytest.mark.django_db(transaction=True)
def test_proxy_round_trip_through_the_native_route(proxy, fake, anthropic_endpoint, owner):
    run = _agent_run(owner, anthropic_endpoint, "tools")
    body = {
        "model": "whatever",
        "messages": [
            {"role": "system", "content": "You build apps."},
            {"role": "user", "content": "write a.txt"},
        ],
        "tools": TOOLS,
        "tool_choice": {"type": "function", "function": {"name": "write"}},
        "parallel_tool_calls": False,
        "max_tokens": 4096,
        "temperature": 0.2,
        "reasoning": {"effort": "medium", "exclude": False},
        "seed": 5,
        "usage": {"include": True},
    }
    with _Pump(anthropic_endpoint.pk):
        status, out = _post(proxy, body, proxy_token(run))
    assert status == 200, out
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert out["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "write"
    assert out["usage"]["prompt_tokens"] == PROMPT
    assert out["usage"]["prompt_tokens_details"]["cached_tokens"] == USAGE["cache_read_input_tokens"]
    assert "cost" not in out["usage"]  # priced from the list-price table, never a stamped $0
    assert out["model"].startswith("tools")
    sent = fake.received[-1]
    assert sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["max_tokens"] == 4096
    assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    for key in ("temperature", "seed", "usage", an.META_KEY):
        assert key not in sent
    request = out["usage"]["anthropic"]["request"]
    assert request["effort_source"] == "policy"
    assert request["stripped"] == ["seed", "temperature"]
    row = LLMRequest.objects.get(job=run)
    assert row.path == MESSAGES


@pytest.mark.django_db(transaction=True)
def test_proxy_honours_prompt_cache_off(proxy, fake, anthropic_endpoint, owner):
    run = _agent_run(owner, anthropic_endpoint, "normal", caching={"prompt_cache": "off", "ttl": "5m"})
    body = {"model": "x", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]}
    with _Pump(anthropic_endpoint.pk):
        status, out = _post(proxy, {**body, "stream": False}, proxy_token(run))
    assert status == 200, out
    assert "cache_control" not in json.dumps(fake.received[-1])
    assert out["usage"]["anthropic"]["request"]["cache"]["prompt_cache"] == "off"


@pytest.mark.django_db(transaction=True)
def test_proxy_passes_a_429_through_and_maps_529_to_a_retryable_503(proxy, fake, anthropic_endpoint, owner):
    limited = _agent_run(owner, anthropic_endpoint, "http-429")
    overloaded = _agent_run(owner, anthropic_endpoint, "http-529")
    body = {"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}
    with _Pump(anthropic_endpoint.pk):
        status_429, out_429 = _post(proxy, body, proxy_token(limited))
        status_529, out_529 = _post(proxy, body, proxy_token(overloaded))
    assert status_429 == 429
    assert "rate limit" in out_429["error"]["message"]
    assert status_529 == 503
    assert out_529["error"]["code"] == "endpoint_unavailable"


def test_proxy_budget_settles_a_cached_call_at_the_frozen_list_prices():
    config = {
        "budget": {
            "max_cost_usd": 5.0,
            "cost_basis": "billed",
            "max_total_tokens": 10_000_000,
            "max_llm_requests": 10,
        },
        "call": {"max_output_tokens": 32_000},
        "pricing": {
            "input_per_mtok": 4.0,
            "output_per_mtok": 20.0,
            "cached_input_per_mtok": 0.2,
            "cache_write_per_mtok": 5.0,
        },
        "reasoning": {"mode": "provider_default"},
        "model": {},
    }
    budgets = proxy_server.ProxyBudgets(config_loader=lambda _job: config)
    body = {"messages": [{"role": "user", "content": "x" * 360}], "max_tokens": 32_000}
    key, refusal = budgets.admit("run-1", 1, body, {}, "dedupe")
    assert refusal is None
    message = {
        "id": "msg_1",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 1000,
            "cache_creation_input_tokens": 10_000,
            "cache_read_input_tokens": 100_000,
            "output_tokens": 2000,
        },
    }
    budgets.settle("run-1", 1, key, 200, an.to_chat_completion(message))
    state = budgets.state("run-1", 1)
    assert state["cost"] == pytest.approx(0.114)
    assert state["list_cost"] == pytest.approx(0.114)
    assert state["cached_tokens"] == 100_000
    assert state["cache_write_tokens"] == 10_000
    # The reservation priced every prompt token at the dearest rate (a 1.25x cache write).
    rules = proxy_server.budget_rules()
    assert rules.BudgetLimits.from_config(config).pricing.worst_input_per_mtok == 5.0


# =====================================================================================
# Endpoint flavor: constraint, migration data step, API
# =====================================================================================


@pytest.mark.django_db
def test_the_anthropic_flavor_needs_a_direct_openai_compatible_endpoint(staff):
    for kwargs in (
        {"kind": "openai_compatible", "connection": "relay"},
        {"kind": "ollama", "connection": "direct", "base_url": "http://gpu.example.org"},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            make_endpoint(staff, api_flavor="anthropic", **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_endpoint(staff, kind="openai_compatible", api_flavor="native")
    ok = make_endpoint(
        staff,
        kind="openai_compatible",
        connection="direct",
        api_flavor="anthropic",
        base_url="https://api.anthropic.com/v1",
    )
    assert ok.api_flavor == LLMEndpoint.ApiFlavor.ANTHROPIC


@pytest.mark.django_db
def test_migration_switches_only_direct_anthropic_hosts(staff):
    from django.apps import apps

    migration = importlib.import_module("backend.inference.migrations.0005_llmendpoint_anthropic_flavor")
    claude = make_endpoint(
        staff,
        kind="openai_compatible",
        connection="direct",
        api_flavor="openai",
        base_url="https://api.anthropic.com/v1",
    )
    opencode = make_endpoint(
        staff,
        kind="openai_compatible",
        connection="direct",
        api_flavor="openai",
        base_url="https://opencode.ai/zen/go",
    )
    relay = make_endpoint(staff, kind="openai_compatible", connection="relay", api_flavor="openai")
    editor = mock.Mock(connection=mock.Mock(alias="default"))
    migration.to_anthropic_flavor(apps, editor)
    flavors = dict(
        LLMEndpoint.objects.filter(pk__in=[claude.pk, opencode.pk, relay.pk]).values_list("pk", "api_flavor"),
    )
    assert flavors == {claude.pk: "anthropic", opencode.pk: "openai", relay.pk: "openai"}
    migration.to_openai_flavor(apps, editor)
    assert LLMEndpoint.objects.get(pk=claude.pk).api_flavor == "openai"


@pytest.mark.django_db
def test_the_anthropic_preset_creates_a_native_flavor_endpoint():
    from backend.integrations.services.providers import PRESETS

    presets = {p.id: p for p in PRESETS}
    assert presets["anthropic"].api_flavor == "anthropic"
    assert all(p.api_flavor == "openai" for p in PRESETS if p.id != "anthropic")


@pytest.mark.django_db
def test_the_endpoints_api_accepts_the_anthropic_flavor_only_on_direct_openai_compatible(staff):
    from django.test import Client

    client = Client()
    client.force_login(staff)
    api = "/api/inference/endpoints/"
    relay = {"name": "Claude relay", "kind": "openai_compatible", "connection": "relay", "api_flavor": "anthropic"}
    resp = client.post(api, data=json.dumps(relay), content_type="application/json")
    assert resp.status_code == 400
    assert "Anthropic flavor" in resp.json()["detail"]
    endpoint = make_endpoint(
        staff,
        kind="openai_compatible",
        connection="direct",
        api_flavor="openai",
        base_url="https://api.anthropic.com/v1",
    )
    url = f"{api}{endpoint.pk}/"
    resp = client.patch(url, data=json.dumps({"api_flavor": "anthropic"}), content_type="application/json")
    assert resp.status_code == 200, resp.content
    assert resp.json()["api_flavor"] == "anthropic"
    resp = client.patch(url, data=json.dumps({"connection": "relay"}), content_type="application/json")
    assert resp.status_code == 400
    assert LLMEndpoint.objects.get(pk=endpoint.pk).connection == "direct"
