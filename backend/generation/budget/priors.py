"""``CostPrior`` maintenance (refreshed nightly and on run completion).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

_PENDING = "WP-11: not implemented yet"


def refresh_priors(*, keys: list[str] | None = None) -> int:
    """Recompute the priors (all, or ``keys``); returns the number updated."""
    raise NotImplementedError(_PENDING)
