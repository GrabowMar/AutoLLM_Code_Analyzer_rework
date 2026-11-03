"""Slot tests: restricted suite and probes; ``full``, ``overlay`` or ``static_only`` (spec section 7.8).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation
    from backend.generation.models import SlotTarget

_PENDING = "WP-12: not implemented yet"


def evaluate_variant(run: GenerationRun, *, mode: str = "full") -> RunEvaluation:
    """Evaluate a variant with the target's check selection (reused by tree)."""
    raise NotImplementedError(_PENDING)


def evaluate_original(target: SlotTarget) -> RunEvaluation:
    """Evaluate the original (variant 0) with the same selection."""
    raise NotImplementedError(_PENDING)
