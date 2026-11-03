"""The three ported harnesses' recorded sessions replay through the v3 gateway with byte-equal upstream
requests (WP-06 "done when"), apart from new policy fields.

``fixtures/v2_sessions/*.json`` were produced by running the v2 gateway's ``_rewrite`` (the frozen
``images/llm-gateway/gateway.py`` at commit 58a56e9) on recorded-shape opencode, mini-swe-agent and aider
bodies, under a ``provider_default`` and an ``explicit`` sampling policy. Each fixture carries the
equivalent v3 ``GatewayConfig`` sections. The test runs the full HTTP path and compares the bytes the
scripted upstream received with the bytes the v2 gateway sent.
"""

from __future__ import annotations

import json

import pytest

from backend.generation.tests.wp06_gateway.support import FIXTURES
from backend.generation.tests.wp06_gateway.support import Reply
from backend.generation.tests.wp06_gateway.support import chat_completion
from backend.generation.tests.wp06_gateway.support import chat_sse
from backend.generation.tests.wp06_gateway.support import config_dict
from backend.generation.tests.wp06_gateway.support import run_gateway

SESSIONS = sorted((FIXTURES / "v2_sessions").glob("*.json"))
# Fields v3 adds to every upstream body when the matching knob is on.
NEW_POLICY_FIELDS = ("session_id",)


def _reply(received):
    return Reply(events=chat_sse()) if received.body.get("stream") else Reply(body=chat_completion())


def test_every_ported_harness_has_fixtures():
    harnesses = {json.loads(p.read_text())["harness"] for p in SESSIONS}
    assert harnesses == {"opencode", "mini_swe_agent", "aider"}


@pytest.mark.parametrize("path", SESSIONS, ids=lambda p: p.stem)
@pytest.mark.parametrize("sticky", [False, True], ids=["exact", "with_session_id"])
def test_session_replays_byte_equal(tmp_path, path, sticky):
    fixture = json.loads(path.read_text())
    overrides = dict(fixture["v3_overrides"])
    overrides["caching"] = {**overrides["caching"], "sticky_session": sticky}
    overrides["budget"] = {"max_llm_requests": 100, "max_aux_requests": 10}
    cfg = config_dict("http://placeholder/api/v1", **overrides)
    with run_gateway(tmp_path, default=_reply, config=cfg) as gw:
        for call in fixture["calls"]:
            resp = gw.request("/api/v1/chat/completions", call["harness_body"])
            assert resp.status == 200, resp.raw
        received = [r.raw for r in gw.upstream.received]
        records = gw.calls(len(fixture["calls"]))
    assert len(received) == len(fixture["calls"])
    for got, call, record in zip(received, fixture["calls"], records, strict=True):
        expected = call["v2_upstream"].encode()
        if not sticky:
            assert got == expected
        else:
            body = json.loads(got)
            assert body.pop("session_id") == cfg["session_id"]
            assert json.dumps(body).encode() == expected
            assert set(json.loads(got)) - set(json.loads(expected)) == set(NEW_POLICY_FIELDS)
        assert record["request"] == json.loads(got)
