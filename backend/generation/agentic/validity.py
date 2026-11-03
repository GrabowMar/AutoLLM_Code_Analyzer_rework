"""``usable`` and the typed validity notes (spec section 6.13).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.outcome import Validity
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunAttempt

_PENDING = "WP-08: not implemented yet"


def validity_for(run: GenerationRun, attempt: RunAttempt) -> Validity:
    """Termination, truncation and notes of the run's current attempt."""
    raise NotImplementedError(_PENDING)
