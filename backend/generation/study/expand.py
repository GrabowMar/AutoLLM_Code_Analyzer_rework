"""Factor expansion (full and fractional grids, exclusions, compatibility pruning) and condition hashing.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.issues import Issue
    from backend.generation.contracts.plan import Design

_PENDING = "WP-10: not implemented yet"


def expand(design: Design, *, drop_incompatible: bool = False) -> tuple[list[dict[str, str]], list[Issue]]:
    """Treatment conditions as ``{factor_key: level}`` plus the issues (``aliasing``, ``combination_pruned``, ...)."""
    raise NotImplementedError(_PENDING)
