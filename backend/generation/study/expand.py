"""Factor expansion: full and fractional grids, exclusions and compatibility pruning (spec section 9.4).

Conditions are combinations of one level per factor, in design order (``itertools.product`` over the factors
as listed, so condition indices are stable for a given design):

* ``crossed`` factors are fully crossed; ``fixed`` factors contribute their single level;
* a ``fractional`` grid has exactly one ``fraction`` factor whose level is generated from the two-level
  crossed factors by the regular generator ``grid.fraction.generator`` (``"D=ABC"``; ``"D=-ABC"`` takes the
  complementary half). Letters ``A``, ``B``, ... name the crossed factors in design order; every letter on
  the right must name a crossed factor with exactly two levels, as must the fraction factor. Level index 0 is
  the ``-1`` coded level. A generator whose word is shorter than 3 letters (resolution < III) aliases main
  effects with each other: issue ``aliasing`` (warn);
* ``grid.exclude`` removes the listed level combinations (a designed exclusion, issue ``unbalanced_grid``);
* the optional ``compatible`` callback reports incompatible combinations (a style that does not fit the
  stack's frontend, a starter missing at the level, a harness that is not enabled, ...). They stay errors
  unless ``drop_incompatible``; dropped ones are listed as ``combination_pruned`` (info) and flag the grid
  ``unbalanced_grid``. ``by_factor`` result slices on a pruned or fractional grid use estimated marginal means.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Final

from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.study.design import DesignError

if TYPE_CHECKING:
    from backend.generation.contracts.plan import Design

__all__ = ["Expansion", "expand", "expand_grid", "parse_generator"]

_GENERATOR_RE: Final = re.compile(r"^\s*([A-Z])\s*=\s*(-?)\s*([A-Z]+)\s*$")
_MIN_RESOLUTION: Final = 3

# combination -> blocking issues (empty when compatible)
Compatibility = Callable[[dict[str, str]], list[Issue]]


@dataclass
class Expansion:
    """The expanded grid: conditions in index order plus what was removed and why."""

    conditions: list[dict[str, str]]
    issues: list[Issue] = field(default_factory=list)
    fractional: bool = False
    pruned: int = 0
    excluded: int = 0
    full_size: int = 0

    @property
    def unbalanced(self) -> bool:
        """``by_factor`` slices need estimated marginal means (pruned, excluded or fractional grid)."""
        return self.fractional or self.pruned > 0 or self.excluded > 0


def parse_generator(generator: str) -> tuple[str, bool, str]:
    """``"D=-ABC"`` -> ``("D", True, "ABC")`` (left letter, negated, word)."""
    match = _GENERATOR_RE.match(generator)
    if match is None:
        msg = f"grid.fraction.generator {generator!r} is not of the form 'D=ABC' (or 'D=-ABC')"
        raise DesignError(msg, path="grid.fraction.generator")
    left, minus, word = match.groups()
    if left in word or len(set(word)) != len(word):
        msg = f"generator {generator!r}: the word repeats a letter or contains its left side"
        raise DesignError(msg, path="grid.fraction.generator")
    return left, bool(minus), word


def _fraction_rule(design: Design) -> tuple[str, Callable[[dict[str, str]], str], list[Issue]]:
    """(fraction factor key, level generator, issues) of a fractional design."""
    assert design.grid.fraction is not None
    left, negated, word = parse_generator(design.grid.fraction.generator)
    crossed = [factor for factor in design.factors if factor.role == "crossed"]
    letters = {chr(ord("A") + position): factor for position, factor in enumerate(crossed)}
    (fraction,) = [factor for factor in design.factors if factor.role == "fraction"]
    if left in letters:
        msg = f"generator {left}=... names a crossed factor on its left side; the left side is the fraction factor"
        raise DesignError(msg, path="grid.fraction.generator")
    if len(fraction.levels) != 2:
        msg = f"the fraction factor {fraction.key!r} must have exactly two levels"
        raise DesignError(msg, path="grid.fraction")
    for letter in word:
        factor = letters.get(letter)
        if factor is None:
            msg = f"generator letter {letter} names no crossed factor (A..{chr(ord('A') + len(crossed) - 1)})"
            raise DesignError(msg, path="grid.fraction.generator")
        if len(factor.levels) != 2:
            msg = f"generator letter {letter} ({factor.key}) must name a two-level crossed factor"
            raise DesignError(msg, path="grid.fraction.generator")
    issues = []
    if len(word) + 1 < _MIN_RESOLUTION:
        issues.append(
            make_issue(
                "aliasing",
                design.kind,
                message=f"{design.grid.fraction.generator}: main effects of {fraction.key} and "
                f"{', '.join(letters[letter].key for letter in word)} are aliased (resolution < III)",
                path="grid.fraction.generator",
                data={"generator": design.grid.fraction.generator, "resolution": len(word) + 1},
            ),
        )

    def level_of(combination: dict[str, str]) -> str:
        sign = -1 if negated else 1
        for letter in word:
            factor = letters[letter]
            sign *= 1 if combination[factor.key] == factor.levels[1] else -1
        return fraction.levels[1] if sign > 0 else fraction.levels[0]

    return fraction.key, level_of, issues


def _excluded(combination: dict[str, str], exclusions: list[dict[str, str]]) -> bool:
    return any(all(combination.get(key) == level for key, level in combo.items()) for combo in exclusions)


def expand_grid(
    design: Design,
    *,
    drop_incompatible: bool = False,
    compatible: Compatibility | None = None,
) -> Expansion:
    """The conditions of ``design`` (raw levels as written in the design) with the expansion issues."""
    issues: list[Issue] = []
    fraction_key, level_of = None, None
    if design.grid.type == "fractional":
        fraction_key, level_of, fraction_issues = _fraction_rule(design)
        issues += fraction_issues
    grid_factors = [factor for factor in design.factors if factor.key != fraction_key]
    combinations: list[dict[str, str]] = []
    for values in itertools.product(*(factor.levels for factor in grid_factors)):
        combination = dict(zip((factor.key for factor in grid_factors), values, strict=True))
        if fraction_key is not None and level_of is not None:
            combination[fraction_key] = level_of(combination)
        # Keep the design's factor order in every condition dict.
        combinations.append({factor.key: combination[factor.key] for factor in design.factors})
    expansion = Expansion(conditions=[], fractional=fraction_key is not None, full_size=len(combinations))
    for combination in combinations:
        if _excluded(combination, design.grid.exclude):
            expansion.excluded += 1
            continue
        problems = compatible(combination) if compatible is not None else []
        if problems and drop_incompatible:
            expansion.pruned += 1
            issues.append(
                make_issue(
                    "combination_pruned",
                    design.kind,
                    message="dropped: " + "; ".join(problem.message or problem.code for problem in problems),
                    data={"levels": combination, "codes": sorted({problem.code for problem in problems})},
                ),
            )
            continue
        issues += problems
        expansion.conditions.append(combination)
    if expansion.excluded or expansion.pruned:
        issues.append(
            make_issue(
                "unbalanced_grid",
                design.kind,
                message=f"{expansion.excluded} excluded and {expansion.pruned} pruned combination(s); "
                "by-factor slices use estimated marginal means",
                data={"excluded": expansion.excluded, "pruned": expansion.pruned, "full_size": expansion.full_size},
            ),
        )
    if not expansion.conditions:
        msg = "the grid has no condition left after exclusions and pruning"
        raise DesignError(msg, path="grid")
    expansion.issues = issues
    return expansion


def expand(design: Design, *, drop_incompatible: bool = False) -> tuple[list[dict[str, str]], list[Issue]]:
    """Treatment conditions as ``{factor_key: level}`` plus the issues (``aliasing``, ``combination_pruned``, ...)."""
    expansion = expand_grid(design, drop_incompatible=drop_incompatible)
    return expansion.conditions, expansion.issues
