"""``manage.py run_executor``: the single process that runs every generation job (spec §C.2)."""

from __future__ import annotations

import logging
import os
import signal
import sys

from django.core.management.base import BaseCommand

from backend.execution.executor import Executor

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the job executor (leader lock, scheduler, pools, LLM proxy, pumps). One instance only."

    def add_arguments(self, parser):
        parser.add_argument("--max-ticks", type=int, default=None, help="Stop after this many ticks (debugging).")
        parser.add_argument("--no-proxy", action="store_true", help="Do not start the internal LLM proxy.")
        parser.add_argument("--no-pumps", action="store_true", help="Do not start the direct-endpoint pumps.")

    def handle(self, *args, **options):
        from backend.runtime.services.docker_manager import new_client

        executor = Executor(
            # The apps daemon: the reconciler reaps lost sandbox / evaluation containers there.
            docker_factory=new_client,
            max_ticks=options["max_ticks"],
            start_proxy=not options["no_proxy"],
            start_pumps=not options["no_pumps"],
        )

        def stop(signum, _frame):
            logger.info("Executor received signal %s; shutting down", signum)
            executor.request_stop(f"signal {signum}")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        code = executor.run()
        sys.stdout.flush()
        sys.stderr.flush()
        logging.shutdown()
        # Running sandboxes are not awaited (§C.2): their leases already expired and
        # the next start reaps and requeues them. Skip joining non-daemon pool threads.
        os._exit(code)
