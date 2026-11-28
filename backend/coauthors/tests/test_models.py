"""Models and token helpers of the co-authors module."""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from backend.coauthors import services
from backend.coauthors.models import CoauthorInvite
from backend.coauthors.models import Coauthorship
from backend.coauthors.tests.factories import CoauthorInviteFactory
from backend.coauthors.tests.factories import CoauthorshipFactory
from backend.coauthors.tests.factories import make_invite
from backend.human_eval.tests.factories import EvaluatorProfileFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

# Characters a display name must never carry: a direction override and a zero-width space.
RLO = chr(0x202E)
ZWSP = chr(0x200B)


def test_token_format_and_hash_only_storage():
    owner = UserFactory()
    raw, invite = make_invite(owner, label=f"  for{RLO} Ada\n ")
    assert services.TOKEN_RE.match(raw)
    assert len(raw) == 47
    assert invite.token_hash == services.hash_token(raw)
    assert len(invite.token_hash) == 64
    assert invite.token_prefix == raw[:12]
    assert invite.label == "for Ada"
    stored = CoauthorInvite.objects.filter(pk=invite.pk).values().get()
    assert raw not in {str(value) for value in stored.values()}
    assert services.invite_path(raw) == f"/join#{raw}"
    assert new_tokens_differ()


def new_tokens_differ() -> bool:
    return services.new_token() != services.new_token()


def test_defaults():
    before = timezone.now()
    invite = CoauthorInviteFactory()
    assert invite.max_uses == 1
    assert invite.uses == 0
    assert invite.signup_uses == 0
    assert dt.timedelta(days=6, hours=23) < invite.expires_at - before < dt.timedelta(days=7, minutes=1)


def test_status_order():
    now = timezone.now()
    invite = CoauthorInviteFactory(max_uses=1)
    assert invite.status(now) == "active"
    invite.uses = 1
    assert invite.status(now) == "used_up"
    invite.expires_at = now
    assert invite.status(now) == "expired"
    invite.revoked_at = now
    assert invite.status(now) == "revoked"


def test_is_usable_needs_an_active_unrestricted_owner():
    invite = CoauthorInviteFactory()
    assert invite.is_usable()
    invite.created_by.is_active = False
    assert not invite.is_usable()
    invite.created_by.is_active = True
    EvaluatorProfileFactory(user=invite.created_by, is_restricted=True)
    assert not invite.is_usable()
    staff_invite = CoauthorInviteFactory(created_by=UserFactory(is_staff=True))
    EvaluatorProfileFactory(user=staff_invite.created_by, is_restricted=True)
    assert staff_invite.is_usable()


def test_unique_active_pair_and_rejoin_after_removal():
    first = CoauthorshipFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Coauthorship.objects.create(owner=first.owner, member=first.member)
    first.revoked_at = timezone.now()
    first.save(update_fields=["revoked_at"])
    second = Coauthorship.objects.create(owner=first.owner, member=first.member)
    assert Coauthorship.objects.filter(owner=first.owner, member=first.member).count() == 2
    assert second.is_active
    assert not first.is_active


def test_self_pair_rejected_by_the_database():
    user = UserFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Coauthorship.objects.create(owner=user, member=user)


def test_usable_coauthorships_and_resolve_selection():
    member = UserFactory()
    good = CoauthorshipFactory(member=member)
    ended = CoauthorshipFactory(member=member, revoked_at=timezone.now())
    inactive = CoauthorshipFactory(member=member, owner=UserFactory(is_active=False))
    restricted = CoauthorshipFactory(member=member)
    EvaluatorProfileFactory(user=restricted.owner, is_restricted=True)
    unrestricted = CoauthorshipFactory(member=member)
    EvaluatorProfileFactory(user=unrestricted.owner, is_restricted=False)
    restricted_staff = CoauthorshipFactory(member=member, owner=UserFactory(is_staff=True))
    EvaluatorProfileFactory(user=restricted_staff.owner, is_restricted=True)
    other = CoauthorshipFactory()

    usable = set(services.usable_coauthorships(member).values_list("pk", flat=True))
    assert usable == {good.pk, unrestricted.pk, restricted_staff.pk}
    assert services.resolve_selection(member, good.pk).owner == good.owner
    for bad in (ended.pk, inactive.pk, restricted.pk, other.pk, 10**9, str(good.pk), None, True, 1.0, [good.pk]):
        assert services.resolve_selection(member, bad) is None, bad


def test_touch_is_written_at_most_once_a_minute():
    row = CoauthorshipFactory()
    assert services.resolve_selection(row.member, row.pk).last_active_at is None
    first = services.resolve_selection(row.member, row.pk, touch=True).last_active_at
    assert first is not None
    assert services.resolve_selection(row.member, row.pk, touch=True).last_active_at == first
    Coauthorship.objects.filter(pk=row.pk).update(last_active_at=first - dt.timedelta(seconds=61))
    assert services.resolve_selection(row.member, row.pk, touch=True).last_active_at > first


def test_clean_name_display_name_and_masked_email():
    assert services.clean_name(f"  Ada{RLO} {ZWSP}Love\nlace\t ") == "Ada Love lace"
    assert services.clean_name("x" * 200) == "x" * 80
    assert services.clean_name(None) == ""
    named = UserFactory(name="Ada", email="ada@example.org")
    blank = UserFactory(name=f" {ZWSP} ", email="grace@example.org")
    assert services.display_name(named) == "Ada"
    assert services.display_name(blank) == "grace@example.org"
    assert services.masked_email("grace@example.org") == "g•••@example.org"


def test_deleting_an_account_cascades():
    row = CoauthorshipFactory()
    invite = CoauthorInviteFactory(created_by=row.owner)
    row.member.delete()
    assert not Coauthorship.objects.filter(pk=row.pk).exists()
    assert CoauthorInvite.objects.filter(pk=invite.pk).exists()
    invite.created_by.delete()
    assert not CoauthorInvite.objects.filter(pk=invite.pk).exists()
