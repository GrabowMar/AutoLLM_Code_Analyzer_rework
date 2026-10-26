"""Rankings test fixtures: fake the measurement/sample engines at their public boundary.

Tests that request the ``real_engine`` fixture opt out of the fakes and run
against the measure stage's actual implementation (contract check).
"""

from __future__ import annotations

import pytest

from backend.rankings.tests.measured import fake_engine  # noqa: F401 - fixture


@pytest.fixture
def real_engine():
    """Marker fixture: use the real measurement/sample engine in this test."""


@pytest.fixture(autouse=True)
def _engine_fakes(request):
    if "real_engine" in request.fixturenames:
        yield None
        return
    yield request.getfixturevalue("fake_engine")
