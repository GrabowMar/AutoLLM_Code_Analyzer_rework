"""App config: seeds the analysis catalog, system profiles and beat schedule after ``migrate``."""

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
    """Sync tools (data/tools), system profiles (data/profiles) and the reconcile beat entry.

    Never contacts Docker (``migrate`` must not depend on the apps daemon);
    tool versions come from the image via ``seed_analysis_tools`` or after
    an image build.
    """
    from backend.analysis.seeding import seed_all

    try:
        report = seed_all(using=using)
    except Exception:
        # seed_all isolates its steps; this only guards an import-time failure.
        logger.exception("Analysis catalog seeding failed")
        return
    if verbosity >= 1:
        logger.info("Analysis seeding: %s", report)
