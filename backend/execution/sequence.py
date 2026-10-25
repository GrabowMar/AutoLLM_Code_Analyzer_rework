"""Run order of an experiment's cells (spec §C.5). Contract stub → Track A. Pure.

Policies: ``interleaved`` (shuffle within each repeat block), ``blocked``
(per repeat block: shuffled condition order, apps shuffled per condition),
``grouped`` (model-major), ``auto`` (``blocked`` for lanes on endpoints with
finite K, ``interleaved`` otherwise). Randomness is drawn per repeat block
(``random.Random(hash((_order_seed(experiment), r)))``) over **all** cells, so
top-ups and added replicates never move existing cells.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

POLICIES = ("auto", "interleaved", "blocked", "grouped")

# A cell is the triple condition id, app requirement id, repeat index.
Cell = tuple[int, int, int]


def cell_sequence(experiment: Any, cells: Iterable[Cell], policy: str) -> dict[Cell, int]:
    """Map every cell to its 0-based position in the launch order under ``policy``.

    ``policy="auto"`` resolves per endpoint group (conditions on finite-K
    endpoints → ``blocked``; others → ``interleaved``).
    """
    msg = "execution.sequence.cell_sequence: Track A"
    raise NotImplementedError(msg)
