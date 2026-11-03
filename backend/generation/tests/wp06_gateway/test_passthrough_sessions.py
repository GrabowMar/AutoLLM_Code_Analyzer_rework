"""Claude Code and Codex sessions in ``passthrough`` (WP-06 "done when", spec 5.4).

Each session fixture is a list of calls: the harness's request (path, headers, native body) and the
upstream's recorded native answer (SSE events or a JSON body), plus what the call record must show.
The gateway must forward the native body unchanged apart from the policy fields, relay the native
answer unchanged (pings aside), and account the native usage per request class.

Fixtures: ``fixtures/passthrough/*.json`` (recorded-shape sessions) and, when WP-07 has recorded smoke
runs, ``tests/wp07_harnesses/fixtures/gateway/*.json`` in the same format.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.generation.tests.wp06_gateway.support import FIXTURES
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import gateway_module
from backend.generation.tests.wp06_gateway.support import run_gateway

WP07_FIXTURES = Path(__file__).resolve().parents[1] / "wp07_harnesses" / "fixtures" / "gateway"
SESSIONS = sorted((FIXTURES / "passthrough").glob("*.json")) + sorted(WP07_FIXTURES.glob("*.json"))
POLICY_KEYS = {
    "anthropic_messages": ("model", "max_tokens", "provider", "session_id"),
    "openai_responses": ("model", "max_output_tokens", "provider", "session_id", "store", "include"),
}
UPSTREAM_MODEL = {"anthropic_messages": "anthropic/claude-sonnet-4.6", "openai_responses": "openai/gpt-5.1-codex"}
PROVIDER = {"order": ["anthropic", "openai"], "allow_fallbacks": False}


def _reply(upstream: dict) -> Reply:
    if "events" in upstream:
        events = [e.encode() for e in upstream["events"]]
        return Reply(status=upstream["status"], events=events, content_type=upstream.get("content_type"))
    return Reply(status=upstream["status"], body=upstream["body"], content_type=upstream.get("content_type"))


def _upstream_bytes(upstream: dict) -> bytes:
    if "events" in upstream:
        return b"".join(e.encode() for e in upstream["events"])
    return json.dumps(upstream["body"]).encode()


def _without(body: dict, keys) -> dict:
    return {k: v for k, v in body.items() if k not in keys}


def test_both_passthrough_harnesses_have_sessions():
    fronts = {json.loads(p.read_text())["front_protocol"] for p in SESSIONS}
    assert {"anthropic_messages", "openai_responses"} <= fronts


@pytest.mark.parametrize("path", SESSIONS, ids=lambda p: f"{p.parent.parent.name}:{p.stem}")
def test_session_passes_through(tmp_path, path):
    fixture = json.loads(path.read_text())
    front = fixture["front_protocol"]
    cfg = config_dict(
        "http://placeholder/api/v1",
        front_protocol=front,
        protocol_mode="passthrough",
        request_class_source=fixture["request_class_source"],
        model={"upstream_model": UPSTREAM_MODEL[front], "presented_names": ["claude-sonnet-4-5"], "max_output": 64000},
        budget={"max_cost_usd": 5.0, "max_llm_requests": 50, "max_aux_requests": 20, "max_total_tokens": 5_000_000},
        call={"max_output_tokens": 16384},
        routing={"provider": PROVIDER},
        caching={"prompt_cache": "on", "ttl": "5m", "sticky_session": True, "cache_control_providers": []},
    )
    replies = [_reply(call["upstream"]) for call in fixture["calls"]]
    with run_gateway(tmp_path, replies=replies, config=cfg, ping_interval_s=5.0) as gw:
        responses = [gw.request(c["path"], c["body"], headers=c.get("headers")) for c in fixture["calls"]]
        records = gw.calls(len(fixture["calls"]))
        state = gw.settled()
        received = list(gw.upstream.received)
        blobs = sorted((gw.logs / "blobs").glob("*.txt"))
    front_module = gateway_module.FRONTS[front]
    classes: dict[str, int] = {}
    for call, resp, got, record in zip(fixture["calls"], responses, received, records, strict=True):
        assert resp.status == call["upstream"]["status"], resp.raw
        # The native answer reaches the harness unchanged (keepalive pings aside).
        assert resp.raw.replace(front_module.PING, b"") == _upstream_bytes(call["upstream"])
        # The native body reaches the upstream unchanged apart from the policy fields, key order included.
        assert got.path == "/api/v1" + front_module.UPSTREAM_PATH
        sent = got.body
        keys = POLICY_KEYS[front]
        assert json.dumps(_without(sent, keys)) == json.dumps(_without(call["body"], keys))
        assert sent["model"] == UPSTREAM_MODEL[front]
        assert sent["provider"] == PROVIDER
        assert sent["session_id"] == cfg["session_id"]
        assert got.headers["authorization"] == "Bearer sk-real"
        assert "x-api-key" not in got.headers
        for name in getattr(front_module, "FORWARD_HEADERS", ()):
            if name in (call.get("headers") or {}):
                assert got.headers[name] == call["headers"][name]
        expect = call["expect"]
        assert record["front_protocol"] == front
        assert record["protocol_mode"] == "passthrough"
        assert record["status"] == 200
        assert record["finish_reason"] == expect["finish_reason"]
        assert record["request_class"] == expect["request_class"]
        assert record["usage"]["prompt_tokens"] == expect["prompt_tokens"]
        assert record["usage"]["completion_tokens"] == expect["completion_tokens"]
        assert record["usage"]["cost"] == expect["cost"]
        assert record["cost_source"] == ("usage.cost" if expect["cost"] is not None else "price_table")
        assert record["response_model"] == UPSTREAM_MODEL[front]
        classes[expect["request_class"]] = classes.get(expect["request_class"], 0) + 1
    assert {k: v for k, v in state["requests_by_class"].items() if v} == classes
    assert state["prompt_tokens"] == sum(c["expect"]["prompt_tokens"] for c in fixture["calls"])
    assert state["budget_exhausted"] is None
    first_main = next(r for r in records if r["request_class"] == "main")
    assert first_main["native_system_prompt"]
    assert [b.stem for b in blobs] == [first_main["native_system_prompt_sha"]]
    assert all(r["native_system_prompt"] is None for r in records if r is not first_main)


def test_claude_code_startup_probes(tmp_path):
    cfg = config_dict(
        "http://placeholder/api/v1",
        front_protocol="anthropic_messages",
        protocol_mode="passthrough",
        request_class_source="x-claude-code-request-class",
        model={"upstream_model": "anthropic/claude-sonnet-4.6", "presented_names": ["claude-sonnet-4-5"]},
    )
    with run_gateway(tmp_path, config=cfg) as gw:
        hello = gw.request("/api/hello", method="HEAD")
        models = gw.request("/v1/models?limit=1000", method="GET")
        count = gw.request(
            "/v1/messages/count_tokens?beta=true",
            {"model": "x", "messages": [{"role": "user", "content": "a" * 36}]},
        )
        assert gw.upstream.received == []
        assert gw.calls() == []
    assert hello.status == 404
    assert models.status == 200
    assert models.json()["data"][0] == {
        "type": "model",
        "id": "claude-sonnet-4-5",
        "display_name": "claude-sonnet-4-5",
        "created_at": "2026-01-01T00:00:00Z",
    }
    assert count.json() == {"input_tokens": 10, "estimate": True}
