"""conformance_pass_rate / conformance_passed: only a graded conformance-suite verdict is a pass rate."""

from __future__ import annotations

import pytest

from backend.generation.services.conformance import conformance_pass_rate
from backend.generation.services.conformance import conformance_passed
from backend.generation.services.conformance import is_conformance

SMOKE = {"passed": True, "health_ok": True, "pass_rate": 1.0, "endpoints_ok": 2, "endpoints_checked": 2}


@pytest.mark.parametrize(
    ("functional", "rate", "passed"),
    [
        ({"source": "conformance", "status": "passed", "pass_rate": 1.0, "passed": True}, 1.0, True),
        ({"source": "conformance", "status": "failed", "pass_rate": 0.4375, "passed": False}, 0.4375, False),
        # A measured 0 stays 0.
        ({"source": "conformance", "status": "failed", "pass_rate": 0.0, "passed": False}, 0.0, False),
        # No ``passed`` key: a pass rate of 1 is a pass.
        ({"source": "conformance", "status": "passed", "pass_rate": 1.0}, 1.0, True),
        # The generation-time smoke check (no source) is not a pass rate, whatever it says.
        (SMOKE, None, None),
        ({**SMOKE, "source": None}, None, None),
        # Ungraded outcomes are not a measured 0.
        ({"source": "conformance", "status": "infra_error", "pass_rate": 0.0, "passed": False}, None, None),
        ({"source": "conformance", "status": "not_applicable", "pass_rate": None, "passed": False}, None, None),
        # Not measurements.
        ({"source": "conformance", "status": "passed", "pass_rate": True}, None, None),
        ({"source": "conformance", "status": "passed", "pass_rate": "n/a"}, None, None),
        ({"source": "conformance", "status": "passed", "pass_rate": float("nan")}, None, None),
        (None, None, None),
        ({}, None, None),
        ("conformance", None, None),
    ],
)
def test_only_a_graded_conformance_verdict_counts(functional, rate, passed):
    assert conformance_pass_rate(functional) == rate
    assert conformance_passed(functional) is passed


def test_is_conformance():
    assert is_conformance({"source": "conformance"}) is True
    assert is_conformance(SMOKE) is False
    assert is_conformance(None) is False
