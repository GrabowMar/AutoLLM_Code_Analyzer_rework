"""Fixtures for the WP-05 tests (stacks, starters, build context, dependency caches).

Docker-dependent tests are marked ``docker`` and skipped unless ``LLMLAB_DOCKER_TESTS=1`` (spec 13.1). They
use ``settings.STARTERS_DOCKER_HOST`` (default: the apps daemon) and run manually, never during live runs.
"""

from __future__ import annotations

import os

import pytest

from backend.generation.build import starters

from .helpers import compose_entry
from .helpers import phase_a_entries


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: needs a Docker daemon (LLMLAB_DOCKER_TESTS=1)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("LLMLAB_DOCKER_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="docker test: set LLMLAB_DOCKER_TESTS=1")
    for item in items:
        if "docker" in item.keywords and "wp05_build" in str(item.path):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def composed() -> dict[str, starters.ComposedStarter]:
    """Every phase-A starter composed in memory (no database, no blobs)."""
    return {starters.starter_slug(e.stack, e.level): compose_entry(e) for e in phase_a_entries()}


@pytest.fixture(scope="session")
def docker_client():
    from backend.generation.build.locks import starters_docker_client

    client = starters_docker_client()
    client.ping()
    return client
