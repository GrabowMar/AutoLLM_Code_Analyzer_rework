"""Restricted evaluator scope (owner B1): ``EvaluatorScopeMiddleware``, default-deny outside the portal.

Spec §5.16 / R2. Restricted evaluator accounts (an ``EvaluatorProfile`` with
``is_restricted``; staff and superusers are never restricted) exist only to take
part in human-evaluation tasks. Every other part of the lab (generation, API
tokens, admin, ...) answers them with a 403 JSON body
``{"detail": ..., "code": "evaluator_only", "errors": []}``. The rule is an
allowlist, so routers added later are denied automatically:

- ``/_allauth/browser/v1/`` (prefix): browser login, session, logout, password;
- ``/api/human-eval/portal/`` (prefix): the participant portal;
- exactly ``/api/users/me/`` with GET or PATCH (``/api/users/me/activity/`` is denied).

Paths with ``.`` / ``..`` segments are never allowed. ``Authorization: Bearer``
requests (and MCP URL tokens ``/api/mcp/t/<token>``) are resolved through
``backend.tokens.services.verify_token``: a token of a restricted user is refused
on every path (their tokens are also revoked when the profile is saved, see
``signals.revoke_restricted_tokens``).

Inserted in ``MIDDLEWARE`` right after ``allauth.account.middleware.AccountMiddleware``
(after authentication). Sync- and async-capable like
``backend.users.middleware._SyncAndAsyncMiddleware``; the async path resolves the
user with ``await request.auser()`` and runs the database checks in a thread.
The profile lookup is cached per request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from asgiref.sync import iscoroutinefunction
from asgiref.sync import markcoroutinefunction
from asgiref.sync import sync_to_async
from django.http import JsonResponse

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

ALLOWED_PREFIXES = ("/_allauth/browser/v1/", "/api/human-eval/portal/")
SELF_PATH = "/api/users/me/"
SELF_METHODS = frozenset({"GET", "PATCH"})
MCP_URL_TOKEN_PREFIX = "/api/mcp/t/"  # noqa: S105 - a URL prefix, not a secret
DENIED_DETAIL = "Evaluator accounts can only use the evaluation portal."
DENIED_CODE = "evaluator_only"
MAX_TOKEN_LENGTH = 256
_CACHE_ATTR = "_human_eval_restricted_users"


def path_allowed(path: str, method: str) -> bool:
    """Whether a restricted evaluator may request ``method path`` (the allowlist in the module docstring)."""
    segments = path.split("/")
    if "." in segments or ".." in segments:
        return False
    if path.startswith(ALLOWED_PREFIXES):
        return True
    return path == SELF_PATH and method.upper() in SELF_METHODS


def denied_response() -> JsonResponse:
    """The 403 every denied request of a restricted evaluator gets (TS ``HumanEvalError`` shape)."""
    return JsonResponse({"detail": DENIED_DETAIL, "code": DENIED_CODE, "errors": []}, status=403)


def _presented_tokens(request: HttpRequest) -> list[str]:
    """API tokens the request presents: ``Authorization: Bearer <token>`` and the MCP URL-token path."""
    tokens: list[str] = []
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        tokens.append(value.strip())
    path = request.path_info or ""
    if path.startswith(MCP_URL_TOKEN_PREFIX):
        url_token = path[len(MCP_URL_TOKEN_PREFIX) :].strip("/")
        if url_token:
            tokens.append(url_token)
    return [token for token in tokens if len(token) <= MAX_TOKEN_LENGTH]


def _restricted(request: HttpRequest, user: Any) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    cache: dict[Any, bool] = getattr(request, _CACHE_ATTR, None) or {}
    if user.pk not in cache:
        from backend.human_eval.services.access import is_restricted_evaluator

        cache[user.pk] = is_restricted_evaluator(user)
        setattr(request, _CACHE_ATTR, cache)
    return cache[user.pk]


def needs_check(request: HttpRequest, user: Any) -> bool:
    """False when no database lookup can matter: no API token presented and no signed-in non-staff user."""
    if _presented_tokens(request):
        return True
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def evaluate(request: HttpRequest, user: Any) -> HttpResponse | None:
    """The 403 response for a request a restricted evaluator may not make, else None (synchronous; DB access).

    ``user`` is the session user (AnonymousUser / None when logged out).
    """
    if not needs_check(request, user):
        return None
    tokens = _presented_tokens(request)
    if tokens:
        from backend.tokens.services import verify_token

        for raw in tokens:
            api_token = verify_token(raw)
            if api_token is not None and _restricted(request, api_token.user):
                return denied_response()
    if _restricted(request, user) and not path_allowed(request.path_info or "", request.method or "GET"):
        return denied_response()
    return None


class EvaluatorScopeMiddleware:
    """Default-deny gate for restricted evaluator accounts (see the module docstring)."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest):
        if self.is_async:
            return self.__acall__(request)
        denied = evaluate(request, getattr(request, "user", None))
        if denied is not None:
            return denied
        return self.get_response(request)

    async def __acall__(self, request: HttpRequest) -> HttpResponse:
        auser = getattr(request, "auser", None)
        user = await auser() if auser is not None else getattr(request, "user", None)
        if needs_check(request, user):  # anonymous / staff requests without tokens skip the thread hop
            denied = await sync_to_async(evaluate)(request, user)
            if denied is not None:
                return denied
        return await self.get_response(request)
