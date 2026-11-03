"""``build_manifest``: copies the identity keys from the resolved cell, never recomputes them (spec section 6.2).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.plan import ResolvedCell
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunManifest

_PENDING = "WP-08: not implemented yet"


def build_manifest(run: GenerationRun, resolved_cell: ResolvedCell) -> RunManifest:
    """Freeze the manifest of ``run`` before it is queued."""
    raise NotImplementedError(_PENDING)
