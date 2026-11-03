"""Resolve a design into a ``ResolvedPlan`` (ephemeral or persisted as ``StudyPlan``) (spec section 6.1).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.plan import ResolvedPlan
    from backend.generation.models import Study
    from backend.generation.models import StudyPlan

_PENDING = "WP-10: not implemented yet"


def resolve(
    study: Study,
    *,
    persist: bool = False,
    drop_incompatible: bool = False,
    sample_cells: int = 50,
) -> ResolvedPlan:
    """Resolve ``study.design``; ``persist=False`` writes no rows (cached in-process by design_hash for 10 minutes)."""
    raise NotImplementedError(_PENDING)


def freeze(study: Study, *, drop_incompatible: bool = False) -> StudyPlan:
    """Persist the resolved plan as a draft ``StudyPlan``."""
    raise NotImplementedError(_PENDING)
