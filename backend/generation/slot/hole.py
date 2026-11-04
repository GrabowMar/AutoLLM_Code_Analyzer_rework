"""Hole application: what the slot file looks like with the part taken out (spec sections 7.4 and 7.5).

``apply_hole(source, address, hole_mode, keep_docstring=False, whole_file_component=False)`` returns a
:class:`HoleResult`. The holed file is always ``original[:start] + hole + original[end:]`` for span kinds, so
a restricted agent's edit can be checked by prefix/suffix comparison and the replacement is the text between
them (section 7.6).

Hole modes:

* ``stub`` (symbol, endpoint, component render body, file): signatures and decorators stay, every function
  body inside the span is replaced by the hole marker plus the language's neutral placeholder
  (``raise NotImplementedError`` / ``throw new Error("not implemented");`` / ``panic("not implemented")`` /
  ``throw new UnsupportedOperationException("not implemented");``). Docstrings and in-span comments are
  stripped unless ``keep_docstring``. A span with no function in it (a region, a Go struct) becomes the
  hole marker line.
* ``remove``: ``file`` and whole-file components delete the file; other kinds replace the span with the hole
  marker line; ``region`` + ``remove`` deletes the lines without a marker.
* ``rewrite``: nothing is removed (the original stays visible and the prompt asks for a rewrite).

Line endings follow the original file (CRLF files get CRLF in the inserted text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Final

from backend.generation.slot.index.languages import PLACEHOLDER
from backend.generation.slot.index.languages import SFC_LANGUAGES
from backend.generation.slot.index.languages import hole_marker
from backend.generation.slot.index.languages import language_for
from backend.generation.slot.index.treesitter import ParseUnit
from backend.generation.slot.index.treesitter import line_start
from backend.generation.slot.index.treesitter import parse_file
from backend.generation.slot.index.treesitter import walk

if TYPE_CHECKING:
    import tree_sitter

    from backend.generation.contracts.slot import SlotAddress

__all__ = ["HoleResult", "apply_hole", "newline_of", "span_bytes"]

_FUNCTION_TYPES: Final = frozenset(
    {
        "function_definition",  # python
        "function_declaration",  # js, go
        "generator_function_declaration",
        "function_expression",
        "function",
        "arrow_function",
        "method_definition",
        "method_declaration",  # go, java
        "constructor_declaration",
        "func_literal",
    },
)
_COMMENT_TYPES: Final = frozenset({"comment", "line_comment", "block_comment"})
_VUE_TEMPLATE_RE: Final = re.compile(rb"(<template\b[^>]*>)(.*)(</template>)", re.S | re.I)
_SFC_BLOCK_RE: Final = re.compile(rb"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)


@dataclass(frozen=True)
class HoleResult:
    """The slot file with the hole applied (``None`` when the file is removed)."""

    path: str
    removed: bool
    holed: bytes | None
    start: int  # byte offset of the hole inside the original (and the holed) file
    end: int  # end of the original span (exclusive) in the original file
    hole: bytes  # what stands in place of original[start:end]

    @property
    def prefix(self) -> bytes:
        return b"" if self.holed is None else self.holed[: self.start]

    @property
    def suffix(self) -> bytes:
        return b"" if self.holed is None else self.holed[self.start + len(self.hole) :]

    @property
    def holed_text(self) -> str:
        return "" if self.holed is None else self.holed.decode("utf-8", errors="replace")


def newline_of(source: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in source else b"\n"


def span_bytes(source: bytes, address: SlotAddress) -> tuple[int, int]:
    """``(start, end)`` of the address in ``source`` (the whole file for kinds without a span)."""
    if address.span is None:
        return 0, len(source)
    return address.span.start_byte, address.span.end_byte


def _indent_of(source: bytes, offset: int) -> bytes:
    begin = line_start(source, offset)
    line = source[begin:offset]
    return line[: len(line) - len(line.lstrip(b" \t"))]


@dataclass
class _Edit:
    start: int
    end: int
    text: bytes


def _units_in(source: bytes, path: str) -> list[ParseUnit]:
    return parse_file(path, source).units


def _docstring_node(block: tree_sitter.Node) -> tree_sitter.Node | None:
    first = next((c for c in block.named_children if c.type != "comment"), None)
    if first is not None and first.type == "string":
        return first
    if first is not None and first.type == "expression_statement" and first.named_children:
        if first.named_children[0].type == "string":
            return first
    return None


def _whole_lines(source: bytes, start: int, end: int) -> tuple[int, int] | None:
    """The full-line range of ``[start, end)`` when only whitespace surrounds it on its lines."""
    begin = line_start(source, start)
    newline = source.find(b"\n", end)
    stop = len(source) if newline < 0 else newline + 1
    if source[begin:start].strip() or source[end:stop].strip():
        return None
    return begin, stop


def _deletion(source: bytes, start: int, end: int) -> _Edit:
    lines = _whole_lines(source, start, end)
    if lines is not None:
        return _Edit(lines[0], lines[1], b"")
    # Trailing comment: drop it and the whitespace before it.
    begin = start
    while begin > 0 and source[begin - 1 : begin] in {b" ", b"\t"}:
        begin -= 1
    return _Edit(begin, end, b"")


def _body_stub(
    source: bytes,
    unit: ParseUnit,
    function: tree_sitter.Node,
    language: str,
    *,
    keep_docstring: bool,
) -> _Edit | None:
    body = function.child_by_field_name("body")
    if body is None:
        return None
    newline = newline_of(source)
    start, end = unit.abs_byte(body.start_byte), unit.abs_byte(body.end_byte)
    marker = hole_marker(language).encode()
    placeholder = PLACEHOLDER.get(language, PLACEHOLDER["javascript"]).encode()
    header_indent = _indent_of(source, unit.abs_byte(function.start_byte))
    if language == "python":
        same_line = bool(source[line_start(source, start) : start].strip())
        indent = header_indent + b"    " if same_line else _indent_of(source, start)
        lines: list[bytes] = []
        doc = _docstring_node(body) if keep_docstring else None
        if doc is not None:
            lines.append(source[unit.abs_byte(doc.start_byte) : unit.abs_byte(doc.end_byte)])
        lines += [marker, placeholder]
        text = (newline + indent).join(lines)
        if same_line:
            text = newline + indent + text
            while start > 0 and source[start - 1 : start] in {b" ", b"\t"}:
                start -= 1
        return _Edit(start, end, text)
    # Brace languages: the block is "{ ... }"; an arrow function's expression body becomes a block.
    inner_indent = header_indent + (b"\t" if language == "go" else b"    ")
    statements = [c for c in body.named_children if c.type not in _COMMENT_TYPES]
    if body.type in {"statement_block", "block", "constructor_body"} and statements:
        first = unit.abs_byte(statements[0].start_byte)
        if source[line_start(source, first) : first].strip() == b"":
            inner_indent = _indent_of(source, first)
    text = (
        b"{" + newline + inner_indent + marker + newline + inner_indent + placeholder + newline + header_indent + b"}"
    )
    return _Edit(start, end, text)


def _outermost_functions(unit: ParseUnit, start: int, end: int) -> list[tree_sitter.Node]:
    found: list[tree_sitter.Node] = []
    for node in walk(unit.tree.root_node):
        node_start, node_end = unit.abs_byte(node.start_byte), unit.abs_byte(node.end_byte)
        if node.type not in _FUNCTION_TYPES or node_start < start or node_end > end:
            continue
        if node.child_by_field_name("body") is None:
            continue
        if any(f.start_byte <= node.start_byte and node.end_byte <= f.end_byte for f in found):
            continue
        found.append(node)
    return found


def _stub_edits(source: bytes, path: str, start: int, end: int, *, keep_docstring: bool) -> list[_Edit] | None:
    """Edits that stub every outermost function in ``[start, end)``; ``None`` when there is none."""
    language = language_for(path)
    edits: list[_Edit] = []
    replaced: list[tuple[int, int]] = []
    for unit in _units_in(source, path):
        script_language = unit.language if language in SFC_LANGUAGES else language
        for function in _outermost_functions(unit, start, end):
            edit = _body_stub(source, unit, function, script_language, keep_docstring=keep_docstring)
            if edit is not None:
                edits.append(edit)
                replaced.append((edit.start, edit.end))
        if keep_docstring:
            continue
        for node in walk(unit.tree.root_node):
            node_start, node_end = unit.abs_byte(node.start_byte), unit.abs_byte(node.end_byte)
            if node_start < start or node_end > end or any(a <= node_start and node_end <= b for a, b in replaced):
                continue
            if node.type in _COMMENT_TYPES:
                edits.append(_deletion(source, node_start, node_end))
            elif unit.language == "python" and node.type in {"block", "module"}:
                doc = _docstring_node(node)
                if doc is not None:
                    doc_start, doc_end = unit.abs_byte(doc.start_byte), unit.abs_byte(doc.end_byte)
                    if start <= doc_start and doc_end <= end:
                        edits.append(_deletion(source, doc_start, doc_end))
    if not replaced:
        return None
    return _non_overlapping(edits)


def _non_overlapping(edits: list[_Edit]) -> list[_Edit]:
    kept: list[_Edit] = []
    for edit in sorted(edits, key=lambda e: (e.start, -e.end)):
        if kept and edit.start < kept[-1].end:
            continue
        kept.append(edit)
    return kept


def _apply(source: bytes, edits: list[_Edit]) -> bytes:
    out = source
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        out = out[: edit.start] + edit.text + out[edit.end :]
    return out


def _sfc_stub(source: bytes, language: str) -> bytes:
    marker = hole_marker(language).encode()
    newline = newline_of(source)
    if language == "vue":
        match = _VUE_TEMPLATE_RE.search(source)
        if match is None:
            return source
        return source[: match.start(2)] + newline + b"  " + marker + newline + source[match.end(2) :]
    blocks = [m.group(0) for m in _SFC_BLOCK_RE.finditer(source)]
    return newline.join([*blocks, marker]) + newline


def apply_hole(  # noqa: PLR0911 - one return per hole shape
    source: bytes,
    address: SlotAddress,
    hole_mode: str,
    *,
    keep_docstring: bool = False,
    whole_file_component: bool = False,
) -> HoleResult:
    """Apply ``hole_mode`` to the address's part of ``source`` (the slot file's original bytes)."""
    path = address.path
    language = language_for(path)
    start, end = span_bytes(source, address)
    original = source[start:end]
    if hole_mode == "rewrite":
        return HoleResult(path=path, removed=False, holed=source, start=start, end=end, hole=original)
    kind = address.kind
    whole = kind == "file" or (kind == "component" and (address.span is None or whole_file_component))
    if hole_mode == "remove" and whole:
        return HoleResult(path=path, removed=True, holed=None, start=0, end=len(source), hole=b"")
    if kind == "component" and address.span is None and language in SFC_LANGUAGES:
        holed = _sfc_stub(source, language)
        return HoleResult(path=path, removed=False, holed=holed, start=0, end=len(source), hole=holed)
    if hole_mode == "remove":
        hole = b"" if kind == "region" else _region_marker(source, start, language)
        return _result(path, source, start, end, hole)
    # stub
    if kind == "region":
        return _result(path, source, start, end, _region_marker(source, start, language))
    edits = _stub_edits(source, path, start, end, keep_docstring=keep_docstring)
    if edits is None:
        return _result(path, source, start, end, _region_marker(source, start, language))
    relative = [_Edit(e.start - start, e.end - start, e.text) for e in edits]
    hole = _apply(original, relative)
    return _result(path, source, start, end, hole)


def _region_marker(source: bytes, start: int, language: str) -> bytes:
    """The hole marker standing in for a span: indented like the span's first line when it starts a line."""
    indent = b""
    if start == line_start(source, start):
        newline = source.find(b"\n", start)
        first_line = source[start : newline if newline >= 0 else len(source)]
        indent = first_line[: len(first_line) - len(first_line.lstrip(b" \t"))]
    return indent + hole_marker(language).encode()


def _result(path: str, source: bytes, start: int, end: int, hole: bytes) -> HoleResult:
    return HoleResult(
        path=path,
        removed=False,
        holed=source[:start] + hole + source[end:],
        start=start,
        end=end,
        hole=hole,
    )
