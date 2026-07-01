"""Reverse-proxy for generated sample apps.

Two modes share one proxy core:

* **Subdomain** (``APPS_SUBDOMAIN_PROXY`` / Traefik): the app is served at the
  ROOT of its own origin ``https://<name>.<APPS_DOMAIN>/``.  Root-absolute asset
  and API URLs (``/assets``, ``/api``) resolve to the app, so arbitrary SPAs work
  unmodified.  Entered via ``AppSubdomainProxyMiddleware`` (routes by Host).
  Public (a different origin from the main app), matching the Traefik design.

* **Path** (``APPS_PROXY_PATH``): ``<origin>/apps/<name>/`` on the main domain.
  Auth-gated; kept for environments without per-app subdomains.  NOTE: SPAs with
  root-absolute ``/assets``/``/api`` break here because those collide with the
  main platform — prefer subdomain mode.

Upstream reachability: an app that binds a host port (dev) is reached at
``APPS_UPSTREAM_HOST:<app_port>``; one on ``DOCKER_APPS_NETWORK`` at
``http://<name>:8000``.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

from backend.runtime.models import ContainerInstance

logger = logging.getLogger(__name__)

# Hop-by-hop headers (RFC 7230) plus length/encoding we must not pass through.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
    "host",
}
_PROXY_TIMEOUT = 30


def _upstream_base(container: ContainerInstance) -> str | None:
    if container.app_port:
        host = getattr(settings, "APPS_UPSTREAM_HOST", "host.docker.internal")
        return f"http://{host}:{container.app_port}"
    if getattr(settings, "DOCKER_APPS_NETWORK", ""):
        return f"http://{container.name}:8000"
    return None


def _running_container(name: str) -> ContainerInstance | None:
    c = ContainerInstance.objects.filter(name=name).first()
    if c is None or c.status != ContainerInstance.Status.RUNNING:
        return None
    return c


def _proxy(request, container: ContainerInstance, upstream_path: str, *, location_prefix: str = ""):
    """Stream *request* to the app container; return the proxied response."""
    base = _upstream_base(container)
    if base is None:
        return HttpResponse("App routing not configured", status=502)

    url = f"{base}/{upstream_path}"
    query = request.META.get("QUERY_STRING", "")
    if query:
        url = f"{url}?{query}"

    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "cookie"
    }

    try:
        upstream = requests.request(
            request.method,
            url,
            data=request.body or None,
            headers=fwd_headers,
            stream=True,
            allow_redirects=False,
            timeout=_PROXY_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("App proxy to %s failed: %s", url, exc)
        return HttpResponse(f"Bad gateway: {exc}", status=502)

    response = StreamingHttpResponse(
        upstream.iter_content(chunk_size=8192),
        status=upstream.status_code,
    )
    for key, value in upstream.headers.items():
        lk = key.lower()
        if lk in _HOP_BY_HOP:
            continue
        # Path mode: keep root-relative redirects inside the app's path prefix.
        if location_prefix and lk == "location" and value.startswith("/"):
            value = f"{location_prefix}{value}"
        response[key] = value
    return response


def proxy_app_subdomain(request, name: str):
    """Serve the app at the root of its own subdomain origin (no prefix strip)."""
    container = _running_container(name)
    if container is None:
        return HttpResponse("App is not running", status=502)
    return _proxy(request, container, request.path.lstrip("/"))


@csrf_exempt
def app_proxy(request, name: str, path: str = ""):
    """Path-mode proxy: <origin>/apps/<name>/… (same origin, auth-gated)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return HttpResponse("Authentication required", status=403)

    container = ContainerInstance.objects.filter(name=name).first()
    if container is None:
        return HttpResponse("Unknown app", status=404)
    if container.status != ContainerInstance.Status.RUNNING:
        return HttpResponse("App is not running", status=502)

    return _proxy(request, container, path, location_prefix=f"/apps/{name}")
