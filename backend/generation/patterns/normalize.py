"""Token-stream normalizers per language (spec section 7.9 step 1; ``NORMALIZER_VERSION = 1``).

``normalized_sha = identity.text_sha(normalize(code, language, NORMALIZER_VERSION))``.

The normalizer parses with tree-sitter (the pinned grammars of ``tree-sitter-language-pack``) and emits the
canonical **token stream** of the parse tree:

- comments are dropped, and so are docstrings (Python: a string that is the first statement of a module,
  class or function body);
- whitespace and formatting disappear because only leaf tokens are emitted; Python blocks emit ``{``/``}``
  markers so indentation structure survives;
- local identifiers (parameters and locals, per function/lambda/comprehension scope) are alpha-renamed by
  first occurrence (``v<depth>_<n>``); free and global names, attribute and property names and keyword
  argument names are kept;
- import statements of one block are sorted (by their token text) and emitted at the first import's place;
- string literals are kept verbatim as single tokens (they are often the pattern, e.g. a hard-coded secret).

Alpha-renaming and docstrings are implemented for ``python``, ``javascript`` (also ``jsx``), ``typescript``
and ``tsx``. ``vue`` and ``svelte`` normalize their ``<script>`` contents with the JS/TS normalizer and
their markup as whitespace-split text tokens. ``go``, ``java``, ``html`` and ``css`` get the generic
treatment (comments and whitespace only).

A variant that does not parse (``ERROR``/``MISSING`` nodes) raises :class:`NormalizationError` with reason
``parse_error``; :func:`normalized_sha` then returns ``""`` with that reason. Such a variant is counted,
excluded from clustering and never dropped (section 7.9).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from functools import lru_cache
from typing import TYPE_CHECKING
from typing import Any

from backend.generation import identity

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator

NORMALIZER_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})

LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "mts": "typescript",
}
EXTENSIONS = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "tsx": "tsx",
    "vue": "vue",
    "svelte": "svelte",
    "go": "go",
    "java": "java",
    "html": "html",
    "css": "css",
}


# The pinned grammars bundled in tree-sitter-language-pack 0.13.0 that v3 uses (verified by WP-01).
GRAMMARS = frozenset({"python", "javascript", "typescript", "tsx", "vue", "svelte", "go", "java", "html", "css"})


class NormalizationError(ValueError):
    """The code cannot be normalized; ``reason`` is ``parse_error``, ``empty`` or ``unsupported_language``."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class NormalizeResult:
    """``sha`` is ``""`` exactly when ``reason`` is set (never a silent value for an unparsable variant)."""

    sha: str
    tokens: tuple[str, ...]
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.reason


def canonical_language(language: str) -> str:
    lang = (language or "").strip().lower()
    return LANGUAGE_ALIASES.get(lang, lang)


@lru_cache(maxsize=32)
def parser_for(language: str) -> Any:
    """A cached tree-sitter parser for the canonical language name."""
    from tree_sitter_language_pack import get_parser

    return get_parser(language)  # type: ignore[arg-type]


@lru_cache(maxsize=32)
def language_for(language: str) -> Any:
    from tree_sitter_language_pack import get_language

    if language not in GRAMMARS:
        reason = "unsupported_language"
        raise NormalizationError(reason, language)

    return get_language(language)  # type: ignore[arg-type]


def parse(code: str, language: str) -> Any:
    """The tree-sitter tree of ``code`` (raises :class:`NormalizationError` for an unknown language)."""
    lang = canonical_language(language)
    if lang not in GRAMMARS:
        # Never ask the language pack for a grammar it may not ship: unknown names can block on a download.
        reason = "unsupported_language"
        raise NormalizationError(reason, lang)
    try:
        parser = parser_for(lang)
    except Exception as exc:
        reason = "unsupported_language"
        raise NormalizationError(reason, lang) from exc
    return parser.parse(code.encode("utf-8"))


# ---------------------------------------------------------------------------
# Language rules
# ---------------------------------------------------------------------------


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace")


def _field(node: Any, child: Any) -> str | None:
    """The field name under which ``child`` hangs off ``node`` (``None`` for an unnamed position)."""
    for index, candidate in enumerate(node.children):
        if candidate.id == child.id:
            return node.field_name_for_child(index)
    return None


@dataclass
class _Spec:
    comment_types: frozenset[str]
    atomic_types: frozenset[str]
    identifier_types: frozenset[str]
    scope_types: frozenset[str]
    import_types: frozenset[str]
    block_marker_types: frozenset[str] = frozenset()
    # Anonymous tokens that are formatting only (JS/TS: optional semicolons under ASI).
    dropped_tokens: frozenset[str] = frozenset()
    # Atomic string types that are expanded when they interpolate code (f-strings, template literals).
    interpolation_types: frozenset[str] = frozenset()
    docstring_containers: frozenset[str] = frozenset()
    binders: Callable[[Any], Iterator[Any]] | None = None
    keep_identifier: Callable[[Any], bool] | None = None
    excluded_names: Callable[[Any], set[str]] | None = None


# -- Python -------------------------------------------------------------------------------------------

_PY_SCOPES = frozenset(
    {
        "function_definition",
        "lambda",
        "list_comprehension",
        "set_comprehension",
        "dictionary_comprehension",
        "generator_expression",
    },
)
_PY_PATTERN_CONTAINERS = frozenset(
    {
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "tuple",
        "list",
        "parenthesized_expression",
        "list_splat_pattern",
        "dictionary_splat_pattern",
        "as_pattern_target",
        "expression_list",
        "list_splat",
    },
)
_PY_IMPORTS = frozenset({"import_statement", "import_from_statement", "future_import_statement"})
_DOCSTRING_OWNERS = frozenset({"function_definition", "class_definition"})


def _py_target_names(node: Any) -> Iterator[Any]:
    """Identifiers bound by an assignment target (attributes and subscripts bind nothing)."""
    if node.type == "identifier":
        yield node
    elif node.type in _PY_PATTERN_CONTAINERS:
        for child in node.named_children:
            yield from _py_target_names(child)


def _py_param_names(params: Any) -> Iterator[Any]:
    for child in params.named_children:
        if child.type == "identifier":
            yield child
        elif child.type in {"default_parameter", "typed_default_parameter"}:
            name = child.child_by_field_name("name")
            if name is not None:
                yield from _py_target_names(name)
        elif child.type == "typed_parameter":
            for sub in child.named_children:
                if sub.type in {"identifier", "list_splat_pattern", "dictionary_splat_pattern"}:
                    yield from _py_target_names(sub)
                    break
        elif child.type in {"list_splat_pattern", "dictionary_splat_pattern", "tuple_pattern"}:
            yield from _py_target_names(child)


def _py_binders(scope: Any) -> Iterator[Any]:
    """Every identifier node that declares a local of ``scope`` (nested scopes contribute their name only)."""
    params = scope.child_by_field_name("parameters")
    if params is not None:
        yield from _py_param_names(params)
    if scope.type.endswith("comprehension") or scope.type == "generator_expression":
        for child in scope.named_children:
            if child.type == "for_in_clause":
                left = child.child_by_field_name("left")
                if left is not None:
                    yield from _py_target_names(left)
        return
    body = scope.child_by_field_name("body")
    if body is None:
        return
    stack = [body]
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in {"function_definition", "class_definition"}:
            name = node.child_by_field_name("name")
            if name is not None:
                yield name
            continue  # a nested scope (classes do not leak their locals either)
        if kind in _PY_SCOPES:
            continue
        if kind in {"assignment", "augmented_assignment", "for_statement"}:
            left = node.child_by_field_name("left")
            if left is not None:
                yield from _py_target_names(left)
        elif kind == "named_expression":
            name = node.child_by_field_name("name")
            if name is not None:
                yield from _py_target_names(name)
        elif kind == "as_pattern":
            alias = node.child_by_field_name("alias")
            if alias is not None:
                yield from _py_target_names(alias)
        stack.extend(reversed(node.children))


def _py_excluded(scope: Any) -> set[str]:
    """Names declared ``global``/``nonlocal`` in the scope (they are not locals)."""
    names: set[str] = set()
    body = scope.child_by_field_name("body")
    stack = [body] if body is not None else []
    while stack:
        node = stack.pop()
        if node.type in _PY_SCOPES or node.type == "class_definition":
            continue
        if node.type in {"global_statement", "nonlocal_statement"}:
            names.update(_text(child) for child in node.named_children if child.type == "identifier")
        stack.extend(node.children)
    return names


def _py_keep(node: Any) -> bool:
    """Attribute names, keyword-argument names and imported names are never renamed."""
    parent = node.parent
    if parent is None:
        return False
    field_name = _field(parent, node)
    if parent.type == "attribute" and field_name == "attribute":
        return True
    if parent.type == "keyword_argument" and field_name == "name":
        return True
    ancestor = parent
    while ancestor is not None:
        if ancestor.type in _PY_IMPORTS:
            return True
        ancestor = ancestor.parent
    return False


_PYTHON = _Spec(
    comment_types=frozenset({"comment"}),
    atomic_types=frozenset({"string", "concatenated_string"}),
    identifier_types=frozenset({"identifier"}),
    scope_types=_PY_SCOPES,
    import_types=_PY_IMPORTS,
    block_marker_types=frozenset({"block"}),
    interpolation_types=frozenset({"interpolation"}),
    docstring_containers=frozenset({"module", "block"}),
    binders=_py_binders,
    keep_identifier=_py_keep,
    excluded_names=_py_excluded,
)

# -- JavaScript / TypeScript --------------------------------------------------------------------------

_JS_SCOPES = frozenset(
    {
        "function_declaration",
        "function_expression",
        "function",
        "generator_function_declaration",
        "generator_function",
        "arrow_function",
        "method_definition",
    },
)


def _js_pattern_names(node: Any) -> Iterator[Any]:
    kind = node.type
    if kind in {"identifier", "shorthand_property_identifier_pattern"}:
        yield node
    elif kind in {"object_pattern", "array_pattern", "rest_pattern"}:
        for child in node.named_children:
            yield from _js_pattern_names(child)
    elif kind == "pair_pattern":
        value = node.child_by_field_name("value")
        if value is not None:
            yield from _js_pattern_names(value)
    elif kind == "assignment_pattern":
        left = node.child_by_field_name("left")
        if left is not None:
            yield from _js_pattern_names(left)
    elif kind in {"required_parameter", "optional_parameter"}:
        pattern = node.child_by_field_name("pattern")
        if pattern is not None:
            yield from _js_pattern_names(pattern)


def _js_binders(scope: Any) -> Iterator[Any]:
    params = scope.child_by_field_name("parameters")
    if params is not None:
        for child in params.named_children:
            yield from _js_pattern_names(child)
    single = scope.child_by_field_name("parameter")
    if single is not None:
        yield from _js_pattern_names(single)
    body = scope.child_by_field_name("body")
    if body is None:
        return
    stack = [body]
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in {"function_declaration", "generator_function_declaration", "class_declaration"}:
            name = node.child_by_field_name("name")
            if name is not None:
                yield name
            continue
        if kind in _JS_SCOPES or kind in {"class", "class_body"}:
            continue
        if kind == "variable_declarator":
            name = node.child_by_field_name("name")
            if name is not None:
                yield from _js_pattern_names(name)
        elif kind == "catch_clause":
            parameter = node.child_by_field_name("parameter")
            if parameter is not None:
                yield from _js_pattern_names(parameter)
        elif kind == "for_in_statement" and node.child_by_field_name("kind") is not None:
            left = node.child_by_field_name("left")
            if left is not None:
                yield from _js_pattern_names(left)
        stack.extend(reversed(node.children))


def _js_keep(node: Any) -> bool:
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in {"import_statement", "export_clause"}:
            return True
        ancestor = ancestor.parent
    return False


def _js_spec() -> _Spec:
    return _Spec(
        comment_types=frozenset({"comment", "html_comment"}),
        atomic_types=frozenset({"string", "template_string", "regex"}),
        identifier_types=frozenset(
            {"identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern"},
        ),
        scope_types=_JS_SCOPES,
        import_types=frozenset({"import_statement"}),
        dropped_tokens=frozenset({";"}),
        interpolation_types=frozenset({"template_substitution"}),
        binders=_js_binders,
        keep_identifier=_js_keep,
    )


_GENERIC = _Spec(
    comment_types=frozenset({"comment", "line_comment", "block_comment"}),
    atomic_types=frozenset({"string", "interpreted_string_literal", "raw_string_literal", "string_literal"}),
    identifier_types=frozenset(),
    scope_types=frozenset(),
    import_types=frozenset(),
)

_SPECS: dict[str, _Spec] = {
    "python": _PYTHON,
    "javascript": _js_spec(),
    "typescript": _js_spec(),
    "tsx": _js_spec(),
}
ALPHA_RENAMED_LANGUAGES = frozenset(_SPECS)


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


@dataclass
class _Scope:
    depth: int
    names: set[str]
    aliases: dict[str, str] = field(default_factory=dict)

    def alias(self, name: str) -> str:
        if name not in self.aliases:
            self.aliases[name] = f"v{self.depth}_{len(self.aliases)}"
        return self.aliases[name]


class _Emitter:
    def __init__(self, spec: _Spec, language: str) -> None:
        self.spec = spec
        self.language = language
        self.scopes: list[_Scope] = []

    def run(self, root: Any) -> list[str]:
        out: list[str] = []
        self.emit(root, out)
        return out

    # -- helpers -------------------------------------------------------------------------------------

    def _is_docstring(self, node: Any) -> bool:
        parent = node.parent
        if parent is None or parent.type not in self.spec.docstring_containers:
            return False
        string = node
        if node.type == "expression_statement":
            named = node.named_children
            if len(named) != 1 or named[0].type != "string":
                return False
            string = named[0]
        if string.type != "string":
            return False
        if parent.type == "block" and (parent.parent is None or parent.parent.type not in _DOCSTRING_OWNERS):
            return False
        for sibling in parent.named_children:
            if sibling.type in self.spec.comment_types:
                continue
            return sibling.id == node.id
        return False

    def _push_scope(self, node: Any) -> None:
        names: set[str] = set()
        if self.spec.binders is not None:
            names = {_text(binder) for binder in self.spec.binders(node)}
        if self.spec.excluded_names is not None:
            names -= self.spec.excluded_names(node)
        self.scopes.append(_Scope(depth=len(self.scopes), names=names))

    def _identifier(self, node: Any) -> str:
        name = _text(node)
        if self.spec.keep_identifier is not None and self.spec.keep_identifier(node):
            return name
        for scope in reversed(self.scopes):
            if name in scope.names:
                return scope.alias(name)
        return name

    # -- the walk ------------------------------------------------------------------------------------

    def emit(self, node: Any, out: list[str]) -> None:  # noqa: PLR0911
        kind = node.type
        if kind in self.spec.comment_types:
            return
        if node.is_named and self.spec.docstring_containers and self._is_docstring(node):
            return
        if kind in self.spec.atomic_types and not any(
            child.type in self.spec.interpolation_types for child in node.children
        ):
            out.append(_text(node))
            return
        if self.language in {"vue", "svelte"} and kind == "raw_text":
            out.extend(self._embedded(node))
            return
        if node.child_count == 0:
            token = _text(node)
            if kind in {"text", "jsx_text"}:
                out.extend(token.split())
                return
            if not token or node.is_missing or (not node.is_named and token in self.spec.dropped_tokens):
                return
            if kind in self.spec.identifier_types:
                token = self._identifier(node)
            out.append(token)
            return
        scoped = kind in self.spec.scope_types
        if scoped:
            self._push_scope(node)
        marker = kind in self.spec.block_marker_types
        if marker:
            out.append("{")
        self._emit_children(node, out)
        if marker:
            out.append("}")
        if scoped:
            self.scopes.pop()

    def _emit_children(self, node: Any, out: list[str]) -> None:
        imports: list[list[str]] = []
        anchor: int | None = None
        for child in node.children:
            if child.type in self.spec.import_types:
                emitted: list[str] = []
                self.emit(child, emitted)
                imports.append(emitted)
                if anchor is None:
                    anchor = len(out)
                continue
            self.emit(child, out)
        if imports and anchor is not None:
            ordered = [token for group in sorted(imports, key="\x00".join) for token in group]
            out[anchor:anchor] = ordered

    def _embedded(self, node: Any) -> list[str]:
        """``<script>`` contents of a Vue/Svelte file through the JS/TS normalizer (else text tokens)."""
        parent = node.parent
        text = _text(node)
        if parent is None or parent.type != "script_element":
            return text.split()
        lang = "javascript"
        start = parent.child(0)
        if start is not None:
            for attribute in start.named_children:
                if attribute.type != "attribute":
                    continue
                parts = [_text(child) for child in attribute.named_children]
                if parts and parts[0] == "lang" and any("ts" in part for part in parts[1:]):
                    lang = "typescript"
        try:
            return list(tokens(text, lang))
        except NormalizationError:
            return text.split()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def tokens(code: str, language: str) -> tuple[str, ...]:
    """The canonical token stream (raises :class:`NormalizationError`)."""
    if code is None or not code.strip():
        reason = "empty"
        raise NormalizationError(reason)
    lang = canonical_language(language)
    tree = parse(identity.normalize_text(code), lang)
    root = tree.root_node
    if root.has_error:
        first = _first_error(root)
        where = f"line {first.start_point[0] + 1}" if first is not None else ""
        reason = "parse_error"
        raise NormalizationError(reason, where)
    spec = _SPECS.get(lang, _GENERIC)
    return tuple(_Emitter(spec, lang).run(root))


def _first_error(root: Any) -> Any | None:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            return node
        if node.has_error:
            stack.extend(reversed(node.children))
    return None


def has_parse_error(code: str, language: str) -> bool:
    """Whether tree-sitter reports ``ERROR``/``MISSING`` nodes (an unknown language counts as an error)."""
    try:
        return bool(parse(identity.normalize_text(code), language).root_node.has_error)
    except NormalizationError:
        return True


def serialize(token_stream: tuple[str, ...] | list[str]) -> str:
    """The canonical text of a token stream (compact JSON array: unambiguous for tokens with spaces)."""
    return json.dumps(list(token_stream), ensure_ascii=False, separators=(",", ":"))


def _check_version(normalizer_version: int) -> None:
    if normalizer_version not in SUPPORTED_VERSIONS:
        msg = f"unknown normalizer version {normalizer_version}; supported: {sorted(SUPPORTED_VERSIONS)}"
        raise ValueError(msg)


def normalize(code: str, language: str, normalizer_version: int = NORMALIZER_VERSION) -> str:
    """Renamed locals, comments, whitespace and import order do not change the output."""
    _check_version(normalizer_version)
    return serialize(tokens(code, language))


def normalized_sha(code: str, language: str, normalizer_version: int = NORMALIZER_VERSION) -> NormalizeResult:
    """``text_sha(normalize(...))``, or ``sha=""`` with the reason when the code cannot be normalized."""
    _check_version(normalizer_version)
    try:
        stream = tokens(code, language)
    except NormalizationError as exc:
        return NormalizeResult(sha="", tokens=(), reason=exc.reason)
    return NormalizeResult(sha=identity.text_sha(serialize(stream)), tokens=stream)


def extension_for(language: str) -> str:
    return EXTENSIONS.get(canonical_language(language), "txt")
