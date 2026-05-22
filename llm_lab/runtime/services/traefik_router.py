"""Write/delete Traefik dynamic config files for sample-app containers.

When TRAEFIK_DYNAMIC_DIR is set, each running container gets a YAML file
written here.  Traefik watches the directory and picks up changes within
seconds, creating an HTTPS entry point on the container's allocated port.

The container runs on the llm_apps Docker network (no host port binding).
Traefik reaches it at http://<container_name>:8000 within that network.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ROUTE_TEMPLATE = """\
http:
  routers:
    app-{port}:
      entryPoints:
        - app-{port}
      rule: "Host(`{domain}`)"
      service: app-{port}
      tls:
        certResolver: letsencrypt
  services:
    app-{port}:
      loadBalancer:
        servers:
          - url: "http://{container_name}:8000"
"""


def _dynamic_dir() -> str:
    from django.conf import settings
    return getattr(settings, "TRAEFIK_DYNAMIC_DIR", "")


def write_route(container_name: str, port: int) -> None:
    """Create (or replace) the Traefik route file for this container."""
    dynamic_dir = _dynamic_dir()
    if not dynamic_dir:
        return
    from django.conf import settings
    domain = getattr(settings, "DJANGO_DOMAIN", "localhost")
    content = _ROUTE_TEMPLATE.format(
        port=port,
        domain=domain,
        container_name=container_name,
    )
    dest = Path(dynamic_dir) / f"app-{port}.yml"
    tmp = dest.with_suffix(".yml.tmp")
    try:
        tmp.write_text(content)
        tmp.rename(dest)
        logger.info("Traefik route written: %s", dest)
    except OSError:
        logger.exception("Failed to write Traefik route %s", dest)


def delete_route(port: int) -> None:
    """Remove the Traefik route file for this port (if it exists)."""
    dynamic_dir = _dynamic_dir()
    if not dynamic_dir:
        return
    dest = Path(dynamic_dir) / f"app-{port}.yml"
    try:
        dest.unlink(missing_ok=True)
        logger.info("Traefik route removed: %s", dest)
    except OSError:
        logger.exception("Failed to remove Traefik route %s", dest)
