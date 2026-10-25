"""Host-based routing for sample-app subdomains.

When ``APPS_SUBDOMAIN_PROXY`` is on, requests whose Host is ``<name>.<APPS_DOMAIN>``
are reverse-proxied to the matching app container and served at the subdomain
root, so the app's root-absolute ``/assets`` and ``/api`` URLs resolve to the
app (not the main platform).  A single wildcard route (``*.<APPS_DOMAIN>`` →
Django) at the edge is enough; the name→container lookup happens here.
"""

from __future__ import annotations

from asgiref.sync import iscoroutinefunction
from asgiref.sync import markcoroutinefunction
from asgiref.sync import sync_to_async
from django.conf import settings

# All generated sample-app containers are named ``llm-<hex>`` (see
# container_service.build_for_job). Restricting the subdomain router to this
# prefix means a broad wildcard (e.g. ``*.grabowmar.ovh``) can never hijack
# sibling subdomains like ``dev1``/``cloud``/``www`` — they fall through to
# normal handling.
APP_NAME_PREFIX = "llm-"


def _app_subdomain_label(host: str) -> str | None:
    """Return the app name if *host* is an ``llm-*`` app subdomain, else None."""
    base = (getattr(settings, "APPS_DOMAIN", "") or getattr(settings, "DJANGO_DOMAIN", "")).lower()
    host = (host or "").split(":")[0].lower()
    if not base or not host.endswith("." + base):
        return None
    label = host[: -(len(base) + 1)]
    # Only a single-label app name (never the apex, a sibling, or a deeper name).
    if "." in label or not label.startswith(APP_NAME_PREFIX):
        return None
    return label


class AppSubdomainProxyMiddleware:
    """Short-circuit ``<name>.<APPS_DOMAIN>`` requests to the app proxy.

    Sync and async capable: on the async path the (blocking) proxy runs in a
    worker thread and every other request goes straight to the async chain.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    @staticmethod
    def _label(request) -> str | None:
        if not getattr(settings, "APPS_SUBDOMAIN_PROXY", False):
            return None
        return _app_subdomain_label(request.get_host())

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        label = self._label(request)
        if label:
            # Imported lazily to avoid app-registry access at import time.
            from backend.runtime.proxy import proxy_app_subdomain

            return proxy_app_subdomain(request, label)
        return self.get_response(request)

    async def __acall__(self, request):
        label = self._label(request)
        if label:
            from backend.runtime.proxy import proxy_app_subdomain

            return await sync_to_async(proxy_app_subdomain)(request, label)
        return await self.get_response(request)
