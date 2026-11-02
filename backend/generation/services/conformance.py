"""A job's conformance pass rate: the one reading of ``metrics.functional`` outside the Results tab.

``metrics.functional`` holds one of two things:

* the conformance suite's verdict (``evaluation.runner.evaluate_job``):
  ``source = "conformance"``, a ``status`` and the share of checks passed;
* on a job that was never graded by the suite, the generation-time smoke
  check (no ``source``): health plus a few endpoints probed, so its
  ``pass_rate`` is 1.0 whenever they answer. That is not a pass rate.

Only a conformance verdict with a sample outcome counts; an ungraded outcome
(:data:`~backend.generation.services.evaluation.runner.NON_SAMPLE_STATUSES`)
is not a measured 0. Both are None here (shown as —).

The Results tab (``experiment_results``) reads the stored
``metrics.functional.pass_rate`` of an experiment's cells without this check;
the experiment variability payload follows it there so both agree.
"""

from __future__ import annotations

import math
from typing import Any

from backend.generation.services.evaluation.runner import NON_SAMPLE_STATUSES

CONFORMANCE_SOURCE = "conformance"


def is_conformance(functional: Any) -> bool:
    """Whether *functional* (``metrics.functional``) is a conformance-suite verdict (not the smoke check)."""
    return isinstance(functional, dict) and functional.get("source") == CONFORMANCE_SOURCE


def conformance_pass_rate(functional: Any) -> float | None:
    """The conformance suite's pass rate in *functional* (``metrics.functional``); None when it is not one.

    None for the generation-time smoke check (no ``source``), an ungraded
    outcome (``NON_SAMPLE_STATUSES``) and a missing or non-numeric value.
    """
    if not is_conformance(functional) or functional.get("status") in NON_SAMPLE_STATUSES:
        return None
    value = functional.get("pass_rate")
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) else None


def conformance_passed(functional: Any) -> bool | None:
    """Whether the conformance suite passed the app (``passed``, else a pass rate of 1); None when ungraded."""
    rate = conformance_pass_rate(functional)
    if rate is None:
        return None
    passed = functional.get("passed")
    return passed is True or (passed is None and rate >= 1.0)


__all__ = ["CONFORMANCE_SOURCE", "conformance_pass_rate", "conformance_passed", "is_conformance"]
