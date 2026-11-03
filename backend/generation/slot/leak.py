"""Leak scan: 8-token shingles of the original against the context and the workspace (spec section 7.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.slot import LeakReport
    from backend.generation.models import SlotTarget

_PENDING = "WP-12: not implemented yet"


def scan(target: SlotTarget) -> LeakReport:
    """The leak report of a target; a verbatim copy is ``slot_leak_verbatim``."""
    raise NotImplementedError(_PENDING)
