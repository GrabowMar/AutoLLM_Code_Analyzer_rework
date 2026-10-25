"""``manage.py executor drain|resume|status`` (spec §C.2)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings
from backend.generation.models import ExperimentPostRun
from backend.generation.models import GenerationJob


def status_payload() -> dict:
    row = ExecutorSettings.get()
    leader = ExecutorInstance.objects.filter(is_leader=True, stopped_at__isnull=True).order_by("-last_seen_at").first()
    online = bool(leader and leader.last_seen_at > timezone.now() - timedelta(seconds=90))
    running = GenerationJob.objects.exclude(lease_owner="").count()
    post_runs = ExperimentPostRun.objects.filter(status=ExperimentPostRun.Status.RUNNING).count()
    return {
        "online": online,
        "leader": leader.id if leader else None,
        "draining": row.draining,
        "running_jobs": running,
        "running_post_runs": post_runs,
        "safe_to_restart": running == 0 and post_runs == 0,
        "slots": {
            "sandbox": row.sandbox_slots,
            "eval": row.eval_slots,
            "llm": row.llm_job_threads,
            "post": row.post_run_slots,
        },
    }


class Command(BaseCommand):
    help = "Drain the executor before a restart, resume it, or show its status."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["drain", "resume", "status"])

    def handle(self, *args, **options):
        action = options["action"]
        if action in ("drain", "resume"):
            row = ExecutorSettings.get()
            row.draining = action == "drain"
            row.save(update_fields=["draining", "updated_at"])
            from backend.execution.api import wake

            wake()
        elif action != "status":
            raise CommandError(action)
        self.stdout.write(json.dumps(status_payload(), indent=2))
