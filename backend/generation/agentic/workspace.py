"""Workspace seed and collection with anchored excludes and file origins (spec section 6.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from backend.generation.models import GenerationRun

_PENDING = "WP-08: not implemented yet"


def seed_workspace(run: GenerationRun, dest: Path) -> str:
    """Write the starter tree into ``dest``; returns its tree hash."""
    raise NotImplementedError(_PENDING)
