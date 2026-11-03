"""Language detection, pinned tree-sitter grammars and per-language source conventions (spec section 7.3).

The grammars come from ``tree-sitter-language-pack==0.13.0`` (pinned by WP-01 because it ships the
grammars inside the wheel; 1.x downloads them at first use). Parsers are cached per thread: a
``tree_sitter.Parser`` is not safe to share between threads.
"""

from __future__ import annotations

import threading
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Final

if TYPE_CHECKING:
    import tree_sitter

__all__ = [
    "COMMENT_PREFIX",
    "GRAMMAR",
    "HOLE_MARKER",
    "LANGUAGE_BY_EXT",
    "PLACEHOLDER",
    "SCRIPT_LANGUAGES",
    "SFC_LANGUAGES",
    "UnsupportedLanguageError",
    "language_for",
    "parse",
]

# File language (stored on ``SymbolEntry.language`` and ``SlotTarget.language``) by extension.
LANGUAGE_BY_EXT: Final[dict[str, str]] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".vue": "vue",
    ".svelte": "svelte",
}
# Grammar per language (the pack's names).
GRAMMAR: Final[dict[str, str]] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "java": "java",
    "html": "html",
    "css": "css",
    "vue": "vue",
    "svelte": "svelte",
}
# Languages whose symbols are extracted (html/css are parsed and listed, but have no symbols).
SCRIPT_LANGUAGES: Final = frozenset({"python", "javascript", "typescript", "tsx", "go", "java"})
# Single-file components: the <script> blocks are parsed as js/ts (by the block's lang attribute).
SFC_LANGUAGES: Final = frozenset({"vue", "svelte"})

# Neutral stub bodies (section 7.4): no hint of what the original did.
PLACEHOLDER: Final[dict[str, str]] = {
    "python": "raise NotImplementedError",
    "javascript": 'throw new Error("not implemented");',
    "typescript": 'throw new Error("not implemented");',
    "tsx": 'throw new Error("not implemented");',
    "vue": 'throw new Error("not implemented");',
    "svelte": 'throw new Error("not implemented");',
    "go": 'panic("not implemented")',
    "java": 'throw new UnsupportedOperationException("not implemented");',
}
# ``slot.hole_marker``: a fixed string in the language's comment syntax (section 7.5).
HOLE_MARKER: Final[dict[str, str]] = {
    "python": "# __SLOT__",
    "javascript": "/* __SLOT__ */",
    "typescript": "/* __SLOT__ */",
    "tsx": "/* __SLOT__ */",
    "go": "/* __SLOT__ */",
    "java": "/* __SLOT__ */",
    "css": "/* __SLOT__ */",
    "html": "<!-- __SLOT__ -->",
    "vue": "<!-- __SLOT__ -->",
    "svelte": "<!-- __SLOT__ -->",
}
DEFAULT_HOLE_MARKER: Final = "/* __SLOT__ */"
COMMENT_PREFIX: Final[dict[str, str]] = {"python": "#"}


class UnsupportedLanguageError(ValueError):
    """The file's language has no pinned grammar."""


def language_for(path: str) -> str:
    """The language of ``path`` by extension ("" when unknown)."""
    return LANGUAGE_BY_EXT.get(PurePosixPath(path).suffix.lower(), "")


def hole_marker(language: str) -> str:
    return HOLE_MARKER.get(language, DEFAULT_HOLE_MARKER)


_local = threading.local()


def _parser(language: str) -> tree_sitter.Parser:
    cache: dict[str, tree_sitter.Parser] = getattr(_local, "parsers", None) or {}
    _local.parsers = cache
    if language not in cache:
        grammar = GRAMMAR.get(language)
        if grammar is None:
            msg = f"no pinned tree-sitter grammar for {language!r}"
            raise UnsupportedLanguageError(msg)
        from tree_sitter_language_pack import get_parser

        cache[language] = get_parser(grammar)  # type: ignore[arg-type]
    return cache[language]


def parse(language: str, source: bytes) -> tree_sitter.Tree:
    """Parse ``source`` with the pinned grammar of ``language``."""
    return _parser(language).parse(source)
