"""Endpoint to handler resolution per framework (Flask, FastAPI, Django, Express, Go, Spring; spec section 7.3).

Each extractor walks the parse trees of one framework's files and yields :class:`Route` records; ``extract``
runs all of them, resolves prefixes across files (Flask ``register_blueprint``, FastAPI ``include_router``,
Django ``include``, Express ``app.use``, gin ``Group``, Spring class-level ``@RequestMapping``) and matches
every route to the TaskSpec endpoint with the same ``(method, normalized path)``.

Confidence:

* ``exact``: literal path, every prefix resolved through the import graph (or none needed);
* ``pattern``: literal path, but a prefix or the receiver was resolved by name only, or the method comes from a
  pattern string (Go 1.22 ``"GET /x"``);
* ``heuristic``: something was guessed (an unregistered blueprint, Django views without method decorators,
  DRF ViewSet actions, unresolved handlers). The UI marks these.

Inline handlers (an arrow function passed to ``app.get``, a Go func literal) become ``route_handler`` symbols
named ``"<METHOD> <path>"``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Final

from backend.generation import identity
from backend.generation.slot.index.treesitter import RawSymbol
from backend.generation.slot.index.treesitter import node_text
from backend.generation.slot.index.treesitter import span_for
from backend.generation.slot.index.treesitter import walk

if TYPE_CHECKING:
    import tree_sitter

    from backend.generation.slot.index.imports import ImportGraph
    from backend.generation.slot.index.treesitter import ParsedFile
    from backend.generation.slot.index.treesitter import ParseUnit

__all__ = ["ANY_METHOD", "HTTP_METHODS", "Route", "extract", "match_spec_endpoints", "normalize_path", "route_key"]

HTTP_METHODS: Final = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
ANY_METHOD: Final = "ANY"
_LOWER_METHODS: Final = frozenset(m.lower() for m in HTTP_METHODS)
_FLASK_CONVERTER_RE: Final = re.compile(r"<(?:[a-zA-Z_]+(?:\([^)]*\))?:)?([a-zA-Z_][a-zA-Z0-9_]*)>")
_EXPRESS_PARAM_RE: Final = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)\??")
_REGEX_GROUP_RE: Final = re.compile(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>[^)]*\)")
_GO_WILDCARD_RE: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:\.\.\.)?\}")
_ANY_PARAM_RE: Final = re.compile(r"\{[^}]*\}")
_CONFIDENCE_ORDER: Final = {"exact": 0, "pattern": 1, "heuristic": 2}
_DRF_ACTIONS: Final = (
    ("list", "GET", False),
    ("create", "POST", False),
    ("retrieve", "GET", True),
    ("update", "PUT", True),
    ("partial_update", "PATCH", True),
    ("destroy", "DELETE", True),
)


@dataclass
class Route:
    method: str
    path: str
    framework: str
    handler: str  # symbol id
    confidence: str
    spec_endpoint_id: str | None = None


@dataclass
class _Ctx:
    files: Mapping[str, ParsedFile]
    symbols: dict[str, list[RawSymbol]]
    graph: ImportGraph
    extra_symbols: list[RawSymbol] = field(default_factory=list)

    def symbol_at(self, path: str, unit: ParseUnit, node: tree_sitter.Node) -> RawSymbol | None:
        """The symbol whose definition node is ``node``."""
        start, end = unit.abs_byte(node.start_byte), unit.abs_byte(node.end_byte)
        for symbol in self.symbols.get(path, ()):
            if symbol.node is not None and symbol.unit is not None:
                if (symbol.unit.abs_byte(symbol.node.start_byte), symbol.unit.abs_byte(symbol.node.end_byte)) == (
                    start,
                    end,
                ):
                    return symbol
        return None

    def symbol_named(self, path: str, name: str, *, kinds: Iterable[str] | None = None) -> RawSymbol | None:
        wanted = set(kinds) if kinds is not None else None
        for symbol in self.symbols.get(path, ()):
            if name in {symbol.qualname, symbol.name} and (wanted is None or symbol.kind in wanted):
                if symbol.qualname == name or symbol.parent_qualname is None:
                    return symbol
        for symbol in self.symbols.get(path, ()):
            if symbol.name == name and (wanted is None or symbol.kind in wanted):
                return symbol
        return None

    def module_id(self, path: str) -> str:
        return f"{path}::<module>"

    def enclosing(self, path: str, offset: int) -> str:
        """The innermost non-module symbol containing ``offset`` (else the module)."""
        best: RawSymbol | None = None
        for symbol in self.symbols.get(path, ()):
            if symbol.kind == "module":
                continue
            if symbol.span.start_byte <= offset < symbol.span.end_byte:
                if best is None or symbol.span.start_byte >= best.span.start_byte:
                    best = symbol
        return best.symbol_id if best is not None else self.module_id(path)

    def imported(self, path: str, name: str) -> tuple[str | None, str | None]:
        """``(path, original module)`` of a name bound by an import in ``path`` (a module alias or a name)."""
        imports = self.graph.imports.get(path, ())
        # A submodule bound by ``from pkg import module`` wins over the package that exports the name.
        for item in imports:
            if item.alias == name and item.path is not None:
                return item.path, item.module
        for item in imports:
            if name in item.names or item.alias == name:
                return item.path, item.module
        return None, None

    def inline_symbol(self, path: str, unit: ParseUnit, node: tree_sitter.Node, label: str) -> str:
        """Register an inline handler (an arrow function or func literal) as a ``route_handler`` symbol."""
        parsed = self.files[path]
        span = span_for(parsed.source, unit.abs_byte(node.start_byte), unit.abs_byte(node.end_byte))
        content = parsed.source[span.start_byte : span.end_byte].decode("utf-8", errors="replace")
        existing = {s.symbol_id for s in self.symbols.get(path, ())} | {s.symbol_id for s in self.extra_symbols}
        base = f"{path}::{label}"
        symbol_id, n = base, 1
        while symbol_id in existing:
            n += 1
            symbol_id = f"{base}#{n}"
        body = node.child_by_field_name("body")
        header = unit.source[node.start_byte : body.start_byte if body is not None else node.end_byte]
        symbol = RawSymbol(
            path=path,
            language=parsed.language,
            kind="route_handler",
            name=label,
            qualname=label,
            span=span,
            signature=" ".join(header.decode("utf-8", "replace").split()).removesuffix("=>").strip(),
            decorators=[],
            content_sha=identity.text_sha(content),
            parent_qualname=None,
            unit=unit,
            node=node,
            symbol_id=symbol_id,
        )
        self.extra_symbols.append(symbol)
        return symbol_id


# ---------------------------------------------------------------------------
# Literal helpers
# ---------------------------------------------------------------------------


def _string(node: tree_sitter.Node | None, source: bytes) -> str | None:  # noqa: PLR0911
    """The value of a literal string node (python, js/ts, go, java); ``None`` for anything dynamic."""
    if node is None:
        return None
    if node.type == "string":  # python and js
        if any(child.type in {"interpolation", "template_substitution"} for child in node.named_children):
            return None
        parts = [node_text(c, source) for c in node.named_children if c.type in {"string_content", "string_fragment"}]
        if not node.named_children:
            return ""
        if any(c.type == "escape_sequence" for c in node.named_children):
            return None
        return "".join(parts)
    if node.type == "template_string":
        if any(child.type == "template_substitution" for child in node.named_children):
            return None
        return node_text(node, source).strip("`")
    if node.type in {"interpreted_string_literal", "raw_string_literal"}:  # go
        return node_text(node, source)[1:-1]
    if node.type == "string_literal":  # java
        return node_text(node, source)[1:-1]
    if node.type == "concatenated_string":
        values = [_string(child, source) for child in node.named_children]
        return None if any(v is None for v in values) else "".join(v or "" for v in values)
    return None


def _strings(node: tree_sitter.Node | None, source: bytes) -> list[str] | None:
    """A literal list/tuple/set/array of strings (or one string)."""
    if node is None:
        return None
    single = _string(node, source)
    if single is not None:
        return [single]
    if node.type in {"list", "tuple", "set", "array", "element_value_array_initializer"}:
        values = [_string(child, source) for child in node.named_children if child.type != "comment"]
        if any(value is None for value in values):
            return None
        return [value for value in values if value is not None]
    return None


def _join(*parts: str) -> str:
    path = "/".join(part.strip("/") for part in parts if part and part.strip("/"))
    return "/" + path if path else "/"


def normalize_path(path: str) -> str:
    """Spec-style display path: converters and ``:params`` become ``{name}``; no trailing slash."""
    value = _FLASK_CONVERTER_RE.sub(lambda m: "{" + m.group(1) + "}", path)
    value = _REGEX_GROUP_RE.sub(lambda m: "{" + m.group(1) + "}", value)
    value = _EXPRESS_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", value)
    value = _GO_WILDCARD_RE.sub(lambda m: "{" + m.group(1) + "}", value)
    value = value.replace("^", "").replace("$", "")
    value = re.sub(r"/{2,}", "/", value)
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1:
        value = value.rstrip("/")
    return value


def route_key(path: str) -> str:
    """The parameter-agnostic key two routes match on (``/api/todos/{}``, lower case)."""
    return _ANY_PARAM_RE.sub("{}", normalize_path(path)).lower()


def _call_parts(
    node: tree_sitter.Node,
) -> tuple[tree_sitter.Node | None, list[tree_sitter.Node], dict[str, tree_sitter.Node]]:
    """``(function, positional args, keyword args)`` of a python or js call node."""
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    positional: list[tree_sitter.Node] = []
    keywords: dict[str, tree_sitter.Node] = {}
    for child in arguments.named_children if arguments is not None else ():
        if child.type == "comment":
            continue
        if child.type == "keyword_argument":
            name, value = child.child_by_field_name("name"), child.child_by_field_name("value")
            if name is not None and value is not None:
                keywords[name.text.decode() if name.text else ""] = value
        else:
            positional.append(child)
    return function, positional, keywords


def _member(function: tree_sitter.Node | None, source: bytes) -> tuple[str, str] | None:
    """``obj.attr`` (python attribute, js member expression, go selector) -> ``(obj text, attr)``."""
    if function is None:
        return None
    if function.type == "attribute":  # python
        obj, attr = function.child_by_field_name("object"), function.child_by_field_name("attribute")
    elif function.type == "member_expression":  # js
        obj, attr = function.child_by_field_name("object"), function.child_by_field_name("property")
    elif function.type == "selector_expression":  # go
        obj, attr = function.child_by_field_name("operand"), function.child_by_field_name("field")
    else:
        return None
    if obj is None or attr is None:
        return None
    return node_text(obj, source), node_text(attr, source)


def _callee_name(function: tree_sitter.Node | None, source: bytes) -> str:
    if function is None:
        return ""
    return node_text(function, source).rsplit(".", 1)[-1]


# -- Python: Flask and FastAPI ------------------------------------------------------


@dataclass
class _PyObject:
    kind: str  # app | blueprint | router
    prefix: str


def _py_framework(text: str) -> str:
    if re.search(r"^\s*(?:from|import)\s+fastapi\b", text, re.M):
        return "fastapi"
    if re.search(r"^\s*(?:from|import)\s+flask\b", text, re.M):
        return "flask"
    return ""


def _py_objects(parsed: ParsedFile, unit: ParseUnit) -> dict[str, _PyObject]:
    """Module-level ``X = Flask(...) | Blueprint(...) | FastAPI(...) | APIRouter(...)`` (searched anywhere)."""
    objects: dict[str, _PyObject] = {}
    for node in walk(unit.tree.root_node):
        if node.type != "assignment":
            continue
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is None or right is None or left.type != "identifier" or right.type != "call":
            continue
        function, _, keywords = _call_parts(right)
        callee = _callee_name(function, unit.source)
        name = node_text(left, unit.source)
        if callee in {"Flask", "FastAPI"}:
            objects[name] = _PyObject("app", _string(keywords.get("root_path"), unit.source) or "")
        elif callee == "Blueprint":
            objects[name] = _PyObject("blueprint", _string(keywords.get("url_prefix"), unit.source) or "")
        elif callee == "APIRouter":
            objects[name] = _PyObject("router", _string(keywords.get("prefix"), unit.source) or "")
    del parsed
    return objects


@dataclass
class _Registration:
    prefix: str
    exact: bool


def _py_registrations(ctx: _Ctx) -> dict[tuple[str, str], _Registration]:
    """``(defining path, variable) -> registration prefix`` from register_blueprint / include_router calls."""
    found: dict[tuple[str, str], _Registration] = {}
    for path, parsed in ctx.files.items():
        if parsed.language != "python":
            continue
        for unit in parsed.units:
            for node in walk(unit.tree.root_node):
                if node.type != "call":
                    continue
                function, positional, keywords = _call_parts(node)
                member = _member(function, unit.source)
                if member is None or member[1] not in {"register_blueprint", "include_router"} or not positional:
                    continue
                prefix_kw = "url_prefix" if member[1] == "register_blueprint" else "prefix"
                prefix = _string(keywords.get(prefix_kw), unit.source)
                if prefix is None and prefix_kw in keywords:
                    continue  # a dynamic prefix: leave the route heuristic
                target = node_text(positional[0], unit.source)
                module_name, _, variable = target.rpartition(".")
                if module_name:  # include_router(todos.router)
                    defining, _ = ctx.imported(path, module_name)
                    exact = defining is not None
                else:
                    variable = target
                    defining, _ = ctx.imported(path, variable)
                    exact = defining is not None
                    if defining is None:
                        defining = path  # defined in the registering file (or unresolved: checked below)
                found[(defining, variable)] = _Registration(prefix=prefix or "", exact=exact or defining == path)
    return found


def _python_routes(ctx: _Ctx) -> list[Route]:
    routes: list[Route] = []
    registrations = _py_registrations(ctx)
    for path, parsed in sorted(ctx.files.items()):
        if parsed.language != "python" or not parsed.units:
            continue
        unit = parsed.units[0]
        framework = _py_framework(parsed.text)
        objects = _py_objects(parsed, unit)
        for node in walk(unit.tree.root_node):
            if node.type != "decorated_definition":
                continue
            definition = node.child_by_field_name("definition")
            if definition is None or definition.type != "function_definition":
                continue
            symbol = ctx.symbol_at(path, unit, definition)
            if symbol is None:
                continue
            for decorator in (d for d in node.named_children if d.type == "decorator"):
                call = next((c for c in decorator.named_children if c.type == "call"), None)
                if call is None:
                    continue
                function, positional, keywords = _call_parts(call)
                member = _member(function, unit.source)
                if member is None:
                    continue
                receiver, attr = member
                if attr in {"route", "api_route"}:
                    methods = _strings(keywords.get("methods"), unit.source) or ["GET"]
                elif attr in _LOWER_METHODS:
                    methods = [attr]
                else:
                    continue
                route_path = _string(
                    positional[0] if positional else keywords.get("path") or keywords.get("rule"),
                    unit.source,
                )
                if route_path is None:
                    continue
                prefix, confidence = _py_prefix(path, receiver, objects, registrations)
                fw = framework or ("flask" if attr == "route" else "fastapi")
                if not framework:
                    confidence = "heuristic"
                for method in methods:
                    routes.append(
                        Route(
                            method=method.upper(),
                            path=normalize_path(_join(prefix, route_path)),
                            framework=fw,
                            handler=symbol.symbol_id,
                            confidence=confidence,
                        ),
                    )
    return routes


def _py_prefix(
    path: str,
    receiver: str,
    objects: Mapping[str, _PyObject],
    registrations: Mapping[tuple[str, str], _Registration],
) -> tuple[str, str]:
    obj = objects.get(receiver)
    registration = registrations.get((path, receiver))
    if obj is None:
        # The receiver is imported (e.g. ``from app import app``): no prefix known.
        return "", "pattern"
    if obj.kind == "app":
        return obj.prefix, "exact"
    if registration is None:
        return obj.prefix, "heuristic"
    confidence = "exact" if registration.exact else "pattern"
    if obj.kind == "blueprint":
        # Flask: the url_prefix given at registration replaces the blueprint's own.
        return (registration.prefix or obj.prefix), confidence
    return _join(registration.prefix, obj.prefix) if (registration.prefix or obj.prefix) else "", confidence


# -- Django: urls.py and DRF ---------------------------------------------------------


def _django_routes(ctx: _Ctx) -> list[Route]:
    url_files = {p: f for p, f in ctx.files.items() if f.language == "python" and PurePosixPath(p).name == "urls.py"}
    if not url_files:
        return []
    included: dict[str, tuple[str, str]] = {}  # urls path -> (including path, route prefix)
    patterns: dict[str, list[tuple[str, tree_sitter.Node, ParseUnit, bool]]] = {}
    routers: dict[str, list[tuple[str, str]]] = {}  # urls path -> [(prefix, viewset expr)]
    router_prefix: dict[str, str] = {}
    for path, parsed in url_files.items():
        if not parsed.units:
            continue
        unit = parsed.units[0]
        for node in walk(unit.tree.root_node):
            if node.type != "call":
                continue
            function, positional, _ = _call_parts(node)
            callee = _callee_name(function, unit.source)
            member = _member(function, unit.source)
            if member is not None and member[1] == "register" and len(positional) >= 2:
                route = _string(positional[0], unit.source)
                if route is not None:
                    routers.setdefault(path, []).append((route, node_text(positional[1], unit.source)))
                continue
            if callee not in {"path", "re_path", "url"} or len(positional) < 2:
                continue
            route = _string(positional[0], unit.source)
            if route is None:
                continue
            target = positional[1]
            if (
                target.type == "call"
                and _callee_name(target.child_by_field_name("function"), unit.source) == "include"
            ):
                _, args, _ = _call_parts(target)
                module = _string(args[0], unit.source) if args else None
                if module is not None:
                    module_path = module.replace(".", "/") + ".py"
                    resolved = next((p for p in url_files if p == module_path or p.endswith("/" + module_path)), None)
                    if resolved:
                        included[resolved] = (path, route)
                elif args and node_text(args[0], unit.source).endswith(".urls"):
                    router_prefix[path] = route
                continue
            patterns.setdefault(path, []).append((route, target, unit, callee != "path"))

    def prefix_of(path: str, depth: int = 0) -> str:
        if path not in included or depth > 8:
            return ""
        parent, route = included[path]
        return _join(prefix_of(parent, depth + 1), route)

    routes: list[Route] = []
    for path in sorted(patterns):
        for route, target, unit, is_regex in patterns[path]:
            full = normalize_path(_join(prefix_of(path), route))
            routes += _django_view_routes(ctx, path, unit, target, full, regex=is_regex)
    for path in sorted(routers):
        for route, viewset in routers[path]:
            base = _join(prefix_of(path), router_prefix.get(path, ""), route)
            routes += _drf_viewset_routes(ctx, path, viewset, normalize_path(base))
    return routes


def _resolve_python_name(ctx: _Ctx, path: str, expression: str) -> tuple[str | None, str]:
    """``views.todo_list`` / ``todo_list`` / ``TodoView`` -> ``(defining path, name)``."""
    module_name, _, name = expression.rpartition(".")
    if module_name:
        defining, _ = ctx.imported(path, module_name)
        return defining, name
    defining, _ = ctx.imported(path, name)
    return defining or path, name


def _django_methods(symbol: RawSymbol) -> list[str] | None:
    for decorator in symbol.decorators:
        text = decorator.lstrip("@")
        match = re.match(r"(?:\w+\.)*(api_view|require_http_methods)\s*\((.*)\)", text, re.S)
        if match:
            methods = re.findall(r"""["']([A-Za-z]+)["']""", match.group(2))
            if methods:
                return [m.upper() for m in methods]
        simple = re.match(r"(?:\w+\.)*require_(GET|POST|safe)\b", text)
        if simple:
            return ["GET", "HEAD"] if simple.group(1) == "safe" else [simple.group(1)]
    return None


def _django_view_routes(
    ctx: _Ctx,
    path: str,
    unit: ParseUnit,
    target: tree_sitter.Node,
    full: str,
    *,
    regex: bool,
) -> list[Route]:
    expression = node_text(target, unit.source)
    if target.type == "call" and expression.endswith(".as_view()"):
        defining, name = _resolve_python_name(ctx, path, expression.removesuffix(".as_view()"))
        cls = ctx.symbol_named(defining, name, kinds={"class"}) if defining else None
        if cls is None:
            return [Route(ANY_METHOD, full, "django", ctx.module_id(path), "heuristic")]
        routes = []
        for method in HTTP_METHODS:
            handler = ctx.symbol_named(defining or path, f"{cls.qualname}.{method.lower()}", kinds={"method"})
            if handler is not None:
                routes.append(Route(method, full, "django", handler.symbol_id, "heuristic" if regex else "exact"))
        return routes or [Route(ANY_METHOD, full, "django", cls.symbol_id, "heuristic")]
    defining, name = _resolve_python_name(ctx, path, expression)
    symbol = ctx.symbol_named(defining, name, kinds={"function"}) if defining else None
    if symbol is None:
        return [Route(ANY_METHOD, full, "django", ctx.module_id(path), "heuristic")]
    methods = _django_methods(symbol)
    if methods is None:
        return [Route(ANY_METHOD, full, "django", symbol.symbol_id, "heuristic")]
    return [Route(m, full, "django", symbol.symbol_id, "heuristic" if regex else "exact") for m in methods]


def _drf_viewset_routes(ctx: _Ctx, path: str, viewset: str, base: str) -> list[Route]:
    defining, name = _resolve_python_name(ctx, path, viewset)
    cls = ctx.symbol_named(defining, name, kinds={"class"}) if defining else None
    if cls is None:
        return []
    routes: list[Route] = []
    generic = "ModelViewSet" in cls.signature
    for action, method, detail in _DRF_ACTIONS:
        handler = ctx.symbol_named(cls.path, f"{cls.qualname}.{action}", kinds={"method"})
        if handler is None and not generic:
            continue
        route_path = _join(base, "{pk}") if detail else base
        routes.append(
            Route(method, normalize_path(route_path), "django", (handler or cls).symbol_id, "heuristic"),
        )
    return routes


# ---------------------------------------------------------------------------
# Express
# ---------------------------------------------------------------------------


def _js_objects(unit: ParseUnit) -> dict[str, str]:
    """``const app = express()`` -> app; ``const router = express.Router()`` / ``Router()`` -> router."""
    objects: dict[str, str] = {}
    for node in walk(unit.tree.root_node):
        if node.type != "variable_declarator":
            continue
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is None or value is None or value.type != "call_expression":
            continue
        callee = (
            node_text(value.child_by_field_name("function"), unit.source)
            if value.child_by_field_name(
                "function",
            )
            else ""
        )
        if callee == "express":
            objects[node_text(name, unit.source)] = "app"
        elif callee.endswith("Router"):
            objects[node_text(name, unit.source)] = "router"
    return objects


def _is_express(parsed: ParsedFile) -> bool:
    return bool(re.search(r"""(?:require\(\s*|from\s+)["']express["']""", parsed.text))


def _express_mounts(ctx: _Ctx) -> dict[str, tuple[str, bool]]:
    """``defining path -> (prefix, exact)`` from ``app.use('/prefix', router)``."""
    mounts: dict[str, tuple[str, bool]] = {}
    for path, parsed in ctx.files.items():
        if parsed.language not in {"javascript", "typescript", "tsx"} or not _is_express(parsed):
            continue
        unit = parsed.units[0]
        for node in walk(unit.tree.root_node):
            if node.type != "call_expression":
                continue
            function, positional, _ = _call_parts(node)
            member = _member(function, unit.source)
            if member is None or member[1] != "use" or len(positional) < 2:
                continue
            prefix = _string(positional[0], unit.source)
            if prefix is None:
                continue
            target = positional[-1]
            resolved: str | None = None
            if target.type == "identifier":
                resolved, _ = ctx.imported(path, node_text(target, unit.source))
            elif (
                target.type == "call_expression"
                and _callee_name(target.child_by_field_name("function"), unit.source) == "require"
            ):
                _, args, _ = _call_parts(target)
                module = _string(args[0], unit.source) if args else None
                if module is not None:
                    resolved = next(
                        (item.path for item in ctx.graph.imports.get(path, ()) if item.module == module),
                        None,
                    )
            if resolved:
                mounts[resolved] = (prefix, True)
    return mounts


def _express_routes(ctx: _Ctx) -> list[Route]:
    routes: list[Route] = []
    mounts = _express_mounts(ctx)
    for path, parsed in sorted(ctx.files.items()):
        if parsed.language not in {"javascript", "typescript", "tsx"} or not parsed.units or not _is_express(parsed):
            continue
        unit = parsed.units[0]
        objects = _js_objects(unit)
        for node in walk(unit.tree.root_node):
            if node.type != "call_expression":
                continue
            function, positional, _ = _call_parts(node)
            member = _member(function, unit.source)
            if member is None or member[1] not in _LOWER_METHODS | {"all"} or not positional:
                continue
            receiver = member[0]
            route_path = _string(positional[0], unit.source)
            obj_node = function.child_by_field_name("object") if function is not None else None
            if route_path is None and obj_node is not None and obj_node.type == "call_expression":
                # Chained form: a route() call on the router, then get/post on its result.
                inner_function, inner_args, _ = _call_parts(obj_node)
                inner = _member(inner_function, unit.source)
                if inner is None or inner[1] != "route" or not inner_args:
                    continue
                route_path = _string(inner_args[0], unit.source)
                receiver = inner[0]
                handler_node = positional[-1]
            else:
                handler_node = positional[-1] if len(positional) > 1 else None
            if route_path is None or handler_node is None or not route_path.startswith("/"):
                continue
            kind = objects.get(receiver)
            if kind is None and receiver not in {"app", "router"}:
                continue
            prefix, confidence = "", "exact" if kind is not None else "pattern"
            if kind == "router":
                mount = mounts.get(path)
                if mount is None:
                    confidence = "heuristic"
                else:
                    prefix = mount[0]
            method = ANY_METHOD if member[1] == "all" else member[1].upper()
            full = normalize_path(_join(prefix, route_path))
            handler, handler_confidence = _js_handler(ctx, path, unit, handler_node, f"{method} {full}")
            routes.append(Route(method, full, "express", handler, _worst(confidence, handler_confidence)))
    return routes


def _js_handler(ctx: _Ctx, path: str, unit: ParseUnit, node: tree_sitter.Node, label: str) -> tuple[str, str]:
    if node.type in {"arrow_function", "function_expression", "function"}:
        return ctx.inline_symbol(path, unit, node, label), "exact"
    text = node_text(node, unit.source)
    module_name, _, name = text.rpartition(".")
    if node.type == "identifier":
        local = ctx.symbol_named(path, text)
        if local is not None:
            return local.symbol_id, "exact"
        defining, _ = ctx.imported(path, text)
        target = ctx.symbol_named(defining, text) if defining else None
        if target is not None:
            return target.symbol_id, "exact"
    elif node.type == "member_expression" and module_name:
        defining, _ = ctx.imported(path, module_name.split(".")[0])
        target = ctx.symbol_named(defining, name) if defining else None
        if target is not None:
            return target.symbol_id, "pattern"
    return ctx.enclosing(path, unit.abs_byte(node.start_byte)), "heuristic"


def _worst(*confidences: str) -> str:
    return max(confidences, key=lambda c: _CONFIDENCE_ORDER[c])


# ---------------------------------------------------------------------------
# Go: net/http (incl. Go 1.22 patterns), gin, chi, echo
# ---------------------------------------------------------------------------

_GO_ROUTE_METHODS: Final = (
    {m: m for m in HTTP_METHODS} | {m.capitalize(): m for m in HTTP_METHODS} | {"Any": ANY_METHOD}
)


def _go_routes(ctx: _Ctx) -> list[Route]:
    routes: list[Route] = []
    for path, parsed in sorted(ctx.files.items()):
        if parsed.language != "go" or not parsed.units:
            continue
        unit = parsed.units[0]
        if not re.search(r'"net/http"|gin-gonic/gin|go-chi/chi|labstack/echo|gorilla/mux', parsed.text):
            continue
        groups: dict[str, str] = {}
        for node in walk(unit.tree.root_node):
            if node.type == "short_var_declaration":
                left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
                call = right.named_children[0] if right is not None and right.named_children else None
                if left is None or call is None or call.type != "call_expression":
                    continue
                member = _member(call.child_by_field_name("function"), unit.source)
                args = call.child_by_field_name("arguments")
                if member is not None and member[1] == "Group" and args is not None and args.named_children:
                    prefix = _string(args.named_children[0], unit.source)
                    if prefix is not None:
                        groups[node_text(left, unit.source)] = _join(groups.get(member[0], ""), prefix)
        for node in walk(unit.tree.root_node):
            if node.type != "call_expression":
                continue
            member = _member(node.child_by_field_name("function"), unit.source)
            args = node.child_by_field_name("arguments")
            if member is None or args is None or len(args.named_children) < 2:
                continue
            receiver, attr = member
            first = _string(args.named_children[0], unit.source)
            if first is None:
                continue
            handler_node = args.named_children[-1]
            if attr in {"HandleFunc", "Handle"}:
                method, _, rest = first.partition(" ")
                if rest and method.upper() in HTTP_METHODS:
                    method, route_path, confidence = method.upper(), rest, "pattern"
                else:
                    method, route_path, confidence = ANY_METHOD, first, "exact"
                if not route_path.startswith("/"):  # host patterns: "example.com/x"
                    route_path = "/" + route_path.split("/", 1)[-1]
            elif attr in _GO_ROUTE_METHODS and receiver != "http":
                method, route_path, confidence = _GO_ROUTE_METHODS[attr], first, "exact"
            else:
                continue
            if not route_path.startswith("/"):
                continue
            full = normalize_path(_join(groups.get(receiver, ""), route_path))
            handler, handler_confidence = _go_handler(ctx, path, unit, handler_node, f"{method} {full}")
            routes.append(Route(method, full, "go", handler, _worst(confidence, handler_confidence)))
    return routes


def _go_handler(ctx: _Ctx, path: str, unit: ParseUnit, node: tree_sitter.Node, label: str) -> tuple[str, str]:
    if node.type == "func_literal":
        return ctx.inline_symbol(path, unit, node, label), "exact"
    if node.type == "call_expression":  # http.HandlerFunc(h) and friends
        args = node.child_by_field_name("arguments")
        if args is not None and args.named_children:
            node = args.named_children[0]
    text = node_text(node, unit.source)
    name = text.rsplit(".", 1)[-1]
    directory = PurePosixPath(path).parent.as_posix()
    same_package = [
        p for p in sorted(ctx.files) if p.endswith(".go") and PurePosixPath(p).parent.as_posix() == directory
    ]
    kinds = {"function"} if node.type == "identifier" else {"method", "function"}
    for candidate in [path, *same_package]:
        symbol = ctx.symbol_named(candidate, name, kinds=kinds)
        if symbol is not None:
            return symbol.symbol_id, "exact" if node.type == "identifier" else "pattern"
    return ctx.enclosing(path, unit.abs_byte(node.start_byte)), "heuristic"


# ---------------------------------------------------------------------------
# Spring
# ---------------------------------------------------------------------------

_SPRING_MAPPINGS: Final = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
    "RequestMapping": ANY_METHOD,
}


def _annotation(node: tree_sitter.Node, source: bytes) -> tuple[str, str | None, str | None]:
    """``(name, path, method)`` of a Spring mapping annotation."""
    name_node = node.child_by_field_name("name")
    name = node_text(name_node, source) if name_node is not None else ""
    arguments = node.child_by_field_name("arguments")
    path: str | None = "" if arguments is None else None
    method: str | None = None
    for argument in arguments.named_children if arguments is not None else ():
        if argument.type == "element_value_pair":
            key = argument.child_by_field_name("key")
            value = argument.child_by_field_name("value")
            key_text = node_text(key, source) if key is not None else ""
            if key_text in {"value", "path"}:
                values = _strings(value, source)
                path = values[0] if values else None
            elif key_text == "method" and value is not None:
                method = node_text(value, source).rsplit(".", 1)[-1].strip("{} ").upper()
        else:
            values = _strings(argument, source)
            if values is not None:
                path = values[0] if values else ""
    if (
        arguments is not None
        and path is None
        and not any(a.type != "element_value_pair" for a in arguments.named_children)
    ):
        path = path if path is not None else ""
    return name, path, method


def _spring_routes(ctx: _Ctx) -> list[Route]:
    routes: list[Route] = []
    for path, parsed in sorted(ctx.files.items()):
        if parsed.language != "java" or not parsed.units:
            continue
        unit = parsed.units[0]
        for symbol in ctx.symbols.get(path, ()):
            if symbol.kind != "method" or symbol.node is None:
                continue
            method_annotations = _spring_annotations(symbol.node, unit.source)
            if not method_annotations:
                continue
            cls_node = symbol.node.parent.parent if symbol.node.parent is not None else None
            prefix = ""
            if cls_node is not None and cls_node.type in {"class_declaration", "interface_declaration"}:
                for name, value, _ in _spring_annotations(cls_node, unit.source):
                    if name == "RequestMapping" and value:
                        prefix = value
            for name, value, explicit_method in method_annotations:
                if value is None:
                    continue
                method = explicit_method if name == "RequestMapping" and explicit_method else _SPRING_MAPPINGS[name]
                if method not in HTTP_METHODS and method != ANY_METHOD:
                    method = ANY_METHOD
                full = normalize_path(_join(prefix, value))
                routes.append(Route(method, full, "spring", symbol.symbol_id, "exact"))
    return routes


def _spring_annotations(node: tree_sitter.Node, source: bytes) -> list[tuple[str, str | None, str | None]]:
    found = []
    for child in node.children:
        if child.type != "modifiers":
            continue
        for annotation in child.named_children:
            if annotation.type == "marker_annotation":
                name_node = annotation.child_by_field_name("name")
                name = node_text(name_node, source) if name_node is not None else ""
                if name in _SPRING_MAPPINGS:
                    found.append((name, "", None))
            elif annotation.type == "annotation":
                name, value, method = _annotation(annotation, source)
                if name in _SPRING_MAPPINGS:
                    found.append((name, value, method))
    return found


# ---------------------------------------------------------------------------


def match_spec_endpoints(routes: list[Route], spec_endpoints: Iterable[Mapping[str, str]]) -> list[Route]:
    """Fill ``spec_endpoint_id`` by ``(method, normalized path)``; an ``ANY`` route fans out to every match."""
    by_key: dict[tuple[str, str], str] = {}
    by_path: dict[str, list[tuple[str, str]]] = {}
    for endpoint in spec_endpoints:
        key = route_key(endpoint["path"])
        by_key[(endpoint["method"].upper(), key)] = endpoint["id"]
        by_path.setdefault(key, []).append((endpoint["method"].upper(), endpoint["id"]))
    matched: list[Route] = []
    for route in routes:
        key = route_key(route.path)
        if route.method == ANY_METHOD:
            candidates = by_path.get(key, [])
            if not candidates:
                matched.append(route)
            for method, endpoint_id in sorted(candidates):
                matched.append(
                    Route(
                        method,
                        route.path,
                        route.framework,
                        route.handler,
                        "heuristic",
                        spec_endpoint_id=endpoint_id,
                    ),
                )
            continue
        route.spec_endpoint_id = by_key.get((route.method, key))
        matched.append(route)
    return matched


def extract(
    files: Mapping[str, ParsedFile],
    symbols: dict[str, list[RawSymbol]],
    graph: ImportGraph,
    spec_endpoints: Iterable[Mapping[str, str]] = (),
) -> tuple[list[Route], list[RawSymbol]]:
    """Every route of the tree (deduplicated, deterministic order) and the inline handler symbols it created."""
    ctx = _Ctx(files=files, symbols=symbols, graph=graph)
    routes: list[Route] = []
    for extractor in (_python_routes, _django_routes, _express_routes, _go_routes, _spring_routes):
        routes += extractor(ctx)
    routes = match_spec_endpoints(routes, spec_endpoints)
    unique: dict[tuple[str, str, str], Route] = {}
    for route in routes:
        key = (route.method, route.path, route.handler)
        current = unique.get(key)
        if current is None or _CONFIDENCE_ORDER[route.confidence] < _CONFIDENCE_ORDER[current.confidence]:
            unique[key] = route
    ordered = sorted(unique.values(), key=lambda r: (r.path, r.method, r.handler))
    return ordered, ctx.extra_symbols
