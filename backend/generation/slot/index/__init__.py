"""Symbol index (``INDEX_VERSION = 1``): tree-sitter symbols, route extractors and UI components (section 7.3).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.slot import SymbolIndex
    from backend.generation.models import GenerationRun

_PENDING = "WP-12: not implemented yet"


def build(run: GenerationRun) -> SymbolIndex:
    """The symbol index of a run's collected tree."""
    raise NotImplementedError(_PENDING)
