from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class RuntimeConfig(AppConfig):
    name = "backend.runtime"
    verbose_name = "Runtime"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            _seed_runtime_data,
            sender=self,
            dispatch_uid="backend.runtime.seed_stacks",
        )


def _seed_runtime_data(sender, *, using, verbosity=1, **kwargs) -> None:
    """Sync builtin Stack rows from runtime/scaffolding/ after migrate."""
    from backend.runtime.seeding import seed_stacks

    try:
        created, updated = seed_stacks(using=using)
    except Exception:
        # Seed data must never break `migrate`.
        logger.exception("Stack seeding failed")
        return
    if verbosity >= 1:
        logger.info("Stack seeding: %s created, %s updated", created, updated)
