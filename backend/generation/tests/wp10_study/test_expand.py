"""Factor expansion and block order (pure; spec section 9.4)."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from backend.generation.contracts.issues import make_issue
from backend.generation.study.design import DesignError
from backend.generation.study.design import check_structure
from backend.generation.study.design import parse_design
from backend.generation.study.expand import expand
from backend.generation.study.expand import expand_grid
from backend.generation.study.order import block_order
from backend.generation.study.order import unit_orders

from .conftest import app_design


def _design(factors: list[dict[str, Any]], **overrides: Any):
    return parse_design(app_design(factors=factors, **overrides))


def _crossed(key: str, *levels: str) -> dict[str, Any]:
    return {"key": key, "role": "crossed", "levels": list(levels)}


def test_full_grid_counts_and_order():
    design = _design([_crossed("model", "a", "b"), _crossed("stack_profile", "s1", "s2", "s3")])
    conditions, issues = expand(design)
    assert len(conditions) == 6
    assert conditions[0] == {"model": "a", "stack_profile": "s1"}
    assert conditions[-1] == {"model": "b", "stack_profile": "s3"}
    assert issues == []


def test_fixed_factors_contribute_one_level_and_must_have_one():
    design = _design([_crossed("model", "a", "b"), {"key": "stack_profile", "role": "fixed", "levels": ["s"]}])
    assert len(expand(design)[0]) == 2
    with pytest.raises(DesignError, match="exactly one level"):
        check_structure(
            _design([_crossed("model", "a"), {"key": "stack_profile", "role": "fixed", "levels": ["s1", "s2"]}]),
        )


def test_fractional_half_of_a_2x2x2x2_has_8_balanced_runs():
    factors = [
        _crossed("model", "a", "b"),
        _crossed("stack_profile", "s1", "s2"),
        _crossed("style", "x", "y"),
        {"key": "guidance", "role": "fraction", "levels": ["g0", "g1"]},
    ]
    design = _design(factors, grid={"type": "fractional", "fraction": {"generator": "D=ABC"}})
    expansion = expand_grid(design)
    assert len(expansion.conditions) == 8
    assert expansion.fractional
    assert expansion.unbalanced
    # Every level of every factor appears equally often (a regular resolution-IV fraction).
    for key in ("model", "stack_profile", "style", "guidance"):
        assert set(Counter(c[key] for c in expansion.conditions).values()) == {4}
    assert not [i for i in expansion.issues if i.code == "aliasing"]
    complement = expand_grid(_design(factors, grid={"type": "fractional", "fraction": {"generator": "D=-ABC"}}))
    assert {tuple(c.values()) for c in complement.conditions}.isdisjoint(
        {tuple(c.values()) for c in expansion.conditions},
    )


def test_short_generator_aliases_main_effects():
    factors = [
        _crossed("model", "a", "b"),
        _crossed("stack_profile", "s1", "s2"),
        {"key": "guidance", "role": "fraction", "levels": ["g0", "g1"]},
    ]
    expansion = expand_grid(_design(factors, grid={"type": "fractional", "fraction": {"generator": "C=A"}}))
    assert [i.code for i in expansion.issues if i.code == "aliasing"] == ["aliasing"]


def test_generator_errors():
    factors = [_crossed("model", "a", "b", "c"), {"key": "guidance", "role": "fraction", "levels": ["g0", "g1"]}]
    with pytest.raises(DesignError, match="two-level"):
        expand_grid(_design(factors, grid={"type": "fractional", "fraction": {"generator": "B=A"}}))
    with pytest.raises(DesignError, match="form"):
        expand_grid(_design(factors, grid={"type": "fractional", "fraction": {"generator": "nonsense"}}))


def test_exclusions_and_pruning():
    factors = [_crossed("model", "a", "b"), _crossed("style", "x", "y")]
    design = _design(factors, grid={"type": "full", "exclude": [{"model": "a", "style": "y"}]})
    expansion = expand_grid(design)
    assert len(expansion.conditions) == 3
    assert [i.code for i in expansion.issues] == ["unbalanced_grid"]

    def incompatible(combination):
        if combination["style"] == "x":
            return [make_issue("incompatible_style", "exploratory", message="no x")]
        return []

    kept = expand_grid(_design(factors), compatible=incompatible)
    assert len(kept.conditions) == 4
    assert {i.code for i in kept.issues} == {"incompatible_style"}
    dropped = expand_grid(_design(factors), compatible=incompatible, drop_incompatible=True)
    assert len(dropped.conditions) == 2
    assert {i.code for i in dropped.issues} == {"combination_pruned", "unbalanced_grid"}


def test_block_order_is_repeat_major_seeded_and_shuffled_per_block():
    keys = ["task:a@1", "task:b@1", "task:c@1"]
    order = block_order(unit_keys=keys, repeats=2, n_conditions=4, order_seed=7)
    assert len(order) == 24
    assert [r for r, _, _ in order] == sorted(r for r, _, _ in order)
    assert order == block_order(unit_keys=keys, repeats=2, n_conditions=4, order_seed=7)
    assert order != block_order(unit_keys=keys, repeats=2, n_conditions=4, order_seed=8)
    blocks = [order[i : i + 4] for i in range(0, 24, 4)]
    assert all(len({(r, u) for r, u, _ in block}) == 1 for block in blocks)
    assert len({tuple(c for *_, c in block) for block in blocks}) > 1  # conditions reshuffled per block
    assert sorted(unit_orders(keys, 7)) == [0, 1, 2]


def test_blocked_scheduling_keeps_a_model_contiguous():
    order = block_order(unit_keys=["u"], repeats=1, n_conditions=4, order_seed=3, groups=["m1", "m2", "m1", "m2"])
    models = ["m1", "m2", "m1", "m2"]
    sequence = [models[c] for *_, c in order]
    assert sequence in (["m1", "m1", "m2", "m2"], ["m2", "m2", "m1", "m1"])


def _fixture_cases() -> list[dict[str, Any]]:
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures" / "expansion.json").read_text())["cases"]


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["name"])
def test_shared_expansion_fixtures(case):
    """The fixtures the builder's vitest suite shares (WP-15): conditions, cells and blocks per case."""
    design = _design(case["factors"], grid=case["grid"], repeats=case["repeats"])
    expansion = expand_grid(design)
    n_conditions = len(expansion.conditions)
    expected = case["expected"]
    assert n_conditions == expected["conditions"]
    assert n_conditions * case["units"] * case["repeats"] == expected["cells"]
    assert case["units"] * case["repeats"] == expected["blocks"]
    assert expansion.unbalanced is expected["unbalanced"]
    order = block_order(
        unit_keys=[f"task:t{i}@1" for i in range(case["units"])],
        repeats=case["repeats"],
        n_conditions=n_conditions,
        order_seed=7,
    )
    assert len(order) == expected["cells"]
