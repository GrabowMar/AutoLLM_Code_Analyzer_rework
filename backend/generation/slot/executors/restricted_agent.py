"""Restricted slot agent: allowlist-limited agentic regeneration (spec section 7.6).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.harness import SlotAgentContext
    from backend.generation.models import GenerationRun

_PENDING = "WP-12: not implemented yet"


def prepare(run: GenerationRun) -> SlotAgentContext:
    """The ``HarnessContext.slot`` extras of a ``slot_agent`` run."""
    raise NotImplementedError(_PENDING)
