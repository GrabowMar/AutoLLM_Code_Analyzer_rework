"""The service-layer error of the human-evaluation module.

Services raise :class:`HumanEvalServiceError`; ``config/api.py`` maps it to the
structured body ``{"detail", "code", "errors"}`` (TS ``HumanEvalError``) with the
error's HTTP status. Ninja returns that response normally, so under
``ATOMIC_REQUESTS`` the request transaction still COMMITS: a service that may
raise after writing must do its writes inside ``with transaction.atomic():`` so
the raise rolls the savepoint (and its on_commit callbacks) back.
"""

from __future__ import annotations

from typing import Any

from backend.common.exceptions import APIError


class HumanEvalServiceError(APIError):
    """A request the module refuses: ``status`` is the HTTP status, ``code`` a stable machine-readable slug.

    ``errors`` lists field problems as ``[{"path": "<dotted path>", "message": "..."}]``.
    """

    def __init__(self, status: int, detail: str, code: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(detail, status_code=status)
        self.status = status
        self.detail = detail
        self.code = code
        self.errors: list[dict[str, Any]] = list(errors or [])

    def payload(self) -> dict[str, Any]:
        """The response body: always ``detail``, ``code`` and ``errors`` (possibly empty)."""
        return {"detail": self.detail, "code": self.code, "errors": self.errors}


def not_found(detail: str = "Not found") -> HumanEvalServiceError:
    """A 404 with code ``not_found`` (also used for other users' objects and malformed ids)."""
    return HumanEvalServiceError(404, detail, "not_found")


def conflict(detail: str, code: str) -> HumanEvalServiceError:
    """A 409 with the given code (``design_frozen``, ``read_only``, ``quota_exceeded``, ...)."""
    return HumanEvalServiceError(409, detail, code)


def invalid(detail: str, errors: list[dict[str, Any]] | None = None, code: str = "invalid") -> HumanEvalServiceError:
    """A 400 carrying field errors (``[{path, message}]``)."""
    return HumanEvalServiceError(400, detail, code, errors)
