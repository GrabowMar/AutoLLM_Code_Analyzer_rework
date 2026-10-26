"""HTTP entry points of the MCP server.

``/api/mcp`` takes the API token as ``Authorization: Bearer llml_…`` (Claude
Code, Claude Desktop, Cursor, VS Code, Codex, Gemini CLI …). Web connectors
that cannot send headers (claude.ai, ChatGPT) use ``/api/mcp/t/<token>``;
that form only accepts tokens carrying the ``mcp`` scope, so an ordinary API
token never works when pasted into a URL. A token with the ``read`` scope
gets the read-only tool set; ``?toolset=core`` lists only the core tools.
"""

from __future__ import annotations

import json

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from backend.integrations.mcp import server
from backend.integrations.mcp.tools import Caller
from backend.tokens import services as token_services

MAX_REQUEST_BYTES = 4 * 1024 * 1024
URL_TOKEN_SCOPE = "mcp"  # noqa: S105 — a scope name, not a secret
READ_ONLY_SCOPES = frozenset({"read", "mcp:read"})

CORS_HEADERS = {
    # Bearer-only auth (no cookies), so a wildcard origin cannot ride a session.
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version",
    "Access-Control-Max-Age": "600",
}


def _with_cors(response: HttpResponse) -> HttpResponse:
    for key, value in CORS_HEADERS.items():
        response[key] = value
    return response


def _unauthorized(message: str) -> HttpResponse:
    response = JsonResponse({"error": "unauthorized", "detail": message}, status=401)
    response["WWW-Authenticate"] = 'Bearer realm="llm-lab", error="invalid_token"'
    return _with_cors(response)


def _bearer(request: HttpRequest) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _handle(request: HttpRequest, raw_token: str, *, url_token: bool) -> HttpResponse:  # noqa: PLR0911
    if request.method == "OPTIONS":
        return _with_cors(HttpResponse(status=204))
    if request.method in {"GET", "DELETE"}:
        # Stateless server: no server-initiated SSE stream and no session to end.
        response = HttpResponse(status=405)
        response["Allow"] = "POST, OPTIONS"
        return _with_cors(response)
    if request.method != "POST":
        return _with_cors(HttpResponse(status=405))

    if not raw_token:
        return _unauthorized("send your LLM Lab API token as 'Authorization: Bearer llml_…'")
    token = token_services.verify_token(raw_token)
    if token is None:
        return _unauthorized("invalid, expired or revoked token")
    scopes = set(token.scopes or [])
    if url_token and URL_TOKEN_SCOPE not in scopes:
        return _unauthorized("tokens in the URL need the 'mcp' scope; create one under Settings → MCP server")
    token.mark_used(request.META.get("REMOTE_ADDR", ""))

    if len(request.body) > MAX_REQUEST_BYTES:
        return _with_cors(JsonResponse({"error": "request too large"}, status=413))
    try:
        payload = json.loads(request.body or b"null")
    except ValueError:
        body = server._error(None, server.PARSE_ERROR, "parse error")  # noqa: SLF001
        return _with_cors(JsonResponse(body, status=400))

    caller = Caller(
        request=request,
        raw_token=raw_token,
        user=token.user,
        read_only=bool(scopes & READ_ONLY_SCOPES),
        core_only=request.GET.get("toolset") == "core",
    )
    if isinstance(payload, list):
        replies = [r for r in (server.handle_message(caller, m) for m in payload) if r is not None]
        if not replies:
            return _with_cors(HttpResponse(status=202))
        return _with_cors(JsonResponse(replies, safe=False))
    reply = server.handle_message(caller, payload)
    if reply is None:
        return _with_cors(HttpResponse(status=202))
    return _with_cors(JsonResponse(reply))


@csrf_exempt
def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    return _handle(request, _bearer(request), url_token=False)


@csrf_exempt
def mcp_endpoint_url_token(request: HttpRequest, token: str) -> HttpResponse:
    return _handle(request, token, url_token=True)
