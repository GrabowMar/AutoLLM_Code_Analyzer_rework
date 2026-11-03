"""Fixtures of the WP-03 suite: the golden library installed as system rows, and two users."""

from __future__ import annotations

import pytest

from backend.generation.testing.golden_library import InstalledGoldenLibrary
from backend.generation.testing.golden_library import install_golden_library
from backend.users.tests.factories import UserFactory


@pytest.fixture
def golden(db) -> InstalledGoldenLibrary:
    return install_golden_library()


@pytest.fixture
def alice(db):
    return UserFactory.create()


@pytest.fixture
def bob(db):
    return UserFactory.create()


@pytest.fixture
def staff(db):
    return UserFactory.create(is_staff=True)
