"""Validated schema for ``GenerationJob.metrics``.

The metrics JSONField stays schemaless at the DB level, but every writer goes
through :class:`JobMetrics` so aggregations (rankings, statistics, reports)
can rely on field names and types instead of defensive ``.get()`` chains.
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict


class JobMetrics(BaseModel):
    """Token usage, cost, and timing for one generation job."""

    model_config = ConfigDict(extra="forbid")

    # Token usage / cost (summed across stages)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0

    # Timing, in seconds
    duration_seconds: float | None = None
    backend_duration: float | None = None
    frontend_duration: float | None = None
    total_duration: float | None = None

    # Output shape
    model: str = ""
    lines_of_code: int = 0
    endpoints_found: int | None = None
    models_found: int | None = None

    # Copilot
    iterations_used: int | None = None
    final_error_count: int | None = None
    engine: str | None = None

    # Robustness bookkeeping (populated by the output guard)
    continuations_used: int = 0
    repair_used: bool = False
    validation_passed: bool | None = None

    def dump(self) -> dict:
        """Serialize for the JSONField, dropping unset optional fields."""
        return self.model_dump(exclude_none=True)
