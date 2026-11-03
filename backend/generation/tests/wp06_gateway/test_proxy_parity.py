"""Local proxy parity (spec 6.6): ``inference/proxy_server.py`` enforces the run budget with the gateway's
own ``budget_rules.py``, so the same scripted calls exhaust the run at the same point on both."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone

from backend.generation.contracts.resources import preset_content
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import budget_rules
from backend.generation.tests.wp06_gateway.support import chat_body
from backend.generation.tests.wp06_gateway.support import chat_completion
from backend.generation.tests.wp06_gateway.support import common
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import openai_chat
from backend.generation.tests.wp06_gateway.support import run_gateway
from backend.inference import proxy_server

CHAT = "/api/v1/chat/completions"
# (request class, prompt chars, prompt tokens billed, completion tokens, cost)
SCRIPT = [
    ("main", 400, 120, 300, 0.002),
    ("main", 3000, 900, 800, 0.004),
    ("compaction", 6000, 1800, 500, 0.003),
    ("main", 2000, 700, 1500, 0.006),
    ("main", 2500, 800, 1200, 0.005),
    ("main", 2600, 820, 1100, 0.005),
    ("main", 2700, 830, 1000, 0.005),
    ("main", 2800, 840, 900, 0.005),
]
LIMITS = {"max_cost_usd": 0.03, "max_llm_requests": 20, "max_aux_requests": 5, "max_total_tokens": 1_000_000}


def _config() -> dict:
    return config_dict(
        "http://placeholder/api/v1",
        budget=LIMITS,
        call={"max_output_tokens": 2000},
        pricing={"input_per_mtok": 1.0, "output_per_mtok": 2.0, "source": "test", "as_of": "2026-11-01"},
        request_class_source="x-llmlab-request-class",
    )


def _body(chars: int) -> dict:
    return chat_body("x" * chars)


def _payload(prompt: int, completion: int, cost: float) -> dict:
    return chat_completion(prompt=prompt, completion=completion, cost=cost)


def _gateway_outcomes(tmp_path) -> tuple[list[int], dict]:
    script = iter(SCRIPT)
    replies = [Reply(body=_payload(p, c, cost)) for _cls, _chars, p, c, cost in SCRIPT]
    statuses = []
    with run_gateway(tmp_path, replies=replies, config=_config()) as gw:
        for cls, chars, *_ in script:
            statuses.append(gw.request(CHAT, _body(chars), headers={"X-LLMLab-Request-Class": cls}).status)
        state = gw.settled()
    return statuses, state


def _proxy_outcomes() -> tuple[list[int], dict]:
    cfg = _config()
    policy = common.Policy.from_config(cfg)
    budgets = proxy_server.ProxyBudgets(config_loader=lambda job_id: cfg)
    job = str(uuid.uuid4())
    statuses = []
    for index, (cls, chars, prompt, completion, cost) in enumerate(SCRIPT, start=1):
        # What the proxy receives: the gateway's policy-applied body and the overlay's headers.
        upstream_body, _ = openai_chat.apply_policy_native(_body(chars), policy)
        headers = {"X-LLMLab-Request-Class": cls, "X-LLMLab-Call-Index": str(index)}
        key, refusal = budgets.admit(job, 1, upstream_body, headers, dedupe=f"d{index}")
        if refusal is not None:
            statuses.append(400)
            continue
        budgets.settle(job, 1, key, 200, _payload(prompt, completion, cost))
        statuses.append(200)
    return statuses, budgets.state(job, 1)


def _comparable(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in ("updated", "last_call_end")}


def test_same_script_same_exhaustion_point(tmp_path):
    gateway_statuses, gateway_state = _gateway_outcomes(tmp_path)
    proxy_statuses, proxy_state = _proxy_outcomes()
    assert 400 in gateway_statuses  # the script does exhaust the run
    assert gateway_statuses == proxy_statuses
    assert _comparable(gateway_state) == _comparable(proxy_state)
    assert proxy_state["budget_exhausted"] == "budget_exhausted:cost"


def test_one_source_file_for_both():
    module = proxy_server.budget_rules()
    assert Path(budget_rules.__file__).resolve() == proxy_server.BUDGET_RULES_PATH
    assert module.RULES_VERSION == budget_rules.RULES_VERSION
    assert module.BUDGET_MESSAGE == budget_rules.BUDGET_MESSAGE
    assert module.__file__ == str(proxy_server.BUDGET_RULES_PATH)


def test_a_retry_with_the_same_call_index_reserves_once():
    cfg = _config()
    budgets = proxy_server.ProxyBudgets(config_loader=lambda job_id: cfg)
    job = str(uuid.uuid4())
    headers = {"X-LLMLab-Call-Index": "1"}
    first, _ = budgets.admit(job, 1, chat_body(max_tokens=2000), headers, dedupe="a")
    again, _ = budgets.admit(job, 1, chat_body(max_tokens=2000), headers, dedupe="a")
    assert first == again
    assert budgets.state(job, 1)["inflight"] == 1
    budgets.settle(job, 1, first, 200, _payload(10, 10, 0.0))
    assert budgets.state(job, 1)["inflight"] == 0


def test_budget_config_from_a_frozen_manifest():
    content = preset_content("frugal").model_dump(mode="json")
    manifest = {
        "model": {
            "endpoint": {"id": 3, "kind": "ollama", "cost_basis": "free"},
            "pricing": {"input_per_mtok": 0.1, "output_per_mtok": 0.3, "source": "list", "as_of": "2026-11-01"},
        },
        "resources": {"effective": {"content": content}},
    }
    cfg = proxy_server.budget_config_from_manifest(manifest)
    assert cfg["budget"]["max_llm_requests"] == content["run"]["max_llm_requests"]
    assert cfg["budget"]["cost_basis"] == "free"
    assert cfg["call"]["max_output_tokens"] == content["call"]["max_output_tokens"]
    limits = budget_rules.BudgetLimits.from_config(cfg)
    assert limits.max_total_tokens == content["run"]["max_total_tokens"]
    bare = {**manifest, "resources": {"effective": content}}
    assert proxy_server.budget_config_from_manifest(bare) == cfg
    assert proxy_server.budget_config_from_manifest({"resources": {"effective": {"content": {"time": {}}}}}) is None


# =====================================================================================
# The proxy handler refuses at the cap (DB, real proxy server)
# =====================================================================================


@pytest.fixture
def proxy(settings):
    settings.INFERENCE_REDIS_PREFIX = f"test-{uuid.uuid4().hex[:10]}:"
    proxy_server._auth_cache._jobs.clear()
    proxy_server._budgets.clear()
    server = proxy_server.make_server("127.0.0.1:0")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _completer(endpoint_id, stop: threading.Event, handled: list) -> None:
    from backend.inference import requests as q

    try:
        while not stop.is_set():
            rows = q.claim(endpoint_id, limit=1, pump_owner="fake")
            if not rows:
                time.sleep(0.05)
                continue
            row = rows[0]
            handled.append(row.pk)
            q.complete(
                row.pk,
                row.claim_token,
                pump_owner="fake",
                ok=True,
                status_code=200,
                payload={
                    "message": {"role": "assistant", "content": "ok"},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 11,
                    "eval_count": 4,
                },
            )
    finally:
        connection.close()


def _post(base: str, token: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(  # noqa: S310
        base + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.mark.django_db(transaction=True)
def test_proxy_refuses_past_the_run_cap(proxy, user):
    from backend.generation.models.choices import RunPhase
    from backend.generation.models.choices import RunStatus
    from backend.generation.tests.factories import GenerationRunFactory
    from backend.generation.tests.factories import RunManifestFactory
    from backend.generation.tests.factories import run_manifest_content
    from backend.inference.gateway_wiring import proxy_token
    from backend.inference.models import LLMRequest
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model

    endpoint = make_endpoint(user)
    model = make_local_model(endpoint)
    run = GenerationRunFactory(
        model=model,
        created_by=user,
        status=RunStatus.RUNNING,
        phase=RunPhase.RUNNING,
        attempt=1,
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )
    manifest = run_manifest_content(run)
    manifest["model"]["endpoint"] = {
        "id": endpoint.pk,
        "kind": endpoint.kind,
        "served_model_name": model.served_model_name,
        "digest": model.digest,
        "cost_basis": "free",
    }
    content = preset_content("frugal").model_dump(mode="json")
    content["run"]["max_llm_requests"] = 1
    content["run"]["max_aux_requests"] = 0
    manifest["resources"]["effective"] = {"content": content}
    RunManifestFactory(run=run, manifest=manifest)
    token = proxy_token(run)
    stop = threading.Event()
    handled: list = []
    worker = threading.Thread(target=_completer, args=(endpoint.pk, stop, handled), daemon=True)
    worker.start()
    try:
        first = _post(proxy, token, chat_body("go", max_tokens=256), {"X-LLMLab-Call-Index": "1"})
        second = _post(proxy, token, chat_body("again", max_tokens=256), {"X-LLMLab-Call-Index": "2"})
    finally:
        stop.set()
        worker.join(timeout=5)
    assert first[0] == 200, first
    assert second == (
        400,
        {
            "error": {
                "message": budget_rules.BUDGET_MESSAGE,
                "code": "budget_exhausted",
                "type": "budget_exhausted",
                "status": 400,
            },
        },
    )
    assert LLMRequest.objects.filter(job=run).count() == 1
    state = proxy_server._budgets.state(str(run.pk), 1)
    assert state["budget_exhausted"] == "budget_exhausted:requests"
    assert state["requests_by_class"]["main"] == 1
    assert state["cost"] == 0.0  # a free endpoint never accrues USD...
    assert state["prompt_tokens"] == 11  # ...but its tokens count


@pytest.mark.django_db(transaction=True)
def test_local_overlay_through_the_real_proxy(proxy, user, tmp_path):
    """The whole local path in v3 form: harness -> llm-gateway-local (subprocess) -> executor proxy."""
    import os
    import socket
    import subprocess
    import sys
    import urllib.request

    from backend.generation.tests.wp06_gateway.support import GATEWAY_DIR
    from backend.generation.tests.wp06_gateway.support import LOCAL_DIR
    from backend.generation.tests.wp06_gateway.support import http_request
    from backend.generation.tests.wp06_gateway.support import read_jsonl
    from backend.inference.gateway_wiring import proxy_token
    from backend.inference.models import LLMRequest
    from backend.inference.tests.conftest import make_agent_run
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model

    endpoint = make_endpoint(user)
    model = make_local_model(endpoint)
    run = make_agent_run(model, user)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = config_dict(
        proxy + "/v1",
        run_id=str(run.pk),
        session_id=str(run.pk),
        upstream={"kind": "local_proxy", "endpoint_id": endpoint.pk},
        model={"upstream_model": model.model_id},
        sampling={"seed": 77},
        call={"max_output_tokens": 128},
        budget={"max_llm_requests": 1, "max_aux_requests": 0, "cost_basis": "free"},
    )
    env = {
        **os.environ,
        "GATEWAY_CONFIG": json.dumps(cfg),
        "GATEWAY_PORT": str(port),
        "GATEWAY_LOG_DIR": str(tmp_path / "logs"),
        "LLMLAB_UPSTREAM_TOKEN": proxy_token(run),
        "PYTHONPATH": str(GATEWAY_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.Popen([sys.executable, "-B", str(LOCAL_DIR / "local.py")], env=env)  # noqa: S603
    stop = threading.Event()
    handled: list = []
    worker = threading.Thread(target=_completer, args=(endpoint.pk, stop, handled), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        first = http_request(port, CHAT, chat_body("list files"), timeout=60)
        second = http_request(port, CHAT, chat_body("and again"), timeout=60)
    finally:
        stop.set()
        worker.join(timeout=5)
        proc.terminate()
        proc.wait(timeout=10)
    assert first.status == 200, first.raw
    assert first.json()["choices"][0]["message"]["content"] == "ok"
    assert second.status == 400
    assert second.json()["error"]["message"] == budget_rules.BUDGET_MESSAGE
    row = LLMRequest.objects.get(job=run)
    assert (row.source, row.status) == ("agent_proxy", "done")
    record = read_jsonl(tmp_path / "logs" / "calls.jsonl")[0]
    assert record["usage"]["prompt_tokens"] == 11
    # The gateway refused the second call itself; the proxy's backstop saw (and counted) only the first.
    backstop = proxy_server._budgets.state(str(run.pk), 1)
    if backstop is not None:
        assert backstop["requests_by_class"]["main"] == 1
        assert backstop["budget_exhausted"] is None
