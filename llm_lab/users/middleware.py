"""Middleware supporting headless allauth login extras (e.g. remember-me)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


class RememberMeMiddleware:
    """Parse ``remember`` from headless JSON login POST for AccountAdapter."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
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
        return self.get_response(request)
