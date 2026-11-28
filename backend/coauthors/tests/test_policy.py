"""The scope and policy tables of the workspace middleware (no database)."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from backend.coauthors import policy
from backend.coauthors.policy import Mode

SWAP, REAL, DENY = Mode.SWAP, Mode.REAL, Mode.DENY

DECISIONS = [
    # everything outside the personal prefixes runs as the owner
    ("/api/generation/studies/", "GET", SWAP),
    ("/api/generation/studies/", "POST", SWAP),
    ("/api/reports/", "GET", SWAP),
    ("/api/credentials/openrouter/", "PUT", SWAP),
    ("/api/inference/endpoints/3/", "PATCH", SWAP),
    ("/api/realtime/stream", "GET", SWAP),
    ("/api/export/reports.json", "GET", SWAP),
    ("/api/system/", "GET", SWAP),
    ("/api/docs", "GET", SWAP),
    ("/api/human-eval/tasks/", "POST", SWAP),
    ("/api/tokenizer/", "GET", SWAP),
    ("/api/usersettings/", "GET", SWAP),
    # personal tokens: nothing is allowed
    ("/api/tokens", "GET", DENY),
    ("/api/tokens/", "GET", DENY),
    ("/api/tokens/", "POST", DENY),
    ("/api/tokens/x/", "DELETE", DENY),
    ("/api/tokens/anything/else", "GET", DENY),
    # users: four reads as the owner, bootstrap as the real person, the rest denied
    ("/api/users/", "GET", SWAP),
    ("/api/users/", "HEAD", SWAP),
    ("/api/users/me/", "GET", SWAP),
    ("/api/users/me/activity/", "GET", SWAP),
    ("/api/users/7/", "GET", SWAP),
    ("/api/users/me/", "PATCH", DENY),
    ("/api/users/me/", "POST", DENY),
    ("/api/users/me/", "DELETE", DENY),
    ("/api/users/me/", "OPTIONS", DENY),
    ("/api/users/7/", "PATCH", DENY),
    ("/api/users/", "POST", DENY),
    ("/api/users", "GET", DENY),
    ("/api/users/me", "GET", DENY),
    ("/api/users/me/activity/", "POST", DENY),
    ("/api/users/7/extra/", "GET", DENY),
    ("/api/users/abc/", "GET", DENY),
    ("/api/users/new-route/", "GET", DENY),
    ("/api/users/bootstrap-status/", "GET", REAL),
    ("/api/users/bootstrap-admin/", "POST", REAL),
    # co-authors: context, switch, join and leave as the real person, management denied
    ("/api/coauthors/context/", "GET", REAL),
    ("/api/coauthors/workspace/", "POST", REAL),
    ("/api/coauthors/join/preview/", "POST", REAL),
    ("/api/coauthors/join/accept/", "POST", REAL),
    ("/api/coauthors/memberships/4/", "DELETE", REAL),
    ("/api/coauthors/invites/", "GET", DENY),
    ("/api/coauthors/invites/", "POST", DENY),
    ("/api/coauthors/invites/3/", "DELETE", DENY),
    ("/api/coauthors/members/", "GET", DENY),
    ("/api/coauthors/members/3/", "DELETE", DENY),
    ("/api/coauthors/members/3/activity/", "GET", DENY),
    ("/api/coauthors/", "GET", DENY),
    ("/api/coauthors", "GET", DENY),
    ("/api/coauthors/context", "GET", DENY),
    ("/api/coauthors/new-route/", "GET", DENY),
]


@pytest.mark.parametrize(("path", "method", "expected"), DECISIONS)
def test_decide(path, method, expected):
    assert policy.decide(path, method) is expected


def test_decide_ignores_method_case_and_missing_method():
    assert policy.decide("/api/users/me/", "get") is SWAP
    assert policy.decide("/api/users/me/", "") is SWAP
    assert policy.decide("/api/users/me/", "patch") is DENY


IN_SCOPE = [
    "/api/users/me/",
    "/api/tokens/",
    "/api/generation/studies/",
    "/api/realtime/stream",
    "/api/human-eval/tasks/",
    "/api/runtime/containers/",
    "/api/relay/",
]
OUT_OF_SCOPE = [
    "/apps/foo/",
    "/apps/foo/index.html",
    "/_allauth/browser/v1/auth/session",
    "/_allauth/browser/v1/account/password/change",
    "/accounts/login/",
    "/admin/",
    "/users/~update/",
    "/media/x.png",
    "/",
    "/api",
    "/api/mcp",
    "/api/mcp/",
    "/api/mcp/t/llml_abc",
    "/api/relay/v1/hello",
    "/api/runtime/app-tls-check/",
    "/api/human-eval/portal/x",
    "/api/human-eval/portal/me/",
]


@pytest.mark.parametrize("path", IN_SCOPE)
def test_in_scope(path):
    assert policy.in_scope(RequestFactory().get(path))


@pytest.mark.parametrize("path", OUT_OF_SCOPE)
def test_out_of_scope(path):
    assert not policy.in_scope(RequestFactory().get(path))


@pytest.mark.parametrize("value", ["Bearer junk", "junk", "", "Basic abc", "bearer llml_x"])
def test_any_authorization_header_is_out_of_scope(value):
    request = RequestFactory().get("/api/users/me/", HTTP_AUTHORIZATION=value)
    assert not policy.in_scope(request)


def test_personal_prefixes_are_exactly_the_documented_ones():
    assert set(policy.PERSONAL_PREFIXES) == {"/api/tokens/", "/api/users/", "/api/coauthors/"}
