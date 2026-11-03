"""Splice a variant into its parent tree; writes the variant's ``slot`` RunFile rows only (spec section 7.7).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunFile

_PENDING = "WP-12: not implemented yet"


def assemble(run: GenerationRun, replacement: dict[str, str]) -> list[RunFile]:
    """The ``slot`` rows of the variant (byte offsets, CRLF kept)."""
    raise NotImplementedError(_PENDING)
