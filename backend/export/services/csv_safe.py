"""Spreadsheet-safe CSV cells.

Excel and LibreOffice read a cell that starts with ``=``, ``+``, ``-``,
``@``, a tab or a carriage return as a formula. Exported text is partly
written by LLMs and tools (finding titles, generated file names, advisory
names such as ``@remix-run/router``), so CSV exports prefix such cells with
an apostrophe, which spreadsheets show as text. Numbers, also negative ones
written as text, are left alone.
"""

from __future__ import annotations

import re
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def safe_cell(value: Any) -> Any:
    """*value* with a leading apostrophe when a spreadsheet would read it as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES) and not _NUMBER_RE.fullmatch(value):
        return "'" + value
    return value


def safe_row(row: list[Any], *, enabled: bool = True) -> list[Any]:
    """*row* with every cell passed through :func:`safe_cell` (unchanged when not *enabled*)."""
    return [safe_cell(v) for v in row] if enabled else row
