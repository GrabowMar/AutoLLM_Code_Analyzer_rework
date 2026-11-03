"""Outcome and termination classification through ``contracts.outcome.TERMINATION_OUTCOME`` (spec section 6.12).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunAttempt

_PENDING = "WP-08: not implemented yet"


def classify(run: GenerationRun, attempt: RunAttempt) -> tuple[str, str]:
    """``(outcome, outcome_reason)`` of a finished attempt."""
    raise NotImplementedError(_PENDING)
