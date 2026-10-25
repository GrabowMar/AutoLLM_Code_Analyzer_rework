"""End to end through the real relay script (spec §I "End-to-end"), no Docker.

fake Ollama ◄── relay/llmlab_relay.py (subprocess) ──HTTP──► Django live_server
(``/api/relay/v1``) ◄── LLMRequest queue ◄── ``inference.chat`` callers and the
internal LLM proxy (fed by the real gateway overlay subprocess).

``test_inventory_and_blocked_lanes`` plays the scheduler's role itself
(model lanes in blocked order, two at a time, K=1);
``test_executor_runs_a_scaffolding_experiment`` drives the same stack through
the real launch (API) and the real executor (§I steps 5-8).
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
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.utils import timezone

from backend.execution.executor import Executor
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.inference import proxy_server
from backend.inference.access import LLMAccessError
from backend.inference.access import resolve_access
from backend.inference.chat import chat
from backend.inference.gateway_wiring import proxy_token
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.inference.models import RelaySession
from backend.inference.snapshot import endpoint_section
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
    rows = LLMRequest.objects.filter(endpoint=endpoint, source="scaffolding")
    assert rows.count() == MODELS * 4
    assert set(rows.values_list("status", flat=True)) == {"done"}
    assert all(r.body is None and r.acked_at is not None for r in rows)
    digests = {m.served_model_name: m.digest for m in models}
    for name, _seed, result in results:
        assert result.served["digest"] == digests[name]
        assert result.response["model"] == f"{name}@{digests[name][7:19]}"


class _SleepingRedis:
    """The executor's doorbell without Redis: every wait is a plain short sleep (the tick drives progress)."""

    def blpop(self, keys, timeout=0):
        time.sleep(min(float(timeout or 0.2), 0.2))


def _launch_scaffolding(client, models, apps, *, policy, seed, repeats=2):
    """Create (one call), preview and launch a scaffolding experiment through the API (§F.1)."""
    profile = GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()
    assert profile is not None
    body = {
        "name": f"e2e {policy}",
        "slug": f"e2e-{policy}-{int(time.time() * 1000) % 10**8}",
        "mode": "scaffolding",
        "stack_slug": "flask-react",
        "repeats": repeats,
        "base_seed": seed,
        "app_requirement_ids": [a.id for a in apps],
        "conditions": {"cross": {"model_ids": [m.id for m in models], "profile_ids": [profile.id]}},
        "execution": {"scheduling_policy": policy},
        "post_run": {"evaluate": False},
    }
    resp = client.post("/api/generation/experiments/", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200, resp.content
    experiment_id = resp.json()["id"]
    preview = client.get(f"/api/generation/experiments/{experiment_id}/preview/")
    assert preview.status_code == 200, preview.content
    resp = client.post(f"/api/generation/experiments/{experiment_id}/launch/", content_type="application/json")
    assert resp.status_code == 200, resp.content
    return Experiment.objects.get(pk=experiment_id), preview.json()


def _start_executor():
    """The real executor (real pools) in a thread; returns ``(executor, thread, outcome)``."""
    executor = Executor(
        start_proxy=False,
        start_pumps=False,
        stop_when_idle=True,
        tick_seconds=0.2,
        redis_client=_SleepingRedis(),
    )
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("code", executor.run()), daemon=True)
    thread.start()
    return executor, thread, outcome


def _run_executor(timeout_s=240):
    return _join_executor(*_start_executor(), timeout_s=timeout_s)


def _join_executor(executor, thread, outcome, *, timeout_s=240):
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        executor.request_stop("test timeout")
        thread.join(timeout=30)
        pytest.fail("the executor did not drain the experiment in time")
    connection.close()
    return outcome.get("code")


def _model_runs(names):
    """Number of maximal runs of equal consecutive names (each run after the first is a model swap)."""
    return sum(1 for i, name in enumerate(names) if i == 0 or names[i - 1] != name)


def test_executor_runs_a_scaffolding_experiment(stack):
    endpoint, fake, _relay, owner, _tmp = stack
    client = Client()
    client.force_login(owner)
    models = list(LLMModel.objects.filter(endpoint=endpoint).order_by("served_model_name"))
    apps = [AppRequirementTemplateFactory(is_default=True) for _ in range(2)]
    repeats = 2

    # §I 5-7: blocked order on a K=1 endpoint, run by the real executor.
    experiment, preview = _launch_scaffolding(client, models, apps, policy="blocked", seed=4242)
    assert not [i for i in preview["issues"] if i["code"] == "model_thrash_risk"]
    assert _run_executor() == 0
    jobs = list(GenerationJob.objects.filter(experiment=experiment).order_by("sequence"))
    assert len(jobs) == len(models) * len(apps) * repeats == 16
    assert {j.status for j in jobs} == {GenerationJob.Status.COMPLETED}, [
        (j.status, j.error_message) for j in jobs if j.status != GenerationJob.Status.COMPLETED
    ]
    experiment.refresh_from_db()
    assert experiment.status == Experiment.Status.COMPLETED
    stats = fake.snapshot()
    assert stats["max_concurrency"] <= 2
    assert stats["max_resident"] == 1
    assert len(stats["loads"]) <= len(models) * repeats
    chat_options = [o for o in stats["options"] if "num_ctx" in o]
    assert chat_options
    assert all(o["num_ctx"] == min(8192, endpoint.num_ctx_cap) for o in chat_options)
    assert {o["seed"] for o in chat_options} == {j.experiment_seed for j in jobs}
    digests = {m.pk: m.digest for m in models}
    for job in jobs:
        assert job.resolved_bundle["endpoint"]["digest"] == digests[job.model_id]
    rows = LLMRequest.objects.filter(job__experiment=experiment)
    assert rows.exists()
    assert set(rows.values_list("status", flat=True)) == {"done"}
    assert not rows.filter(body__isnull=False).exists()
    assert not rows.filter(response__isnull=False).exists()
    # Within each repeat block a model's cells are contiguous.
    for repeat in range(repeats):
        order = [j.model_id for j in jobs if j.repeat_index == repeat]
        assert _model_runs(order) == len(models), order

    # §I 8: the same design interleaved thrashes the single resident slot (and the preview said so).
    loads_before = len(stats["loads"])
    experiment, preview = _launch_scaffolding(client, models, apps, policy="interleaved", seed=4242)
    assert [i for i in preview["issues"] if i["code"] == "model_thrash_risk"]
    assert _run_executor() == 0
    jobs = list(GenerationJob.objects.filter(experiment=experiment))
    assert {j.status for j in jobs} == {GenerationJob.Status.COMPLETED}
    stats = fake.snapshot()
    assert stats["max_resident"] == 1
    assert len(stats["loads"]) - loads_before > len(models) * repeats


def test_executor_job_survives_a_relay_restart(stack, tmp_path):
    """§I 9 through the executor: the relay dies mid-request; the job still completes, once per stage."""
    endpoint, fake, relay, owner, _tmp = stack
    client = Client()
    client.force_login(owner)
    model = LLMModel.objects.filter(endpoint=endpoint).order_by("served_model_name").first()
    apps = [AppRequirementTemplateFactory(is_default=True)]
    fake.latency_s = 3
    experiment, _preview = _launch_scaffolding(client, [model], apps, policy="blocked", seed=7, repeats=1)
    running = _start_executor()
    restarted = None
    try:
        _wait(
            lambda: LLMRequest.objects.filter(job__experiment=experiment, acked_at__isnull=False).exists(),
            60,
            "the job's request was never acked by the relay",
        )
        relay.kill()
        fake.latency_s = 0.2
        restarted = relay.clone(tmp_path / "relay2.log").start()
        assert _join_executor(*running) == 0
    finally:
        if running[1].is_alive():
            running[0].request_stop("test cleanup")
            running[1].join(timeout=30)
        if restarted is not None:
            restarted.stop()
    job = GenerationJob.objects.get(experiment=experiment)
    assert job.status == GenerationJob.Status.COMPLETED, job.error_message
    assert job.attempt == 0  # the request was re-sent; the job itself never restarted
    resent = LLMRequest.objects.filter(job=job, attempt__gte=1)
    assert resent.exists()
    assert set(LLMRequest.objects.filter(job=job).values_list("status", flat=True)) == {"done"}
    stages = list(job.artifacts.values_list("stage", flat=True))
    assert len(stages) == len(set(stages))  # one artifact per stage, no duplicates from the re-send
    assert RelaySession.objects.filter(endpoint=endpoint, ended_reason="replaced").exists()


def test_executor_cancel_aborts_the_upstream_call(stack):
    """§I 10 through the executor: cancelling the experiment aborts the model server's in-flight request."""
    endpoint, fake, _relay, owner, _tmp = stack
    client = Client()
    client.force_login(owner)
    model = LLMModel.objects.filter(endpoint=endpoint).order_by("served_model_name").first()
    apps = [AppRequirementTemplateFactory(is_default=True)]
    fake.latency_s = 30
    experiment, _preview = _launch_scaffolding(client, [model], apps, policy="blocked", seed=8, repeats=1)
    running = _start_executor()
    try:
        _wait(lambda: fake.snapshot()["concurrency"] >= 1, 60, "the request never reached the model server")
        resp = client.post(
            f"/api/generation/experiments/{experiment.id}/cancel/",
            data=json.dumps({"scope": "all"}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content
        started = time.monotonic()
        _wait(lambda: fake.snapshot()["aborted"] >= 1, 12, "the upstream request was not aborted")
        assert time.monotonic() - started < 6 + 5  # the waiter notices within 5 s, the relay within a poll
        assert _join_executor(*running, timeout_s=60) == 0
    finally:
        if running[1].is_alive():
            running[0].request_stop("test cleanup")
            running[1].join(timeout=30)
    job = GenerationJob.objects.get(experiment=experiment)
    assert job.status == GenerationJob.Status.CANCELLED
    experiment.refresh_from_db()
    assert experiment.status == Experiment.Status.CANCELLED


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
        row = LLMRequest.objects.get(endpoint=endpoint, source="scaffolding")
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
    job = GenerationJobFactory(
        model=model,
        created_by=owner,
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.RUNNING,
        phase=GenerationJob.Phase.RUNNING,
        lease_expires_at=timezone.now() + timedelta(minutes=10),
        resolved_bundle={
            "mode": "agent",
            "endpoint": endpoint_section(model),
            "llm": {"model_slug": model.model_id},
            "agent": {"wall_clock_seconds": 600},
        },
    )
    server = proxy_server.make_server("127.0.0.1:0")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        gateway_port = sock.getsockname()[1]
    config = {
        "upstream": f"http://127.0.0.1:{server.server_address[1]}/v1",
        "model": model.model_id,
        "forced_params": {"seed": 77, "max_tokens": 128},
        "stripped_params": ["seed", "provider"],
        "budget": {"max_requests": 5},
        "port": gateway_port,
        "log_path": str(tmp_path / "calls.jsonl"),
        "events_path": str(tmp_path / "events.jsonl"),
    }
    env = {
        **os.environ,
        "GATEWAY_CONFIG": json.dumps(config),
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
