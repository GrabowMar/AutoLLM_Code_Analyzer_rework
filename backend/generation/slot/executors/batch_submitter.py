"""Batch submission of slot calls (behind ``BATCH_BACKENDS_VERIFIED``; spec section 7.6).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-12: not implemented yet"


def run_batch(runs: list[GenerationRun]) -> None:
    """Submit, poll and record one provider batch."""
    raise NotImplementedError(_PENDING)
