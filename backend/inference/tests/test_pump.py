"""Direct-endpoint pumps (spec §C.2, §D.7): inventory, chat through the queue, caps, cancellation."""

from __future__ import annotations

import threading
import time
from unittest import mock

import pytest
from django.db import connection

from backend.inference import pump
from backend.inference import requests as q
from backend.inference import security
from backend.inference.access import LLMAccessError
from backend.inference.access import resolve_access
from backend.inference.chat import chat
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.fake_ollama import FakeOllama
from backend.llm_models.models import LLMModel

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def fake():
    server = FakeOllama(models=2, max_loaded=1, latency_s=0.2).start()
    yield server
    server.stop()


@pytest.fixture
def direct(staff, fake):
    endpoint = make_endpoint(staff, connection="direct", base_url="http://gpu.example.org", max_concurrency=2)
    port = int(fake.url.rsplit(":", 1)[1])
    target = security.Target(scheme="http", host="gpu.example.org", port=port, base_path="", addresses=("127.0.0.1",))
    with mock.patch("backend.inference.upstream.validate_direct_base_url", return_value=target):
        yield endpoint


@pytest.fixture
def pumps(direct):
    stop = threading.Event()
    thread = threading.Thread(target=pump.run_direct_pumps, args=(stop, "exec-test"), daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and LLMModel.objects.filter(endpoint=direct, is_available=True).count() != 2:
        time.sleep(0.2)
    yield direct
    stop.set()
    thread.join(timeout=15)


def test_inventory_and_chat_through_the_pump(pumps, staff, fake):
    models = list(LLMModel.objects.filter(endpoint=pumps).order_by("served_model_name"))
    assert [m.served_model_name for m in models] == ["fake-coder-0:7b", "fake-coder-1:7b"]
    assert all(m.supports_function_calling and m.context_window == 8192 for m in models)
    endpoint = LLMEndpoint.objects.get(pk=pumps.pk)
    assert endpoint.server_version == "0.12.3-fake"
    access = resolve_access(staff, models[0])
    results = []

    def call(i):
        try:
            results.append(
                chat(access, messages=[{"role": "user", "content": f"hi {i}"}], params={"max_tokens": 32}, seed=5),
            )
        finally:
            connection.close()

    threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 4
    assert all(r.response["choices"][0]["message"]["content"].startswith("answer") for r in results)
    assert results[0].response["model"].startswith("fake-coder-0:7b@")
    stats = fake.snapshot()
    assert stats["max_concurrency"] <= 2
    assert all(o["seed"] == 5 and o["num_ctx"] == 8192 for o in stats["options"])
    rows = LLMRequest.objects.filter(endpoint=pumps, source="scaffolding")
    assert set(rows.values_list("status", "pump_owner")) == {("done", "exec-test")}
    assert all(r.body is None for r in rows)


def test_upstream_http_error_maps_to_access_error(pumps, staff):
    model = LLMModel.objects.filter(endpoint=pumps).first()
    access = resolve_access(staff, model)
    from dataclasses import replace

    missing = replace(access, served_model_name="not-pulled:1b")
    with pytest.raises(LLMAccessError) as exc:
        chat(missing, messages=[{"role": "user", "content": "x"}], params={}, seed=None)
    assert exc.value.code == "upstream_error"
    assert exc.value.status_code == 404


def test_cancel_aborts_the_inflight_call(pumps, staff, fake):
    fake.latency_s = 10
    model = LLMModel.objects.filter(endpoint=pumps).first()
    access = resolve_access(staff, model)
    cancel = threading.Event()
    errors = []

    def call():
        try:
            chat(access, messages=[{"role": "user", "content": "slow"}], params={}, seed=1, cancel_check=cancel.is_set)
        except LLMAccessError as exc:
            errors.append(exc.code)
        finally:
            connection.close()

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not LLMRequest.objects.filter(endpoint=pumps, status="leased").exists():
        time.sleep(0.1)
    started = time.monotonic()
    cancel.set()
    thread.join(timeout=15)
    assert errors == ["cancelled"]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not fake.snapshot()["aborted"]:
        time.sleep(0.1)
    assert fake.snapshot()["aborted"] >= 1
    assert time.monotonic() - started < 8


def test_requeue_pump_orphans(direct):
    from backend.inference.access import endpoint_access

    rid = q.enqueue(endpoint_access(direct, served_model_name="m"), "/api/chat", {})
    q.claim(direct.pk, limit=1, pump_owner="dead-executor")
    assert q.requeue_pump_orphans(["exec-live"]) == 1
    row = LLMRequest.objects.get(pk=rid)
    assert (row.status, row.pump_owner, row.attempt) == ("queued", "", 1)
