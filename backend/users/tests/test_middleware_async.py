"""RememberMe / ForceCsrfCookie middlewares are sync- and async-capable and behave the same on both paths."""

from __future__ import annotations

import asyncio
import json

from asgiref.sync import async_to_sync
from asgiref.sync import iscoroutinefunction
from django.http import HttpResponse
from django.test import RequestFactory

from backend.users.middleware import ForceCsrfCookieMiddleware
from backend.users.middleware import RememberMeMiddleware

LOGIN = "/_allauth/browser/v1/auth/login"


def _sync_view(request):
    return HttpResponse("ok")


async def _async_view(request):
    await asyncio.sleep(0)
    return HttpResponse("ok")


def _login_request(*, remember):
    return RequestFactory().post(LOGIN, data=json.dumps({"remember": remember}), content_type="application/json")


def test_capability_flags():
    for cls in (RememberMeMiddleware, ForceCsrfCookieMiddleware):
        assert cls.sync_capable
        assert cls.async_capable
        assert not iscoroutinefunction(cls(_sync_view))
        assert iscoroutinefunction(cls(_async_view))


def test_remember_me_same_on_both_paths():
    sync_request = _login_request(remember=True)
    RememberMeMiddleware(_sync_view)(sync_request)
    async_request = _login_request(remember=True)
    response = async_to_sync(RememberMeMiddleware(_async_view))(async_request)
    assert response.content == b"ok"
    assert sync_request._remember_me is True
    assert async_request._remember_me is True
    bad = RequestFactory().post(LOGIN, data="{not json", content_type="application/json")
    async_to_sync(RememberMeMiddleware(_async_view))(bad)
    assert bad._remember_me is False


def test_force_csrf_cookie_same_on_both_paths():
    sync_request = RequestFactory().get("/api/models/")
    ForceCsrfCookieMiddleware(_sync_view)(sync_request)
    async_request = RequestFactory().get("/api/models/")
    async_to_sync(ForceCsrfCookieMiddleware(_async_view))(async_request)
    assert sync_request.META.get("CSRF_COOKIE")
    assert async_request.META.get("CSRF_COOKIE")
    other = RequestFactory().get("/healthz")
    async_to_sync(ForceCsrfCookieMiddleware(_async_view))(other)
    assert not other.META.get("CSRF_COOKIE")
