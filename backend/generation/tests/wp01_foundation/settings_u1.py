"""Test settings for WP-01 while merge unit U1 is in progress. DELETE at the U1 gate.

WP-01 lands the v3 models before WP-02a/WP-02b re-point the v2 consumers. Until they do, two things
break ``django.setup()`` or the system checks under ``config.settings.test``:

* ``backend.integrations`` connects webhook signals in ``ready()`` through
  ``apps.get_model("generation.GenerationJob")``, which no longer exists (WP-02b re-points
  ``integrations/services/webhooks.py``);
* the URLconf mounts ``backend.generation.api.views.router``, whose v2 view modules import removed models
  (WP-02a deletes them; ``config/api.py`` must then mount ``backend.generation.api.router``).

This module keeps ``config.settings.test`` and only swaps the integrations app config for one whose
``ready()`` connects nothing. Run the WP-01 suite with::

    .wt-test.sh wp01m -- pytest -q --create-db \\
        --ds=backend.generation.tests.wp01_foundation.settings_u1 backend/generation/tests/wp01_foundation

and management commands with ``DJANGO_SETTINGS_MODULE=backend.generation.tests.wp01_foundation.settings_u1``
(``migrate --skip-checks``, ``makemigrations --check``, ``check --tag models``).
"""

from backend.integrations.apps import IntegrationsConfig
from config.settings.test import *  # noqa: F403
from config.settings.test import INSTALLED_APPS


class IntegrationsWithoutSignalsConfig(IntegrationsConfig):
    """``backend.integrations`` without its webhook signal wiring (it names the deleted v2 models)."""

    def ready(self) -> None:
        return


INSTALLED_APPS = [
    f"{__name__}.IntegrationsWithoutSignalsConfig" if app == "backend.integrations" else app for app in INSTALLED_APPS
]
