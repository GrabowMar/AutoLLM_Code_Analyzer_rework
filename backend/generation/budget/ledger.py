"""Reservations, usage and settlement on ``SpendLedgerEntry`` (idempotent through ``dedupe_key``; section 4.5).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

    from backend.generation.models import GenerationRun
    from backend.generation.models import SpendLedgerEntry

_PENDING = "WP-11: not implemented yet"


def reserve(run: GenerationRun, *, attempt: int, amount_usd: Decimal, tokens: int = 0) -> SpendLedgerEntry:
    """Reserve ``min(run cap, cell cap)`` for one attempt."""
    raise NotImplementedError(_PENDING)


def reserve_block(runs: list[GenerationRun]) -> list[SpendLedgerEntry]:
    """Reserve a whole replicate block (balanced claims)."""
    raise NotImplementedError(_PENDING)


def record_usage(
    run: GenerationRun,
    *,
    attempt: int,
    source: str,
    amount_usd: Decimal,
    list_cost_usd: Decimal,
    tokens_in: int,
    tokens_out: int,
    requests: int,
    dedupe_key: str,
) -> SpendLedgerEntry:
    """Append one usage row."""
    raise NotImplementedError(_PENDING)


def settle_attempt(run: GenerationRun, *, attempt: int) -> None:
    """Release the attempt's reservation and refresh the study and cell caches."""
    raise NotImplementedError(_PENDING)


def release(entry: SpendLedgerEntry) -> SpendLedgerEntry:
    """Release one reservation (a negative ``release`` row)."""
    raise NotImplementedError(_PENDING)
