"""Which requests of a co-author are resolved to the workspace owner (pure functions, no I/O).

``in_scope`` picks the requests the workspace middleware looks at; ``decide``
says what happens to one of them while a workspace is selected:

- ``SWAP``: the request runs as the workspace owner (full parity);
- ``REAL``: the request stays the real person;
- ``DENY``: 403, the operation belongs to a sign-in and is refused while a
  workspace is selected.

Everything under ``/api/`` is ``SWAP`` unless it sits under a *personal prefix*.
Personal prefixes are deny-by-default with listed exceptions:

==================  ==========================================================
``/api/tokens/``    no exceptions
``/api/users/``     SWAP: ``GET /api/users/``, ``/api/users/me/``,
                    ``/api/users/me/activity/``, ``/api/users/<id>/``;
                    REAL: ``/api/users/bootstrap-status/`` and
                    ``/api/users/bootstrap-admin/`` (any method)
``/api/coauthors/`` REAL: ``context/``, ``workspace/``, ``join/…``,
                    ``memberships/…``
==================  ==========================================================

Out of scope (never touched): everything outside ``/api/``, any request with an
``Authorization`` header (a token always means its own user), ``/api/mcp…``,
``/api/relay/v1/``, the app TLS check and the evaluation portal.

**Adding a router.** A new router under ``/api/`` gets parity automatically. A
router that manages a *sign-in* (credentials of the account itself, sessions,
people with access) must be added to ``PERSONAL_PREFIXES`` here. The route
snapshot test (``tests/test_security.py``) fails when a top-level router appears,
so that decision cannot be skipped. Paths are matched on ``request.path_info``
exactly as Django resolves them: a variant that dodges a prefix (``..``, ``//``,
case, encoding) does not reach the router either.
"""

from __future__ import annotations

import enum
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest

API_PREFIX = "/api/"
APPS_PREFIX = "/apps/"
OUT_OF_SCOPE_PREFIXES = (
    "/api/mcp",
    "/api/relay/v1/",
    "/api/runtime/app-tls-check/",
    "/api/human-eval/portal/",
)

TOKENS_PREFIX = "/api/tokens/"
USERS_PREFIX = "/api/users/"
COAUTHORS_PREFIX = "/api/coauthors/"
PERSONAL_PREFIXES = (TOKENS_PREFIX, USERS_PREFIX, COAUTHORS_PREFIX)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_USERS_SWAP_GET = frozenset({"/api/users/", "/api/users/me/", "/api/users/me/activity/"})
_USERS_PK_RE = re.compile(r"^/api/users/\d+/$")
_USERS_REAL = frozenset({"/api/users/bootstrap-status/", "/api/users/bootstrap-admin/"})
_COAUTHORS_REAL_EXACT = frozenset({"/api/coauthors/context/", "/api/coauthors/workspace/"})
_COAUTHORS_REAL_PREFIXES = ("/api/coauthors/join/", "/api/coauthors/memberships/")


class Mode(enum.Enum):
    """What happens to an in-scope request while a workspace is selected."""

    SWAP = "swap"
    REAL = "real"
    DENY = "deny"


def under(path: str, prefix: str) -> bool:
    """Whether ``path`` is ``prefix`` (with or without its trailing slash) or below it."""
    return path == prefix.rstrip("/") or path.startswith(prefix)


def in_scope(request: HttpRequest) -> bool:
    """Whether the workspace middleware looks at this request at all (see the module docstring)."""
    path = request.path_info or ""
    if not path.startswith(API_PREFIX):
        return False
    if "authorization" in request.headers:
        return False
    return not path.startswith(OUT_OF_SCOPE_PREFIXES)


def decide(path: str, method: str) -> Mode:  # noqa: PLR0911 - one return per row of the table
    """The mode of an in-scope request ``method path`` while a workspace is selected."""
    verb = (method or "GET").upper()
    if verb == "HEAD":
        verb = "GET"
    if under(path, TOKENS_PREFIX):
        return Mode.DENY
    if under(path, USERS_PREFIX):
        if path in _USERS_REAL:
            return Mode.REAL
        if verb == "GET" and (path in _USERS_SWAP_GET or _USERS_PK_RE.match(path)):
            return Mode.SWAP
        return Mode.DENY
    if under(path, COAUTHORS_PREFIX):
        if path in _COAUTHORS_REAL_EXACT or path.startswith(_COAUTHORS_REAL_PREFIXES):
            return Mode.REAL
        return Mode.DENY
    return Mode.SWAP
