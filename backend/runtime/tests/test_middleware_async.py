"""AppSubdomainProxyMiddleware is sync- and async-capable and routes the same on both paths."""

from __future__ import annotations

from unittest import mock

from asgiref.sync import async_to_sync
from asgiref.sync import iscoroutinefunction
from django.http import HttpResponse
from django.test import RequestFactory

from backend.runtime.middleware import AppSubdomainProxyMiddleware


def _sync_view(request):
    return HttpResponse("platform")


async def _async_view(request):
    return HttpResponse("platform")


def test_capability_flags():
    assert AppSubdomainProxyMiddleware.sync_capable
    assert AppSubdomainProxyMiddleware.async_capable
    assert not iscoroutinefunction(AppSubdomainProxyMiddleware(_sync_view))
    assert iscoroutinefunction(AppSubdomainProxyMiddleware(_async_view))


def test_routes_identically(settings):
    settings.APPS_SUBDOMAIN_PROXY = True
    settings.APPS_DOMAIN = "apps.example.org"
    settings.ALLOWED_HOSTS = ["*"]
    with mock.patch("backend.runtime.proxy.proxy_app_subdomain", return_value=HttpResponse("app")) as proxy:
        app_request = RequestFactory().get("/", HTTP_HOST="llm-abc.apps.example.org")
        assert AppSubdomainProxyMiddleware(_sync_view)(app_request).content == b"app"
        async_app = RequestFactory().get("/", HTTP_HOST="llm-abc.apps.example.org")
        assert async_to_sync(AppSubdomainProxyMiddleware(_async_view))(async_app).content == b"app"
        assert [c.args[1] for c in proxy.call_args_list] == ["llm-abc", "llm-abc"]
        platform = RequestFactory().get("/", HTTP_HOST="dev1.apps.example.org")
        assert async_to_sync(AppSubdomainProxyMiddleware(_async_view))(platform).content == b"platform"
        assert AppSubdomainProxyMiddleware(_sync_view)(platform).content == b"platform"
