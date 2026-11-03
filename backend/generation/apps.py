from __future__ import annotations

import logging

from django.apps import AppConfig
from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class GenerationConfig(AppConfig):
    name = "backend.generation"
    verbose_name = _("Generation")

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            _seed_library_after_migrate,
            sender=self,
            dispatch_uid="backend.generation.seed_library",
        )


def _seed_library_after_migrate(sender, *, using, verbosity=1, **kwargs) -> None:
    """Seed the library from ``generation/data/`` after ``migrate`` when ``GENERATION_SEED_ON_MIGRATE`` is on.

    Section 11.1: off by default in production, on in dev and test. WP-03 owns the setting and
    ``library.seeding.seed_library``. Seeding never breaks ``migrate``.
    """
    if not getattr(settings, "GENERATION_SEED_ON_MIGRATE", False):
        return
    from backend.generation.library.seeding import seed_library

    try:
        report = seed_library(using=using)
    except Exception:
        # Malformed seed data, a stub not implemented yet, or tables torn down by `migrate generation zero`.
        logger.exception("Generation library seeding failed")
        return
    if verbosity >= 1:
        logger.info("Generation library seeding: %s", report)
