"""Statistics test fixtures: engine fakes, no self-test record, a clean cache."""

from __future__ import annotations

import pytest

from backend.rankings.tests.measured import fake_engine  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _engine_fakes(fake_engine, monkeypatch):  # noqa: F811
    from django.core.cache import cache

    from backend.analysis.engine import canary
    from backend.analysis.engine import sandbox

    monkeypatch.setattr(canary, "latest", lambda image_tag=None: None)
    monkeypatch.setattr(canary, "latest_per_tool", lambda image_tag=None: {}, raising=False)
    monkeypatch.setattr(sandbox, "image_tag", lambda: "llmlab/analyzer:test")
    # Dashboard aggregations cache for 15 s keyed by user; tests must not
    # see each other's payloads.
    cache.clear()
    yield fake_engine
    cache.clear()
