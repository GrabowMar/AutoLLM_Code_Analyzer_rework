"""Session auth classes that refuse to run on behalf of a workspace owner (the second line of defence).

``policy.decide`` already denies personal routes while a workspace is selected.
These classes repeat the rule at the router, so a mistake in the path table still
cannot mint an owner's API tokens or let a co-author invite further people:

- :class:`PersonalSessionAuth` refuses a request that was resolved to an owner
  (``request.actor`` is set). Used by the personal-token router.
- :class:`CoauthorSessionAuth` also refuses any request that presents an
  ``Authorization`` header (401). The default auth of the co-authors router.
- :class:`OwnAccountAuth` also refuses while any workspace is selected
  (``request.coauthorship`` is set). Used by invite and member management.

All are session auth with ninja's CSRF check. The co-author classes look at the
header themselves because the MCP dispatcher calls API views in-process with
the token owner as ``request.user``: without the check (and without the
``/api/coauthors/`` entry in the MCP exclusion list) a bearer token could create
an invite link, which would turn an API token into full workspace access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from ninja.security import SessionAuth

from backend.coauthors import errors
from backend.coauthors.identity import is_acting

if TYPE_CHECKING:
    from django.http import HttpRequest


class PersonalSessionAuth(SessionAuth):
    """Session auth for routes that belong to the real person's sign-in: 403 ``acting_denied`` on a swapped request."""

    def authenticate(self, request: HttpRequest, key: str | None) -> Any:
        if is_acting(request):
            raise errors.acting_denied()
        return super().authenticate(request, key)


class CoauthorSessionAuth(PersonalSessionAuth):
    """Session auth of the co-authors router: never satisfied by a request that presents a token (401)."""

    def authenticate(self, request: HttpRequest, key: str | None) -> Any:
        if "authorization" in request.headers:
            return None
        return super().authenticate(request, key)


class OwnAccountAuth(CoauthorSessionAuth):
    """Session auth for managing one's own workspace: also 403 while any workspace is selected."""

    def authenticate(self, request: HttpRequest, key: str | None) -> Any:
        if getattr(request, "coauthorship", None) is not None:
            raise errors.acting_denied()
        return super().authenticate(request, key)
