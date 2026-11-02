"""Authentication of the participant portal (``/api/human-eval/portal/``).

Two identities can reach the portal, possibly both at once:

- a **cookie participant** — an anonymous participant who joined through an
  invite link or an access code. The browser holds a random session token in an
  HttpOnly cookie (``settings.HUMAN_EVAL_COOKIE_NAME``); the database stores only
  its sha256 (``Participant.session_token_hash``). The session ends after
  ``HUMAN_EVAL_SESSION_IDLE_HOURS`` without activity, on logout, withdrawal or
  code regeneration.
- a **session user** — a logged-in platform account (evaluator accounts, and
  researchers previewing their own task).

:class:`PortalAuth` is the portal router's single auth class. It enforces Django's
CSRF check for unsafe methods (whether or not the participant cookie is present)
and returns a :class:`PortalIdentity` carrying both identities, or None (401) when
neither is present. Views resolve the participant for a task with
``services.access.portal_participant`` (the cookie participant when it belongs to
the task, else the user's account/preview participant).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from ninja.errors import HttpError
from ninja.security import APIKeyCookie
from ninja.utils import check_csrf

if TYPE_CHECKING:
    from django.http import HttpRequest

    from backend.human_eval.models import Participant
    from backend.users.models import User

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class PortalIdentity:
    """Who is calling the portal: the cookie participant and/or the session user (at least one is set)."""

    participant: Participant | None
    user: User | None

    def __bool__(self) -> bool:
        return self.participant is not None or self.user is not None


def enforce_csrf(request: HttpRequest) -> None:
    """Raise ``HttpError(403)`` unless the request passes Django's CSRF check (safe methods always pass).

    ``ninja.utils.check_csrf`` *returns* a 403 response instead of raising; this wraps it.
    Public portal POSTs (join, resume, logout) call it explicitly.
    """
    if request.method in SAFE_METHODS or getattr(request, "_ninja_csrf_exempt", False):
        return
    if check_csrf(request) is not None:
        raise HttpError(403, "CSRF check failed")


class PortalAuth(APIKeyCookie):
    """Combined participant-cookie + session auth of the portal router (``request.auth`` = :class:`PortalIdentity`).

    - unsafe methods: CSRF enforced first (403), even without the participant cookie;
    - never raises for a missing, unknown or expired cookie: returns None so ninja answers 401
      when there is no session user either.
    """

    def __init__(self) -> None:
        from backend.human_eval.services.access import cookie_name

        # Instance attribute before APIKeyBase.__init__ so the OpenAPI scheme names the real cookie.
        self.param_name = cookie_name()
        super().__init__(csrf=True)

    def _get_key(self, request: HttpRequest) -> str | None:
        from backend.human_eval.services.access import cookie_name

        if self.csrf:
            enforce_csrf(request)
        return request.COOKIES.get(cookie_name())

    def authenticate(self, request: HttpRequest, key: str | None) -> Any:
        from backend.human_eval.services.access import portal_identity

        return portal_identity(request)
