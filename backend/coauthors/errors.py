"""The service-layer error of the co-authors module.

Services raise :class:`CoauthorError`; ``config/api.py`` maps it to the body
``{"detail", "code", "errors"}`` with the error's HTTP status. Ninja returns that
response normally, so under ``ATOMIC_REQUESTS`` the request transaction still
COMMITS: a service that may raise after writing does its writes inside
``with transaction.atomic():`` so the raise rolls them back.
"""

from __future__ import annotations

from typing import Any

from backend.common.exceptions import APIError

ENDED_DETAIL = "You no longer have access to this workspace."
ENDED_CODE = "workspace_ended"
DENIED_DETAIL = "This belongs to the account owner's sign-in. Switch to your own account to do this."
DENIED_CODE = "acting_denied"
INVITE_INVALID_DETAIL = "This invite link is not valid or has expired."
RATE_LIMITED_DETAIL = "Too many attempts. Try again in a few minutes."


class CoauthorError(APIError):
    """A request the module refuses: ``status`` is the HTTP status, ``code`` a stable machine-readable slug."""

    def __init__(self, status: int, detail: str, code: str) -> None:
        super().__init__(detail, status_code=status)
        self.status = status
        self.detail = detail
        self.code = code

    def payload(self) -> dict[str, Any]:
        """The response body: ``detail``, ``code`` and an empty ``errors`` list."""
        return {"detail": self.detail, "code": self.code, "errors": []}


def not_found(detail: str = "Not found") -> CoauthorError:
    """A 404 with code ``not_found`` (also the answer for other people's objects)."""
    return CoauthorError(404, detail, "not_found")


def conflict(detail: str, code: str) -> CoauthorError:
    """A 409 with the given code (``own_invite``, ``too_many_invites``)."""
    return CoauthorError(409, detail, code)


def invite_invalid() -> CoauthorError:
    """The one 404 every unusable invite link gets, whatever the reason."""
    return CoauthorError(404, INVITE_INVALID_DETAIL, "invite_invalid")


def rate_limited() -> CoauthorError:
    """The 429 of a caller blocked by earlier failed lookups."""
    return CoauthorError(429, RATE_LIMITED_DETAIL, "rate_limited")


def acting_denied() -> CoauthorError:
    """The 403 of a personal operation attempted while working in someone else's workspace."""
    return CoauthorError(403, DENIED_DETAIL, DENIED_CODE)


def ended_payload() -> dict[str, Any]:
    """Body of the single 409 a request with a stale workspace selection gets."""
    return {"detail": ENDED_DETAIL, "code": ENDED_CODE, "errors": []}


def denied_payload() -> dict[str, Any]:
    """Body of the 403 for personal routes while a workspace is selected."""
    return {"detail": DENIED_DETAIL, "code": DENIED_CODE, "errors": []}
