"""/api/integrations/mcp/ — how to connect MCP clients to this instance.

Returns ready-to-paste configuration for each client. Snippets carry the
placeholder ``<TOKEN>``; the UI substitutes a freshly minted token, the
server never sees or returns one here.
"""

from __future__ import annotations

import json

from ninja import Router
from ninja import Schema

from backend.inference.api.views import server_url
from backend.integrations.mcp import server
from backend.integrations.mcp import tools

router = Router(tags=["integrations"])

TOKEN = "<TOKEN>"  # noqa: S105 — placeholder, not a secret


class ClientSnippet(Schema):
    id: str
    name: str
    auth: str  # "header" | "url"
    language: str  # shell | json | toml | python | text
    location: str  # where the snippet goes
    snippet: str
    docs_url: str = ""


class McpConfigOut(Schema):
    server_url: str
    url_token_template: str
    protocol_versions: list[str]
    tool_count: int
    tools: list[dict]
    prompts: list[str]
    clients: list[ClientSnippet]


def _json(value) -> str:
    return json.dumps(value, indent=2)


def _clients(url: str, url_with_token: str) -> list[dict]:
    header = {"Authorization": f"Bearer {TOKEN}"}
    return [
        {
            "id": "claude-code",
            "name": "Claude Code",
            "auth": "header",
            "language": "shell",
            "location": "terminal",
            "snippet": f'claude mcp add --transport http llm-lab {url} --header "Authorization: Bearer {TOKEN}"',
            "docs_url": "https://code.claude.com/docs/en/mcp",
        },
        {
            "id": "claude-ai",
            "name": "Claude (web, desktop, mobile) — custom connector",
            "auth": "url",
            "language": "text",
            "location": "Settings → Connectors → Add custom connector → Remote MCP server URL",
            "snippet": url_with_token,
            "docs_url": "https://support.claude.com/en/articles/11175166",
        },
        {
            "id": "claude-desktop",
            "name": "Claude Desktop (config file)",
            "auth": "header",
            "language": "json",
            "location": "claude_desktop_config.json",
            "snippet": _json(
                {
                    "mcpServers": {
                        "llm-lab": {
                            "command": "npx",
                            "args": ["-y", "mcp-remote", url, "--header", "Authorization:${LLMLAB_AUTH}"],
                            "env": {"LLMLAB_AUTH": f"Bearer {TOKEN}"},
                        },
                    },
                },
            ),
        },
        {
            "id": "chatgpt",
            "name": "ChatGPT — developer-mode connector",
            "auth": "url",
            "language": "text",
            "location": (
                "Settings → Apps & Connectors → Advanced → Developer mode, then Create: "
                "MCP server URL below, Authentication: No authentication"
            ),
            "snippet": url_with_token,
            "docs_url": "https://platform.openai.com/docs/guides/developer-mode",
        },
        {
            "id": "codex",
            "name": "OpenAI Codex CLI",
            "auth": "header",
            "language": "toml",
            "location": "~/.codex/config.toml (export LLMLAB_TOKEN=<TOKEN>)",
            "snippet": f'[mcp_servers.llm-lab]\nurl = "{url}"\nbearer_token_env_var = "LLMLAB_TOKEN"\n',
        },
        {
            "id": "cursor",
            "name": "Cursor",
            "auth": "header",
            "language": "json",
            "location": "~/.cursor/mcp.json",
            "snippet": _json({"mcpServers": {"llm-lab": {"url": url, "headers": header}}}),
        },
        {
            "id": "vscode",
            "name": "VS Code (GitHub Copilot agent mode)",
            "auth": "header",
            "language": "json",
            "location": ".vscode/mcp.json",
            "snippet": _json({"servers": {"llm-lab": {"type": "http", "url": url, "headers": header}}}),
        },
        {
            "id": "gemini-cli",
            "name": "Gemini CLI",
            "auth": "header",
            "language": "json",
            "location": "~/.gemini/settings.json",
            "snippet": _json({"mcpServers": {"llm-lab": {"httpUrl": url, "headers": header}}}),
        },
        {
            "id": "windsurf",
            "name": "Windsurf",
            "auth": "header",
            "language": "json",
            "location": "~/.codeium/windsurf/mcp_config.json",
            "snippet": _json({"mcpServers": {"llm-lab": {"serverUrl": url, "headers": header}}}),
        },
        {
            "id": "anthropic-api",
            "name": "Claude API (MCP connector)",
            "auth": "header",
            "language": "python",
            "location": "your code — the instance must be reachable from the internet",
            "snippet": (
                "import anthropic\n\n"
                "client = anthropic.Anthropic()\n"
                "response = client.beta.messages.create(\n"
                '    model="claude-opus-5-5",\n'
                "    max_tokens=16000,\n"
                '    betas=["mcp-client-2025-11-20"],\n'
                f'    mcp_servers=[{{"type": "url", "url": "{url}", "name": "llm-lab", '
                f'"authorization_token": "{TOKEN}"}}],\n'
                '    tools=[{"type": "mcp_toolset", "mcp_server_name": "llm-lab"}],\n'
                '    messages=[{"role": "user", "content": "Which model tops the LLM Lab rankings?"}],\n'
                ")\n"
            ),
            "docs_url": "https://platform.claude.com/docs/en/agents-and-tools/mcp-connector",
        },
        {
            "id": "openai-api",
            "name": "OpenAI Responses API (remote MCP tool)",
            "auth": "header",
            "language": "python",
            "location": "your code — the instance must be reachable from the internet",
            "snippet": (
                "from openai import OpenAI\n\n"
                "client = OpenAI()\n"
                "response = client.responses.create(\n"
                '    model="gpt-5",\n'
                "    tools=[{\n"
                '        "type": "mcp",\n'
                '        "server_label": "llm-lab",\n'
                f'        "server_url": "{url}",\n'
                f'        "headers": {{"Authorization": "Bearer {TOKEN}"}},\n'
                '        "require_approval": "never",\n'
                "    }],\n"
                '    input="Which model tops the LLM Lab rankings?",\n'
                ")\n"
            ),
        },
        {
            "id": "inspector",
            "name": "MCP Inspector (debugging)",
            "auth": "header",
            "language": "shell",
            "location": "terminal",
            "snippet": "npx @modelcontextprotocol/inspector",
            "docs_url": "https://github.com/modelcontextprotocol/inspector",
        },
    ]


@router.get("/config/", response=McpConfigOut)
def mcp_config(request):
    base = server_url(request)
    url = f"{base}/api/mcp"
    url_with_token = f"{base}/api/mcp/t/{TOKEN}"
    caller = tools.Caller(request=request, raw_token="", user=request.auth, read_only=False)
    listed = tools.list_tools(caller)
    return {
        "server_url": url,
        "url_token_template": url_with_token,
        "protocol_versions": list(server.SUPPORTED_PROTOCOL_VERSIONS),
        "tool_count": len(listed),
        "tools": [
            {
                "name": t["name"],
                "description": t["description"].split("\n\n[")[0],
                "read_only": bool(t.get("annotations", {}).get("readOnlyHint")),
            }
            for t in listed
        ],
        "prompts": list(server.PROMPTS),
        "clients": _clients(url, url_with_token),
    }
