"""Common HTTP-aware exception classes for API layers."""

from __future__ import annotations


class APIError(Exception):
    """Base class for HTTP-aware API errors."""

    status_code: int = 500

    def __init__(self, message: str = "", *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class ValidationFailed(APIError):  # noqa: N818
    """Request payload or arguments failed validation."""

    status_code = 400


class NotFoundError(APIError):
    """Requested resource does not exist."""

    status_code = 404


class OperationFailed(APIError):  # noqa: N818
    """A backend operation could not be completed."""

    status_code = 500
