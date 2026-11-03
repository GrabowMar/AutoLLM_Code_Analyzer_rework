"""End to end through the real relay script (spec §I "End-to-end"), no Docker.

fake Ollama ◄── relay/llmlab_relay.py (subprocess) ──HTTP──► Django live_server
(``/api/relay/v1``) ◄── LLMRequest queue ◄── ``inference.chat`` callers and the
internal LLM proxy (fed by the real gateway overlay subprocess).

``test_inventory_and_blocked_lanes`` plays the scheduler's role itself
(model lanes in blocked order, two at a time, K=1). The v2 executor-driven
end-to-end tests went with the v2 launch API; the v3 executor E2E belongs to
the study launch (WP-08/WP-11, spec 13.5).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client

from backend.generation.testing.fake_gateway import gateway_config
from backend.inference import proxy_server
from backend.inference.access import LLMAccessError
from backend.inference.access import resolve_access
from backend.inference.chat import chat
from backend.inference.gateway_wiring import proxy_token
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.tests.conftest import make_agent_run
from backend.inference.tests.fake_ollama import FakeOllama
from backend.llm_models.models import LLMModel

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]
RELAY_SCRIPT = Path(settings.BASE_DIR) / "relay" / "llmlab_relay.py"
GATEWAY_DIR = Path(settings.BASE_DIR) / "backend" / "generation" / "images" / "llm-gateway"
OVERLAY = Path(settings.BASE_DIR) / "backend" / "generation" / "images" / "llm-gateway-local" / "local.py"
MODELS = 4


@pytest.fixture(autouse=True)
def _fast_protocol(settings):
    settings.RELAY_POLL_TIMEOUT_S = 2
    settings.RELAY_RATE_LIMIT_PER_S = 1000


def _wait(predicate, timeout, message):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    pytest.fail(message)


class RelayProcess:
    def __init__(self, server_url, fake_url, token_file, state_dir, log_path):
        self.args = [
            sys.executable,
            str(RELAY_SCRIPT),
            "--server",
            server_url,
            "--token-file",
            str(token_file),
            "--upstream",
            fake_url,
            "--parallel",
            "2",
            "--state-dir",
            str(state_dir),
        ]
        self.log_path = log_path
        self.proc = None

    def clone(self, log_path):
        """The same relay (same token, state dir and relay_id) restarted, logging elsewhere."""
        other = RelayProcess.__new__(RelayProcess)
        other.args = list(self.args)
        other.log_path = log_path
        other.proc = None
        return other

    def start(self):
        self.log = open(self.log_path, "ab")  # noqa: SIM115, PTH123
        self.proc = subprocess.Popen(self.args, stdout=self.log, stderr=subprocess.STDOUT)  # noqa: S603
        return self

    def kill(self):
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(timeout=10)
        self.log.close()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=40)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if not self.log.closed:
            self.log.close()

    def output(self):
        return Path(self.log_path).read_text(errors="replace")


@pytest.fixture
def stack(live_server, tmp_path, owner):
    fake = FakeOllama(models=MODELS, max_loaded=1, latency_s=0.3).start()
    client = Client()
    client.force_login(owner)
    resp = client.post(
        "/api/inference/endpoints/",
        data=json.dumps({"name": "e2e box", "kind": "ollama", "max_concurrency": 2, "max_resident_models": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    data = resp.json()
    endpoint = LLMEndpoint.objects.get(pk=data["endpoint"]["id"])
    token_file = tmp_path / "token"
    token_file.write_text(data["token"]["secret_once"])
    token_file.chmod(0o600)
    relay = RelayProcess(live_server.url, fake.url, token_file, tmp_path / "state", tmp_path / "relay.log").start()
    try:
        _wait(
            lambda: LLMModel.objects.filter(endpoint=endpoint, supports_function_calling=True).count() == MODELS,
            30,
            "inventory never arrived",
        )
        yield endpoint, fake, relay, owner, tmp_path
    finally:
        relay.stop()
        fake.stop()
        print(relay.output())  # noqa: T201 — shown on failure


def _call(access, model, *, seed, cancel_check=None, tools=None):
    params = {"max_tokens": 64, "temperature": 0}
    if tools:
        params["tools"] = tools
    try:
        return chat(
            access,
            messages=[{"role": "user", "content": f"build {model}"}],
            params=params,
            seed=seed,
            source=LLMRequest.Source.ANALYSIS,
            cancel_check=cancel_check,
        )
    finally:
        connection.close()


def test_inventory_and_blocked_lanes(stack):
    endpoint, fake, _relay, owner, _tmp = stack
    models = list(LLMModel.objects.filter(endpoint=endpoint).order_by("served_model_name"))
    assert len(models) == MODELS
    assert all(m.model_id.startswith(f"local-{endpoint.slug}/") and m.context_window == 8192 for m in models)
    session = RelaySession.objects.get(endpoint=endpoint, ended_at__isnull=True)
    assert session.capacity == 2
    assert session.hostname == socket.gethostname()

    results = []
    lock = threading.Lock()
    # Blocked policy, K=1: each model's cells (2 apps × 2 repeats) run as one block, two at a time.
    for model in models:
        access = resolve_access(owner, model)
        threads = []
        for cell in range(4):
            seed = 1000 + cell

            def run(access=access, model=model, seed=seed):
                result = _call(access, model.served_model_name, seed=seed)
                with lock:
                    results.append((model.served_model_name, seed, result))

            threads.append(threading.Thread(target=run))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
    assert len(results) == MODELS * 4
    stats = fake.snapshot()
    assert stats["max_concurrency"] <= 2
    assert stats["max_resident"] == 1
    assert len(stats["loads"]) == MODELS  # one load per block, never thrashing
    assert all(o["num_ctx"] == min(8192, endpoint.num_ctx_cap) for o in stats["options"])
    assert sorted(o["seed"] for o in stats["options"]) == sorted(seed for _m, seed, _r in results)
    rows = LLMRequest.objects.filter(endpoint=endpoint, source="analysis")
    assert rows.count() == MODELS * 4
    assert set(rows.values_list("status", flat=True)) == {"done"}
    assert all(r.body is None and r.acked_at is not None for r in rows)
    digests = {m.served_model_name: m.digest for m in models}
    for name, _seed, result in results:
        assert result.served["digest"] == digests[name]
        assert result.response["model"] == f"{name}@{digests[name][7:19]}"


def test_relay_killed_mid_request_recovers(stack, tmp_path):
    endpoint, fake, relay, owner, _tmp = stack
    fake.latency_s = 3
    model = LLMModel.objects.filter(endpoint=endpoint).first()
    access = resolve_access(owner, model)
    outcome = {}

    def run():
        outcome["result"] = _call(access, model.served_model_name, seed=1)

    thread = threading.Thread(target=run)
    thread.start()
    _wait(lambda: LLMRequest.objects.filter(endpoint=endpoint, acked_at__isnull=False).exists(), 20, "never acked")
    relay.kill()
    fake.latency_s = 0.2
    restarted = relay.clone(tmp_path / "relay2.log").start()
    try:
        thread.join(timeout=60)
        assert "result" in outcome, restarted.output()
        row = LLMRequest.objects.get(endpoint=endpoint, source="analysis")
        assert row.status == "done"
        assert row.attempt >= 1  # relay_interruptions counts these
        assert RelaySession.objects.filter(endpoint=endpoint, ended_reason="replaced").exists()
    finally:
        restarted.stop()


def test_cancel_aborts_upstream_within_seconds(stack):
    endpoint, fake, _relay, owner, _tmp = stack
    fake.latency_s = 20
    model = LLMModel.objects.filter(endpoint=endpoint).first()
    access = resolve_access(owner, model)
    cancel = threading.Event()
    errors = []

    def run():
        try:
            _call(access, model.served_model_name, seed=2, cancel_check=cancel.is_set)
        except LLMAccessError as exc:
            errors.append(exc.code)

    thread = threading.Thread(target=run)
    thread.start()
    _wait(lambda: fake.snapshot()["concurrency"] >= 1, 20, "request never reached the model server")
    cancel.set()
    started = time.monotonic()
    _wait(lambda: fake.snapshot()["aborted"] >= 1, 12, "upstream request was not aborted")
    assert time.monotonic() - started < 6 + 5  # the waiter notices within 5 s, the relay within a poll
    thread.join(timeout=15)
    assert errors == ["cancelled"]


def test_agent_proxy_path_through_gateway_overlay(stack, tmp_path):
    endpoint, _fake, _relay, owner, _tmp = stack
    model = LLMModel.objects.filter(endpoint=endpoint).first()
    job = make_agent_run(model, owner)
    server = proxy_server.make_server("127.0.0.1:0")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        gateway_port = sock.getsockname()[1]
    # The v3 GatewayConfig (WP-01 contract) plus the env-based runtime keys of the overlay.
    config = gateway_config(
        run_id=str(job.pk),
        session_id=str(job.pk),
        upstream={
            "kind": "local_proxy",
            "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
            "endpoint_id": endpoint.pk,
        },
        model={"upstream_model": model.model_id},
        sampling={"seed": 77},
        call={"max_output_tokens": 128},
        budget={"max_llm_requests": 5, "cost_basis": "free"},
    ).model_dump(mode="json")
    env = {
        **os.environ,
        "GATEWAY_CONFIG": json.dumps(config),
        "GATEWAY_PORT": str(gateway_port),
        "GATEWAY_LOG_DIR": str(tmp_path),
        "LLMLAB_UPSTREAM_TOKEN": proxy_token(job),
        "OPENROUTER_API_KEY": "",
        "PYTHONPATH": str(GATEWAY_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    gateway = subprocess.Popen([sys.executable, str(OVERLAY)], env=env)  # noqa: S603
    try:
        base = f"http://127.0.0.1:{gateway_port}"

        def healthy():
            try:
                urllib.request.urlopen(f"{base}/healthz", timeout=1).read()  # noqa: S310
            except OSError:
                return False
            return True

        _wait(healthy, 15, "gateway did not start")
        body = {
            "model": "anything",
            "messages": [{"role": "user", "content": "list files"}],
            "tools": [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}],
        }
        request = urllib.request.Request(  # noqa: S310
            f"{base}/api/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
            out = json.loads(resp.read())
        assert out["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "bash"
        _wait((tmp_path / "calls.jsonl").exists, 5, "no call log")
        record = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[0])
        assert record["status"] == 200
        row = LLMRequest.objects.get(job=job)
        assert (row.source, row.status) == ("agent_proxy", "done")
    finally:
        gateway.terminate()
        gateway.wait(timeout=10)
        server.shutdown()
        server.server_close()
