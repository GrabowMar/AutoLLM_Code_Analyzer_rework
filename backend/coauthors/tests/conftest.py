"""Shared fixtures of the co-authors tests: an owner O, a member M and M's client with O's workspace selected."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client

from backend.coauthors.tests.factories import CoauthorshipFactory
from backend.coauthors.tests.factories import select
from backend.users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _clean_throttles():
    """Throttle counters live in the cache; keep tests independent of each other."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def owner(db):
    return UserFactory(name="Olga Owner", email="olga@example.org")


@pytest.fixture
def member(db):
    return UserFactory(name="Mia Member", email="mia@example.org")


@pytest.fixture
def coauthorship(owner, member):
    return CoauthorshipFactory(owner=owner, member=member)


def login(user: Any, **kwargs: Any) -> Client:
    """A fresh test client signed in as ``user``."""
    client = Client(**kwargs)
    client.force_login(user)
    return client


@pytest.fixture
def owner_client(owner):
    return login(owner)


@pytest.fixture
def member_client(member):
    """M signed in, working in their own account."""
    return login(member)


@pytest.fixture
def acting_client(member, coauthorship):
    """M signed in with O's workspace selected."""
    client = login(member)
    select(client, coauthorship)
    return client


def post(client: Client, path: str, payload: Any = None, **extra: Any):
    return client.post(
        path,
        data=json.dumps(payload if payload is not None else {}),
        content_type="application/json",
        **extra,
    )


def patch(client: Client, path: str, payload: Any, **extra: Any):
    return client.patch(path, data=json.dumps(payload), content_type="application/json", **extra)
