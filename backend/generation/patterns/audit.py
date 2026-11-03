"""Detector precision/NPV audit samples through human evaluation.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-13 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import DetectorAudit
    from backend.generation.models import PatternRun

_PENDING = "WP-13: not implemented yet"


def create_audit(
    pattern_run: PatternRun,
    feature_key: str,
    *,
    n_hits: int = 20,
    n_non_hits: int = 20,
) -> DetectorAudit:
    """A stratified, seeded audit sample and its human-eval task."""
    raise NotImplementedError(_PENDING)
