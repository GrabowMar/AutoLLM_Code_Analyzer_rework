"""Block order: repeat-major, seeded unit order and a seeded condition shuffle per block (spec section 9.4).

* ``unit_order`` is a permutation of the units seeded by ``order_seed`` (and the unit keys, so adding a unit
  reshuffles deterministically);
* blocks are ``(repeat_index, unit)`` sorted by ``(repeat_index, unit_order)``: every unit gets replicate 0
  before any unit gets replicate 1, which is what makes a budget stop balanced;
* inside each block the conditions are shuffled with a seed derived from ``(order_seed, block_index)``;
* ``blocked`` scheduling keeps the conditions of one model contiguous inside each block (local endpoints keep
  the model resident); the model groups and the conditions inside each group are shuffled with the same seed.

Everything here is a pure function of its arguments; the seeds go through ``identity.H`` so the order is the
same on every machine and Python version (``random.Random`` seeded with an int is stable across versions).
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from backend.generation.identity import H

__all__ = ["block_order", "block_sequence", "condition_order", "unit_orders"]

_SEED_HEX: int = 16


def _rng(*parts: object) -> random.Random:
    return random.Random(int(H(list(parts))[:_SEED_HEX], 16))  # noqa: S311 - reproducible order, not crypto


def unit_orders(unit_keys: Sequence[str], order_seed: int) -> list[int]:
    """``unit_order`` per unit index: the unit's position in the seeded permutation (0-based)."""
    indices = list(range(len(unit_keys)))
    _rng("units", order_seed, list(unit_keys)).shuffle(indices)
    orders = [0] * len(unit_keys)
    for position, index in enumerate(indices):
        orders[index] = position
    return orders


def condition_order(
    n_conditions: int,
    *,
    order_seed: int,
    block_index: int,
    groups: Sequence[str] | None = None,
) -> list[int]:
    """Condition indices of one block in claim order; ``groups`` (one key per condition) keeps groups contiguous."""
    rng = _rng("block", order_seed, block_index)
    if groups is None:
        order = list(range(n_conditions))
        rng.shuffle(order)
        return order
    by_group: dict[str, list[int]] = {}
    for index in range(n_conditions):
        by_group.setdefault(groups[index], []).append(index)
    names = sorted(by_group)
    rng.shuffle(names)
    order = []
    for name in names:
        members = by_group[name]
        rng.shuffle(members)
        order.extend(members)
    return order


def block_sequence(unit_keys: Sequence[str], repeats: Sequence[int], order_seed: int) -> list[tuple[int, int]]:
    """``(repeat_index, unit_index)`` blocks in order: repeat-major, then the seeded unit order."""
    orders = unit_orders(unit_keys, order_seed)
    return sorted(((r, u) for r in repeats for u in range(len(unit_keys))), key=lambda b: (b[0], orders[b[1]]))


def block_order(
    *,
    unit_keys: list[str],
    repeats: int,
    n_conditions: int,
    order_seed: int,
    groups: Sequence[str] | None = None,
) -> list[tuple[int, int, int]]:
    """``(repeat_index, unit_index, condition_index)`` in claim order."""
    out = []
    for block_index, (repeat, unit) in enumerate(block_sequence(unit_keys, range(repeats), order_seed)):
        for condition in condition_order(n_conditions, order_seed=order_seed, block_index=block_index, groups=groups):
            out.append((repeat, unit, condition))
    return out
