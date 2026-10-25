"""Middleware supporting headless allauth login extras (e.g. remember-me)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from asgiref.sync import iscoroutinefunction
from asgiref.sync import markcoroutinefunction

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


class _SyncAndAsyncMiddleware:
    """Base for middlewares that run on both paths (async views stay on the event loop).

    Subclasses implement :meth:`process` (request-side work only, no I/O
    beyond the request object); the response is passed through unchanged.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def process(self, request: HttpRequest) -> None:
        raise NotImplementedError

    def __call__(self, request: HttpRequest):
        if self.is_async:
            return self.__acall__(request)
        self.process(request)
        return self.get_response(request)

    async def __acall__(self, request: HttpRequest) -> HttpResponse:
        self.process(request)
        return await self.get_response(request)


class RememberMeMiddleware(_SyncAndAsyncMiddleware):
    """Parse ``remember`` from headless JSON login POST for AccountAdapter."""

    def process(self, request: HttpRequest) -> None:
        if (
            request.method == "POST"
            and "_allauth/" in request.path
            and request.path.rstrip("/").endswith("/auth/login")
        ):
            content_type = request.content_type or ""
            if "json" in content_type:
                try:
                    raw = request.body
                    if raw:
                        data = json.loads(raw.decode("utf-8"))
                        request._remember_me = bool(data.get("remember", False))  # noqa: SLF001
                except (json.JSONDecodeError, UnicodeDecodeError):
                    request._remember_me = False  # noqa: SLF001


class ForceCsrfCookieMiddleware(_SyncAndAsyncMiddleware):
    """Ensure CSRF cookie is set on GET/safe requests to allauth and API paths."""

    def process(self, request: HttpRequest) -> None:
        if request.method in ("GET", "HEAD") and ("_allauth/" in request.path or "api/" in request.path):
            from django.middleware.csrf import get_token

            get_token(request)
