"""Who is making a request: the real person, and whether they work in someone else's workspace.

``middleware.WorkspaceMiddleware`` sets two request attributes:

- ``request.coauthorship``: the validated :class:`~backend.coauthors.models.Coauthorship`
  selected in this session (any mode);
- ``request.actor``: the real user, only on requests that were resolved to the
  workspace owner (``request.user`` is the owner there).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.http import HttpRequest


def real_user(request: HttpRequest) -> Any:
    """The person who signed in: ``request.actor`` on a swapped request, else ``request.user``."""
    actor = getattr(request, "actor", None)
    return actor if actor is not None else getattr(request, "user", None)


def is_acting(request: HttpRequest) -> bool:
    """True when the request runs as a workspace owner on behalf of a co-author."""
    return getattr(request, "actor", None) is not None
