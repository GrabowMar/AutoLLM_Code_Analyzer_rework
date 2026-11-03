"""The builder's factor-expansion fixtures agree with the backend expansion (spec 10.8.1, WP-15).

Two fixture files under ``frontend/src/lib/components/studies/__fixtures__/`` are read by the frontend
(``builder/expansion.test.ts``) and checked here:

* ``expansion.json`` is WP-10's count fixture (``tests/wp10_study/fixtures/expansion.json``), copied verbatim:
  the copy must stay equal to the original;
* ``expansion-conditions.json`` (WP-15) lists the full expected conditions and the refused designs; every
  case runs through ``study/expand.py:expand_grid``.

A change to the expansion rules must update the fixtures, which then fails whichever side was not ported.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from backend.generation.contracts.plan import Design
from backend.generation.study import design as design_rules
from backend.generation.study.design import DesignError
from backend.generation.study.expand import expand_grid

FIXTURES = Path(__file__).resolve().parents[4] / "frontend" / "src" / "lib" / "components" / "studies" / "__fixtures__"
FIXTURE = FIXTURES / "expansion-conditions.json"
WP10_FIXTURE = Path(__file__).resolve().parents[1] / "wp10_study" / "fixtures" / "expansion.json"


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _design(case: dict[str, Any]) -> Design:
    return Design.model_validate(
        {
            "mode": "app",
            "kind": "exploratory",
            "factors": case["factors"],
            "units": {"tasks": ["crud-todo@1"]},
            "repeats": 1,
            "grid": case["grid"],
            "base_seed": 1,
            "order_seed": 1,
            "resource_policy": "frugal@1",
            "evaluation": {"eval_suite": "eval-v2@1"},
        },
    )


def test_frontend_copy_of_the_wp10_fixture_is_current():
    copy = json.loads((FIXTURES / "expansion.json").read_text(encoding="utf-8"))
    original = json.loads(WP10_FIXTURE.read_text(encoding="utf-8"))
    assert copy == original, "copy tests/wp10_study/fixtures/expansion.json to the frontend __fixtures__ again"


def test_fixture_is_present_and_non_trivial():
    cases = _cases()
    assert len(cases) >= 8
    assert any("error" in case for case in cases)
    assert any(case["grid"]["type"] == "fractional" for case in cases)


def _expand_checked(case: dict[str, Any]) -> None:
    design = _design(case)
    # The structural factor/grid rules (the unit and stack rules are not part of the expansion).
    design_rules._check_factors(design)
    design_rules._check_grid(design)
    expand_grid(design)


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_backend_expansion_matches_the_fixture(case):
    if "error" in case:
        with pytest.raises(DesignError, match=re.escape(case["error"])):
            _expand_checked(case)
        return
    design = _design(case)
    expansion = expand_grid(design)
    expected = case["expected"]
    assert expansion.conditions == expected["conditions"]
    assert len(expansion.conditions) == expected["n_conditions"]
    assert expansion.full_size == expected["full_size"]
    assert expansion.excluded == expected["excluded"]
    assert expansion.fractional is expected["fractional"]
    assert sorted(issue.code for issue in expansion.issues) == sorted(expected["issues"])
