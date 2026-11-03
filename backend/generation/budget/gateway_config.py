"""``ResourcePolicy`` to the gateway's ``GATEWAY_CONFIG`` budget and routing sections.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.resources import GatewayConfig
    from backend.generation.models import GenerationRun

_PENDING = "WP-11: not implemented yet"


def build(run: GenerationRun) -> GatewayConfig:
    """The gateway configuration of one run attempt."""
    raise NotImplementedError(_PENDING)
