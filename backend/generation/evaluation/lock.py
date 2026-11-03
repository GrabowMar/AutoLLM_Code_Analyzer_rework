"""Platform lock step: ``resolved_lock`` and constraint-pinned installs (spec section 6.10).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.evaluation import ResolvedLock
    from backend.generation.models import GenerationRun

_PENDING = "WP-09: not implemented yet"


def resolve_lock(run: GenerationRun) -> ResolvedLock:
    """The lock every build and re-evaluation installs under."""
    raise NotImplementedError(_PENDING)
