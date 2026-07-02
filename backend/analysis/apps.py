from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AnalysisConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.analysis"
    verbose_name = "Analysis"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            _seed_catalog,
            sender=self,
            dispatch_uid="backend.analysis.seed_catalog",
        )


def _seed_catalog(sender, *, using, verbosity=1, **kwargs) -> None:
    """Sync the analyzer tool catalog with data/tools/*.yaml after migrate."""
    from backend.analysis.seeding import seed_tools

    try:
        created, updated = seed_tools(using=using)
    except Exception:
        # Seed data must never break `migrate` (malformed YAML, table torn
        # down by `migrate analysis zero`, ...).
        logger.exception("Analyzer tool catalog seeding failed")
        return
    if verbosity >= 1:
        logger.info("Analyzer tool catalog: %s created, %s updated", created, updated)
