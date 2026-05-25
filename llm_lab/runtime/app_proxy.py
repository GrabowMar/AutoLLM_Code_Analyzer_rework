"""Reverse-proxy for sample-app containers (non-Traefik environments).

In production (TRAEFIK_DYNAMIC_DIR set), Traefik intercepts /apps/<uuid>/
traffic and forwards it directly to the container on the internal Docker
network — this view is never reached.

In local/dev deployments without Traefik, this view receives the request,
looks up the container's allocated host port, and proxies the request to it
via the Docker host bridge IP (detected from the routing table).
"""

from __future__ import annotations

import functools
import socket
import struct

import httpx
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse

from llm_lab.runtime.models import ContainerInstance

_HOP_BY_HOP = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)


@functools.lru_cache(maxsize=1)
def _docker_host_ip() -> str:
    """Return the Docker host's bridge IP reachable from this container.

    Reads the default gateway from /proc/net/route, which is the host's
    Docker bridge IP. Sample containers bind to 0.0.0.0:<port> on the host,
    so they're reachable at this IP from inside any Docker container.
    """
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000" and parts[2] != "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
    except Exception:
        pass
    return "127.0.0.1"


def _proxy_base_url(container: ContainerInstance) -> str:
    apps_network = getattr(settings, "DOCKER_APPS_NETWORK", "")
    if apps_network:
        # Production: container is on a dedicated Docker network, reachable by name.
        return f"http://{container.name}:8000"
    # Local: container binds a host port; reach it via the Docker host bridge.
    host = getattr(settings, "CONTAINER_APP_HOST", "") or _docker_host_ip()
    return f"http://{host}:{container.app_port}"


def _forward_headers(request) -> dict[str, str]:
    headers = {}
    for key, value in request.META.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            headers[name] = value
    headers.pop("Host", None)
    return headers


@transaction.non_atomic_requests
async def app_proxy_view(request, container_id, subpath=""):
    try:
        container = await ContainerInstance.objects.aget(id=container_id)
    except ContainerInstance.DoesNotExist:
        return HttpResponse("Not found", status=404)

    if not container.app_port:
        return HttpResponse("App not running", status=503)

    target = f"{_proxy_base_url(container)}/{subpath}"
    qs = request.META.get("QUERY_STRING", "")
    if qs:
        target += f"?{qs}"

    body = request.body if request.method in ("POST", "PUT", "PATCH") else None

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target,
                headers=_forward_headers(request),
                content=body,
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            return HttpResponse("App unreachable — container may have stopped", status=503)

    response = HttpResponse(content=resp.content, status=resp.status_code)
    for name, value in resp.headers.items():
        if name.lower() not in _HOP_BY_HOP and name.lower() != "content-encoding":
            response[name] = value
    return response
