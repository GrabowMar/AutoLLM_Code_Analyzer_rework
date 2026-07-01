"""Tests for the tokens app."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from uuid import UUID

import pytest
from django.utils import timezone

from backend.tokens import services
from backend.tokens.auth import TokenAuth
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

_SHA256_HEX_LEN = 64
_PREFIX_LEN = 12


@pytest.fixture
def user():
    return UserFactory.create()


@pytest.fixture
def auth_client(client):
    u = UserFactory.create()
    client.force_login(u)
    return client, u


# ---------------------------------------------------------------------------
# services.generate_token
# ---------------------------------------------------------------------------


def test_generate_token_returns_raw_and_instance(user):
    raw, token = services.generate_token(user=user, name="test")
    assert raw.startswith("llml_")
    assert len(raw) > _PREFIX_LEN
    assert token.prefix == raw[:_PREFIX_LEN]
    assert token.name == "test"
    assert isinstance(token.id, UUID)


def test_generate_token_hashes_are_not_stored_raw(user):
    raw, token = services.generate_token(user=user, name="sec")
    assert token.token_hash != raw
    assert len(token.token_hash) == _SHA256_HEX_LEN


def test_generate_token_with_scopes(user):
    _raw, token = services.generate_token(user=user, name="s", scopes=["read", "write"])
    assert token.scopes == ["read", "write"]


def test_generate_token_with_expiry(user):
    expires = timezone.now() + timedelta(days=30)
    _raw, token = services.generate_token(user=user, name="e", expires_at=expires)
    assert token.expires_at is not None


# ---------------------------------------------------------------------------
# services.verify_token
# ---------------------------------------------------------------------------


def test_verify_token_valid(user):
    raw, token = services.generate_token(user=user, name="v")
    found = services.verify_token(raw)
    assert found is not None
    assert found.id == token.id


def test_verify_token_wrong_value(user):
    services.generate_token(user=user, name="v")
    assert services.verify_token("llml_bad_token") is None


def test_verify_token_revoked(user):
    raw, token = services.generate_token(user=user, name="r")
    token.revoked_at = timezone.now()
    token.save()
    assert services.verify_token(raw) is None


def test_verify_token_expired(user):
    raw, _token = services.generate_token(
        user=user,
        name="exp",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert services.verify_token(raw) is None


# ---------------------------------------------------------------------------
# ApiToken.is_valid / mark_used
# ---------------------------------------------------------------------------


def test_is_valid_fresh_token(user):
    _, token = services.generate_token(user=user, name="fresh")
    assert token.is_valid() is True


def test_mark_used_updates_fields(user):
    _, token = services.generate_token(user=user, name="mu")
    assert token.last_used_at is None
    token.mark_used("1.2.3.4")
    token.refresh_from_db()
    assert token.last_used_at is not None
    assert token.last_used_ip == "1.2.3.4"


# ---------------------------------------------------------------------------
# TokenAuth Ninja auth class
# ---------------------------------------------------------------------------


def test_tokenauth_returns_user_for_valid_token(user, rf):
    raw, _ = services.generate_token(user=user, name="auth")
    request = rf.get("/")
    request.META["REMOTE_ADDR"] = "127.0.0.1"
    auth = TokenAuth()
    result = auth.authenticate(request, raw)
    assert result == user


def test_tokenauth_returns_none_for_invalid(user, rf):
    request = rf.get("/")
    auth = TokenAuth()
    result = auth.authenticate(request, "garbage")
    assert result is None


# ---------------------------------------------------------------------------
# API CRUD via HTTP
# ---------------------------------------------------------------------------


def test_list_tokens_empty(auth_client):
    client, _ = auth_client
    res = client.get("/api/tokens/")
    assert res.status_code == HTTPStatus.OK
    assert res.json() == []


def test_create_token(auth_client):
    client, _ = auth_client
    res = client.post(
        "/api/tokens/",
        data='{"name": "my-token"}',
        content_type="application/json",
    )
    assert res.status_code == HTTPStatus.CREATED
    body = res.json()
    assert body["name"] == "my-token"
    assert body["token"].startswith("llml_")
    assert "id" in body


def test_create_token_with_scopes(auth_client):
    client, _ = auth_client
    res = client.post(
        "/api/tokens/",
        data='{"name": "scoped", "scopes": ["read"]}',
        content_type="application/json",
    )
    assert res.status_code == HTTPStatus.CREATED
    assert res.json()["scopes"] == ["read"]


def test_list_tokens_after_create(auth_client):
    client, user = auth_client
    services.generate_token(user=user, name="t1")
    services.generate_token(user=user, name="t2")
    res = client.get("/api/tokens/")
    assert res.status_code == HTTPStatus.OK
    assert len(res.json()) == 2


def test_list_tokens_excludes_other_users(auth_client):
    client, _ = auth_client
    other = UserFactory.create()
    services.generate_token(user=other, name="other")
    res = client.get("/api/tokens/")
    assert res.status_code == HTTPStatus.OK
    assert res.json() == []


def test_revoke_token(auth_client):
    client, user = auth_client
    _raw, token = services.generate_token(user=user, name="rev")
    res = client.delete(f"/api/tokens/{token.id}/")
    assert res.status_code == HTTPStatus.NO_CONTENT
    token.refresh_from_db()
    assert token.revoked_at is not None
    assert token.is_valid() is False


def test_revoke_other_users_token_returns_404(auth_client):
    client, _ = auth_client
    other = UserFactory.create()
    _, token = services.generate_token(user=other, name="other")
    res = client.delete(f"/api/tokens/{token.id}/")
    assert res.status_code == HTTPStatus.NOT_FOUND


def test_unauthenticated_cannot_list_tokens(client):
    res = client.get("/api/tokens/")
    assert res.status_code == HTTPStatus.UNAUTHORIZED


def test_bearer_token_auth_on_protected_endpoint(client):
    user = UserFactory.create()
    raw, _ = services.generate_token(user=user, name="bearer")
    res = client.get(
        "/api/users/me/",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert res.status_code == HTTPStatus.OK
    assert res.json()["email"] == user.email


def test_response_does_not_include_token_hash(auth_client):
    client, user = auth_client
    services.generate_token(user=user, name="nohash")
    res = client.get("/api/tokens/")
    assert res.status_code == HTTPStatus.OK
    body = res.json()
    assert len(body) == 1
    assert "token_hash" not in body[0]
    assert "token" not in body[0]
