"""Errors of the study engine that the API maps to HTTP statuses (spec section 9.4)."""

from __future__ import annotations

from typing import Any

__all__ = ["StudyError"]


class StudyError(Exception):
    """A refused study operation: ``status`` (400/404/409), a stable ``code`` and structured ``data``.

    Codes: ``plan_stale`` (409, the design changed since the plan), ``plan_not_found`` (404),
    ``plan_has_errors`` (400), ``confirmation_required`` (400), ``not_launchable`` / ``not_piloting`` /
    ``not_running`` / ``not_paused`` (409, wrong status), ``study_locked`` (409), ``pilot_gates_failed`` (409),
    ``reason_required`` (400), ``design_drifted`` (409, added replicates would not be replicates),
    ``invalid_cells`` (400), ``prompt_set_ambiguous`` (400, an override must name its prompt set).
    """

    def __init__(self, status: int, code: str, detail: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.data = data or {}

    def payload(self) -> dict[str, Any]:
        return {"detail": self.detail, "code": self.code, **({"data": self.data} if self.data else {})}
