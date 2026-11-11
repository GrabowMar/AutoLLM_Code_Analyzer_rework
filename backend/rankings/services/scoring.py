"""Empirical quality and the composite score.

The MSS v2 components live in :mod:`backend.rankings.services.mss`; this
module keeps the locally measured empirical quality and the composite
(0.6 MSS + 0.4 empirical).
"""

from __future__ import annotations

from typing import Any

from backend.rankings.services.constants import COMPOSITE_EMPIRICAL_WEIGHT
from backend.rankings.services.constants import COMPOSITE_MSS_WEIGHT
from backend.rankings.services.constants import EMPIRICAL_DENSITY_CAP
from backend.rankings.services.constants import EMPIRICAL_FUNCTIONAL_WEIGHT
from backend.rankings.services.constants import SEVERITY_WEIGHTS


def compute_empirical_quality(
    entry: dict[str, Any],
    *,
    severity_weights: dict[str, int] | None = None,
) -> float | None:
    """Quality measured by this platform's own experiments, 0..1.

    Combines severity-weighted code-issue density (per KLOC of source code,
    lower is better) with the smoke-test pass rate. Returns None when the
    model has no completed local generations — no data is not the same as
    bad data. `severity_weights` lets the sensitivity analysis rescore under
    alternative schemes; the default is the baseline SEVERITY_WEIGHTS.

    The aggregator supplies ``empirical_findings``/``empirical_loc`` (repeats
    collapsed to app means); entries without them (report generators) use
    ``findings``/``local_loc``.
    """
    if not entry.get("apps_completed"):
        return None
    if "empirical_loc" in entry:
        loc = entry.get("empirical_loc") or 0
        findings = entry.get("empirical_findings") or {}
    else:
        loc = entry.get("local_loc", 0) or 0
        findings = entry.get("findings") or {}
    if loc <= 0:
        return None

    weights = severity_weights or SEVERITY_WEIGHTS
    weighted = sum(weights.get(sev, 0) * (count or 0) for sev, count in findings.items())
    density = weighted / loc * 1000
    density_score = max(0.0, 1.0 - min(density, EMPIRICAL_DENSITY_CAP) / EMPIRICAL_DENSITY_CAP)

    functional_rate = entry.get("functional_pass_rate")
    if functional_rate is None:
        # No smoke data — score on density alone rather than punishing.
        return round(density_score, 4)
    return round(
        (1 - EMPIRICAL_FUNCTIONAL_WEIGHT) * density_score + EMPIRICAL_FUNCTIONAL_WEIGHT * float(functional_rate),
        4,
    )


def compute_composite(mss: float | None, empirical: float | None) -> float | None:
    """MSS blended with measured quality; plain MSS when nothing was measured.

    None when the model has no MSS (excluded for insufficient evidence or not
    in the frame): such a model has no composite and sorts last.
    """
    if mss is None:
        return None
    if empirical is None:
        return round(mss, 4)
    return round(COMPOSITE_MSS_WEIGHT * mss + COMPOSITE_EMPIRICAL_WEIGHT * empirical, 4)
