"""Reconcile ContainerInstance.status with the real Docker state.

Fixes rows that drifted (e.g. an app crashed/exited but the DB still says
``running``).  Safe to run anytime; intended for a cron/heartbeat or manual use:

    python manage.py sync_container_status
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from llm_lab.runtime.models import ContainerInstance
from llm_lab.runtime.services import docker_manager

# DB statuses we actively reconcile against Docker.
_LIVE = {
    ContainerInstance.Status.RUNNING,
    ContainerInstance.Status.STOPPED,
}


class Command(BaseCommand):
    help = "Update ContainerInstance.status from the live Docker state."

    def handle(self, *args, **options) -> None:
        if not docker_manager.ping():
            self.stdout.write(self.style.WARNING("Docker daemon unavailable."))
            return

        changed = 0
        for c in ContainerInstance.objects.filter(status__in=_LIVE):
            info = docker_manager.inspect(c.name) or {}
            docker_state = info.get("State", {}).get("Status", "")  # running/exited/...
            if "error" in info or not docker_state:
                new = ContainerInstance.Status.STOPPED
            elif docker_state == "running":
                new = ContainerInstance.Status.RUNNING
            else:
                new = ContainerInstance.Status.STOPPED
            if new != c.status:
                self.stdout.write(f"  {c.name}: {c.status} -> {new} (docker={docker_state or 'gone'})")
                c.status = new
                c.save(update_fields=["status"])
                changed += 1
        self.stdout.write(self.style.SUCCESS(f"Synced {changed} container(s)."))
