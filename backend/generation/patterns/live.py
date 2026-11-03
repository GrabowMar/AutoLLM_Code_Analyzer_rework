"""Per-variant provisional scoring of the primary features on the eval lane.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-13 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-13: not implemented yet"


def score_variant(run: GenerationRun) -> None:
    """Write provisional hits of the study's live pattern run for one variant."""
    raise NotImplementedError(_PENDING)
