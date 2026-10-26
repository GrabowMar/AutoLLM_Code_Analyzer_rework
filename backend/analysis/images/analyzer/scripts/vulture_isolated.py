"""Run vulture with flake8-style ``# noqa`` comments ignored.

vulture honours ``# noqa`` (every item on the line) and ``# noqa: F401`` /
``F841`` (unused imports / variables) in every scanned file and has no option
to turn that off, so an analysed app could hide its own dead code. This
wrapper replaces vulture's noqa parser with one that finds no suppressions,
then runs vulture's own command line unchanged (arguments, output and exit
codes are vulture's).

Usage: <vulture venv python> -I vulture_isolated.py <vulture args...>
"""

# ruff: noqa: INP001 - build/runtime script of the analyzer image, not an importable module

from __future__ import annotations

from collections import defaultdict

import vulture.noqa
from vulture.core import main


def no_noqa(_code: object) -> defaultdict[str, set[int]]:
    """vulture.noqa.parse_noqa without suppressions: no line is ignored."""
    return defaultdict(set)


def disable_noqa(module: object = vulture.noqa) -> None:
    """Make vulture ignore ``# noqa`` (vulture.core calls ``noqa.parse_noqa`` per scanned file)."""
    module.parse_noqa = no_noqa  # type: ignore[attr-defined]


if __name__ == "__main__":
    disable_noqa()
    main()
