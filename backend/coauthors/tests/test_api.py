"""The co-authors API: invite links, preview, accept, context, switching, members, leaving and the activity trail."""

from __future__ import annotations

import datetime as dt

import pytest
from django.test import Client
from django.utils import timezone

from backend.coauthors import services
from backend.coauthors.models import CoauthorAction
from backend.coauthors.models import CoauthorInvite
from backend.coauthors.models import Coauthorship
from backend.coauthors.tests.conftest import login
from backend.coauthors.tests.conftest import post
from backend.coauthors.tests.factories import CoauthorInviteFactory
from backend.coauthors.tests.factories import CoauthorshipFactory
from backend.coauthors.tests.factories import make_invite
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/coauthors"
INVALID = {"detail": "This invite link is not valid or has expired.", "code": "invite_invalid", "errors": []}


def person(user):
    return {"id": user.pk, "email": user.email, "name": user.name, "display_name": user.name or user.email}


# ---------------------------------------------------------------------------
# Invite links
# ---------------------------------------------------------------------------


def test_create_invite_returns_the_token_once(owner_client, owner):
    before = timezone.now()
    response = post(owner_client, f"{API}/invites/", {"label": "For Mia"})
    assert response.status_code == 201, response.content
    body = response.json()
    assert set(body) == {
        "id",
        "label",
        "token_prefix",
        "status",
        "max_uses",
        "uses",
        "expires_at",
        "created_at",
        "revoked_at",
        "token",
        "path",
    }
    assert services.TOKEN_RE.match(body["token"])
    assert body["path"] == f"/join#{body['token']}"
    assert body["token_prefix"] == body["token"][:12]
    assert (body["label"], body["status"], body["max_uses"], body["uses"]) == ("For Mia", "active", 1, 0)
    invite = CoauthorInvite.objects.get(pk=body["id"])
    assert invite.created_by == owner
    assert dt.timedelta(days=6, hours=23) < invite.expires_at - before < dt.timedelta(days=7, minutes=1)

    listed = owner_client.get(f"{API}/invites/")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [invite.pk]
    assert "token" not in listed.json()[0]
    assert body["token"] not in listed.content.decode()


def test_create_invite_options_and_validation(owner_client):
    ok = post(owner_client, f"{API}/invites/", {"expires_in_days": 30, "max_uses": 20})
    assert ok.status_code == 201
    assert ok.json()["max_uses"] == 20
    for bad in (
        {"expires_in_days": 0},
        {"expires_in_days": 31},
        {"max_uses": 0},
        {"max_uses": 21},
        {"label": "x" * 121},
    ):
        assert post(owner_client, f"{API}/invites/", bad).status_code == 422, bad
    assert CoauthorInvite.objects.count() == 1


def test_at_most_twenty_active_links(owner_client, owner):
    for _ in range(20):
        make_invite(owner)
    refused = post(owner_client, f"{API}/invites/", {})
    assert refused.status_code == 409
    assert refused.json()["code"] == "too_many_invites"
    CoauthorInvite.objects.filter(created_by=owner).first().delete()
    CoauthorInviteFactory(created_by=owner, revoked_at=timezone.now())
    CoauthorInviteFactory(created_by=owner, expires_at=timezone.now())
    CoauthorInviteFactory(created_by=owner, uses=1, max_uses=1)
    assert post(owner_client, f"{API}/invites/", {}).status_code == 201


def test_revoke_invite_is_idempotent_and_owner_scoped(owner_client, owner):
    _raw, invite = make_invite(owner)
    foreign = CoauthorInviteFactory()
    delete_response = owner_client.delete(f"{API}/invites/{invite.pk}/")
    assert delete_response.status_code == 204
    invite.refresh_from_db()
    first = invite.revoked_at
    assert first is not None
    delete_response = owner_client.delete(f"{API}/invites/{invite.pk}/")
    assert delete_response.status_code == 204
    invite.refresh_from_db()
    assert invite.revoked_at == first
    missing = owner_client.delete(f"{API}/invites/{foreign.pk}/")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"
    foreign.refresh_from_db()
    assert foreign.revoked_at is None
    assert owner_client.get(f"{API}/invites/").json()[0]["status"] == "revoked"


def test_management_needs_a_session(client):
    for method, path in (
        ("get", f"{API}/invites/"),
        ("post", f"{API}/invites/"),
        ("get", f"{API}/members/"),
        ("get", f"{API}/context/"),
        ("post", f"{API}/workspace/"),
        ("post", f"{API}/join/accept/"),
    ):
        response = client.get(path) if method == "get" else post(client, path, {})
        assert response.status_code == 401, path


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_signed_out(client, owner):
    raw, invite = make_invite(owner)
    response = post(client, f"{API}/join/preview/", {"token": raw})
    assert response.status_code == 200, response.content
    assert response.json() == {
        "state": "valid",
        "inviter_name": "Olga Owner",
        "inviter_email": "o•••@example.org",
        "expires_at": response.json()["expires_at"],
        "authenticated": False,
        "can_sign_up": True,
        "coauthorship_id": None,
    }
    sent = dt.datetime.fromisoformat(response.json()["expires_at"])
    assert abs(sent - invite.expires_at) < dt.timedelta(milliseconds=2)


def test_preview_signed_in_states(owner, member, owner_client, member_client):
    raw, invite = make_invite(owner)
    valid = post(member_client, f"{API}/join/preview/", {"token": raw}).json()
    assert (valid["state"], valid["inviter_email"], valid["authenticated"], valid["can_sign_up"]) == (
        "valid",
        "olga@example.org",
        True,
        False,
    )
    own = post(owner_client, f"{API}/join/preview/", {"token": raw}).json()
    assert own["state"] == "own_invite"
    row = CoauthorshipFactory(owner=owner, member=member)
    CoauthorInvite.objects.filter(pk=invite.pk).update(uses=1)  # used up: a member still sees "already"
    already = post(member_client, f"{API}/join/preview/", {"token": raw}).json()
    assert (already["state"], already["coauthorship_id"]) == ("already_member", row.pk)
    stranger = post(login(UserFactory()), f"{API}/join/preview/", {"token": raw})
    assert stranger.status_code == 404
    assert stranger.json() == INVALID


def test_preview_rejects_long_or_missing_tokens(client):
    assert post(client, f"{API}/join/preview/", {"token": "x" * 65}).status_code == 422
    assert post(client, f"{API}/join/preview/", {}).status_code == 422
    assert post(client, f"{API}/join/preview/", {"token": "nope"}).json() == INVALID


# ---------------------------------------------------------------------------
# Accept, context, switch
# ---------------------------------------------------------------------------


def test_accept_joins_and_selects_the_workspace(owner, member, member_client):
    raw, invite = make_invite(owner, label="For Mia")
    response = post(member_client, f"{API}/join/accept/", {"token": raw})
    assert response.status_code == 200, response.content
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert row.invite == invite
    assert row.last_selected_at is not None
    body = response.json()
    assert body["status"] == "joined"
    workspace = {
        "coauthorship_id": row.pk,
        "owner": person(owner),
        "owner_is_staff": False,
        "joined_at": body["context"]["acting_as"]["joined_at"],
        "last_active_at": None,
    }
    assert body["context"] == {"actor": person(member), "acting_as": workspace, "workspaces": [workspace]}
    invite.refresh_from_db()
    assert invite.uses == 1
    assert member_client.session[services.SESSION_KEY] == row.pk
    # They land in the owner's workspace.
    assert member_client.get("/api/users/me/").json()["email"] == owner.email
    context = member_client.get(f"{API}/context/").json()
    assert context["actor"]["email"] == member.email
    assert context["acting_as"]["owner"]["email"] == owner.email

    again = post(member_client, f"{API}/join/accept/", {"token": raw})
    assert again.status_code == 200
    assert again.json()["status"] == "already_member"
    invite.refresh_from_db()
    assert invite.uses == 1
    assert Coauthorship.objects.count() == 1


def test_context_for_a_user_without_coauthorships(member_client, member):
    assert member_client.get(f"{API}/context/").json() == {
        "actor": person(member),
        "acting_as": None,
        "workspaces": [],
    }


def test_context_lists_only_usable_workspaces_newest_first(member, member_client):
    first = CoauthorshipFactory(member=member)
    second = CoauthorshipFactory(member=member)
    CoauthorshipFactory(member=member, revoked_at=timezone.now())
    CoauthorshipFactory(member=member, owner=UserFactory(is_active=False))
    CoauthorshipFactory()
    workspaces = member_client.get(f"{API}/context/").json()["workspaces"]
    assert [row["coauthorship_id"] for row in workspaces] == [second.pk, first.pk]


def test_switch_workspace_and_back(member, member_client, coauthorship, owner):
    other = CoauthorshipFactory(member=member)
    there = post(member_client, f"{API}/workspace/", {"coauthorship_id": coauthorship.pk})
    assert there.status_code == 200, there.content
    assert there.json()["acting_as"]["coauthorship_id"] == coauthorship.pk
    assert member_client.session[services.SESSION_KEY] == coauthorship.pk
    coauthorship.refresh_from_db()
    assert coauthorship.last_selected_at is not None
    assert member_client.get("/api/users/me/").json()["email"] == owner.email

    elsewhere = post(member_client, f"{API}/workspace/", {"coauthorship_id": other.pk})
    assert elsewhere.json()["acting_as"]["coauthorship_id"] == other.pk
    assert member_client.get("/api/users/me/").json()["email"] == other.owner.email

    back = post(member_client, f"{API}/workspace/", {"coauthorship_id": None})
    assert back.status_code == 200
    assert back.json()["acting_as"] is None
    assert len(back.json()["workspaces"]) == 2
    assert services.SESSION_KEY not in member_client.session
    assert not Coauthorship.objects.filter(member=member, last_selected_at__isnull=False).exists()
    assert member_client.get("/api/users/me/").json()["email"] == member.email


def test_switch_refuses_what_is_not_a_usable_membership(member_client, member, owner):
    foreign = CoauthorshipFactory()
    ended = CoauthorshipFactory(member=member, revoked_at=timezone.now())
    owned = CoauthorshipFactory(owner=member)
    for bad in (foreign.pk, ended.pk, owned.pk, 10**9):
        response = post(member_client, f"{API}/workspace/", {"coauthorship_id": bad})
        assert response.status_code == 404, bad
        assert response.json()["code"] == "not_found"
    assert services.SESSION_KEY not in member_client.session
    assert post(member_client, f"{API}/workspace/", {"coauthorship_id": "abc"}).status_code == 422
    assert post(member_client, f"{API}/workspace/", {}).status_code == 422


# ---------------------------------------------------------------------------
# Members and leaving
# ---------------------------------------------------------------------------


def test_members_list_active_first_then_ended(owner, owner_client):
    _raw, invite = make_invite(owner, label="Team", max_uses=5)
    now = timezone.now()
    left_member = UserFactory()
    left = CoauthorshipFactory(owner=owner, member=left_member, revoked_at=now, revoked_by=left_member)
    removed = CoauthorshipFactory(owner=owner, revoked_at=now - dt.timedelta(hours=1), revoked_by=owner)
    active_old = CoauthorshipFactory(owner=owner, invite=invite)
    active_new = CoauthorshipFactory(owner=owner)
    CoauthorshipFactory()
    response = owner_client.get(f"{API}/members/")
    assert response.status_code == 200
    rows = response.json()
    assert [row["coauthorship_id"] for row in rows] == [active_new.pk, active_old.pk, left.pk, removed.pk]
    assert [row["status"] for row in rows] == ["active", "active", "left", "removed"]
    assert rows[1]["invite_label"] == "Team"
    assert rows[0]["invite_label"] == ""
    assert rows[0]["member"] == person(active_new.member)
    assert rows[0]["ended_at"] is None
    assert rows[2]["ended_at"] is not None
    assert set(rows[0]) == {
        "coauthorship_id",
        "member",
        "status",
        "joined_at",
        "last_active_at",
        "ended_at",
        "invite_label",
    }


def test_remove_member_is_idempotent_and_owner_scoped(owner, owner_client, coauthorship):
    foreign = CoauthorshipFactory()
    delete_response = owner_client.delete(f"{API}/members/{coauthorship.pk}/")
    assert delete_response.status_code == 204
    coauthorship.refresh_from_db()
    ended = coauthorship.revoked_at
    assert ended is not None
    assert coauthorship.revoked_by == owner
    delete_response = owner_client.delete(f"{API}/members/{coauthorship.pk}/")
    assert delete_response.status_code == 204
    coauthorship.refresh_from_db()
    assert coauthorship.revoked_at == ended
    delete_response = owner_client.delete(f"{API}/members/{foreign.pk}/")
    assert delete_response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.revoked_at is None
    assert Coauthorship.objects.count() == 2  # rows are never deleted


def test_leave_clears_the_selection_and_is_idempotent(member, acting_client, coauthorship, owner):
    assert acting_client.get("/api/users/me/").json()["email"] == owner.email
    delete_response = acting_client.delete(f"{API}/memberships/{coauthorship.pk}/")
    assert delete_response.status_code == 204
    coauthorship.refresh_from_db()
    assert coauthorship.revoked_by == member
    assert coauthorship.last_selected_at is None
    assert services.SESSION_KEY not in acting_client.session
    assert acting_client.get("/api/users/me/").json()["email"] == member.email
    delete_response = acting_client.delete(f"{API}/memberships/{coauthorship.pk}/")
    assert delete_response.status_code == 204
    foreign = CoauthorshipFactory()
    delete_response = acting_client.delete(f"{API}/memberships/{foreign.pk}/")
    assert delete_response.status_code == 404
    # The owner cannot "leave" on the member's behalf through this route.
    theirs = CoauthorshipFactory(owner=member)
    delete_response = acting_client.delete(f"{API}/memberships/{theirs.pk}/")
    assert delete_response.status_code == 404
    assert owner_listed_status(owner, coauthorship) == "left"


def owner_listed_status(owner, coauthorship) -> str:
    rows = login(owner).get(f"{API}/members/").json()
    return next(row["status"] for row in rows if row["coauthorship_id"] == coauthorship.pk)


# ---------------------------------------------------------------------------
# Activity trail
# ---------------------------------------------------------------------------


def test_activity_pages_back_with_before(owner_client, coauthorship):
    for index in range(7):
        CoauthorAction.objects.create(
            coauthorship=coauthorship,
            method="POST",
            path=f"/api/x/{index}/",
            status_code=200,
        )
    ids = list(CoauthorAction.objects.order_by("-id").values_list("pk", flat=True))
    url = f"{API}/members/{coauthorship.pk}/activity/"
    first = owner_client.get(url, {"limit": 3}).json()
    assert [row["id"] for row in first] == ids[:3]
    assert set(first[0]) == {"id", "method", "path", "status_code", "created_at"}
    second = owner_client.get(url, {"limit": 3, "before": first[-1]["id"]}).json()
    assert [row["id"] for row in second] == ids[3:6]
    last = owner_client.get(url, {"limit": 3, "before": second[-1]["id"]}).json()
    assert [row["id"] for row in last] == ids[6:]
    assert len(owner_client.get(url).json()) == 7
    for bad in ({"limit": 0}, {"limit": 201}, {"before": 0}):
        assert owner_client.get(url, bad).status_code == 422, bad


def test_activity_of_an_ended_coauthorship_stays_readable(owner_client, coauthorship):
    CoauthorAction.objects.create(coauthorship=coauthorship, method="DELETE", path="/api/x/", status_code=204)
    owner_client.delete(f"{API}/members/{coauthorship.pk}/")
    rows = owner_client.get(f"{API}/members/{coauthorship.pk}/activity/").json()
    assert [(row["method"], row["path"], row["status_code"]) for row in rows] == [("DELETE", "/api/x/", 204)]


def test_no_route_takes_a_token_in_its_url_or_on_get(client):
    raw = services.new_token()
    for path in (f"{API}/join/preview/", f"{API}/join/accept/"):
        assert client.get(path).status_code == 405
    for method in ("get", "post", "put", "delete"):
        assert getattr(Client(), method)(f"{API}/join/{raw}/").status_code == 404
