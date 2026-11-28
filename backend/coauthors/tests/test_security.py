"""Security tests of the co-authors module.

Each test carries the id (S01-S34) the security section of ``docs/COAUTHORS.md``
points at. M is a co-author (member) of the workspace owner O.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import logging
import threading
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from asgiref.sync import async_to_sync
from asgiref.sync import sync_to_async
from django.apps import apps
from django.conf import settings as django_settings
from django.contrib import admin
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.db import connection
from django.db import connections
from django.http import HttpResponse
from django.http import StreamingHttpResponse
from django.test import Client
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import get_resolver
from django.urls import reverse
from django.utils import timezone

from backend.coauthors import middleware
from backend.coauthors import policy
from backend.coauthors import services
from backend.coauthors.auth import CoauthorSessionAuth
from backend.coauthors.auth import OwnAccountAuth
from backend.coauthors.auth import PersonalSessionAuth
from backend.coauthors.errors import CoauthorError
from backend.coauthors.models import CoauthorAction
from backend.coauthors.models import CoauthorInvite
from backend.coauthors.models import Coauthorship
from backend.coauthors.policy import Mode
from backend.coauthors.tests.conftest import login
from backend.coauthors.tests.conftest import patch
from backend.coauthors.tests.conftest import post
from backend.coauthors.tests.factories import CoauthorshipFactory
from backend.coauthors.tests.factories import make_invite
from backend.coauthors.tests.factories import select
from backend.common.crypto import encrypt
from backend.human_eval.tests.factories import EvaluatorProfileFactory
from backend.inference.models import LLMEndpoint
from backend.integrations.models import WebhookSubscription
from backend.integrations.services import webhooks
from backend.runtime.models import ContainerInstance
from backend.runtime.tests.factories import ContainerInstanceFactory
from backend.tokens.models import ApiToken
from backend.tokens.services import generate_token
from backend.users.adapters import AccountAdapter
from backend.users.models import User
from backend.users.tests.factories import UserFactory
from config.api import api
from config.settings.base import build_headless_frontend_urls

pytestmark = pytest.mark.django_db

API = "/api/coauthors"
ALLAUTH = "/_allauth/browser/v1"
DENIED = {
    "detail": "This belongs to the account owner's sign-in. Switch to your own account to do this.",
    "code": "acting_denied",
    "errors": [],
}
ENDED = {"detail": "You no longer have access to this workspace.", "code": "workspace_ended", "errors": []}
INVALID = {"detail": "This invite link is not valid or has expired.", "code": "invite_invalid", "errors": []}
RATE_LIMITED = {"detail": "Too many attempts. Try again in a few minutes.", "code": "rate_limited", "errors": []}
PASSWORD = "A-long-first-password-472!"  # noqa: S105 - test credential
NEW_PASSWORD = "Another-long-password-913?"  # noqa: S105 - test credential
ORIGIN = "https://lab.example.org"


def me(client) -> str:
    return client.get("/api/users/me/").json()["email"]


# ---------------------------------------------------------------------------
# 14.1 Getting more than the owner's data and operations
# ---------------------------------------------------------------------------


def test_s01_sign_in_stays_the_real_person(settings, owner):
    settings.ACCOUNT_RATE_LIMITS = False
    member = UserFactory(email="mia@example.org", password=PASSWORD)
    EmailAddress.objects.create(user=member, email=member.email, primary=True, verified=True)
    coauthorship = CoauthorshipFactory(owner=owner, member=member)
    client = login(member)
    select(client, coauthorship)
    owner_hash = owner.password
    assert me(client) == owner.email

    session = client.get(f"{ALLAUTH}/auth/session")
    assert session.status_code == 200
    assert session.json()["data"]["user"]["email"] == member.email

    changed = post(
        client,
        f"{ALLAUTH}/account/password/change",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code in (200, 401), changed.content
    member.refresh_from_db()
    owner.refresh_from_db()
    assert member.check_password(NEW_PASSWORD)
    assert owner.password == owner_hash

    client = login(member)
    select(client, coauthorship)
    added = post(client, f"{ALLAUTH}/account/email", {"email": "second@example.org"})
    assert added.status_code == 200, added.content
    assert EmailAddress.objects.filter(user=member, email="second@example.org").exists()
    assert not EmailAddress.objects.filter(user=owner).exists()
    owner.refresh_from_db()
    assert owner.email == "olga@example.org"


def test_s02_personal_tokens_are_denied(acting_client, owner, member, coauthorship):
    _raw, owner_token = generate_token(owner, "owner-cli")
    for response in (
        acting_client.get("/api/tokens/"),
        post(acting_client, "/api/tokens/", {"name": "sneaky"}),
        acting_client.delete(f"/api/tokens/{owner_token.id}/"),
    ):
        assert response.status_code == 403
        assert response.json() == DENIED
    assert ApiToken.objects.filter(user=owner).count() == 1
    owner_token.refresh_from_db()
    assert owner_token.revoked_at is None
    assert not ApiToken.objects.filter(user=member).exists()


def test_s02_personal_session_auth_refuses_a_swapped_request(owner, member):
    request = RequestFactory().get("/api/tokens/")
    request.user = owner
    request._dont_enforce_csrf_checks = True
    assert PersonalSessionAuth()(request) == owner
    request.actor = member
    with pytest.raises(CoauthorError) as denied:
        PersonalSessionAuth()(request)
    assert (denied.value.status, denied.value.code) == (403, "acting_denied")


def test_s02_the_token_router_refuses_even_if_the_policy_let_it_through(acting_client, owner, monkeypatch):
    monkeypatch.setattr(policy, "decide", lambda path, method: Mode.SWAP)
    response = post(acting_client, "/api/tokens/", {"name": "sneaky"})
    assert response.status_code == 403
    assert response.json() == DENIED
    assert not ApiToken.objects.filter(user=owner).exists()


def test_s03_user_routes(acting_client, owner):
    for response in (
        patch(acting_client, "/api/users/me/", {"name": "Renamed"}),
        patch(acting_client, f"/api/users/{owner.pk}/", {"name": "Renamed"}),
        post(acting_client, "/api/users/", {}),
        acting_client.delete("/api/users/me/"),
        acting_client.get("/api/users/some-new-route/"),
        acting_client.get("/api/users/me"),
    ):
        assert response.status_code == 403
        assert response.json() == DENIED
    owner.refresh_from_db()
    assert owner.name == "Olga Owner"


def test_s04_management_is_denied_while_a_workspace_is_selected(acting_client, owner, member, coauthorship):
    _raw, invite = make_invite(owner)
    for response in (
        acting_client.get(f"{API}/invites/"),
        post(acting_client, f"{API}/invites/", {}),
        acting_client.delete(f"{API}/invites/{invite.pk}/"),
        acting_client.get(f"{API}/members/"),
        acting_client.delete(f"{API}/members/{coauthorship.pk}/"),
        acting_client.get(f"{API}/members/{coauthorship.pk}/activity/"),
    ):
        assert response.status_code == 403
        assert response.json() == DENIED
    assert CoauthorInvite.objects.count() == 1
    invite.refresh_from_db()
    coauthorship.refresh_from_db()
    assert invite.revoked_at is None
    assert coauthorship.revoked_at is None


def test_s04_management_is_never_reachable_with_a_bearer_token(owner, member):
    owner_raw, _ = generate_token(owner, "cli")
    raw, _invite = make_invite(owner)
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {owner_raw}"}
    client = Client()
    assert client.get(f"{API}/invites/", **bearer).status_code == 401
    assert post(client, f"{API}/invites/", {}, **bearer).status_code == 401
    assert client.get(f"{API}/members/", **bearer).status_code == 401
    assert client.get(f"{API}/context/", **bearer).status_code == 401
    member_raw, _ = generate_token(member, "cli")
    accept = post(client, f"{API}/join/accept/", {"token": raw}, HTTP_AUTHORIZATION=f"Bearer {member_raw}")
    assert accept.status_code == 401
    assert CoauthorInvite.objects.count() == 1
    assert not Coauthorship.objects.exists()


def test_s04_a_token_never_satisfies_the_coauthor_router(owner, member_client, member, coauthorship):
    """Also when something else already put a user on the request, as the MCP dispatcher does."""
    raw, _ = generate_token(owner, "cli")
    for auth_class in (CoauthorSessionAuth, OwnAccountAuth):
        request = RequestFactory().post(f"{API}/invites/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        request.user = owner
        request._dont_enforce_csrf_checks = True
        assert auth_class()(request) is None
    # A session cookie does not help a request that also presents a token.
    for value in (f"Bearer {raw}", "junk"):
        assert member_client.get(f"{API}/context/", HTTP_AUTHORIZATION=value).status_code == 401
        assert post(member_client, f"{API}/invites/", {}, HTTP_AUTHORIZATION=value).status_code == 401
    assert not CoauthorInvite.objects.exists()


def test_s04_coauthor_routes_are_not_reachable_through_mcp(owner, rf):
    from backend.integrations.mcp import openapi
    from backend.integrations.mcp.dispatch import DispatchError
    from backend.integrations.mcp.dispatch import call_api

    assert "/api/coauthors/" in openapi.EXCLUDED_PATH_PREFIXES
    assert not any(op.path.startswith("/api/coauthors") for op in openapi.operations().values())
    raw, _ = generate_token(owner, "mcp")
    origin = rf.post("/api/mcp")
    for method, path in (
        ("POST", f"{API}/invites/"),
        ("GET", f"{API}/members/"),
        ("GET", f"{API}/context/"),
        ("POST", f"{API}/join/accept/"),
        ("POST", f"{API}/join/preview/"),
    ):
        with pytest.raises(DispatchError):
            call_api(origin, raw, owner, method, path, body={})
    assert not CoauthorInvite.objects.exists()


def test_s04_own_account_auth_refuses_while_any_workspace_is_selected(owner, coauthorship):
    request = RequestFactory().get(f"{API}/invites/")
    request.user = owner
    request._dont_enforce_csrf_checks = True
    assert OwnAccountAuth()(request) == owner
    request.coauthorship = coauthorship
    with pytest.raises(CoauthorError) as denied:
        OwnAccountAuth()(request)
    assert denied.value.code == "acting_denied"


def test_s04_management_refuses_even_if_the_policy_let_it_through(acting_client, monkeypatch):
    monkeypatch.setattr(policy, "decide", lambda path, method: Mode.REAL)
    response = post(acting_client, f"{API}/invites/", {})
    assert response.status_code == 403
    assert response.json() == DENIED
    monkeypatch.setattr(policy, "decide", lambda path, method: Mode.SWAP)
    assert post(acting_client, f"{API}/invites/", {}).status_code == 403
    assert not CoauthorInvite.objects.exists()


def test_s05_not_transitive(acting_client, owner, member, coauthorship):
    third = UserFactory(email="pat@example.org")
    owner_in_third = CoauthorshipFactory(owner=third, member=owner)

    context = acting_client.get(f"{API}/context/").json()
    assert context["actor"]["email"] == member.email
    assert [row["coauthorship_id"] for row in context["workspaces"]] == [coauthorship.pk]

    refused = post(acting_client, f"{API}/workspace/", {"coauthorship_id": owner_in_third.pk})
    assert refused.status_code == 404
    assert me(acting_client) == owner.email

    # Joining somewhere else while working as O creates M's membership, not O's.
    fourth = UserFactory(email="quinn@example.org")
    raw, _invite = make_invite(fourth)
    joined = post(acting_client, f"{API}/join/accept/", {"token": raw})
    assert joined.status_code == 200, joined.content
    assert Coauthorship.objects.filter(owner=fourth, member=member).exists()
    assert not Coauthorship.objects.filter(owner=fourth, member=owner).exists()
    assert me(acting_client) == fourth.email


def test_s06_admin_is_never_swapped(member, settings):
    staff_owner = UserFactory(is_staff=True, is_superuser=True)
    client = login(member)
    select(client, CoauthorshipFactory(owner=staff_owner, member=member))
    assert me(client) == staff_owner.email
    response = client.get(f"/{settings.ADMIN_URL}")
    assert response.status_code == 302
    assert "login" in response["Location"]
    listing = client.get(reverse("admin:coauthors_coauthorship_changelist"))
    assert listing.status_code == 302


def test_s07_staff_parity_both_ways(member, monkeypatch):
    monkeypatch.setattr("backend.system.api.views.services.db_stats", lambda: {"ok": True})
    staff_owner = UserFactory(is_staff=True)
    client = login(member)
    assert client.get("/api/system/db").status_code == 403
    select(client, CoauthorshipFactory(owner=staff_owner, member=member))
    assert client.get("/api/system/db").json() == {"ok": True}

    staff_member = UserFactory(is_staff=True)
    plain_owner = UserFactory()
    other = login(staff_member)
    assert other.get("/api/system/db").status_code == 200
    select(other, CoauthorshipFactory(owner=plain_owner, member=staff_member))
    assert other.get("/api/system/db").status_code == 403


@pytest.fixture
def direct_endpoint(settings):
    settings.INFERENCE_DIRECT_ALLOWED_TARGETS = ["100.64.0.0/10:11434"]
    staff_owner = UserFactory(is_staff=True)
    endpoint = LLMEndpoint.objects.create(
        slug="tail-box",
        name="Tail box",
        kind="openai_compatible",
        connection="direct",
        api_flavor="openai",
        base_url="http://gpu.tail:11434",
        encrypted_api_key=encrypt("sk-supersecret"),
        api_key_prefix="sk-super",
        max_concurrency=2,
        owner=staff_owner,
    )
    with mock.patch("backend.inference.security.resolve", return_value=("100.64.1.2",)):
        yield endpoint


def test_s08_a_stored_key_cannot_be_sent_to_a_new_address(direct_endpoint, member):
    owner = direct_endpoint.owner
    url = f"/api/inference/endpoints/{direct_endpoint.pk}/"
    acting = login(member)
    select(acting, CoauthorshipFactory(owner=owner, member=member))
    stored = direct_endpoint.encrypted_api_key

    moved = patch(acting, url, {"base_url": "http://evil.tail:11434"})
    assert moved.status_code == 400
    assert moved.json()["detail"] == "Enter the API key again to change the address of this endpoint."
    explicit_null = patch(acting, url, {"base_url": "http://evil.tail:11434", "api_key": None})
    assert explicit_null.status_code == 400
    direct_endpoint.refresh_from_db()
    assert direct_endpoint.base_url == "http://gpu.tail:11434"
    assert direct_endpoint.encrypted_api_key == stored

    # The same address, and other fields, are not affected.
    assert patch(acting, url, {"base_url": "http://gpu.tail:11434/", "name": "Renamed"}).status_code == 200
    direct_endpoint.refresh_from_db()
    assert direct_endpoint.encrypted_api_key == stored

    with_key = patch(acting, url, {"base_url": "http://other.tail:11434", "api_key": "sk-their-own-key"})
    assert with_key.status_code == 200, with_key.content
    direct_endpoint.refresh_from_db()
    assert direct_endpoint.base_url == "http://other.tail:11434"
    assert direct_endpoint.api_key_prefix == "sk-their"
    assert direct_endpoint.encrypted_api_key != stored

    cleared = patch(acting, url, {"base_url": "http://third.tail:11434", "api_key": ""})
    assert cleared.status_code == 200
    direct_endpoint.refresh_from_db()
    assert direct_endpoint.encrypted_api_key == ""


def test_s08_the_owner_in_their_own_account_is_unchanged(direct_endpoint):
    stored = direct_endpoint.encrypted_api_key
    response = patch(
        login(direct_endpoint.owner),
        f"/api/inference/endpoints/{direct_endpoint.pk}/",
        {"base_url": "http://other.tail:11434"},
    )
    assert response.status_code == 200, response.content
    direct_endpoint.refresh_from_db()
    assert direct_endpoint.base_url == "http://other.tail:11434"
    assert direct_endpoint.encrypted_api_key == stored


@pytest.fixture
def ntfy_webhook(owner):
    """O's ntfy subscription with a stored access token; every host resolves to a public address."""
    sub = WebhookSubscription(user=owner, name="phone", kind="ntfy", events=[])
    sub.set_url("https://ntfy.sh/owner-topic")
    sub.set_secret("tk_owner_secret")
    sub.save()
    with mock.patch.object(webhooks.security, "resolve", return_value=("93.184.216.34",)):
        yield sub


def test_s08b_a_stored_webhook_secret_cannot_be_sent_to_a_new_address(ntfy_webhook, acting_client):
    url = f"/api/integrations/webhooks/{ntfy_webhook.pk}/"
    stored = ntfy_webhook.encrypted_secret

    moved = patch(acting_client, url, {"url": "https://example.org/attacker-topic"})
    assert moved.status_code == 400
    assert moved.json()["detail"] == "Enter the secret again to change the address of this webhook."
    explicit_null = patch(acting_client, url, {"url": "https://example.org/attacker-topic", "secret": None})
    assert explicit_null.status_code == 400
    ntfy_webhook.refresh_from_db()
    assert ntfy_webhook.get_url() == "https://ntfy.sh/owner-topic"
    assert ntfy_webhook.encrypted_secret == stored

    # The same address, and other fields, are not affected.
    same = patch(acting_client, url, {"url": "https://ntfy.sh/owner-topic", "name": "renamed"})
    assert same.status_code == 200, same.content
    ntfy_webhook.refresh_from_db()
    assert ntfy_webhook.name == "renamed"
    assert ntfy_webhook.get_secret() == "tk_owner_secret"

    with_secret = patch(acting_client, url, {"url": "https://example.org/topic", "secret": "tk_their_own"})
    assert with_secret.status_code == 200, with_secret.content
    ntfy_webhook.refresh_from_db()
    assert ntfy_webhook.get_url() == "https://example.org/topic"
    assert ntfy_webhook.get_secret() == "tk_their_own"

    cleared = patch(acting_client, url, {"url": "https://example.net/topic", "secret": ""})
    assert cleared.status_code == 200
    ntfy_webhook.refresh_from_db()
    assert ntfy_webhook.encrypted_secret == ""
    # Without a stored secret there is nothing to protect: the address changes freely.
    assert patch(acting_client, url, {"url": "https://example.com/topic"}).status_code == 200


def test_s08b_the_owner_in_their_own_account_is_unchanged(ntfy_webhook, owner_client):
    response = patch(
        owner_client,
        f"/api/integrations/webhooks/{ntfy_webhook.pk}/",
        {"url": "https://example.org/new-topic"},
    )
    assert response.status_code == 200, response.content
    ntfy_webhook.refresh_from_db()
    assert ntfy_webhook.get_url() == "https://example.org/new-topic"
    assert ntfy_webhook.get_secret() == "tk_owner_secret"


# Every stored secret (``encrypted_*`` field) and where it is sent. A secret whose destination a
# co-author can change must need the secret again in the same request (``is_acting`` guard in the
# named handler). A new encrypted field fails ``test_s08_every_stored_secret_is_classified`` until
# it is classified here.
STORED_SECRETS = {
    "credentials.UserApiCredential.encrypted_secret": ("fixed provider host", None),
    "inference.LLMEndpoint.encrypted_api_key": (
        "base_url",
        "backend.inference.api.views.update_endpoint",
    ),
    "integrations.WebhookSubscription.encrypted_url": ("is the destination itself", None),
    "integrations.WebhookSubscription.encrypted_secret": (
        "url",
        "backend.integrations.api.webhooks.update_subscription",
    ),
}
# Route handlers that read a stored secret themselves, and why that is safe while acting.
SECRET_READING_HANDLERS = {
    "backend.credentials.api.views.set_credential_key": "writes the key; nothing is returned",
    "backend.credentials.api.views.test_credential_key": "sends the key to its fixed provider host",
    "backend.inference.api.views.update_endpoint": "guarded (S08)",
    "backend.integrations.api.webhooks.update_subscription": "guarded (S08b)",
}


def _api_handlers():
    for _prefix, router in api._routers:
        for path_view in router.path_operations.values():
            for operation in path_view.operations:
                yield operation.view_func


def test_s08_every_stored_secret_is_classified():
    found = {
        f"{model._meta.app_label}.{model.__name__}.{field.name}"
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if field.name.startswith("encrypted_")
    }
    assert found == set(STORED_SECRETS)

    handlers = {f"{func.__module__}.{func.__qualname__}": func for func in _api_handlers()}
    for destination, guard in STORED_SECRETS.values():
        if guard is None:
            continue
        assert guard in handlers, f"{guard} (changes {destination}) is no longer a route handler"
        assert "is_acting(request)" in inspect.getsource(handlers[guard]), guard

    readers = {
        name
        for name, func in handlers.items()
        if any(needle in inspect.getsource(func) for needle in ("get_secret(", "encrypted_", "decrypt("))
    }
    assert readers == set(SECRET_READING_HANDLERS)


def test_s09_apps_are_never_swapped(acting_client, owner_client, owner, monkeypatch, settings):
    settings.APPS_SUBDOMAIN_PROXY = False
    settings.TRAEFIK_DYNAMIC_DIR = ""
    seen: list = []

    def fake_proxy(request, container, path, **kwargs):
        seen.append((request.user.pk, getattr(request, "actor", None)))
        return HttpResponse("proxied")

    monkeypatch.setattr("backend.runtime.proxy._proxy", fake_proxy)
    ContainerInstanceFactory(created_by=owner, name="owner-app", status=ContainerInstance.Status.RUNNING)
    assert me(acting_client) == owner.email
    response = acting_client.get("/apps/owner-app/")
    assert response.status_code == 404
    assert seen == []
    # The owner (no own-origin apps on this deployment) still reaches it, as themselves.
    assert owner_client.get("/apps/owner-app/").content == b"proxied"
    assert seen == [(owner.pk, None)]


def test_s10_apps_gate(settings, owner, member, coauthorship, monkeypatch):
    monkeypatch.setattr("backend.runtime.proxy._proxy", lambda request, container, path, **kw: HttpResponse("proxied"))
    ContainerInstanceFactory(created_by=owner, name="owner-app", status=ContainerInstance.Status.RUNNING)
    ContainerInstanceFactory(created_by=member, name="member-app", status=ContainerInstance.Status.RUNNING)
    plain = UserFactory()
    ContainerInstanceFactory(created_by=plain, name="plain-app", status=ContainerInstance.Status.RUNNING)
    owner_client, member_client, plain_client = login(owner), login(member), login(plain)

    settings.APPS_SUBDOMAIN_PROXY = False
    settings.TRAEFIK_DYNAMIC_DIR = ""
    assert owner_client.get("/apps/owner-app/").content == b"proxied"

    for own_origin in ({"APPS_SUBDOMAIN_PROXY": True}, {"TRAEFIK_DYNAMIC_DIR": "/etc/traefik/dynamic"}):
        settings.APPS_SUBDOMAIN_PROXY = own_origin.get("APPS_SUBDOMAIN_PROXY", False)
        settings.TRAEFIK_DYNAMIC_DIR = own_origin.get("TRAEFIK_DYNAMIC_DIR", "")
        blocked = owner_client.get("/apps/owner-app/")
        assert blocked.status_code == 403
        assert blocked.content == b"This app opens at its own address. Use the link on the application page."
        assert blocked["Content-Type"].startswith("text/plain")
        assert owner_client.get("/apps/owner-app/static/app.js").status_code == 403
        # People who never shared a workspace are not affected; a member is not an owner.
        assert plain_client.get("/apps/plain-app/").content == b"proxied"
        assert member_client.get("/apps/member-app/").content == b"proxied"
        assert Client().get("/apps/owner-app/").status_code == 403  # the proxy's own "Authentication required"

    # An ended co-authorship still counts: what the co-author built is still there.
    services.remove_member(owner, coauthorship.pk)
    assert owner_client.get("/apps/owner-app/").status_code == 403


def test_s10_apps_gate_on_the_async_path(settings, owner, coauthorship):
    settings.APPS_SUBDOMAIN_PROXY = True

    async def view(request):
        return HttpResponse("proxied")

    def request_for(user):
        request = RequestFactory().get("/apps/owner-app/")
        request.user = user

        async def auser():
            return user

        request.auser = auser
        return request

    gate = middleware.WorkspaceMiddleware(view)
    assert async_to_sync(gate)(request_for(owner)).status_code == 403
    assert async_to_sync(gate)(request_for(coauthorship.member)).content == b"proxied"
    settings.APPS_SUBDOMAIN_PROXY = False
    assert async_to_sync(gate)(request_for(owner)).content == b"proxied"


def test_s11_restricted_evaluators_cannot_join(owner):
    evaluator = UserFactory()
    EvaluatorProfileFactory(user=evaluator, is_restricted=True)
    raw, invite = make_invite(owner)
    client = login(evaluator)
    for response in (
        post(client, f"{API}/join/preview/", {"token": raw}),
        post(client, f"{API}/join/accept/", {"token": raw}),
        client.get(f"{API}/context/"),
        post(client, f"{API}/invites/", {}),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "evaluator_only"
    assert not Coauthorship.objects.exists()
    # Behind the gate the service refuses as well.
    request = RequestFactory().post(f"{API}/join/accept/")
    request.user = evaluator
    request.session = SessionStore()
    with pytest.raises(CoauthorError) as denied:
        services.accept(request, raw)
    assert (denied.value.status, denied.value.code) == (403, "evaluator_denied")
    invite.refresh_from_db()
    assert invite.uses == 0


def test_s11_a_restricted_owner_cannot_be_worked_as(acting_client, owner, member, coauthorship):
    staff_owner = UserFactory(is_staff=True)
    staff_row = CoauthorshipFactory(owner=staff_owner, member=member)
    assert me(acting_client) == owner.email
    EvaluatorProfileFactory(user=owner, is_restricted=True)
    EvaluatorProfileFactory(user=staff_owner, is_restricted=True)  # staff are never restricted

    ended = acting_client.get("/api/users/me/")
    assert ended.status_code == 409
    assert ended.json() == ENDED
    assert me(acting_client) == member.email
    workspaces = acting_client.get(f"{API}/context/").json()["workspaces"]
    assert [row["coauthorship_id"] for row in workspaces] == [staff_row.pk]
    assert post(acting_client, f"{API}/workspace/", {"coauthorship_id": coauthorship.pk}).status_code == 404
    assert post(acting_client, f"{API}/workspace/", {"coauthorship_id": staff_row.pk}).status_code == 200
    assert me(acting_client) == staff_owner.email
    # Their invite links stop working too.
    raw, _invite = make_invite(owner)
    assert post(login(UserFactory()), f"{API}/join/accept/", {"token": raw}).json() == INVALID


# First path segments under /api/ and how the workspace middleware treats each. A router added
# later fails ``test_s12_route_snapshot`` until someone classifies it here and, if it manages a
# sign-in, adds it to ``policy.PERSONAL_PREFIXES``.
PARITY = "parity"  # runs as the workspace owner
PERSONAL = "personal"  # deny-by-default while a workspace is selected
UNTOUCHED = "untouched"  # out of scope: token or portal identity, never swapped
API_SEGMENTS = {
    "analysis": PARITY,
    "analyzers": PARITY,
    "credentials": PARITY,
    "docs": PARITY,
    "execution": PARITY,
    "export": PARITY,
    "generation": PARITY,
    "human-eval": PARITY,
    "inference": PARITY,
    "integrations": PARITY,
    "models": PARITY,
    "rankings": PARITY,
    "realtime": PARITY,
    "reports": PARITY,
    "runtime": PARITY,
    "statistics": PARITY,
    "system": PARITY,
    "coauthors": PERSONAL,
    "tokens": PERSONAL,
    "users": PERSONAL,
    "mcp": UNTOUCHED,
    "relay": UNTOUCHED,
}


def _api_segments() -> set[str]:
    segments = {path.split("/")[2] for path in api.get_openapi_schema()["paths"]}
    for pattern in get_resolver().url_patterns:
        route = str(pattern.pattern)
        if route.startswith("api/") and route != "api/":
            segments.add(route.split("/")[1])
    return segments


def test_s12_route_snapshot():
    assert _api_segments() == set(API_SEGMENTS)
    for segment, kind in API_SEGMENTS.items():
        path = f"/api/{segment}/anything/"
        request = RequestFactory().post(path)
        if kind == UNTOUCHED:
            scoped = f"/api/{segment}/v1/anything" if segment == "relay" else path
            assert not policy.in_scope(RequestFactory().post(scoped)), segment
            continue
        assert policy.in_scope(request), segment
        expected = Mode.SWAP if kind == PARITY else Mode.DENY
        assert policy.decide(path, "POST") is expected, segment
        assert policy.decide(path, "GET") is expected, segment
        assert any(policy.under(path, prefix) for prefix in policy.PERSONAL_PREFIXES) is (kind == PERSONAL)
    # The two carve-outs below parity routers.
    assert not policy.in_scope(RequestFactory().get("/api/human-eval/portal/me/"))
    assert not policy.in_scope(RequestFactory().get("/api/runtime/app-tls-check/"))


def test_s13_tokens_are_never_a_way_in(acting_client, owner, member):
    raw, _ = generate_token(member, "cli")
    assert me(acting_client) == owner.email
    with_token = acting_client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert with_token.json()["email"] == member.email
    junk = acting_client.get("/api/users/me/", HTTP_AUTHORIZATION="junk")
    assert junk.json()["email"] == member.email
    assert CoauthorAction.objects.count() == 0
    created = acting_client.put(
        "/api/credentials/nope/",
        data=json.dumps({"api_key": "x"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert created.status_code == 404
    assert CoauthorAction.objects.count() == 0  # not swapped, so nothing is recorded
    for path in ("/api/mcp", "/api/mcp/", f"/api/mcp/t/{raw}", "/api/relay/v1/hello"):
        assert not policy.in_scope(RequestFactory().post(path))


def test_s14_the_portal_sees_the_member(acting_client, owner, member):
    assert me(acting_client) == owner.email
    portal = acting_client.get("/api/human-eval/portal/me/")
    assert portal.status_code == 200
    assert portal.json()["user"]["email"] == member.email


def test_s15_mutual_coauthors(owner, member, coauthorship):
    reverse_row = CoauthorshipFactory(owner=member, member=owner)
    member_client, owner_client = login(member), login(owner)
    select(member_client, coauthorship)
    select(owner_client, reverse_row)
    assert me(member_client) == owner.email
    assert me(owner_client) == member.email
    member_context = member_client.get(f"{API}/context/").json()
    assert member_context["actor"]["email"] == member.email
    assert [row["owner"]["email"] for row in member_context["workspaces"]] == [owner.email]
    assert member_client.get("/api/users/me/activity/").status_code == 200


# ---------------------------------------------------------------------------
# 14.2 The invite token
# ---------------------------------------------------------------------------


def test_s16_the_token_is_never_in_a_url_a_trail_row_or_a_log(owner, member, caplog):
    caplog.set_level(logging.DEBUG)
    owner_client, member_client = login(owner), login(member)
    created = post(owner_client, f"{API}/invites/", {"label": "For Mia"}).json()
    raw = created["token"]
    assert created["path"] == f"/join#{raw}"
    assert created["path"].startswith("/join#")

    assert post(member_client, f"{API}/join/preview/", {"token": raw}).status_code == 200
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200
    assert member_client.delete("/api/credentials/openrouter/").status_code == 200  # one recorded action
    assert post(member_client, "/api/tokens/", {"name": raw}).status_code == 403  # and one denied

    for path in (f"{API}/join/preview/", f"{API}/join/accept/"):
        assert member_client.get(path).status_code == 405

    # Nothing the platform wrote down during that run holds the token.
    assert CoauthorAction.objects.count() == 2
    assert not CoauthorAction.objects.filter(path__contains=raw).exists()
    for record in caplog.records:
        assert raw not in record.getMessage(), record
        assert raw not in repr(record.__dict__), record
    assert any(record.getMessage() == "coauthor action" for record in caplog.records)
    assert all(raw not in message.body for message in mail.outbox)

    # No route takes a token in its path (this is the only place the test puts one into a URL).
    for method in ("get", "post", "put", "patch", "delete"):
        assert getattr(Client(), method)(f"{API}/join/{raw}/").status_code == 404
        assert getattr(Client(), method)(f"{API}/join/accept/{raw}/").status_code == 404


def test_s17_preview_has_no_side_effects(client, owner, member_client, settings):
    settings.ACCOUNT_ALLOW_REGISTRATION = True
    raw, invite = make_invite(owner, max_uses=3)
    for caller in (client, member_client):
        assert post(caller, f"{API}/join/preview/", {"token": raw}).status_code == 200
        assert post(caller, f"{API}/join/preview/", {"token": raw}).status_code == 200
    invite.refresh_from_db()
    assert (invite.uses, invite.signup_uses, invite.revoked_at) == (0, 0, None)
    assert Coauthorship.objects.count() == 0
    assert CoauthorAction.objects.count() == 0
    # A signed-out visitor gets no session from looking at a link.
    assert settings.SESSION_COOKIE_NAME not in client.cookies
    assert services.SESSION_KEY not in member_client.session


def test_s18_one_answer_for_every_dead_link(client, member_client, owner):
    revoked_raw, revoked = make_invite(owner)
    expired_raw, expired = make_invite(owner)
    used_raw, used = make_invite(owner)
    CoauthorInvite.objects.filter(pk=revoked.pk).update(revoked_at=timezone.now())
    CoauthorInvite.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    CoauthorInvite.objects.filter(pk=used.pk).update(uses=1)
    inactive_raw, _ = make_invite(UserFactory(is_active=False))
    restricted_owner = UserFactory()
    restricted_raw, _ = make_invite(restricted_owner)
    EvaluatorProfileFactory(user=restricted_owner, is_restricted=True)
    dead = {
        "malformed": "coi_short",
        "wrong prefix": "llml_" + "a" * 43,
        "empty": "",
        "unknown": services.new_token(),
        "revoked": revoked_raw,
        "expired": expired_raw,
        "used up": used_raw,
        "inactive owner": inactive_raw,
        "restricted owner": restricted_raw,
    }
    answers = set()
    for name, token in dead.items():
        for caller in (client, member_client):
            response = post(caller, f"{API}/join/preview/", {"token": token})
            assert response.status_code == 404, name
            assert response.json() == INVALID, name
            answers.add(response.content)
        accepted = post(member_client, f"{API}/join/accept/", {"token": token})
        assert accepted.status_code == 404, name
        assert accepted.json() == INVALID, name
        answers.add(accepted.content)
    assert len(answers) == 1
    assert not Coauthorship.objects.exists()
    assert services.SESSION_KEY not in member_client.session


def test_s19_throttle_split(client, owner, member_client, member):
    raw, _invite = make_invite(owner, max_uses=5)
    for _ in range(60):
        assert post(client, f"{API}/join/preview/", {"token": services.new_token()}).status_code == 404
    blocked = post(client, f"{API}/join/preview/", {"token": raw})
    assert blocked.status_code == 429
    assert blocked.json() == RATE_LIMITED

    # The shared signed-out bucket does not touch people who are signed in.
    assert post(member_client, f"{API}/join/preview/", {"token": raw}).status_code == 200
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200

    noisy = login(UserFactory())
    for index in range(20):
        route = "preview" if index % 2 else "accept"
        assert post(noisy, f"{API}/join/{route}/", {"token": services.new_token()}).status_code == 404
    for route in ("preview", "accept"):
        refused = post(noisy, f"{API}/join/{route}/", {"token": raw})
        assert refused.status_code == 429
        assert refused.json() == RATE_LIMITED
    quiet = login(UserFactory())
    assert post(quiet, f"{API}/join/preview/", {"token": raw}).status_code == 200
    assert post(quiet, f"{API}/join/accept/", {"token": raw}).status_code == 200


def test_s19_dead_links_are_not_counted(member_client, owner):
    raw, invite = make_invite(owner)
    CoauthorInvite.objects.filter(pk=invite.pk).update(revoked_at=timezone.now())
    for _ in range(25):
        assert post(member_client, f"{API}/join/preview/", {"token": raw}).status_code == 404
    live_raw, _ = make_invite(owner)
    assert post(member_client, f"{API}/join/preview/", {"token": live_raw}).status_code == 200


def test_s20_who_is_inviting(client, member_client):
    spoofer = UserFactory(name=f"Admin{chr(0x202E)} {chr(0x200B)}Team\n" + "x" * 100, email="real.person@example.org")
    raw, _invite = make_invite(spoofer)
    signed_in = post(member_client, f"{API}/join/preview/", {"token": raw}).json()
    signed_out = post(client, f"{API}/join/preview/", {"token": raw}).json()
    assert signed_in["inviter_email"] == "real.person@example.org"
    assert signed_out["inviter_email"] == "r•••@example.org"
    assert "real.person" not in json.dumps(signed_out)
    for body in (signed_in, signed_out):
        name = body["inviter_name"]
        assert name.startswith("Admin Team x")
        assert len(name) == 80
        assert chr(0x202E) not in name
        assert chr(0x200B) not in name
        assert "\n" not in name


# ---------------------------------------------------------------------------
# 14.2 / 14.6 At rest
# ---------------------------------------------------------------------------


def test_s21_only_the_hash_is_stored_and_admin_is_read_only(owner, coauthorship, admin_client, rf):
    raw, invite = make_invite(owner)
    CoauthorAction.objects.create(coauthorship=coauthorship, method="POST", path="/api/x/", status_code=200)
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM coauthors_coauthorinvite WHERE id = %s", [invite.pk])
        row = cursor.fetchone()
    assert raw not in [str(value) for value in row]
    assert services.hash_token(raw) in [str(value) for value in row]

    request = rf.get("/")
    request.user = UserFactory(is_staff=True, is_superuser=True)
    for model in (CoauthorInvite, Coauthorship, CoauthorAction):
        model_admin = admin.site._registry[model]
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_change_permission(request, model.objects.first())
        assert model_admin.has_delete_permission(request)
    invite_admin = admin.site._registry[CoauthorInvite]
    assert "token_hash" not in invite_admin.get_fields(request, invite)
    assert "token_hash" not in invite_admin.get_list_display(request)

    listing = admin_client.get(reverse("admin:coauthors_coauthorinvite_changelist"))
    detail = admin_client.get(reverse("admin:coauthors_coauthorinvite_change", args=[invite.pk]))
    assert listing.status_code == detail.status_code == 200
    for page in (listing, detail):
        assert invite.token_hash not in page.content.decode()
        assert raw not in page.content.decode()
    assert admin_client.get(reverse("admin:coauthors_coauthorinvite_add")).status_code == 403
    changed = admin_client.post(reverse("admin:coauthors_coauthoraction_change", args=[1]), {"path": "/x"})
    assert changed.status_code in (302, 403, 404)
    assert CoauthorAction.objects.get().path == "/api/x/"


# ---------------------------------------------------------------------------
# 14.3 Accepting
# ---------------------------------------------------------------------------


def test_s22_csrf_on_every_write(owner, member, coauthorship):
    raw, invite = make_invite(owner, max_uses=5)
    stranger = UserFactory()
    strict_owner = login(owner, enforce_csrf_checks=True)
    strict_member = login(member, enforce_csrf_checks=True)
    strict_stranger = login(stranger, enforce_csrf_checks=True)
    strict_anonymous = Client(enforce_csrf_checks=True)
    attempts = [
        post(strict_anonymous, f"{API}/join/preview/", {"token": raw}),
        post(strict_stranger, f"{API}/join/preview/", {"token": raw}),
        post(strict_stranger, f"{API}/join/accept/", {"token": raw}),
        post(strict_member, f"{API}/workspace/", {"coauthorship_id": coauthorship.pk}),
        strict_member.delete(f"{API}/memberships/{coauthorship.pk}/"),
        post(strict_owner, f"{API}/invites/", {}),
        strict_owner.delete(f"{API}/invites/{invite.pk}/"),
        strict_owner.delete(f"{API}/members/{coauthorship.pk}/"),
    ]
    for response in attempts:
        assert response.status_code == 403, response.content
    invite.refresh_from_db()
    coauthorship.refresh_from_db()
    assert (invite.revoked_at, invite.uses, coauthorship.revoked_at) == (None, 0, None)
    assert CoauthorInvite.objects.count() == 1
    assert Coauthorship.objects.count() == 1
    assert services.SESSION_KEY not in strict_member.session

    # With the token from the cookie the same calls work.
    strict_stranger.get(f"{ALLAUTH}/config")
    header = {"HTTP_X_CSRFTOKEN": strict_stranger.cookies[django_settings.CSRF_COOKIE_NAME].value}
    assert post(strict_stranger, f"{API}/join/preview/", {"token": raw}, **header).status_code == 200
    assert post(strict_stranger, f"{API}/join/accept/", {"token": raw}, **header).status_code == 200
    strict_anonymous.get(f"{ALLAUTH}/config")
    header = {"HTTP_X_CSRFTOKEN": strict_anonymous.cookies[django_settings.CSRF_COOKIE_NAME].value}
    assert post(strict_anonymous, f"{API}/join/preview/", {"token": raw}, **header).status_code == 200


def test_s23_a_selection_cannot_be_forged(member, member_client, coauthorship):
    foreign = CoauthorshipFactory()
    ended = CoauthorshipFactory(member=member, revoked_at=timezone.now())
    owned = CoauthorshipFactory(owner=member)
    for forged in (foreign.pk, ended.pk, owned.pk, str(coauthorship.pk), "abc", 10**9, True, [coauthorship.pk], 0):
        session = member_client.session
        session[services.SESSION_KEY] = forged
        session.save()
        once = member_client.get("/api/users/me/")
        assert once.status_code == 409, forged
        assert once.json() == ENDED
        assert services.SESSION_KEY not in member_client.session
        assert me(member_client) == member.email
    assert post(Client(), f"{API}/workspace/", {"coauthorship_id": coauthorship.pk}).status_code == 401


def test_s23_login_gives_a_new_session_holding_only_the_restored_choice(member, coauthorship, owner):
    client = login(member)
    select(client, coauthorship)  # put there by hand: the database does not know this choice
    first_key = client.session.session_key
    client.logout()
    client.force_login(member)
    assert client.session.session_key != first_key
    assert services.SESSION_KEY not in client.session
    assert me(client) == member.email

    assert post(client, f"{API}/workspace/", {"coauthorship_id": coauthorship.pk}).status_code == 200
    client.logout()
    assert client.get("/api/users/me/").status_code == 401
    client.force_login(member)
    assert client.session[services.SESSION_KEY] == coauthorship.pk
    assert me(client) == owner.email

    assert post(client, f"{API}/workspace/", {"coauthorship_id": None}).status_code == 200
    client.logout()
    client.force_login(member)
    assert services.SESSION_KEY not in client.session


def test_login_restore_picks_the_latest_usable_choice_and_never_breaks_login(member, coauthorship, monkeypatch):
    newer = CoauthorshipFactory(member=member)
    now = timezone.now()
    Coauthorship.objects.filter(pk=coauthorship.pk).update(last_selected_at=now - dt.timedelta(days=1))
    Coauthorship.objects.filter(pk=newer.pk).update(last_selected_at=now)
    assert login(member).session[services.SESSION_KEY] == newer.pk
    services.remove_member(newer.owner, newer.pk)
    assert login(member).session[services.SESSION_KEY] == coauthorship.pk
    User.objects.filter(pk=coauthorship.owner_id).update(is_active=False)
    assert services.SESSION_KEY not in login(member).session

    def boom(request, user):
        raise RuntimeError

    monkeypatch.setattr(services, "restore_selection", boom)
    client = login(member)
    assert me(client) == member.email


def test_login_restore_through_the_login_form(settings, owner):
    settings.ACCOUNT_RATE_LIMITS = False
    member = UserFactory(email="mia@example.org", password=PASSWORD)
    EmailAddress.objects.create(user=member, email=member.email, primary=True, verified=True)
    coauthorship = CoauthorshipFactory(owner=owner, member=member, last_selected_at=timezone.now())
    client = Client()
    response = post(client, f"{ALLAUTH}/auth/login", {"email": member.email, "password": PASSWORD})
    assert response.status_code == 200, response.content
    assert client.session[services.SESSION_KEY] == coauthorship.pk
    assert me(client) == owner.email
    assert client.get(f"{ALLAUTH}/auth/session").json()["data"]["user"]["email"] == member.email


def test_s24_single_use_link_two_people(owner):
    raw, invite = make_invite(owner)
    first, second = login(UserFactory()), login(UserFactory())
    assert post(first, f"{API}/join/accept/", {"token": raw}).json()["status"] == "joined"
    late = post(second, f"{API}/join/accept/", {"token": raw})
    assert late.status_code == 404
    assert late.json() == INVALID
    invite.refresh_from_db()
    assert invite.uses == 1
    assert Coauthorship.objects.count() == 1
    assert services.SESSION_KEY not in second.session


def test_s24_one_person_twice_and_through_two_links(owner, member_client, member):
    first_raw, first = make_invite(owner, max_uses=3)
    second_raw, second = make_invite(owner, max_uses=3)
    assert post(member_client, f"{API}/join/accept/", {"token": first_raw}).json()["status"] == "joined"
    assert post(member_client, f"{API}/join/accept/", {"token": first_raw}).json()["status"] == "already_member"
    assert post(member_client, f"{API}/join/accept/", {"token": second_raw}).json()["status"] == "already_member"
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.uses, second.uses) == (1, 0)
    assert Coauthorship.objects.filter(owner=owner, member=member).count() == 1


def test_s24_a_lost_insert_race_answers_already_member(owner, member_client, member, monkeypatch):
    """Two accepts by one person through two links: the loser of the unique constraint consumes no use."""
    raw, invite = make_invite(owner, max_uses=3)
    real_status = CoauthorInvite.status

    def status_then_the_other_request_commits(self, now=None):
        # Runs after the "already a member?" check and before the insert.
        Coauthorship.objects.create(owner=owner, member=member)
        return real_status(self, now)

    monkeypatch.setattr(CoauthorInvite, "status", status_then_the_other_request_commits)
    response = post(member_client, f"{API}/join/accept/", {"token": raw})
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "already_member"
    invite.refresh_from_db()
    assert invite.uses == 0
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert member_client.session[services.SESSION_KEY] == row.pk


@pytest.mark.django_db(transaction=True)
def test_s24_single_use_link_under_real_concurrency():
    owner = UserFactory()
    raw, invite = make_invite(owner)
    users = [UserFactory() for _ in range(4)]
    barrier = threading.Barrier(len(users))
    statuses: list[int] = []

    def join(user):
        try:
            client = login(user)
            barrier.wait(timeout=10)
            statuses.append(post(client, f"{API}/join/accept/", {"token": raw}).status_code)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=join, args=(user,)) for user in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [200, 404, 404, 404]
    invite.refresh_from_db()
    assert invite.uses == 1
    assert Coauthorship.objects.count() == 1


def test_s25_own_link(owner_client, owner):
    raw, invite = make_invite(owner)
    response = post(owner_client, f"{API}/join/accept/", {"token": raw})
    assert response.status_code == 409
    assert response.json() == {"detail": "This is your own invite link.", "code": "own_invite", "errors": []}
    assert not Coauthorship.objects.exists()
    invite.refresh_from_db()
    assert invite.uses == 0
    assert services.SESSION_KEY not in owner_client.session


def test_s26_removal_kills_the_link(owner, owner_client, member_client, member):
    raw, invite = make_invite(owner, max_uses=5)
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert owner_client.delete(f"{API}/members/{row.pk}/").status_code == 204
    invite.refresh_from_db()
    assert invite.status() == "revoked"
    assert member_client.get("/api/users/me/").status_code == 409  # the stale selection, once
    back = post(member_client, f"{API}/join/accept/", {"token": raw})
    assert back.status_code == 404
    assert back.json() == INVALID
    assert Coauthorship.objects.filter(owner=owner, member=member, revoked_at__isnull=True).count() == 0


def test_s26b_no_older_link_lets_a_removed_member_back_in(owner, owner_client, member_client, member):
    raw, _invite = make_invite(owner, max_uses=5)
    team_raw, team_link = make_invite(owner, max_uses=5, label="team")
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert owner_client.delete(f"{API}/members/{row.pk}/").status_code == 204
    assert member_client.get("/api/users/me/").status_code == 409  # the stale selection, once
    team_link.refresh_from_db()
    assert team_link.status() == "active"  # still works for other people

    assert post(member_client, f"{API}/join/preview/", {"token": team_raw}).json() == INVALID
    back = post(member_client, f"{API}/join/accept/", {"token": team_raw})
    assert back.status_code == 404
    assert back.json() == INVALID
    assert not Coauthorship.objects.filter(owner=owner, member=member, revoked_at__isnull=True).exists()
    team_link.refresh_from_db()
    assert team_link.uses == 0

    # Somebody else still joins through the shared link.
    other = login(UserFactory())
    assert post(other, f"{API}/join/accept/", {"token": team_raw}).json()["status"] == "joined"

    # A link made after the removal is the owner's decision to let them back in.
    fresh_raw, _fresh = make_invite(owner)
    assert post(member_client, f"{API}/join/preview/", {"token": fresh_raw}).json()["state"] == "valid"
    assert post(member_client, f"{API}/join/accept/", {"token": fresh_raw}).json()["status"] == "joined"


def test_s26_leaving_does_not_revoke_the_link(owner, member_client, member):
    raw, invite = make_invite(owner, max_uses=5)
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert member_client.delete(f"{API}/memberships/{row.pk}/").status_code == 204
    invite.refresh_from_db()
    assert invite.status() == "active"
    again = post(member_client, f"{API}/join/accept/", {"token": raw})
    assert again.json()["status"] == "joined"
    assert Coauthorship.objects.filter(owner=owner, member=member).count() == 2
    invite.refresh_from_db()
    assert invite.uses == 2


def test_s26_an_expired_or_used_up_link_keeps_its_status_on_removal(owner, owner_client, member_client, member):
    raw, invite = make_invite(owner)
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert owner_client.delete(f"{API}/members/{row.pk}/").status_code == 204
    invite.refresh_from_db()
    assert invite.status() == "used_up"
    assert invite.revoked_at is None


@pytest.fixture
def commit(monkeypatch, django_capture_on_commit_callbacks):
    """``with commit():`` runs the on-commit hooks of the block, sending the join e-mail in this thread."""
    monkeypatch.setattr(services, "dispatch_in_thread", lambda target, *args, **kwargs: target(*args))
    return lambda: django_capture_on_commit_callbacks(execute=True)


def test_s27_the_owner_is_told_about_every_new_join(
    owner,
    member_client,
    member,
    commit,
    settings,
):
    settings.FRONTEND_PUBLIC_ORIGIN = "https://lab.example.org"
    raw, _invite = make_invite(owner, label="For Mia", max_uses=3)
    with commit():
        assert post(member_client, f"{API}/join/accept/", {"token": raw}).json()["status"] == "joined"
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [owner.email]
    assert message.subject == "New co-author in your LLM Lab workspace"
    assert member.email in message.body
    assert 'with the invite link "For Mia"' in message.body
    assert "They can see and change everything in your workspace." in message.body
    assert "https://lab.example.org/users/settings#coauthors" in message.body
    assert raw not in message.body

    with commit():
        assert post(member_client, f"{API}/join/accept/", {"token": raw}).json()["status"] == "already_member"
    assert len(mail.outbox) == 1


def test_s27_an_unlabelled_link_is_named_by_its_prefix(
    owner,
    member_client,
    commit,
):
    raw, invite = make_invite(owner)
    with commit():
        post(member_client, f"{API}/join/accept/", {"token": raw})
    assert f'with the invite link "{invite.token_prefix}"' in mail.outbox[0].body
    assert raw not in mail.outbox[0].body


def test_s27_a_failing_mail_backend_does_not_fail_the_join(
    owner,
    member_client,
    member,
    commit,
    monkeypatch,
):
    def broken(*args, **kwargs):
        raise OSError

    monkeypatch.setattr("django.core.mail.send_mail", broken)
    raw, _invite = make_invite(owner)
    with commit():
        response = post(member_client, f"{API}/join/accept/", {"token": raw})
    assert response.status_code == 200
    assert Coauthorship.objects.filter(owner=owner, member=member).exists()
    assert mail.outbox == []


def test_s27_a_refused_join_sends_nothing(owner, owner_client, commit):
    raw, _invite = make_invite(owner)
    with commit():
        assert post(owner_client, f"{API}/join/accept/", {"token": raw}).status_code == 409
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# 14.4 Closed registration
# ---------------------------------------------------------------------------


@pytest.fixture
def closed(settings):
    settings.ACCOUNT_ALLOW_REGISTRATION = False
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"
    settings.ACCOUNT_RATE_LIMITS = False
    settings.ALLOWED_HOSTS = ["lab.example.org", "testserver"]
    settings.HEADLESS_FRONTEND_URLS = build_headless_frontend_urls(ORIGIN)
    return settings


def browser() -> Client:
    return Client(enforce_csrf_checks=True, headers={"host": "lab.example.org"})


def bpost(client, path, data):
    client.get(f"{ALLAUTH}/config", secure=True)
    return client.post(
        path,
        data=json.dumps(data),
        content_type="application/json",
        secure=True,
        HTTP_ORIGIN=ORIGIN,
        HTTP_X_CSRFTOKEN=client.cookies[django_settings.CSRF_COOKIE_NAME].value,
    )


def signup(client, email):
    return bpost(client, f"{ALLAUTH}/auth/signup", {"email": email, "password": PASSWORD})


def test_s28_an_ordinary_users_link_does_not_reopen_sign_up(closed, owner):
    raw, invite = make_invite(owner, max_uses=5)
    visitor = browser()
    preview = bpost(visitor, f"{API}/join/preview/", {"token": raw})
    assert preview.status_code == 200
    assert preview.json()["can_sign_up"] is False
    assert services.SIGNUP_SESSION_KEY not in visitor.session
    assert signup(visitor, "new@example.org").status_code == 403
    assert not User.objects.filter(email="new@example.org").exists()
    invite.refresh_from_db()
    assert invite.signup_uses == 0
    # Existing accounts can still join with it.
    assert post(login(UserFactory()), f"{API}/join/accept/", {"token": raw}).status_code == 200


def test_s28_an_administrators_link_lets_exactly_its_holders_register(closed, mailoutbox):
    admin_owner = UserFactory(is_staff=True)
    raw, invite = make_invite(admin_owner)

    assert signup(browser(), "nolink@example.org").status_code == 403

    visitor = browser()
    preview = bpost(visitor, f"{API}/join/preview/", {"token": raw})
    assert preview.json()["can_sign_up"] is True
    assert visitor.session[services.SIGNUP_SESSION_KEY] == invite.pk
    flows = visitor.get(f"{ALLAUTH}/auth/session", secure=True).json()["data"]["flows"]
    assert {"id": "signup"} in flows
    response = signup(visitor, "invited@example.org")
    assert response.status_code == 401, response.content
    assert {"id": "verify_email", "is_pending": True} in response.json()["data"]["flows"]
    assert User.objects.filter(email="invited@example.org").exists()
    invite.refresh_from_db()
    assert (invite.signup_uses, invite.uses) == (1, 0)  # registering is not joining
    assert services.SIGNUP_SESSION_KEY not in visitor.session
    assert len(mailoutbox) == 1
    assert not Coauthorship.objects.exists()

    # The cap: a second visitor with the same single-use link cannot register.
    second = browser()
    again = bpost(second, f"{API}/join/preview/", {"token": raw})
    assert again.status_code == 200
    assert again.json()["can_sign_up"] is False
    assert services.SIGNUP_SESSION_KEY not in second.session
    assert signup(second, "second@example.org").status_code == 403
    assert not User.objects.filter(email="second@example.org").exists()
    # The first visitor's session cannot be reused for another account either.
    assert signup(visitor, "third@example.org").status_code in (403, 409)
    assert not User.objects.filter(email="third@example.org").exists()


def test_s28_a_stale_flag_is_cleared_by_the_next_preview(closed):
    raw, invite = make_invite(UserFactory(is_staff=True))
    visitor = browser()
    bpost(visitor, f"{API}/join/preview/", {"token": raw})
    assert visitor.session[services.SIGNUP_SESSION_KEY] == invite.pk
    assert bpost(visitor, f"{API}/join/preview/", {"token": services.new_token()}).status_code == 404
    assert services.SIGNUP_SESSION_KEY not in visitor.session
    bpost(visitor, f"{API}/join/preview/", {"token": raw})
    CoauthorInvite.objects.filter(pk=invite.pk).update(revoked_at=timezone.now())
    assert signup(visitor, "late@example.org").status_code == 403
    assert not User.objects.filter(email="late@example.org").exists()


def test_s28_open_registration_is_the_unchanged_path(settings, rf):
    settings.ACCOUNT_ALLOW_REGISTRATION = True
    request = rf.post(f"{ALLAUTH}/auth/signup")
    request.session = SessionStore()
    request.session[services.SIGNUP_SESSION_KEY] = 12345
    with CaptureQueriesContext(connection) as context:
        assert AccountAdapter().is_open_for_signup(request) is True
    assert context.captured_queries == []


def _signup_request(rf, method, invite_pk=None):
    request = getattr(rf, method)(f"{ALLAUTH}/auth/signup")
    request.session = SessionStore()
    if invite_pk is not None:
        request.session[services.SIGNUP_SESSION_KEY] = invite_pk
    return request


def test_s29_the_sign_up_check_locks_the_invite_on_post_only(closed, rf):
    _raw, invite = make_invite(UserFactory(is_staff=True))
    adapter = AccountAdapter()

    with CaptureQueriesContext(connection) as context:
        assert adapter.is_open_for_signup(_signup_request(rf, "post")) is False
        assert adapter.is_open_for_signup(_signup_request(rf, "get")) is False
    assert context.captured_queries == []  # no flag, no query: allauth asks on every signed-out poll

    with CaptureQueriesContext(connection) as context:
        assert adapter.is_open_for_signup(_signup_request(rf, "post", invite.pk)) is True
    locking = [query["sql"] for query in context.captured_queries if "coauthors_coauthorinvite" in query["sql"]]
    assert len(locking) == 1
    assert "FOR UPDATE" in locking[0]
    assert 'OF "coauthors_coauthorinvite"' in locking[0]

    with CaptureQueriesContext(connection) as context:
        assert adapter.is_open_for_signup(_signup_request(rf, "get", invite.pk)) is True
    assert all("FOR UPDATE" not in query["sql"] for query in context.captured_queries)

    CoauthorInvite.objects.filter(pk=invite.pk).update(signup_uses=1)
    assert adapter.is_open_for_signup(_signup_request(rf, "post", invite.pk)) is False
    for junk in ("abc", True, 10**9, None, [invite.pk]):
        assert adapter.is_open_for_signup(_signup_request(rf, "post", junk)) is False


def test_s29_errors_in_the_check_mean_no(closed, rf, monkeypatch):
    _raw, invite = make_invite(UserFactory(is_staff=True))

    def boom(*args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(services, "_may_sign_up_with", boom)
    assert AccountAdapter().is_open_for_signup(_signup_request(rf, "get", invite.pk)) is False


# ---------------------------------------------------------------------------
# 14.5 Removal
# ---------------------------------------------------------------------------


def test_s30_removal_is_immediate(acting_client, owner_client, owner, member, coauthorship):
    assert me(acting_client) == owner.email
    assert owner_client.delete(f"{API}/members/{coauthorship.pk}/").status_code == 204
    once = acting_client.get("/api/reports/")
    assert once.status_code == 409
    assert once.json() == ENDED
    assert services.SESSION_KEY not in acting_client.session
    assert me(acting_client) == member.email
    assert acting_client.get(f"{API}/context/").json() == {
        "actor": acting_client.get(f"{API}/context/").json()["actor"],
        "acting_as": None,
        "workspaces": [],
    }


def test_s30_a_write_after_removal_changes_nothing(acting_client, owner, coauthorship, monkeypatch):
    services.remove_member(owner, coauthorship.pk)
    response = acting_client.delete("/api/credentials/openrouter/")
    assert response.status_code == 409
    assert CoauthorAction.objects.count() == 0


def test_s30_a_deactivated_owner_ends_the_workspace(acting_client, owner, member):
    User.objects.filter(pk=owner.pk).update(is_active=False)
    assert acting_client.get("/api/users/me/").json() == ENDED
    assert me(acting_client) == member.email


def test_s31_open_streams_end(coauthorship, member, owner, monkeypatch):
    monkeypatch.setattr(middleware, "STREAM_RECHECK_SECONDS", 0)
    monkeypatch.setattr(middleware, "_close_db", lambda: None)
    state = {"closed": False}

    async def source():
        try:
            yield b"one"
            await sync_to_async(services.remove_member)(owner, coauthorship.pk)
            yield b"two"
            yield b"three"
        finally:
            state["closed"] = True

    response = StreamingHttpResponse(source())
    guarded = middleware.guard_stream(response, coauthorship, member)
    assert guarded is response

    async def collect():
        return [chunk async for chunk in guarded.streaming_content]

    assert async_to_sync(collect)() == [b"one"]
    assert state["closed"] is True


def test_s31_a_stream_of_a_live_coauthorship_passes_through(coauthorship, member, monkeypatch):
    monkeypatch.setattr(middleware, "STREAM_RECHECK_SECONDS", 0)
    monkeypatch.setattr(middleware, "_close_db", lambda: None)

    async def source():
        for chunk in ("one", "two", "three"):
            yield chunk

    guarded = middleware.guard_stream(StreamingHttpResponse(source()), coauthorship, member)

    async def collect():
        return [chunk async for chunk in guarded.streaming_content]

    with CaptureQueriesContext(connection) as context:
        assert async_to_sync(collect)() == [b"one", b"two", b"three"]
    assert len(context.captured_queries) == 3  # one re-check per due chunk

    plain = HttpResponse("x")
    assert middleware.guard_stream(plain, coauthorship, member) is plain


def test_s31_the_default_interval_does_not_query_for_a_short_stream(coauthorship, member):
    assert middleware.STREAM_RECHECK_SECONDS == 30

    async def source():
        yield b"one"
        yield b"two"

    guarded = middleware.guard_stream(StreamingHttpResponse(source()), coauthorship, member)

    async def collect():
        return [chunk async for chunk in guarded.streaming_content]

    with CaptureQueriesContext(connection) as context:
        assert async_to_sync(collect)() == [b"one", b"two"]
    assert context.captured_queries == []


def test_s32_a_deactivated_member_is_signed_out(acting_client, member):
    User.objects.filter(pk=member.pk).update(is_active=False)
    assert acting_client.get("/api/users/me/").status_code == 401
    assert acting_client.get(f"{API}/context/").status_code == 401
    assert acting_client.get("/api/reports/").status_code == 401


def test_s32_a_deleted_owner_ends_the_workspace(acting_client, owner, member, coauthorship):
    CoauthorAction.objects.create(coauthorship=coauthorship, method="POST", path="/api/x/", status_code=200)
    owner.delete()
    assert not Coauthorship.objects.exists()
    assert not CoauthorAction.objects.exists()
    assert acting_client.get("/api/users/me/").json() == ENDED
    assert me(acting_client) == member.email


def test_s32_a_deleted_member_takes_their_rows_along(owner, member, coauthorship):
    CoauthorAction.objects.create(coauthorship=coauthorship, method="POST", path="/api/x/", status_code=200)
    _raw, invite = make_invite(owner)
    member.delete()
    assert not Coauthorship.objects.exists()
    assert not CoauthorAction.objects.exists()
    assert CoauthorInvite.objects.filter(pk=invite.pk).exists()


# ---------------------------------------------------------------------------
# 14.6 The activity trail
# ---------------------------------------------------------------------------


def test_s33_trail_content(owner, member_client, member):
    raw, _invite = make_invite(owner)
    assert post(member_client, f"{API}/join/preview/", {"token": raw}).status_code == 200
    assert post(member_client, f"{API}/join/accept/", {"token": raw}).status_code == 200
    row = Coauthorship.objects.get(owner=owner, member=member)
    assert post(member_client, f"{API}/workspace/", {"coauthorship_id": None}).status_code == 200
    assert post(member_client, f"{API}/workspace/", {"coauthorship_id": row.pk}).status_code == 200
    assert CoauthorAction.objects.count() == 0  # join and switch are the co-authorship row itself

    member_client.get("/api/reports/?limit=3")
    assert CoauthorAction.objects.count() == 0
    assert member_client.delete("/api/credentials/openrouter/?reason=secret-looking").status_code == 200
    assert post(member_client, "/api/tokens/?x=1", {"name": "sneaky"}).status_code == 403
    assert post(member_client, f"{API}/invites/", {}).status_code == 403
    assert member_client.get("/api/tokens/").status_code == 403  # a denied read is not recorded
    assert member_client.delete(f"{API}/memberships/{row.pk}/").status_code == 204

    recorded = list(CoauthorAction.objects.order_by("id").values_list("method", "path", "status_code"))
    assert recorded == [
        ("DELETE", "/api/credentials/openrouter/", 200),
        ("POST", "/api/tokens/", 403),
        ("POST", "/api/coauthors/invites/", 403),
    ]
    assert all(action.coauthorship_id == row.pk for action in CoauthorAction.objects.all())


def test_s33_long_paths_are_cut(acting_client):
    long_path = "/api/credentials/" + "x" * 400 + "/"
    acting_client.delete(long_path)
    action = CoauthorAction.objects.get()
    assert len(action.path) == 300
    assert long_path.startswith(action.path)


def test_s34_trail_access(acting_client, owner_client, member_client, coauthorship, owner):
    for index in range(5):
        CoauthorAction.objects.create(
            coauthorship=coauthorship,
            method="POST",
            path=f"/api/x/{index}/",
            status_code=200,
        )
    url = f"{API}/members/{coauthorship.pk}/activity/"
    assert acting_client.get(url).status_code == 403  # working as O
    assert acting_client.get(url).json() == DENIED
    hidden = member_client.get(url)  # in their own account
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "not_found"
    assert login(UserFactory()).get(url).status_code == 404

    first = owner_client.get(url, {"limit": 2}).json()
    rest = owner_client.get(url, {"limit": 200, "before": first[-1]["id"]}).json()
    assert len(first) == 2
    assert len(rest) == 3
    assert [row["path"] for row in first + rest] == [f"/api/x/{index}/" for index in (4, 3, 2, 1, 0)]

    # Nothing in the API writes or deletes trail rows.
    operations = {
        (path, method)
        for path, methods in api.get_openapi_schema()["paths"].items()
        if path.startswith("/api/coauthors/")
        for method in methods
    }
    assert operations == {
        ("/api/coauthors/context/", "get"),
        ("/api/coauthors/workspace/", "post"),
        ("/api/coauthors/join/preview/", "post"),
        ("/api/coauthors/join/accept/", "post"),
        ("/api/coauthors/memberships/{coauthorship_id}/", "delete"),
        ("/api/coauthors/invites/", "get"),
        ("/api/coauthors/invites/", "post"),
        ("/api/coauthors/invites/{invite_id}/", "delete"),
        ("/api/coauthors/members/", "get"),
        ("/api/coauthors/members/{coauthorship_id}/", "delete"),
        ("/api/coauthors/members/{coauthorship_id}/activity/", "get"),
    }
    for method in ("post", "put", "patch", "delete"):
        assert getattr(owner_client, method)(url).status_code == 405
    assert CoauthorAction.objects.count() == 5
