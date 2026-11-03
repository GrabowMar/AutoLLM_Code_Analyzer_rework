"""``RunFile`` classification, source LOC and excludes (spec section 6.8).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from backend.generation.contracts.outcome import CollectionSummary
    from backend.generation.models import GenerationRun

_PENDING = "WP-08: not implemented yet"


def collect(run: GenerationRun, workspace: Path, *, attempt: int) -> CollectionSummary:
    """Write the ``RunFile`` rows and blobs of a finished workspace."""
    raise NotImplementedError(_PENDING)
