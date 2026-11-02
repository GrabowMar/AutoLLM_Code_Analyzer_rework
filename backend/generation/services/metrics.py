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

    # Agent mode (harness runs; see services/agent/runner.py)
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    llm_calls: int | None = None
    harness_steps: int | None = None
    tool_calls: int | None = None
    termination: str | None = None
    providers: dict[str, int] | None = None

    # Copilot (legacy) / harness identity
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


def _count(value: object) -> int | None:
    """A token count from a response, or ``None`` when absent or not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def served_summary(calls: list[tuple[str, dict | None]]) -> dict:
    """Template-mode served summary in agent mode's :class:`JobMetrics` keys.

    ``calls`` is every LLM call of the job as ``(stage, response)`` --
    ``response`` is ``None`` for a call that failed. Returns ``llm_calls``
    (all calls), ``providers`` (``{response.provider: successful calls}``) and
    ``reasoning_tokens`` / ``cached_tokens`` summed over the same calls as the
    job's token totals (every stage but ``*_repair``). A key is ``None`` when
    no response reported it (unknown, not 0), so ``dump()`` leaves it out.
    """
    providers: dict[str, int] = {}
    reasoning: int | None = None
    cached: int | None = None
    for stage, response in calls:
        if not isinstance(response, dict):
            continue
        provider = str(response.get("provider") or "unknown")
        providers[provider] = providers.get(provider, 0) + 1
        if stage.endswith("_repair"):
            continue
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        completion_details = usage.get("completion_tokens_details")
        if (
            isinstance(completion_details, dict)
            and (n := _count(completion_details.get("reasoning_tokens"))) is not None
        ):
            reasoning = (reasoning or 0) + n
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict) and (n := _count(prompt_details.get("cached_tokens"))) is not None:
            cached = (cached or 0) + n
    return {
        "llm_calls": len(calls) or None,
        "providers": providers or None,
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
    }
