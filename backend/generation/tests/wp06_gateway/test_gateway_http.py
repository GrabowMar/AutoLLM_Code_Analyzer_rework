"""The gateway over HTTP (spec 6.6): routes, native error shapes, budgets, streaming, logs, state.json."""

from __future__ import annotations

import json
import re
import threading
import time

import pytest

from backend.generation import identity
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import budget_rules
from backend.generation.tests.wp06_gateway.support import chat_body
from backend.generation.tests.wp06_gateway.support import chat_completion
from backend.generation.tests.wp06_gateway.support import chat_sse
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import gateway_module
from backend.generation.tests.wp06_gateway.support import http_request
from backend.generation.tests.wp06_gateway.support import read_jsonl
from backend.generation.tests.wp06_gateway.support import run_gateway

CHAT = "/api/v1/chat/completions"
# Phrases harnesses (Claude Code, Codex, opencode, aider/litellm, qwen, goose) treat as "context overflow,
# compact and retry". A budget refusal must match none of them.
CONTEXT_OVERFLOW = re.compile(
    r"context[ _-]?(length|window)|prompt is too long|too many tokens|maximum context|reduce the length"
    r"|exceeds? the (context|maximum)|input is too long|token limit",
    re.IGNORECASE,
)
ANTHROPIC = {
    "front_protocol": "anthropic_messages",
    "protocol_mode": "passthrough",
    "request_class_source": "x-claude-code-request-class",
    "model": {"upstream_model": "anthropic/claude-sonnet-4.6"},
}
RESPONSES = {
    "front_protocol": "openai_responses",
    "protocol_mode": "passthrough",
    "model": {"upstream_model": "openai/gpt-5.1-codex"},
}
FRONTS = {
    "openai_chat": ({}, CHAT, chat_body()),
    "anthropic_messages": (
        ANTHROPIC,
        "/v1/messages?beta=true",
        {"model": "claude", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
    ),
    "openai_responses": (RESPONSES, "/api/v1/responses", {"model": "codex", "input": "hi", "store": False}),
}


def _error_message(front: str, payload: dict) -> str:
    return payload["error"]["message"]


def _assert_native_error_shape(front: str, payload: dict) -> None:
    if front == "anthropic_messages":
        assert payload["type"] == "error"
        assert set(payload["error"]) == {"type", "message"}
    else:
        assert set(payload) == {"error"}
        assert {"message", "type", "code"} <= set(payload["error"])


# =====================================================================================
# Round trips and the call record
# =====================================================================================


def test_chat_round_trip_and_the_call_record(tmp_path):
    body = chat_body(messages=[{"role": "system", "content": "SYS PROMPT"}, {"role": "user", "content": "go"}])
    reply = Reply(body=chat_completion(prompt=1234, completion=56, cost=0.0042, cached=1000, reasoning=10))
    with run_gateway(tmp_path, replies=[reply]) as gw:
        resp = gw.request(CHAT, body)
        second = gw.request(CHAT, body)
        records = gw.calls(2)
        state = gw.settled()
        upstream_body = gw.upstream.received[0].body
    assert resp.status == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Done."
    assert second.status == 200
    record = records[0]
    assert set(canned_call()) <= set(record)  # the WP-01 FakeGateway record keys, plus v3 extras
    assert record["usage"] == {
        "prompt_tokens": 1234,
        "completion_tokens": 56,
        "reasoning_tokens": 10,
        "cached_tokens": 1000,
        "cache_write_tokens": None,
        "cost": 0.0042,
    }
    assert (record["cost"], record["cost_source"]) == (0.0042, "usage.cost")
    assert record["list_cost"] == pytest.approx((1234 * 1.0 + 56 * 2.0) / 1e6)
    assert record["served_provider"] == "DeepInfra"
    assert record["response_model"] == "deepseek/deepseek-v4.1-flash"
    assert record["system_fingerprint"] == "fp_1"
    assert record["requested_model"] == "whatever-the-harness-says"
    assert record["upstream_model"] == upstream_body["model"] == "deepseek/deepseek-v4.1-flash"
    assert record["native_request_sha"] == identity.H(body)
    assert record["native_system_prompt"] == "SYS PROMPT"
    assert record["native_system_prompt_sha"] == identity.text_sha("SYS PROMPT")
    assert (gw.logs / "blobs" / f"{identity.text_sha('SYS PROMPT')}.txt").read_text() == "SYS PROMPT"
    assert records[1]["native_system_prompt"] is None  # only the first main call
    assert record["request"] == upstream_body
    assert record["upstream_max_tokens"] == upstream_body["max_tokens"] == 1024
    assert record["ttft_ms"] is not None
    assert state["requests_by_class"]["main"] == 2
    assert state["cost"] == pytest.approx(0.0042 + 0.0001)
    assert state["last_call_end"] is not None
    assert set(state) == set(budget_rules.STATE_KEYS)


def test_chat_stream_is_relayed_unchanged(tmp_path):
    events = chat_sse("Hello world", prompt=70, completion=3, cost=0.00003)
    with run_gateway(tmp_path, replies=[Reply(events=events)]) as gw:
        resp = gw.request(CHAT, chat_body(stream=True))
        record = gw.calls(1)[0]
        sent = gw.upstream.received[0].body
    assert resp.status == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.raw == b"".join(events)
    assert sent["stream_options"] == {"include_usage": True}
    assert record["stream"] is True
    assert record["response"]["content"] == "Hello world"
    assert (record["usage"]["prompt_tokens"], record["usage"]["cost"]) == (70, 0.00003)
    assert record["finish_reason"] == "stop"


def test_pings_keep_a_silent_upstream_alive_before_the_first_byte(tmp_path):
    events = chat_sse()
    with run_gateway(tmp_path, replies=[Reply(events=events, delay_s=1.0)], ping_interval_s=0.2) as gw:
        resp = gw.request(CHAT, chat_body(stream=True))
    assert resp.status == 200
    pings = resp.raw.count(b": ping\n\n")
    assert pings >= 3
    assert resp.chunks[0][1] == b": ping\n\n"
    assert resp.chunks[0][0] < 0.8  # the harness saw bytes long before the upstream answered
    assert resp.raw.replace(b": ping\n\n", b"") == b"".join(events)


@pytest.mark.parametrize(
    ("front", "ping"),
    [("anthropic_messages", b'event: ping\ndata: {"type": "ping"}\n\n'), ("openai_responses", b": keepalive\n\n")],
)
def test_pings_are_protocol_native_and_only_between_events(tmp_path, front, ping):
    overrides, path, body = FRONTS[front]
    if front == "anthropic_messages":
        payloads = [
            {"type": "message_start", "message": {"id": "m", "model": "x", "usage": {"input_tokens": 5}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        events = [f"event: {p['type']}\ndata: {json.dumps(p)}\n\n".encode() for p in payloads]
    else:
        done = {
            "type": "response.completed",
            "response": {
                "id": "r",
                "object": "response",
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        }
        events = [
            b'event: response.created\ndata: {"type": "response.created", "response": {"id": "r"}}\n\n',
            b'event: response.output_text.delta\ndata: {"type": "response.output_text.delta", "delta": "hi"}\n\n',
            f"event: response.completed\ndata: {json.dumps(done)}\n\n".encode(),
        ]
    # Split one event across two chunks: a ping must never land inside it.
    split = [events[0], events[1][:20], events[1][20:], *events[2:]]
    reply = Reply(events=split, chunk_delay_s=0.35)
    with run_gateway(tmp_path, replies=[reply], ping_interval_s=0.1, **overrides) as gw:
        resp = gw.request(path, {**body, "stream": True})
        record = gw.calls(1)[0]
    assert resp.status == 200
    assert resp.raw.count(ping) >= 3
    assert resp.raw.replace(ping, b"") == b"".join(events)
    assert record["usage"]["prompt_tokens"] == 5
    assert record["usage"]["completion_tokens"] == 2


# =====================================================================================
# Budgets: refusals in the native shape
# =====================================================================================


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_budget_exhaustion_is_a_native_400_that_never_looks_like_context_overflow(tmp_path, front):
    overrides, path, body = FRONTS[front]
    cfg_budget = {"max_llm_requests": 1}
    with run_gateway(tmp_path, default=lambda r: Reply(body=_ok_payload(front)), budget=cfg_budget, **overrides) as gw:
        first = gw.request(path, body)
        refused = gw.request(path, body)
        again = gw.request(path, body)
        records = gw.calls(1)
        events = gw.events()
        state = gw.settled()
        upstream_calls = len(gw.upstream.received)
    assert first.status == 200, first.raw
    for resp in (refused, again):
        assert resp.status == 400
        payload = resp.json()
        _assert_native_error_shape(front, payload)
        message = _error_message(front, payload)
        assert message == budget_rules.BUDGET_MESSAGE
        assert not CONTEXT_OVERFLOW.search(json.dumps(payload))
    assert upstream_calls == 1
    assert len(records) == 1  # refused calls are not call records
    assert state["budget_exhausted"] == "budget_exhausted:requests"
    kinds = [e["event"] for e in events]
    assert kinds.count("budget_exhausted") == 1
    assert kinds.count("refused") == 2


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_max_input_tokens_refusal_reads_as_context_overflow_and_keeps_the_run_going(tmp_path, front):
    overrides, path, body = FRONTS[front]
    big = json.loads(json.dumps(body))
    if front == "openai_responses":
        big["input"] = "x" * 3600
    else:
        big["messages"][0]["content"] = "x" * 3600  # 1000 tokens
    with run_gateway(
        tmp_path,
        default=lambda r: Reply(body=_ok_payload(front)),
        call={"max_output_tokens": 100, "max_input_tokens": 900},
        **overrides,
    ) as gw:
        refused = gw.request(path, big)
        ok = gw.request(path, body)
        state = gw.settled()
    assert refused.status == 400
    _assert_native_error_shape(front, refused.json())
    assert CONTEXT_OVERFLOW.search(_error_message(front, refused.json()))
    assert ok.status == 200
    assert state["budget_exhausted"] is None


def test_cost_cap_refuses_before_forwarding_and_never_clamps(tmp_path):
    # Worst case per call: 1 prompt token x $1/M + 100k max_tokens x $2/M ~= $0.2; cap $0.5; each call bills $0.2.
    with run_gateway(
        tmp_path,
        default=lambda r: Reply(body=chat_completion(cost=0.2)),
        budget={"max_cost_usd": 0.5},
        call={"max_output_tokens": 100_000},
        model={"upstream_model": "m/x", "max_output": 200_000},
    ) as gw:
        statuses = [gw.request(CHAT, chat_body("hi")).status for _ in range(4)]
        sent = [r.body["max_tokens"] for r in gw.upstream.received]
        state = gw.settled()
    # After $0.4 a third call could cost up to $0.2 more: refused, although it might have needed far less.
    assert statuses == [200, 200, 400, 400]
    assert set(sent) == {100_000}
    assert state["budget_exhausted"] == "budget_exhausted:cost"
    assert state["cost"] <= 0.5


def test_concurrent_calls_never_overshoot_the_cost_cap(tmp_path):
    """Spec 13.4 (WP-06) over HTTP: eight calls in flight at once, the cap fits three worst cases."""
    hold = threading.Event()
    worst = (2 * 1.0 + 100_000 * 2.0) / 1e6  # 'hi' is 1 token; generous rounding below
    cap = 3 * worst + 1e-4

    def reply(_received):
        return Reply(body=chat_completion(prompt=2, completion=100_000, cost=worst - 1e-9), hold=hold)

    with run_gateway(
        tmp_path,
        default=reply,
        budget={"max_cost_usd": cap, "max_llm_requests": 100},
        call={"max_output_tokens": 100_000},
        model={"upstream_model": "m/x", "max_output": 200_000},
    ) as gw:
        results: list[int] = []
        lock = threading.Lock()

        def call():
            status = gw.request(CHAT, chat_body("hi")).status
            with lock:
                results.append(status)

        threads = [threading.Thread(target=call) for _ in range(8)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 5
        while len([s for s in results if s == 400]) < 5 and time.monotonic() < deadline:
            time.sleep(0.02)
        in_flight = gw.state()["inflight"]
        hold.set()
        for t in threads:
            t.join()
        state = gw.settled()
    assert in_flight == 3
    assert sorted(results) == [200] * 3 + [400] * 5
    assert state["cost"] <= cap


def test_truncated_calls_are_counted(tmp_path):
    replies = [
        Reply(body=chat_completion(finish="length")),
        # Empty answer that spent the whole cap thinking: a truncated call, never a clean one.
        Reply(body=chat_completion(content="", completion=1024, reasoning=1024, finish="stop")),
        Reply(body=chat_completion()),
    ]
    with run_gateway(tmp_path, replies=replies) as gw:
        for _ in range(3):
            gw.request(CHAT, chat_body())
        records = gw.calls(3)
        state = gw.settled()
    assert [r["truncated"] for r in records] == [True, True, False]
    assert state["call_truncations"] == 2


def test_success_without_usage_is_charged_at_its_reservation(tmp_path):
    payload = chat_completion()
    payload.pop("usage")
    with run_gateway(tmp_path, replies=[Reply(body=payload)]) as gw:
        gw.request(CHAT, chat_body())
        record = gw.calls(1)[0]
    assert record["usage_estimated"] is True
    assert record["cost"] == record["reserved_usd"] > 0
    assert record["cost_source"] == "reservation"


def test_a_stream_dropped_mid_way_is_charged_at_its_reservation(tmp_path):
    """The upstream billed the call (chat usage only arrives in the last chunk): never a silent zero."""
    events = chat_sse("Hello world")[:3]  # the comment, the first two deltas; no usage, no [DONE]
    with run_gateway(tmp_path, replies=[Reply(events=events, drop=True)]) as gw:
        resp = gw.request(CHAT, chat_body(stream=True))
        record = gw.calls(1)[0]
        stats = gw.gateway.ledger.stats()
    assert resp.status == 200
    assert record["error"]
    assert record["usage_estimated"] is True
    assert record["cost_source"] == "reservation"
    assert record["cost"] == record["reserved_usd"] > 0
    assert stats["failed_by_class"]["main"] == 1
    assert stats["requests_by_class"]["main"] == 0
    assert stats["cost"] == pytest.approx(record["reserved_usd"])


def test_an_unreadable_2xx_body_is_charged_at_its_reservation(tmp_path):
    with run_gateway(tmp_path, replies=[Reply(body=chat_completion(), drop=True)]) as gw:
        resp = gw.request(CHAT, chat_body())
        record = gw.calls(1)[0]
    assert resp.status == 502
    assert record["transport_error"] is True
    assert (record["usage_estimated"], record["cost_source"]) == (True, "reservation")
    assert record["cost"] == record["reserved_usd"] > 0


def test_a_gateway_error_after_the_upstream_answered_is_charged_at_its_reservation(tmp_path, monkeypatch):
    with run_gateway(tmp_path, replies=[Reply(body=chat_completion())]) as gw:

        def boom(*_args, **_kwargs):
            msg = "summary failed"
            raise RuntimeError(msg)

        monkeypatch.setattr(gw.gateway, "_summary", boom)
        gw.request(CHAT, chat_body(), timeout=5)
        record = gw.calls(1)[0]
    assert record["transport_error"] is True
    assert "summary failed" in record["error"]
    assert (record["usage_estimated"], record["cost_source"]) == (True, "reservation")


def test_request_classes_count_separately(tmp_path):
    overrides, path, body = FRONTS["anthropic_messages"]
    with run_gateway(
        tmp_path,
        default=lambda r: Reply(body=_ok_payload("anthropic_messages")),
        budget={"max_llm_requests": 2, "max_aux_requests": 1},
        **overrides,
    ) as gw:
        heads = [{"x-claude-code-request-class": c} for c in ("main", "compaction", "main", "subagent", "main")]
        statuses = [gw.request(path, body, headers=h).status for h in heads]
        records = gw.calls(3)
        state = gw.settled()
    assert statuses == [200, 200, 200, 400, 400]
    assert [r["request_class"] for r in records] == ["main", "compaction", "main"]
    assert records[1]["attribution"] == {"x-claude-code-request-class": "compaction"}
    assert state["requests_by_class"] == {"main": 2, "subagent": 0, "compaction": 1, "auxiliary": 0}
    assert state["budget_exhausted"] == "budget_exhausted:aux_requests"


# =====================================================================================
# Upstream failures and retries
# =====================================================================================


def test_transient_failures_are_retried_before_the_first_byte(tmp_path):
    replies = [
        Reply(status=429, body={"error": {"message": "rate"}}),
        Reply(status=503, body={}),
        Reply(body=chat_completion()),
    ]
    with run_gateway(
        tmp_path,
        replies=replies,
        retries={"upstream_retries": 3, "backoff_s": 0.01, "retry_on": ["429", "5xx"]},
    ) as gw:
        resp = gw.request(CHAT, chat_body())
        record = gw.calls(1)[0]
        state = gw.settled()
    assert resp.status == 200
    assert record["upstream_retries"] == 2
    assert state["requests_by_class"]["main"] == 1


def test_a_final_upstream_error_passes_through_and_is_a_failed_call(tmp_path):
    error = {"error": {"message": "Provider returned error", "code": 500}}
    replies = [Reply(status=500, body=error)] * 2
    with run_gateway(
        tmp_path,
        replies=replies,
        retries={"upstream_retries": 1, "backoff_s": 0.01, "retry_on": ["5xx"]},
    ) as gw:
        resp = gw.request(CHAT, chat_body())
        record = gw.calls(1)[0]
        stats = gw.request("/stats", method="GET").json()
    assert (resp.status, resp.json()) == (500, error)
    assert record["status"] == 500
    assert record["error"] is not None
    assert record["finish_reason"] is None
    assert stats["failed_by_class"]["main"] == 1
    assert stats["requests_by_class"]["main"] == 0


def test_unreachable_upstream_is_a_native_502(tmp_path):
    cfg = config_dict(
        "http://127.0.0.1:9/api/v1",
        retries={"upstream_retries": 1, "backoff_s": 0.01, "retry_on": ["5xx"]},
    )
    gw = gateway_module.Gateway(cfg, log_dir=tmp_path / "logs", api_key="k")
    server = gw.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        resp = http_request(server.server_address[1], CHAT, chat_body())
        stream = http_request(server.server_address[1], CHAT, chat_body(stream=True))
    finally:
        server.shutdown()
        server.server_close()
    assert resp.status == 502
    assert resp.json()["error"]["code"] == "upstream_error"
    assert stream.status == 502
    records = read_jsonl(tmp_path / "logs" / "calls.jsonl")
    assert [r["transport_error"] for r in records] == [True, True]
    assert gw.ledger.snapshot()["cost"] == 0.0


# =====================================================================================
# Routes and request checks
# =====================================================================================


def test_routes_outside_the_front_protocol_are_404(tmp_path):
    with run_gateway(tmp_path) as gw:
        messages = gw.request("/v1/messages", {"messages": []})
        hello = gw.request("/api/hello", method="HEAD")
        models = gw.request("/v1/models", method="GET")
        health = gw.request("/healthz", method="GET")
        events = gw.events()
    assert messages.status == 404
    assert messages.json()["error"]["code"] == "not_found"
    assert hello.status == 404
    assert models.json() == {
        "object": "list",
        "data": [{"id": "deepseek-v4.1-flash", "object": "model", "owned_by": "llm-gateway"}],
    }
    assert health.json()["ok"] is True
    assert [e["event"] for e in events].count("rejected_path") == 2


@pytest.mark.parametrize("front", sorted(FRONTS))
def test_images_are_rejected(tmp_path, front):
    overrides, path, body = FRONTS[front]
    image = {"type": "input_image" if front == "openai_responses" else "image", "source": {"data": "..."}}
    with_image = json.loads(json.dumps(body))
    if front == "openai_responses":
        with_image["input"] = [{"role": "user", "content": [image]}]
    else:
        with_image["messages"][0]["content"] = [image]
    with run_gateway(tmp_path, **overrides) as gw:
        resp = gw.request(path, with_image)
        assert gw.upstream.received == []
    assert resp.status == 400
    _assert_native_error_shape(front, resp.json())


def test_responses_previous_response_id_is_refused(tmp_path):
    overrides, path, body = FRONTS["openai_responses"]
    with run_gateway(tmp_path, **overrides) as gw:
        resp = gw.request(path, {**body, "previous_response_id": "resp_9"})
        assert gw.upstream.received == []
    assert resp.status == 400
    assert "stateless" in resp.json()["error"]["message"]


def test_bad_bodies(tmp_path):
    with run_gateway(tmp_path) as gw:
        conn_status = http_request(gw.port, CHAT, None).status  # empty body -> {} -> forwarded
        array = gw.request(CHAT, [1, 2])  # type: ignore[arg-type]
    assert conn_status == 200
    assert array.status == 400


# =====================================================================================
# state.json
# =====================================================================================


def test_state_json_is_rewritten_atomically(tmp_path):
    stop = threading.Event()
    errors: list[Exception] = []
    seen_inflight: set[int] = set()

    with run_gateway(
        tmp_path,
        default=lambda r: Reply(body=chat_completion(), delay_s=0.01),
        budget={"max_llm_requests": 200},
    ) as gw:
        path = gw.logs / "state.json"

        def reader():
            while not stop.is_set():
                try:
                    seen_inflight.add(json.loads(path.read_text())["inflight"])
                except Exception as exc:  # noqa: BLE001 - any partial read fails the test
                    errors.append(exc)

        thread = threading.Thread(target=reader)
        thread.start()
        workers = [
            threading.Thread(target=lambda: [gw.request(CHAT, chat_body()) for _ in range(10)]) for _ in range(4)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        stop.set()
        thread.join()
        state = gw.settled()
        leftovers = list(gw.logs.glob(".state.json.*"))
    assert errors == []
    assert max(seen_inflight) >= 1
    assert state["requests_by_class"]["main"] == 40
    assert state["inflight"] == 0
    assert leftovers == []


# =====================================================================================
# Rate limit
# =====================================================================================


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_rpm_token_bucket_spaces_calls():
    clock = _Clock()
    limiter = gateway_module.RateLimiter(60, clock=clock, sleep=clock.sleep)  # burst 5, then one per second
    waits = [limiter.acquire(timeout_s=60)[0] for _ in range(8)]
    assert waits[:5] == [0.0] * 5
    assert waits[5:] == pytest.approx([1.0, 1.0, 1.0])
    assert limiter.acquire(timeout_s=0.5) == (0.0, True)


def test_rpm_bucket_is_shared_through_a_directory(tmp_path):
    clock = _Clock()
    one = gateway_module.RateLimiter(12, "study-1:model", tmp_path, clock=clock, sleep=clock.sleep)  # burst 1
    two = gateway_module.RateLimiter(12, "study-1:model", tmp_path, clock=clock, sleep=clock.sleep)
    assert one.acquire(timeout_s=60) == (0.0, False)
    waited, timed_out = two.acquire(timeout_s=60)  # the other gateway took the only token
    assert not timed_out
    assert waited == pytest.approx(5.0)


def test_rate_limited_gateway_records_the_wait(tmp_path):
    with run_gateway(tmp_path, rate_limit={"rpm_per_model": 60}) as gw:
        for _ in range(6):
            gw.request(CHAT, chat_body())
        records = gw.calls(6)
    assert records[-1]["rate_wait_s"] > 0.5


# =====================================================================================
# Startup
# =====================================================================================


def test_unrunnable_configs_exit_78_with_an_event(tmp_path):
    cfg = config_dict("http://x/api/v1", front_protocol="gemini_generate", protocol_mode="passthrough")
    env = {"GATEWAY_CONFIG": json.dumps(cfg), "GATEWAY_LOG_DIR": str(tmp_path)}
    assert gateway_module.main(env) == gateway_module.EX_CONFIG
    events = read_jsonl(tmp_path / "gateway_events.jsonl")
    assert events[0]["event"] == "config_invalid"
    assert "does not run in protocol_mode 'passthrough'" in events[0]["error"]


def test_local_upstream_needs_the_overlay(tmp_path, monkeypatch):
    monkeypatch.delitem(gateway_module.UPSTREAMS, "local_proxy", raising=False)
    cfg = config_dict("http://x/v1", upstream={"kind": "local_proxy"})
    with pytest.raises(ValueError, match="no upstream adapter"):
        gateway_module.Gateway(cfg, log_dir=tmp_path)


def _ok_payload(front: str) -> dict:
    if front == "anthropic_messages":
        return {
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": "anthropic/claude-sonnet-4.6",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    if front == "openai_responses":
        return {
            "id": "resp",
            "object": "response",
            "status": "completed",
            "model": "openai/gpt-5.1-codex",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    return chat_completion()


def test_sandbox_runtime_extras_in_the_config(tmp_path):
    cfg = config_dict("http://x/api/v1")
    cfg |= {
        "listen": {"host": "0.0.0.0", "port": 18080},  # noqa: S104 - the sandbox's extra, never bound here
        "connect_allowlist": ["registry.npmjs.org", "pypi.org"],
    }
    gw = gateway_module.from_env({"GATEWAY_CONFIG": json.dumps(cfg), "GATEWAY_LOG_DIR": str(tmp_path)})
    assert gw.connect_allowed("PyPI.org", 443)
    assert not gw.connect_allowed("pypi.org", 80)
    assert not gw.connect_allowed("evil.example", 443)
    env_wins = gateway_module.from_env(
        {"GATEWAY_CONFIG": json.dumps(cfg), "GATEWAY_LOG_DIR": str(tmp_path), "GATEWAY_CONNECT_ALLOWLIST": "a.org"},
    )
    assert env_wins.connect_allowlist == frozenset({"a.org"})


def test_connect_outside_the_allowlist_is_refused(tmp_path):
    with run_gateway(tmp_path) as gw:
        resp = gw.request("evil.example:443", method="CONNECT")
        events = gw.events()
    assert resp.status == 403
    assert events[-1] == {**events[-1], "event": "rejected_connect", "target": "evil.example:443"}


def _cache_server():
    from http.server import BaseHTTPRequestHandler
    from http.server import ThreadingHTTPServer

    seen: list[tuple[str, str, dict]] = []

    class Cache(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def do_GET(self):
            seen.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}))
            data = b"tarball-bytes" * 1000
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Cache)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server, seen


def test_forward_proxy_reaches_exactly_the_dependency_caches(tmp_path):
    import http.client

    cache, seen = _cache_server()
    cache_port = cache.server_address[1]
    cfg = config_dict("http://x/api/v1")
    cfg["proxy_hosts"] = [f"127.0.0.1:{cache_port}"]
    gw = gateway_module.from_env({"GATEWAY_CONFIG": json.dumps(cfg), "GATEWAY_LOG_DIR": str(tmp_path)})
    server = gw.make_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request(
            "GET",
            f"http://127.0.0.1:{cache_port}/flask/-/flask-3.1.tgz?x=1",
            headers={"Proxy-Connection": "keep-alive"},
        )
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        denied = http_request(server.server_address[1], f"http://127.0.0.1:{cache_port + 1}/x", method="GET")
        put = http_request(server.server_address[1], f"http://127.0.0.1:{cache_port}/publish", {"a": 1}, method="PUT")
        assert gw.connect_allowed("127.0.0.1", cache_port)  # CONNECT to the cache port too
    finally:
        server.shutdown()
        server.server_close()
        cache.shutdown()
        cache.server_close()
    assert resp.status == 200
    assert data == b"tarball-bytes" * 1000
    assert seen[0][:2] == ("GET", "/flask/-/flask-3.1.tgz?x=1")
    assert "proxy-connection" not in seen[0][2]
    assert denied.status == 403
    assert put.status == 501  # no PUT handler at all: caches are read-only
    events = [e["event"] for e in read_jsonl(tmp_path / "gateway_events.jsonl")]
    assert "proxy" in events
    assert "rejected_proxy" in events
