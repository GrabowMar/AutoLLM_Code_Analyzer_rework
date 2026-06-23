"""Write/delete Traefik dynamic config files for sample-app containers.

When TRAEFIK_DYNAMIC_DIR is set, each running container gets a YAML file
written here.  Traefik watches the directory and picks up changes within
seconds, exposing the app on a unique **subdomain** of APPS_DOMAIN.

Each app is reached at ``https://<container_name>.<APPS_DOMAIN>``.  Traefik
terminates TLS using the wildcard ``*.<APPS_DOMAIN>`` certificate (issued once
via the DNS-01 challenge — see the default TLS store in dynamic/static.yml) and
forwards the request to the container at ``http://<container_name>:8000`` over
the shared ``llm_apps`` Docker network (no host port binding).

This replaces the old "one TLS entry point per allocated port (9001-9020)"
scheme, which capped the system at 20 concurrent apps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_lab.runtime.models import ContainerInstance

logger = logging.getLogger(__name__)

# ``tls: {}`` enables TLS and lets Traefik pick the matching certificate from
# the default store — i.e. the pre-issued ``*.<APPS_DOMAIN>`` wildcard — so we
# do NOT request a separate certificate per app.
_ROUTE_TEMPLATE = """\
http:
  routers:
    app-{name}:
      entryPoints:
        - web-secure
      rule: "Host(`{name}.{apps_domain}`)"
      service: app-{name}
      tls: {{}}
  services:
    app-{name}:
      loadBalancer:
        servers:
          - url: "http://{name}:8000"
"""


def _dynamic_dir() -> str:
    from django.conf import settings

    return getattr(settings, "TRAEFIK_DYNAMIC_DIR", "")


def _apps_domain() -> str:
    from django.conf import settings

    return getattr(settings, "APPS_DOMAIN", "") or getattr(
        settings,
        "DJANGO_DOMAIN",
        "localhost",
    )


def _route_path(name: str) -> Path:
    return Path(_dynamic_dir()) / f"app-{name}.yml"


def write_route(container: ContainerInstance) -> None:
    """Create (or replace) the Traefik route file for this container.

    No-op when TRAEFIK_DYNAMIC_DIR is unset (local dev / non-Traefik), where
    apps are reached via direct host-port binding instead.
    """
    if not _dynamic_dir():
        return

    name = container.name
    content = _ROUTE_TEMPLATE.format(name=name, apps_domain=_apps_domain())
    dest = _route_path(name)
    tmp = dest.with_suffix(".yml.tmp")
    try:
        tmp.write_text(content)
        tmp.rename(dest)
        logger.info("Traefik route written: %s", dest)
    except OSError:
        logger.exception("Failed to write Traefik route %s", dest)


def delete_route(container: ContainerInstance) -> None:
    """Remove the Traefik route file for this container (if it exists)."""
    if not _dynamic_dir():
        return

    dest = _route_path(container.name)
    try:
        dest.unlink(missing_ok=True)
        logger.info("Traefik route removed: %s", dest)
    except OSError:
        logger.exception("Failed to remove Traefik route %s", dest)
