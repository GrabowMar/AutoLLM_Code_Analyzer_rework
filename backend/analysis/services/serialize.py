"""JSON shapes of runs and tool results, shared by the API and the live (SSE) events."""

from __future__ import annotations

from typing import Any


def iso(value) -> str | None:
    return value.isoformat() if value else None


def serialize_result(result) -> dict[str, Any]:
    """One tool's result within a run (no raw output: see the result detail endpoint)."""
    return {
        "id": str(result.id),
        "tool_slug": result.tool_slug,
        "category": result.category,
        "status": result.status,
        "summary": result.summary or {},
        "metrics": result.metrics or {},
        "error_message": result.error_message,
        "started_at": iso(result.started_at),
        "duration_seconds": result.duration_seconds,
    }
