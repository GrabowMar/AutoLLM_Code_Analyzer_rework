from __future__ import annotations

import logging

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class GenerationConfig(AppConfig):
    name = "backend.generation"
    verbose_name = _("Generation")

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            _seed_generation_data,
            sender=self,
            dispatch_uid="backend.generation.seed_templates",
        )


def _seed_generation_data(sender, *, using, verbosity=1, **kwargs) -> None:
    """Sync scaffolding/requirement/prompt/block/bundle seeds after migrate."""
    from backend.generation.seeding import seed_all

    try:
        results = seed_all(using=using)
    except Exception:
        # Seed data must never break `migrate` (malformed YAML/JSON, table
        # torn down by `migrate generation zero`, ...).
        logger.exception("Generation template seeding failed")
        return
    if verbosity >= 1:
        for name, (created, updated) in results.items():
            logger.info("Generation seeding (%s): %s created, %s updated", name, created, updated)
