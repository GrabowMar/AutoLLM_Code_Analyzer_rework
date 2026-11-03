"""Adherence-checker driver (spec section 8.7).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.evaluation import AdherenceRecord
    from backend.generation.models import GenerationRun

_PENDING = "WP-09: not implemented yet"


def check_adherence(run: GenerationRun) -> AdherenceRecord:
    """Stack, style and auth adherence of the sample."""
    raise NotImplementedError(_PENDING)
