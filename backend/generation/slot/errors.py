"""Slot-mode service errors (HTTP-aware, mapped by ``slot/api.py`` to ``{"detail", "code"}`` bodies)."""

from __future__ import annotations

from typing import Any

from backend.common.exceptions import APIError

__all__ = [
    "AddressStaleError",
    "InUseError",
    "SlotError",
    "SlotInvalidError",
    "SlotNotFoundError",
]


class SlotError(APIError):
    """Base of every slot service error; ``code`` is one of ``contracts.API_ERROR_CODES``."""

    status_code = 400
    code = "invalid"

    def __init__(self, message: str = "", *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.data = data or {}

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.message, "code": self.code}
        if self.data:
            body["data"] = self.data
        return body


class SlotInvalidError(SlotError):
    """The request names something that cannot be a slot (bad kind/span, unusable parent, ...)."""

    status_code = 422


class SlotNotFoundError(SlotError):
    status_code = 404
    code = "not_found"


class AddressStaleError(SlotError):
    """The address's slice does not hash to its ``content_sha`` and relocation by anchors failed."""

    status_code = 409
    code = "address_stale"


class InUseError(SlotError):
    """A target referenced by a study (or by runs) cannot be deleted."""

    status_code = 409
    code = "in_use"
