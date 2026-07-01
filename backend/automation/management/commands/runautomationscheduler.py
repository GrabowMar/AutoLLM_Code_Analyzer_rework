"""Django management command: run the automation scheduler in a loop.

Usage::

    python manage.py runautomationscheduler

This process should be run as a separate long-lived process (or as a
Celery beat task via ``run_scheduler_tick``). It calls ``scheduler.tick()``
every 30 seconds.

Production recommendation: use Celery beat with ``run_scheduler_tick`` task
instead of this management command to avoid having an extra process.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_TICK_INTERVAL = 30  # seconds


class Command(BaseCommand):
    help = "Run the automation scheduler loop (polls every 30s for due schedules)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=_TICK_INTERVAL,
            help="Poll interval in seconds (default: 30)",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single tick then exit (useful for testing)",
        )

    def handle(self, *args, **options):
        from backend.automation.engine.scheduler import tick

        interval = options["interval"]
        once = options["once"]

        self.stdout.write(f"Automation scheduler starting (interval={interval}s)")

        while True:
            try:
                fired = tick()
                if fired:
                    self.stdout.write(f"Tick: {fired} schedule(s) fired")
                else:
                    logger.debug("Scheduler tick: no schedules due")
            except Exception:
                logger.exception("Scheduler tick error")

            if once:
                break

            time.sleep(interval)
