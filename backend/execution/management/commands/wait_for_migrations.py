"""``manage.py wait_for_migrations``: block until every migration is applied (the executor never migrates)."""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS
from django.db import OperationalError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


def pending_migrations(alias: str = DEFAULT_DB_ALIAS) -> list[str]:
    connection = connections[alias]
    connection.prepare_database()
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _backwards in plan]


class Command(BaseCommand):
    help = "Wait until the database is reachable and fully migrated."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=600.0, help="Give up after this many seconds.")
        parser.add_argument("--interval", type=float, default=3.0)

    def handle(self, *args, **options):
        deadline = time.monotonic() + options["timeout"]
        last = None
        while True:
            try:
                pending = pending_migrations()
            except OperationalError as exc:
                pending = [f"database unavailable: {exc}"]
            finally:
                connections.close_all()
            if not pending:
                self.stdout.write("All migrations applied.")
                return
            if pending != last:
                self.stdout.write(f"Waiting for {len(pending)} migration(s): {', '.join(pending[:5])}")
                last = pending
            if time.monotonic() > deadline:
                msg = "Timed out waiting for migrations"
                raise CommandError(msg)
            time.sleep(options["interval"])
