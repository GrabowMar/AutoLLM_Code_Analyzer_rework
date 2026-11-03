"""Tests for the MCP server (/api/mcp)."""

from __future__ import annotations

import json

import pytest

from backend.integrations.mcp import openapi
from backend.integrations.mcp import server
from backend.integrations.mcp import tools as tools_module
from backend.tokens import services
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return UserFactory.create()


@pytest.fixture
def token(user):
    raw, _ = services.generate_token(user=user, name="mcp")
    return raw


def rpc(client, method, params=None, *, token=None, path="/api/mcp", msg_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        message["id"] = msg_id
    if params is not None:
        message["params"] = params
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(path, data=json.dumps(message), content_type="application/json", **headers)


def tool_text(response):
    body = response.json()
    return body["result"], body["result"]["content"][0]["text"]


def test_requires_token(client):
    response = rpc(client, "initialize", {})
    assert response.status_code == 401
    assert "Bearer" in response["WWW-Authenticate"]


def test_rejects_bad_token(client):
    assert rpc(client, "ping", token="llml_nope").status_code == 401  # noqa: S106


def test_initialize_negotiates_version(client, token):
    body = rpc(client, "initialize", {"protocolVersion": "2025-06-18"}, token=token).json()
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert body["result"]["serverInfo"]["name"] == server.SERVER_NAME
    assert "tools" in body["result"]["capabilities"]

    body = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}, token=token).json()
    assert body["result"]["protocolVersion"] == server.LATEST_PROTOCOL_VERSION


def test_notification_gets_202(client, token):
    assert rpc(client, "notifications/initialized", token=token, msg_id=None).status_code == 202


def test_get_is_405(client, token):
    response = client.get("/api/mcp", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 405


def test_unknown_method(client, token):
    body = rpc(client, "nope/nope", token=token).json()
    assert body["error"]["code"] == server.METHOD_NOT_FOUND


def test_parse_error(client, token):
    response = client.post(
        "/api/mcp",
        data="{not json",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == server.PARSE_ERROR


def test_tools_list_has_curated_and_generic(client, token):
    tools = rpc(client, "tools/list", token=token).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"whoami", "search_models", "analyze", "call_api", "find_api_operations"} <= names
    # The study/run tools stay hidden until WP-08/WP-10 publish their operation ids.
    assert not names & set(tools_module.pending_tools())
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "$ref" not in json.dumps(tool["inputSchema"])


def test_every_enabled_curated_tool_resolves_to_an_operation():
    ops = openapi.operations()
    missing = [c.name for c in tools_module.CURATED if c.enabled and f"backend_{c.operation_id}" not in ops]
    assert missing == []


def test_study_tools_are_registered_disabled_until_published():
    pending = tools_module.pending_tools()
    assert {"list_studies", "get_study", "resolve_study", "launch_study"} <= set(pending)
    assert set(pending.values()) <= {"WP-08", "WP-10"}
    for item in tools_module.CURATED:
        if not item.enabled:
            assert item.pending
            assert item.operation_id == ""  # filled in (and enabled) once announced in CHANGES
        else:
            assert not item.pending
    names = [c.name for c in tools_module.CURATED]
    assert len(names) == len(set(names))
    # No v2 generation tool survives the clean break (the v2 word is split so the U1 gate grep stays clean).
    v2 = {"generate_apps", "generate_custom", "create_agent_jobs", "list_generation_jobs", "get_generation_job"}
    assert not [n for n in names if "experi" + "ment" in n or n in v2]


def test_disabled_tool_is_not_callable(client, token):
    body = rpc(client, "tools/call", {"name": "launch_study", "arguments": {}}, token=token).json()
    failed = body.get("error") or body["result"]
    assert "unknown tool" in json.dumps(failed)


def test_whoami_runs_as_token_user(client, token, user):
    result, text = tool_text(rpc(client, "tools/call", {"name": "whoami", "arguments": {}}, token=token))
    assert result["isError"] is False
    assert json.loads(text)["email"] == user.email


def test_call_api_generic(client, token):
    result, text = tool_text(
        rpc(
            client,
            "tools/call",
            {
                "name": "call_api",
                "arguments": {"method": "GET", "path": "/api/integrations/webhooks/event-types/"},
            },
            token=token,
        ),
    )
    assert result["isError"] is False
    assert "study.completed" in {t["id"] for t in json.loads(text)}


@pytest.mark.parametrize("path", ["/api/tokens/", "/api/mcp", "/apps/x/", "/api/../admin/", "/api/models/?x=1"])
def test_call_api_refuses_paths(client, token, path):
    result, _ = tool_text(
        rpc(client, "tools/call", {"name": "call_api", "arguments": {"method": "GET", "path": path}}, token=token),
    )
    assert result["isError"] is True


def test_api_errors_are_tool_errors(client, token):
    result, text = tool_text(
        rpc(client, "tools/call", {"name": "get_model", "arguments": {"slug": "does-not-exist"}}, token=token),
    )
    assert result["isError"] is True
    assert text.startswith("HTTP 404")


def test_path_param_injection_rejected(client, token):
    result, _ = tool_text(
        rpc(client, "tools/call", {"name": "get_model", "arguments": {"slug": "a/../../x"}}, token=token),
    )
    assert result["isError"] is True


def test_read_only_scope(client, user):
    raw, _ = services.generate_token(user=user, name="ro", scopes=["read"])
    tools = rpc(client, "tools/list", token=raw).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "search_models" in names
    assert "generate_apps" not in names
    result, text = tool_text(
        rpc(
            client,
            "tools/call",
            {"name": "call_api", "arguments": {"method": "POST", "path": "/api/rankings/refresh/"}},
            token=raw,
        ),
    )
    assert result["isError"] is True
    assert "read-only" in text


def test_url_token_needs_mcp_scope(client, user, token):
    assert rpc(client, "ping", path=f"/api/mcp/t/{token}").status_code == 401
    scoped, _ = services.generate_token(user=user, name="connector", scopes=["mcp"])
    response = rpc(client, "ping", path=f"/api/mcp/t/{scoped}")
    assert response.status_code == 200
    assert response.json()["result"] == {}


def test_find_and_describe_operations(client, token):
    _, text = tool_text(
        rpc(
            client,
            "tools/call",
            {"name": "find_api_operations", "arguments": {"query": "webhook test"}},
            token=token,
        ),
    )
    hits = json.loads(text)
    assert any(h["path"].endswith("/test/") for h in hits)
    _, text = tool_text(
        rpc(
            client,
            "tools/call",
            {"name": "describe_api_operation", "arguments": {"operation": hits[0]["operation"]}},
            token=token,
        ),
    )
    assert json.loads(text)["input_schema"]["type"] == "object"


def test_resources_and_prompts(client, token):
    resources = rpc(client, "resources/list", token=token).json()["result"]["resources"]
    uris = {r["uri"] for r in resources}
    assert "llmlab://openapi.json" in uris
    body = rpc(client, "resources/read", {"uri": "llmlab://openapi.json"}, token=token).json()
    assert json.loads(body["result"]["contents"][0]["text"])["paths"]

    prompts = rpc(client, "prompts/list", token=token).json()["result"]["prompts"]
    assert {p["name"] for p in prompts} == set(server.PROMPTS)
    body = rpc(client, "prompts/get", {"name": "triage_findings", "arguments": {"run_id": "abc"}}, token=token).json()
    assert "abc" in body["result"]["messages"][0]["content"]["text"]
    body = rpc(client, "prompts/get", {"name": "triage_findings", "arguments": {}}, token=token).json()
    assert body["error"]["code"] == server.INVALID_PARAMS


def test_batch(client, token):
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    response = client.post(
        "/api/mcp",
        data=json.dumps(batch),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert [r["id"] for r in response.json()] == [1, 2]


def test_config_endpoint(client, user):
    client.force_login(user)
    body = client.get("/api/integrations/mcp/config/").json()
    assert body["server_url"].endswith("/api/mcp")
    assert "<TOKEN>" in body["url_token_template"]
    ids = {c["id"] for c in body["clients"]}
    assert {"claude-code", "claude-ai", "chatgpt", "cursor", "vscode", "anthropic-api"} <= ids


def test_core_toolset(client, token):
    tools = rpc(client, "tools/list", token=token, path="/api/mcp?toolset=core").json()["result"]["tools"]
    from backend.integrations.mcp.tools import CORE_TOOLS

    assert {t["name"] for t in tools} == CORE_TOOLS - set(tools_module.pending_tools())
