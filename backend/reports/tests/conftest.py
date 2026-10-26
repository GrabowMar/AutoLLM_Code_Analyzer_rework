"""Reports test fixtures: fake the measurement/sample engines at their public boundary."""

from __future__ import annotations

import pytest

from backend.rankings.tests.measured import fake_engine  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _engine_fakes(fake_engine):  # noqa: F811
    return fake_engine
