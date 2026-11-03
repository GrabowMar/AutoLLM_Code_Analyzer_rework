"""Harness registry: enabled adapters only when their facts have no open questions.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-07 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.harnesses.base import HarnessAdapter

_PENDING = "WP-07: not implemented yet"


def get(name: str) -> HarnessAdapter:
    """The adapter named ``name`` (raises ``KeyError`` when unknown)."""
    raise NotImplementedError(_PENDING)


def adapters() -> list[HarnessAdapter]:
    """Every registered adapter, enabled or not."""
    raise NotImplementedError(_PENDING)
