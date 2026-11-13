"""EvaluatorScopeMiddleware: restricted evaluator accounts reach only the portal (default-deny, Bearer included)."""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import async_to_sync
from asgiref.sync import iscoroutinefunction
from django.conf import settings as django_settings
from django.http import HttpResponse
from django.test import Client
from django.test import RequestFactory

from backend.human_eval import middleware
from backend.human_eval.middleware import EvaluatorScopeMiddleware
from backend.human_eval.tests.factories import EvaluatorProfileFactory
from backend.tokens.services import generate_token
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

DENIED = {"detail": middleware.DENIED_DETAIL, "code": "evaluator_only", "errors": []}

BLOCKED_GET = [
    "/api/human-eval/tasks/",
    "/api/tokens/",
    f"/{django_settings.ADMIN_URL}",
    "/_allauth/app/v1/auth/session",
    "/api/users/me/activity/",
    "/api/human-eval/tasks/",
    "/api/realtime/stream",
    "/accounts/login/",
    "/users/",
    "/api/human-eval/portal/../../tokens/",
]


@pytest.fixture
def evaluator():
    user = UserFactory()
    EvaluatorProfileFactory(user=user, is_restricted=True)
    return user


@pytest.fixture
def evaluator_client(client, evaluator):
    client.force_login(evaluator)
    return client


def _is_denied(response) -> bool:
    return response.status_code == 403 and response.json() == DENIED


def test_registered_right_after_allauth_account_middleware():
    stack = list(django_settings.MIDDLEWARE)
    index = stack.index("allauth.account.middleware.AccountMiddleware")
    assert stack[index + 1] == "backend.human_eval.middleware.EvaluatorScopeMiddleware"


@pytest.mark.parametrize("path", BLOCKED_GET)
def test_restricted_evaluator_is_denied_everywhere_else(evaluator_client, path):
    assert _is_denied(evaluator_client.get(path))


def test_users_me_only_get_and_patch(evaluator_client, evaluator):
    response = evaluator_client.get("/api/users/me/")
    assert response.status_code == 200
    assert response.json()["is_evaluator"] is True
    assert response.json()["email"] == evaluator.email
    patched = evaluator_client.patch("/api/users/me/", data={"name": "Eva"}, content_type="application/json")
    assert patched.status_code == 200
    assert patched.json()["name"] == "Eva"
    assert _is_denied(evaluator_client.post("/api/users/me/", data={}, content_type="application/json"))
    assert _is_denied(evaluator_client.delete("/api/users/me/"))
    assert _is_denied(evaluator_client.get(f"/api/users/{evaluator.pk}/"))
    assert _is_denied(evaluator_client.get("/api/users/me"))


def test_portal_and_browser_allauth_are_allowed(evaluator_client, evaluator):
    me = evaluator_client.get("/api/human-eval/portal/me/")
    assert me.status_code == 200
    assert me.json()["kind"] == "account"
    assert me.json()["user"] == {"email": evaluator.email, "name": evaluator.name, "is_evaluator": True}
    session = evaluator_client.get("/_allauth/browser/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["data"]["user"]["email"] == evaluator.email
    config = evaluator_client.get("/_allauth/browser/v1/config")
    assert config.status_code == 200


def test_bearer_token_of_a_restricted_user_is_refused(evaluator):
    # Restricting revoked older tokens; a token minted afterwards (e.g. by admin) still must not work.
    raw, _ = generate_token(evaluator, "late")
    client = Client()
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    for path in ("/api/users/me/", "/api/human-eval/tasks/", "/api/human-eval/portal/me/"):
        assert _is_denied(client.get(path, **bearer))
    assert _is_denied(client.post(f"/api/mcp/t/{raw}", data="{}", content_type="application/json"))
    assert _is_denied(client.post("/api/mcp", data="{}", content_type="application/json", **bearer))


def test_bearer_tokens_of_other_users_still_work(client):
    user = UserFactory()
    raw, _ = generate_token(user, "cli")
    response = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert response.status_code == 200
    assert response.json()["is_evaluator"] is False
    unknown = client.get("/api/users/me/", HTTP_AUTHORIZATION="Bearer llml_not-a-token")
    assert unknown.status_code == 401


def test_staff_are_never_restricted(client):
    staff = UserFactory(is_staff=True)
    EvaluatorProfileFactory(user=staff, is_restricted=True)
    client.force_login(staff)
    assert client.get("/api/human-eval/tasks/").status_code == 200
    assert client.get("/api/users/me/").json()["is_evaluator"] is False
    raw, _ = generate_token(staff, "cli")
    assert client.get("/api/human-eval/tasks/", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code == 200


def test_unrestricted_profiles_and_ordinary_users_pass(client):
    user = UserFactory()
    EvaluatorProfileFactory(user=user, is_restricted=False)
    client.force_login(user)
    assert client.get("/api/human-eval/tasks/").status_code == 200
    assert client.get("/api/users/me/").json()["is_evaluator"] is False


def test_anonymous_requests_are_left_to_normal_auth(client):
    response = client.get("/api/human-eval/tasks/")
    assert response.status_code == 401
    assert response.json() != DENIED


def test_path_allowed():
    assert middleware.path_allowed("/api/human-eval/portal/tasks/x/", "POST")
    assert middleware.path_allowed("/_allauth/browser/v1/auth/session", "DELETE")
    assert middleware.path_allowed("/api/users/me/", "GET")
    assert middleware.path_allowed("/api/users/me/", "patch")
    assert not middleware.path_allowed("/api/users/me/", "PUT")
    assert not middleware.path_allowed("/api/users/me/activity/", "GET")
    assert not middleware.path_allowed("/_allauth/app/v1/auth/session", "GET")
    assert not middleware.path_allowed("/api/human-eval/portal/../../tokens/", "GET")
    assert not middleware.path_allowed("/api/human-eval/portal/./x", "GET")
    assert not middleware.path_allowed("/api/human-eval/", "GET")
    assert not middleware.path_allowed("/", "GET")


def test_anonymous_and_staff_requests_without_tokens_need_no_lookup(django_assert_num_queries):
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get("/api/human-eval/tasks/")
    staff = UserFactory(is_staff=True)
    with django_assert_num_queries(0):
        assert not middleware.needs_check(request, AnonymousUser())
        assert not middleware.needs_check(request, None)
        assert not middleware.needs_check(request, staff)
        assert middleware.evaluate(request, staff) is None
    assert middleware.needs_check(RequestFactory().get("/", HTTP_AUTHORIZATION="Bearer x"), None)
    assert middleware.needs_check(RequestFactory().post("/api/mcp/t/abc"), None)
    assert middleware.needs_check(request, UserFactory())


def test_profile_lookup_is_cached_per_request(evaluator, django_assert_num_queries):
    request = RequestFactory().get("/api/human-eval/tasks/")
    with django_assert_num_queries(1):
        assert middleware.evaluate(request, evaluator) is not None
        assert middleware.evaluate(request, evaluator) is not None


# --- sync and async paths ----------------------------------------------------------------


def _sync_view(request):
    return HttpResponse("ok")


async def _async_view(request):
    await asyncio.sleep(0)
    return HttpResponse("ok")


def _request(path, user):
    request = RequestFactory().get(path)
    request.user = user

    async def auser():
        return user

    request.auser = auser
    return request


def test_capability_flags():
    assert EvaluatorScopeMiddleware.sync_capable
    assert EvaluatorScopeMiddleware.async_capable
    assert not iscoroutinefunction(EvaluatorScopeMiddleware(_sync_view))
    assert iscoroutinefunction(EvaluatorScopeMiddleware(_async_view))


def test_same_decision_on_both_paths(evaluator):
    sync_mw = EvaluatorScopeMiddleware(_sync_view)
    async_mw = EvaluatorScopeMiddleware(_async_view)
    for path, allowed in (("/api/human-eval/tasks/", False), ("/api/human-eval/portal/me/", True)):
        sync_response = sync_mw(_request(path, evaluator))
        async_response = async_to_sync(async_mw)(_request(path, evaluator))
        for response in (sync_response, async_response):
            assert (response.status_code == 200) is allowed
            if not allowed:
                assert response.status_code == 403
    ordinary = UserFactory()
    assert async_to_sync(async_mw)(_request("/api/human-eval/tasks/", ordinary)).content == b"ok"


# --- MCP ------------------------------------------------------------------------------------


def test_portal_is_not_reachable_through_mcp():
    from backend.integrations.mcp import openapi

    assert "/api/human-eval/portal/" in openapi.EXCLUDED_PATH_PREFIXES
    assert not any(op.path.startswith("/api/human-eval/portal/") for op in openapi.operations().values())
