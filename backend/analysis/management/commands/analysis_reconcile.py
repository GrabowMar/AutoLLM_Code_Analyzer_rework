"""Recover analysis runs whose worker died and reap orphaned analysis containers.

Runs the same procedure as the ``analysis-reconcile`` beat entry (every
minute when Celery beat is running): stale RUNNING runs are requeued (or
failed after ``ANALYSIS_MAX_ATTEMPTS``), lost QUEUED runs re-dispatched, and
containers/volumes of runs that are not running removed.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from backend.analysis.engine import executor


class Command(BaseCommand):
    help = "Requeue stale analysis runs, re-dispatch lost queued runs and reap orphaned analysis containers"

    def handle(self, *args, **options) -> None:
        report = executor.reconcile()
        self.stdout.write(json.dumps(report, indent=2, default=str))
