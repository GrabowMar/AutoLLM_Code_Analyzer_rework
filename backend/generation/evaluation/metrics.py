"""``RunMetrics`` (replaces ``services/metrics.py``).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.outcome import RunMetrics
    from backend.generation.models import GenerationRun

_PENDING = "WP-09: not implemented yet"


def run_metrics(run: GenerationRun) -> RunMetrics:
    """The documented metric keys of a run, with the latest evaluation mirrored."""
    raise NotImplementedError(_PENDING)
