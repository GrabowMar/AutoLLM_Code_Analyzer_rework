"""Pattern analysis (spec section 7.9): normalizers, clustering, detectors, prevalence, audits and export.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-13 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import PatternRun

_PENDING = "WP-13: not implemented yet"


def run(pattern_run: PatternRun) -> None:
    """Run every detector of ``pattern_run`` and compute clusters and prevalence."""
    raise NotImplementedError(_PENDING)
