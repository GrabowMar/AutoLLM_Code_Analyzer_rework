"""Tree-sitter symbol extraction (pinned grammars from tree-sitter-language-pack; spec section 7.3).

``parse_file(path, source)`` parses one file into a :class:`ParsedFile` (single-file components are split
into their ``<script>`` blocks, each parsed as javascript or typescript). ``extract_symbols(parsed)``
returns the :class:`RawSymbol` list of the file: functions, methods, classes and the ``<module>``
pseudo-symbol, with spans in **file** coordinates (1-based inclusive lines, byte offsets with an
exclusive end).

Span convention (shared by selection, holes, assembly and the restricted agent): a symbol span starts at
the beginning of its first line when only whitespace precedes it there (so a replacement carries its own
indentation), includes decorators, annotations and ``export``, and ends where the node ends (before the
newline that follows it).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Final

from backend.generation import identity
from backend.generation.contracts.slot import Span
from backend.generation.slot.index.languages import SCRIPT_LANGUAGES
from backend.generation.slot.index.languages import SFC_LANGUAGES
from backend.generation.slot.index.languages import UnsupportedLanguageError
from backend.generation.slot.index.languages import language_for
from backend.generation.slot.index.languages import parse

if TYPE_CHECKING:
    import tree_sitter

__all__ = [
    "MAX_PARSE_BYTES",
    "ParseUnit",
    "ParsedFile",
    "RawSymbol",
    "extract_symbols",
    "line_start",
    "node_text",
    "parse_file",
    "span_for",
    "span_from_bytes",
    "walk",
]

MAX_PARSE_BYTES: Final = 512 * 1024
_SCRIPT_BLOCK_RE: Final = re.compile(rb"<script\b([^>]*)>", re.IGNORECASE)
_LANG_ATTR_RE: Final = re.compile(rb"""\blang\s*=\s*["']?(ts|typescript|tsx)\b""", re.IGNORECASE)
_JSX_TYPES: Final = frozenset({"jsx_element", "jsx_self_closing_element", "jsx_fragment"})
_JS_FUNCTION_VALUES: Final = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function", "class", "class_expression"},
)
_JS_DECLARATIONS: Final = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
}


@dataclass(frozen=True)
class ParseUnit:
    """One parsed region of a file: the whole file, or one ``<script>`` block of a Vue/Svelte component."""

    language: str  # the grammar's language (javascript/typescript for SFC script blocks)
    tree: tree_sitter.Tree
    source: bytes  # the parsed bytes (the block's bytes for a script block)
    base_byte: int = 0  # offset of ``source`` inside the file
    base_line: int = 0  # 0-based line of ``source[0]`` inside the file

    def abs_byte(self, offset: int) -> int:
        return self.base_byte + offset


@dataclass
class ParsedFile:
    path: str
    language: str  # the file's language ("" when unknown)
    source: bytes
    units: list[ParseUnit] = field(default_factory=list)
    parsed: bool = False
    error: str = ""

    @property
    def text(self) -> str:
        return self.source.decode("utf-8", errors="replace")


@dataclass
class RawSymbol:
    """A symbol before it becomes a ``contracts.slot.SymbolEntry`` (keeps the parse nodes)."""

    path: str
    language: str
    kind: str
    name: str
    qualname: str
    span: Span
    signature: str
    decorators: list[str]
    content_sha: str
    parent_qualname: str | None
    unit: ParseUnit | None = None
    node: tree_sitter.Node | None = None  # the definition node (not the decorated wrapper)
    has_jsx: bool = False
    symbol_id: str = ""


def node_text(node: tree_sitter.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def walk(node: tree_sitter.Node) -> Iterator[tree_sitter.Node]:
    """Pre-order traversal of named and anonymous nodes."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def line_start(source: bytes, offset: int) -> int:
    """The byte offset of the start of the line holding ``offset``."""
    return source.rfind(b"\n", 0, offset) + 1


def line_of(source: bytes, offset: int) -> int:
    """1-based line number of byte ``offset``."""
    return source.count(b"\n", 0, offset) + 1


def span_from_bytes(source: bytes, start: int, end: int) -> Span:
    """A span over ``source[start:end]`` (lines are 1-based and inclusive)."""
    last = end - 1 if end > start else start
    return Span(start_line=line_of(source, start), end_line=line_of(source, last), start_byte=start, end_byte=end)


def span_for(source: bytes, start: int, end: int) -> Span:
    """A symbol span: snapped to the line start when only whitespace precedes ``start`` on its line."""
    begin = line_start(source, start)
    if source[begin:start].strip(b" \t"):
        begin = start
    return span_from_bytes(source, begin, end)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _script_units(source: bytes) -> list[ParseUnit]:
    """The ``<script>`` blocks of a Vue/Svelte component, each parsed as javascript or typescript."""
    units: list[ParseUnit] = []
    for match in _SCRIPT_BLOCK_RE.finditer(source):
        open_end = match.end()
        close = source.find(b"</script>", open_end)
        if close < 0:
            continue
        block = source[open_end:close]
        block_language = "typescript" if _LANG_ATTR_RE.search(match.group(1) or b"") else "javascript"
        units.append(
            ParseUnit(
                language=block_language,
                tree=parse(block_language, block),
                source=block,
                base_byte=open_end,
                base_line=source.count(b"\n", 0, open_end),
            ),
        )
    return units


def parse_file(path: str, source: bytes) -> ParsedFile:
    """Parse ``source``; files without a grammar, binary files and files over the size cap stay unparsed.

    A file with syntax errors keeps its (error-recovered) units so its symbols can still be listed, but it is
    marked ``parsed=False`` and reported in the index's ``unparsed`` list.
    """
    language = language_for(path)
    parsed = ParsedFile(path=path, language=language, source=source)
    if not language:
        parsed.error = "no grammar"
        return parsed
    if len(source) > MAX_PARSE_BYTES:
        parsed.error = "too large"
        return parsed
    if b"\x00" in source:
        parsed.error = "binary"
        return parsed
    try:
        if language in SFC_LANGUAGES:
            whole = parse(language, source)
            parsed.units = _script_units(source)
            broken = whole.root_node.has_error or any(unit.tree.root_node.has_error for unit in parsed.units)
        else:
            tree = parse(language, source)
            parsed.units = [ParseUnit(language=language, tree=tree, source=source)]
            broken = tree.root_node.has_error
    except UnsupportedLanguageError as exc:
        parsed.error = str(exc)
        return parsed
    if broken:
        parsed.error = "syntax error"
    parsed.parsed = not broken
    return parsed


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


@dataclass
class _Builder:
    parsed: ParsedFile
    unit: ParseUnit
    symbols: list[RawSymbol] = field(default_factory=list)

    def text(self, node: tree_sitter.Node) -> str:
        return node_text(node, self.unit.source)

    def add(
        self,
        *,
        node: tree_sitter.Node,
        span_node: tree_sitter.Node,
        kind: str,
        name: str,
        parent: str | None,
        body: tree_sitter.Node | None,
        decorators: list[str],
    ) -> str:
        qualname = f"{parent}.{name}" if parent else name
        span = span_for(
            self.parsed.source,
            self.unit.abs_byte(span_node.start_byte),
            self.unit.abs_byte(span_node.end_byte),
        )
        header_end = body.start_byte if body is not None else node.end_byte
        signature = " ".join(self.unit.source[node.start_byte : header_end].decode("utf-8", "replace").split())
        signature = signature.removesuffix("=>").strip().removesuffix(":").strip()
        content = self.parsed.source[span.start_byte : span.end_byte].decode("utf-8", errors="replace")
        self.symbols.append(
            RawSymbol(
                path=self.parsed.path,
                language=self.parsed.language,
                kind=kind,
                name=name,
                qualname=qualname,
                span=span,
                signature=signature,
                decorators=decorators,
                content_sha=identity.text_sha(content),
                parent_qualname=parent,
                unit=self.unit,
                node=node,
                has_jsx=any(child.type in _JSX_TYPES for child in walk(node)),
            ),
        )
        return qualname


# -- python -------------------------------------------------------------------

_PY_SCOPE_BLOCKS: Final = frozenset({"block", "else_clause", "elif_clause", "except_clause", "finally_clause"})
_PY_SKIP: Final = frozenset({"expression_statement", "import_statement", "import_from_statement", "comment"})


def _python(builder: _Builder, node: tree_sitter.Node, parent: str | None, *, in_class: bool) -> None:
    for child in node.named_children:
        definition, decorators = child, []
        if child.type == "decorated_definition":
            definition = child.child_by_field_name("definition") or child
            decorators = [builder.text(d) for d in child.named_children if d.type == "decorator"]
        if definition.type in {"function_definition", "class_definition"}:
            name_node = definition.child_by_field_name("name")
            if name_node is None:
                continue
            is_class = definition.type == "class_definition"
            body = definition.child_by_field_name("body")
            qualname = builder.add(
                node=definition,
                span_node=child,
                kind="class" if is_class else ("method" if in_class else "function"),
                name=builder.text(name_node),
                parent=parent,
                body=body,
                decorators=decorators,
            )
            if body is not None:
                _python(builder, body, qualname, in_class=is_class)
        elif child.type not in _PY_SKIP:
            # Definitions inside if/try/with/for blocks belong to the enclosing scope.
            for part in child.named_children:
                if part.type == "block":
                    _python(builder, part, parent, in_class=in_class)
                elif part.type in _PY_SCOPE_BLOCKS:
                    for block in part.named_children:
                        if block.type == "block":
                            _python(builder, block, parent, in_class=in_class)


# -- javascript / typescript ---------------------------------------------------


def _js_decorators(builder: _Builder, node: tree_sitter.Node) -> list[str]:
    return [builder.text(child) for child in node.children if child.type == "decorator"]


def _unwrap_call(value: tree_sitter.Node) -> tree_sitter.Node:
    """``memo(() => ...)`` / ``forwardRef(function X() {...})``: the wrapped function (a React idiom)."""
    if value.type == "call_expression":
        arguments = value.child_by_field_name("arguments")
        if arguments is not None:
            for argument in arguments.named_children:
                if argument.type in _JS_FUNCTION_VALUES:
                    return argument
    return value


def _js_declaration(
    builder: _Builder,
    node: tree_sitter.Node,
    span_node: tree_sitter.Node,
    parent: str | None,
) -> None:
    if node.type in _JS_DECLARATIONS or (
        node.type in {"function_expression", "function", "class"} and node.child_by_field_name("name") is not None
    ):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        kind = _JS_DECLARATIONS.get(node.type, "class" if node.type == "class" else "function")
        body = node.child_by_field_name("body")
        qualname = builder.add(
            node=node,
            span_node=span_node,
            kind=kind,
            name=builder.text(name_node),
            parent=parent,
            body=body,
            decorators=_js_decorators(builder, node),
        )
        if body is not None:
            if kind == "class":
                _js_class_body(builder, body, qualname)
            else:
                _js_statements(builder, body, qualname)
        return
    if node.type not in {"lexical_declaration", "variable_declaration"}:
        return
    declarators = [child for child in node.named_children if child.type == "variable_declarator"]
    for declarator in declarators:
        name_node = declarator.child_by_field_name("name")
        value = declarator.child_by_field_name("value")
        if name_node is None or value is None or name_node.type != "identifier":
            continue
        value = _unwrap_call(value)
        if value.type not in _JS_FUNCTION_VALUES:
            continue
        is_class = value.type in {"class", "class_expression"}
        body = value.child_by_field_name("body")
        qualname = builder.add(
            node=value,
            # One declarator: the whole statement (keeps `const` / `export`); several: the declarator only.
            span_node=span_node if len(declarators) == 1 else declarator,
            kind="class" if is_class else "function",
            name=builder.text(name_node),
            parent=parent,
            body=body,
            decorators=[],
        )
        if body is not None:
            if is_class:
                _js_class_body(builder, body, qualname)
            elif body.type == "statement_block":
                _js_statements(builder, body, qualname)


def _js_statements(builder: _Builder, node: tree_sitter.Node, parent: str | None) -> None:
    for child in node.named_children:
        if child.type == "export_statement":
            declaration = child.child_by_field_name("declaration")
            value = child.child_by_field_name("value")
            if declaration is not None:
                _js_declaration(builder, declaration, child, parent)
            elif value is not None:
                _js_declaration(builder, value, child, parent)
        else:
            _js_declaration(builder, child, child, parent)


def _js_class_body(builder: _Builder, body: tree_sitter.Node, parent: str) -> None:
    for child in body.named_children:
        if child.type == "method_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            builder.add(
                node=child,
                span_node=child,
                kind="method",
                name=builder.text(name_node),
                parent=parent,
                body=child.child_by_field_name("body"),
                decorators=_js_decorators(builder, child),
            )
        elif child.type in {"public_field_definition", "field_definition"}:
            name_node = child.child_by_field_name("name") or child.child_by_field_name("property")
            value = child.child_by_field_name("value")
            if name_node is None or value is None or value.type not in {"arrow_function", "function_expression"}:
                continue
            builder.add(
                node=value,
                span_node=child,
                kind="method",
                name=builder.text(name_node),
                parent=parent,
                body=value.child_by_field_name("body"),
                decorators=_js_decorators(builder, child),
            )


# -- go -------------------------------------------------------------------------


def _go_receiver_type(builder: _Builder, receiver: tree_sitter.Node | None) -> str:
    if receiver is None:
        return ""
    for node in walk(receiver):
        if node.type == "type_identifier":
            return builder.text(node)
    return ""


def _go(builder: _Builder, root: tree_sitter.Node) -> None:
    for child in root.named_children:
        if child.type == "type_declaration":
            specs = [spec for spec in child.named_children if spec.type == "type_spec"]
            for spec in specs:
                name_node = spec.child_by_field_name("name")
                if name_node is None:
                    continue
                builder.add(
                    node=spec,
                    span_node=child if len(specs) == 1 else spec,
                    kind="class",
                    name=builder.text(name_node),
                    parent=None,
                    body=spec.child_by_field_name("type"),
                    decorators=[],
                )
        elif child.type in {"function_declaration", "method_declaration"}:
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            receiver = ""
            if child.type == "method_declaration":
                receiver = _go_receiver_type(builder, child.child_by_field_name("receiver"))
            builder.add(
                node=child,
                span_node=child,
                kind="method" if receiver else "function",
                name=builder.text(name_node),
                parent=receiver or None,
                body=child.child_by_field_name("body"),
                decorators=[],
            )


# -- java -----------------------------------------------------------------------

_JAVA_TYPES: Final = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
)


def _java_annotations(builder: _Builder, node: tree_sitter.Node) -> list[str]:
    for child in node.children:
        if child.type == "modifiers":
            return [builder.text(a) for a in child.named_children if a.type in {"annotation", "marker_annotation"}]
    return []


def _java(builder: _Builder, node: tree_sitter.Node, parent: str | None) -> None:
    for child in node.named_children:
        if child.type in _JAVA_TYPES:
            name_node = child.child_by_field_name("name")
            body = child.child_by_field_name("body")
            if name_node is None:
                continue
            qualname = builder.add(
                node=child,
                span_node=child,
                kind="class",
                name=builder.text(name_node),
                parent=parent,
                body=body,
                decorators=_java_annotations(builder, child),
            )
            if body is not None:
                _java(builder, body, qualname)
        elif child.type in {"method_declaration", "constructor_declaration"} and parent:
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            builder.add(
                node=child,
                span_node=child,
                kind="method",
                name=builder.text(name_node),
                parent=parent,
                body=child.child_by_field_name("body"),
                decorators=_java_annotations(builder, child),
            )


def _strip_annotations(symbol: RawSymbol) -> None:
    text = symbol.signature
    for decorator in symbol.decorators:
        text = text.replace(" ".join(decorator.split()), "")
    symbol.signature = " ".join(text.split())


# ---------------------------------------------------------------------------


def extract_symbols(parsed: ParsedFile) -> list[RawSymbol]:
    """Every symbol of a parsed file (plus ``<module>``) in source order, with unique ``path::qualname`` ids."""
    symbols: list[RawSymbol] = []
    for unit in parsed.units:
        builder = _Builder(parsed=parsed, unit=unit)
        root = unit.tree.root_node
        if unit.language == "python":
            _python(builder, root, None, in_class=False)
        elif unit.language in {"javascript", "typescript", "tsx"}:
            _js_statements(builder, root, None)
        elif unit.language == "go":
            _go(builder, root)
        elif unit.language == "java":
            _java(builder, root, None)
            for symbol in builder.symbols:
                _strip_annotations(symbol)
        symbols.extend(builder.symbols)
    if parsed.units or parsed.language in SCRIPT_LANGUAGES | SFC_LANGUAGES:
        symbols.append(
            RawSymbol(
                path=parsed.path,
                language=parsed.language,
                kind="module",
                name="<module>",
                qualname="<module>",
                span=span_from_bytes(parsed.source, 0, len(parsed.source)),
                signature="",
                decorators=[],
                content_sha=identity.text_sha(parsed.text),
                parent_qualname=None,
            ),
        )
    symbols.sort(key=lambda s: (s.span.start_byte, -s.span.end_byte, s.kind != "module"))
    seen: dict[str, int] = {}
    for symbol in symbols:
        base = f"{parsed.path}::{symbol.qualname}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        symbol.symbol_id = base if count == 1 else f"{base}#{count}"
    return symbols
