"""Factories and helpers for the co-authors tests."""

from __future__ import annotations

from typing import Any

import factory
from factory.django import DjangoModelFactory

from backend.coauthors import services
from backend.coauthors.models import CoauthorInvite
from backend.coauthors.models import Coauthorship
from backend.users.tests.factories import UserFactory


class CoauthorInviteFactory(DjangoModelFactory):
    """An invite whose raw token is unknown (use :func:`make_invite` when the token is needed)."""

    class Meta:
        model = CoauthorInvite

    created_by = factory.SubFactory(UserFactory)
    token_hash = factory.LazyFunction(lambda: services.hash_token(services.new_token()))
    token_prefix = "coi_factory0"  # noqa: S105 - a display prefix
    label = ""


class CoauthorshipFactory(DjangoModelFactory):
    class Meta:
        model = Coauthorship

    owner = factory.SubFactory(UserFactory)
    member = factory.SubFactory(UserFactory)
    invite = None


def make_invite(owner: Any, **kwargs: Any) -> tuple[str, CoauthorInvite]:
    """``(raw token, invite)`` created through the service, like the API does."""
    return services.create_invite(owner, **kwargs)


def select(client: Any, coauthorship: Coauthorship) -> None:
    """Put a workspace selection into the test client's session, as accept and switch do."""
    session = client.session
    session[services.SESSION_KEY] = coauthorship.pk
    session.save()
