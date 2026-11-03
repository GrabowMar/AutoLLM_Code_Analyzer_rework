"""Block order: repeat-major, seeded unit order and a seeded condition shuffle per block (spec section 9.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

_PENDING = "WP-10: not implemented yet"


def block_order(
    *,
    unit_keys: list[str],
    repeats: int,
    n_conditions: int,
    order_seed: int,
) -> list[tuple[int, int, int]]:
    """``(repeat_index, unit_index, condition_index)`` in claim order."""
    raise NotImplementedError(_PENDING)
