"""Live spend polling and SSE spend events (spec section 4.7).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-11: not implemented yet"


def poll(run: GenerationRun) -> dict[str, Any]:
    """The gateway's live ``state.json`` totals for a running run."""
    raise NotImplementedError(_PENDING)
