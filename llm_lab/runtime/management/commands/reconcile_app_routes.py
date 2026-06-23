"""Reconcile Traefik dynamic route files with the database.

Rewrites the subdomain route file for every running sample-app container and
removes stale files (including legacy ``app-<port>.yml`` files from the old
per-port routing scheme) that no longer correspond to a running container.

Run after deploying the subdomain-routing change, or any time the dynamic
config dir may have drifted from the DB (e.g. after a crash):

    python manage.py reconcile_app_routes
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.services import traefik_router

# Matches both the new ``app-<name>.yml`` and the legacy ``app-<port>.yml``.
_ROUTE_FILE_RE = re.compile(r"^app-.+\.yml$")


class Command(BaseCommand):
    help = "Rewrite Traefik route files for running apps and drop stale ones."

    def handle(self, *args, **options) -> None:
        dynamic_dir = getattr(settings, "TRAEFIK_DYNAMIC_DIR", "")
        if not dynamic_dir:
            self.stdout.write("TRAEFIK_DYNAMIC_DIR not set — nothing to do.")
            return

        running = list(
            ContainerInstance.objects.filter(
                status=ContainerInstance.Status.RUNNING,
            ),
        )
        expected = set()
        for container in running:
            traefik_router.write_route(container)
            expected.add(f"app-{container.name}.yml")
        self.stdout.write(f"Wrote {len(running)} route file(s).")

        removed = 0
        for path in Path(dynamic_dir).iterdir():
            if not _ROUTE_FILE_RE.match(path.name):
                continue
            if path.name in expected:
                continue
            path.unlink(missing_ok=True)
            removed += 1
            self.stdout.write(f"  removed stale {path.name}")
        self.stdout.write(self.style.SUCCESS(f"Removed {removed} stale file(s)."))
