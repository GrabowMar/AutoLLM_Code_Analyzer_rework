"""Human-eval test configuration."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _human_eval_urlconf(settings):
    """Route requests through ``urls_u1`` (the project URLconf when it imports). DELETE at the U1 gate."""
    settings.ROOT_URLCONF = "backend.human_eval.tests.urls_u1"
