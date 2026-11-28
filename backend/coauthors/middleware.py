"""``WorkspaceMiddleware``: resolve a co-author's requests to the owner of the workspace they selected.

A co-author's session can hold a selected workspace (``SESSION_KEY`` → the primary
key of a :class:`~backend.coauthors.models.Coauthorship`). While it does, every
in-scope API request is checked against the database and then handled in one of
three modes (``policy.decide``):

- ``SWAP``: ``request.user`` and ``request.auser`` become the workspace owner for
  the view (ninja's ``request.auth`` follows), and ``request.actor`` is the real
  person. Both are put back afterwards, so outer middleware and the session only
  ever see the real person.
- ``REAL``: the request is handled as the real person.
- ``DENY``: 403 ``acting_denied``; the route belongs to a sign-in.

A selection that is no longer usable (removed, left, owner deactivated, forged
value) is dropped from the session and answered once with 409 ``workspace_ended``;
the next request runs as the member.

Requests without a selection pass straight through: no query, nothing set. Sign-in
surfaces (``/_allauth/``, ``/accounts/``, admin, ``/users/``), token requests and
the evaluation portal are out of scope and never swapped (``policy.in_scope``).

Two more rules live here:

- **Sample apps on the platform origin** (``/apps/…``) are never swapped, and are
  refused for anyone who owns a shared workspace where apps have their own origin
  (``apps_gate``): an app page there runs with the viewer's session, so neither
  side of a co-authorship may open the other's app on this origin.
- **Open event streams** re-check the co-authorship every
  ``STREAM_RECHECK_SECONDS`` and end when it is gone (``guard_stream``).
- **A tab that is behind** (the workspace was switched in another tab of the same
  session) can say which workspace it shows with the ``X-Workspace`` header
  (``own`` or the co-authorship id). When that differs from the session's choice
  the request is refused with 409 ``workspace_changed`` before anything runs, so
  the tab reloads instead of writing as somebody it does not show. Requests
  without the header are not affected (``workspace_mismatch``).

Inserted in ``MIDDLEWARE`` right after
``backend.human_eval.middleware.EvaluatorScopeMiddleware``, so the evaluator gate
always judges the real person. Sync- and async-capable; the async path resolves
the user with ``await request.auser()`` and runs database work in a thread.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from typing import Any

from asgiref.sync import iscoroutinefunction
from asgiref.sync import markcoroutinefunction
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.http import JsonResponse

from backend.coauthors import errors
from backend.coauthors import policy
from backend.coauthors import services
from backend.coauthors.policy import Mode
from backend.coauthors.services import SESSION_KEY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from django.http import HttpRequest

    from backend.coauthors.models import Coauthorship

__all__ = [
    "SESSION_KEY",
    "STREAM_RECHECK_SECONDS",
    "WORKSPACE_HEADER",
    "WorkspaceMiddleware",
    "apps_gate_applies",
    "guard_stream",
    "workspace_mismatch",
]

STREAM_RECHECK_SECONDS = 30
APPS_GATE_DETAIL = "This app opens at its own address. Use the link on the application page."
WORKSPACE_HEADER = "X-Workspace"
OWN_WORKSPACE = "own"
CHANGED_DETAIL = "The workspace was switched in another tab. Reload to continue."
CHANGED_CODE = "workspace_changed"


def ended_response() -> JsonResponse:
    """The single 409 a request with a stale selection gets."""
    return JsonResponse(errors.ended_payload(), status=409)


def changed_response() -> JsonResponse:
    """The 409 of a request from a tab that shows another workspace than the session has selected."""
    return JsonResponse({"detail": CHANGED_DETAIL, "code": CHANGED_CODE, "errors": []}, status=409)


def workspace_mismatch(request: HttpRequest, selected: Any) -> bool:
    """Whether the tab's ``X-Workspace`` header names another workspace than the session's ``selected`` one.

    No header means no check. Routes that always run as the real person (``REAL``:
    switching, joining, leaving) are never refused, so a tab that is behind can still
    switch. Pure: no database access.
    """
    claimed = request.headers.get(WORKSPACE_HEADER)
    if claimed is None:
        return False
    expected = OWN_WORKSPACE if selected is None else str(selected)
    if claimed.strip() == expected:
        return False
    return policy.decide(request.path_info or "", request.method or "GET") is not Mode.REAL


def denied_response() -> JsonResponse:
    """The 403 of a personal route while a workspace is selected."""
    return JsonResponse(errors.denied_payload(), status=403)


def apps_gate_response() -> HttpResponse:
    """The 403 of a platform-origin app address for the owner of a shared workspace."""
    return HttpResponse(APPS_GATE_DETAIL, status=403, content_type="text/plain")


def apps_have_own_origin() -> bool:
    """Whether sample apps are reachable on an origin of their own (subdomain proxy or the Traefik bridge)."""
    return bool(getattr(settings, "APPS_SUBDOMAIN_PROXY", False) or getattr(settings, "TRAEFIK_DYNAMIC_DIR", ""))


def apps_gate_applies(user: Any) -> bool:
    """Whether ``/apps/…`` is refused for ``user`` (synchronous; one ``EXISTS`` for signed-in users)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return services.owns_shared_workspace(user.pk)


def _is_authenticated(user: Any) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def _records(request: HttpRequest) -> bool:
    """Whether this request's method goes into the activity trail (everything but GET, HEAD and OPTIONS)."""
    return (request.method or "").upper() not in services.SAFE_METHODS


def _swap(request: HttpRequest, real: Any, owner: Any) -> None:
    """Make the request the owner's; the real person stays on ``request.actor``."""

    async def owner_auser() -> Any:
        return owner

    request.actor = real
    request.user = owner
    request.auser = owner_auser


def _restore(request: HttpRequest, user: Any, auser: Any) -> None:
    """Put the real person back, so outer middleware never sees the owner."""
    request.user = user
    if auser is not None:
        request.auser = auser
    else:
        del request.auser
    del request.actor


def _close_db() -> None:
    """Close this thread's connection (see ``backend.realtime.api.views._close_db``)."""
    connection.close()


def _still_usable(member: Any, coauthorship_pk: int) -> bool:
    """Stream re-check: is the co-authorship still usable? Leaves no database connection open."""
    try:
        return services.resolve_selection(member, coauthorship_pk) is not None
    finally:
        _close_db()


def guard_stream(response: Any, coauthorship: Coauthorship, member: Any) -> Any:
    """Make an async streaming response end once the co-authorship behind it is gone.

    The event stream never ends on its own and is authorised only when it opens.
    The returned response passes chunks through and, before a chunk that comes
    more than ``STREAM_RECHECK_SECONDS`` after the last check, asks the database
    again; when the co-authorship is no longer usable the stream ends (the
    browser reconnects and gets the 409). Any other response is returned unchanged.
    """
    if not getattr(response, "streaming", False) or not getattr(response, "is_async", False):
        return response
    source = response.streaming_content
    coauthorship_pk = coauthorship.pk

    async def guarded() -> AsyncIterator[bytes]:
        checked_at = time.monotonic()
        try:
            async for chunk in source:
                if time.monotonic() - checked_at >= STREAM_RECHECK_SECONDS:
                    if not await sync_to_async(_still_usable)(member, coauthorship_pk):
                        return
                    checked_at = time.monotonic()
                yield chunk
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                await aclose()

    response.streaming_content = guarded()
    return response


class WorkspaceMiddleware:
    """Resolve requests of a co-author to the selected workspace's owner (see the module docstring)."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    # -- sync ---------------------------------------------------------------

    def __call__(self, request: HttpRequest):  # noqa: PLR0911 - one early return per rule
        if self.is_async:
            return self.__acall__(request)
        path = request.path_info or ""
        if path.startswith(policy.APPS_PREFIX):
            if apps_have_own_origin() and apps_gate_applies(getattr(request, "user", None)):
                return apps_gate_response()
            return self.get_response(request)
        if not policy.in_scope(request):
            return self.get_response(request)
        real = getattr(request, "user", None)
        session = getattr(request, "session", None)
        if session is None or not _is_authenticated(real):
            return self.get_response(request)
        selected = session.get(SESSION_KEY)
        if workspace_mismatch(request, selected):
            return changed_response()
        if selected is None:
            return self.get_response(request)
        mode = policy.decide(path, request.method or "GET")
        coauthorship = services.resolve_selection(real, selected, touch=mode is Mode.SWAP)
        if coauthorship is None:
            session.pop(SESSION_KEY, None)
            return ended_response()
        request.coauthorship = coauthorship
        if mode is Mode.DENY:
            services.record_action(coauthorship, request, 403)
            return denied_response()
        if mode is Mode.REAL:
            return self.get_response(request)
        original_auser = getattr(request, "auser", None)
        _swap(request, real, coauthorship.owner)
        try:
            response = self.get_response(request)
        finally:
            _restore(request, real, original_auser)
        services.record_action(coauthorship, request, response.status_code)
        return guard_stream(response, coauthorship, real)

    # -- async --------------------------------------------------------------

    async def __acall__(self, request: HttpRequest) -> HttpResponse:  # noqa: PLR0911 - mirrors __call__
        path = request.path_info or ""
        auser = getattr(request, "auser", None)
        if path.startswith(policy.APPS_PREFIX):
            if apps_have_own_origin():
                user = await auser() if auser is not None else getattr(request, "user", None)
                if _is_authenticated(user) and await sync_to_async(apps_gate_applies)(user):
                    return apps_gate_response()
            return await self.get_response(request)
        if not policy.in_scope(request):
            return await self.get_response(request)
        real = await auser() if auser is not None else getattr(request, "user", None)
        session = getattr(request, "session", None)
        if session is None or not _is_authenticated(real):
            return await self.get_response(request)
        selected = await session.aget(SESSION_KEY)
        if workspace_mismatch(request, selected):
            return changed_response()
        if selected is None:
            return await self.get_response(request)
        mode = policy.decide(path, request.method or "GET")
        coauthorship = await sync_to_async(services.resolve_selection)(real, selected, touch=mode is Mode.SWAP)
        if coauthorship is None:
            await session.apop(SESSION_KEY, None)
            return ended_response()
        request.coauthorship = coauthorship
        if mode is Mode.DENY:
            if _records(request):
                await sync_to_async(services.record_action)(coauthorship, request, 403)
            return denied_response()
        if mode is Mode.REAL:
            return await self.get_response(request)
        original_user = request.user
        _swap(request, real, coauthorship.owner)
        try:
            response = await self.get_response(request)
        finally:
            _restore(request, original_user, auser)
        if _records(request):
            await sync_to_async(services.record_action)(coauthorship, request, response.status_code)
        return guard_stream(response, coauthorship, real)
