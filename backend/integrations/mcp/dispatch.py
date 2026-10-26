"""In-process calls into the platform's own ``/api/`` views.

An MCP tool call becomes a synthetic request to the same django-ninja view a
browser or script would hit, carrying the caller's own bearer token. Auth,
ownership checks, validation and error mapping therefore stay exactly the
API's — the MCP layer adds no permissions of its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from asgiref.sync import async_to_sync
from django.http import HttpRequest
from django.http import StreamingHttpResponse
from django.test import RequestFactory
from django.urls import Resolver404
from django.urls import resolve

from backend.integrations.mcp.openapi import EXCLUDED_PATH_PREFIXES

MAX_BODY_BYTES = 2 * 1024 * 1024
API_NAMESPACE = "api"


class DispatchError(Exception):
    """The call was refused before reaching a view."""


@dataclass
class ApiResult:
    status: int
    content_type: str
    data: Any  # parsed JSON, or None
    text: str  # raw body (truncated) when not JSON
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _encode_query(query: dict[str, Any] | None) -> str:
    if not query:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in query.items():
        if value is None:
            continue
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            text = ("true" if item else "false") if isinstance(item, bool) else str(item)
            pairs.append((key, text))
    return urlencode(pairs)


def _read(response) -> tuple[bytes, bool]:
    if isinstance(response, StreamingHttpResponse):
        chunks: list[bytes] = []
        size = 0
        for chunk in response.streaming_content:
            data = chunk if isinstance(chunk, bytes) else str(chunk).encode()
            chunks.append(data)
            size += len(data)
            if size > MAX_BODY_BYTES:
                break
        if hasattr(response, "close"):
            response.close()
        raw = b"".join(chunks)
    else:
        raw = response.content
    return raw[:MAX_BODY_BYTES], len(raw) > MAX_BODY_BYTES


async def _await(awaitable):
    return await awaitable


def call_api(
    origin: HttpRequest,
    raw_token: str,
    user,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: Any = None,
) -> ApiResult:
    """Run ``method path`` against the API as the token's owner."""
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        msg = f"unsupported method {method}"
        raise DispatchError(msg)
    if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path:
        msg = "path must be an /api/ path without a query string (pass query separately)"
        raise DispatchError(msg)
    if path.startswith(EXCLUDED_PATH_PREFIXES):
        msg = f"{path} is not reachable through MCP"
        raise DispatchError(msg)
    try:
        match = resolve(path)
    except Resolver404 as exc:
        msg = f"no API route matches {path}"
        raise DispatchError(msg) from exc
    if API_NAMESPACE not in match.namespaces:
        msg = f"{path} is not part of the REST API"
        raise DispatchError(msg)

    factory = RequestFactory()
    payload = json.dumps(body) if body is not None else ""
    request = factory.generic(
        method,
        path,
        data=payload,
        content_type="application/json",
        QUERY_STRING=_encode_query(query),
        HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        HTTP_ACCEPT="application/json",
        REMOTE_ADDR=origin.META.get("REMOTE_ADDR", ""),
        HTTP_HOST=origin.get_host(),
        HTTP_USER_AGENT="llmlab-mcp",
    )
    request.user = user
    request._dont_enforce_csrf_checks = True  # noqa: SLF001 — bearer-only request, no cookies
    response = match.func(request, *match.args, **match.kwargs)
    if hasattr(response, "__await__"):
        response = async_to_sync(_await)(response)
    raw, truncated = _read(response)
    content_type = response.get("Content-Type", "")
    data = None
    text = ""
    if "json" in content_type and not truncated:
        try:
            data = json.loads(raw or b"null")
        except ValueError:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return ApiResult(status=response.status_code, content_type=content_type, data=data, text=text, truncated=truncated)
