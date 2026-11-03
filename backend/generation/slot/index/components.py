"""React, Vue and Svelte component extraction (whole-file fallback for single-file components; section 7.3).

* React: a function, arrow function or class whose name starts with an upper-case letter, defined at module
  level of a js/ts file, that renders JSX (or extends ``Component``). ``whole_file`` is set when the file
  defines exactly one component and the component is named like the file (``TodoList.jsx``), so a
  ``remove`` hole may delete the file.
* Vue / Svelte: the file is the component (``whole_file=True``, named after the file); the SFC parse quality
  is not good enough for finer selection (appendix B), region selection still works.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Final

from backend.generation.contracts.slot import ComponentEntry

if TYPE_CHECKING:
    from backend.generation.slot.index.treesitter import ParsedFile
    from backend.generation.slot.index.treesitter import RawSymbol

__all__ = ["extract"]

_CLASS_COMPONENT_RE: Final = re.compile(r"\bextends\s+(?:React\.)?(?:Pure)?Component\b")
_REACT_LANGUAGES: Final = frozenset({"javascript", "typescript", "tsx"})


def _react_components(parsed: ParsedFile, symbols: list[RawSymbol]) -> list[tuple[RawSymbol, str]]:
    found: list[tuple[RawSymbol, str]] = []
    for symbol in symbols:
        if symbol.parent_qualname is not None or not symbol.name[:1].isupper():
            continue
        if (symbol.kind == "class" and _CLASS_COMPONENT_RE.search(symbol.signature)) or (
            symbol.kind == "function" and symbol.has_jsx
        ):
            found.append((symbol, symbol.name))
    del parsed
    return found


def extract(files: Mapping[str, ParsedFile], symbols: Mapping[str, list[RawSymbol]]) -> list[ComponentEntry]:
    """Every UI component of the tree, sorted by path then name. Marks React component symbols ``component``."""
    components: list[ComponentEntry] = []
    for path in sorted(files):
        parsed = files[path]
        stem = PurePosixPath(path).stem
        if parsed.language in {"vue", "svelte"}:
            components.append(
                ComponentEntry(name=stem, path=path, framework=parsed.language, symbol=None, whole_file=True),
            )
            continue
        if parsed.language not in _REACT_LANGUAGES:
            continue
        found = _react_components(parsed, symbols.get(path, []))
        for symbol, name in found:
            symbol.kind = "component"
            components.append(
                ComponentEntry(
                    name=name,
                    path=path,
                    framework="react",
                    symbol=symbol.symbol_id,
                    whole_file=len(found) == 1 and name == stem,
                ),
            )
    return components
