from __future__ import annotations

import logging

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class InferenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.inference"
    verbose_name = _("Inference")

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        # The system OpenRouter row must survive transactional_db/live_server
        # flushes under --reuse-db, so it is re-seeded after every migrate
        # (same pattern as backend.generation.apps), not only by RunPython.
        post_migrate.connect(
            _seed_system_endpoint,
            sender=self,
            dispatch_uid="backend.inference.seed_system_endpoint",
        )


def _seed_system_endpoint(sender, *, using, verbosity=1, **kwargs) -> None:
    """``get_or_create`` the single ``is_system`` OpenRouter endpoint."""
    from django.db import connections

    from backend.inference.models import SYSTEM_ENDPOINT_DEFAULTS
    from backend.inference.models import LLMEndpoint

    # After `migrate inference zero` the table is gone: nothing to seed.
    if "inference_llmendpoint" not in connections[using].introspection.table_names():
        return
    try:
        LLMEndpoint.objects.using(using).get_or_create(is_system=True, defaults=dict(SYSTEM_ENDPOINT_DEFAULTS))
    except Exception:
        # Seeding must never break `migrate` (e.g. `migrate inference zero`).
        logger.exception("Seeding the system OpenRouter endpoint failed")
