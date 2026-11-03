"""Symbol index (``INDEX_VERSION = 1``): tree-sitter symbols, route extractors and UI components (section 7.3).

* ``build(run) -> SymbolIndex``: the index of a run's collected ``app`` tree (pure: nothing is stored);
* ``build_from_files(files, spec_endpoints=...) -> IndexBuild``: the same from ``{path: bytes}`` (fixtures,
  tests, the holed tree), keeping the parse trees for in-process callers;
* ``ensure_index(run) -> RunSymbolIndex``: the stored index row (blob + stats), built once per
  ``(run, INDEX_VERSION)``;
* ``load(row) -> SymbolIndex``: read a stored index back.

The index is deterministic for a given tree: files, symbols, endpoints and components are sorted, symbol ids
are ``f"{path}::{qualname}"`` (``#n`` appended to duplicates in source order).
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.contracts.slot import INDEX_VERSION
from backend.generation.contracts.slot import ComponentEntry
from backend.generation.contracts.slot import EndpointEntry
from backend.generation.contracts.slot import IndexedFile
from backend.generation.contracts.slot import SlotIndexStats
from backend.generation.contracts.slot import SymbolEntry
from backend.generation.contracts.slot import SymbolIndex
from backend.generation.slot.index import components as components_mod
from backend.generation.slot.index import routes as routes_mod
from backend.generation.slot.index.imports import ImportGraph
from backend.generation.slot.index.imports import build_graph
from backend.generation.slot.index.languages import language_for
from backend.generation.slot.index.treesitter import ParsedFile
from backend.generation.slot.index.treesitter import RawSymbol
from backend.generation.slot.index.treesitter import extract_symbols
from backend.generation.slot.index.treesitter import parse_file

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunSymbolIndex

__all__ = [
    "INDEX_VERSION",
    "IndexBuild",
    "build",
    "build_from_files",
    "ensure_index",
    "load",
    "spec_endpoints_of",
    "stats_of",
]


@dataclass
class IndexBuild:
    """An index plus the in-process artefacts it was built from (parse trees, raw symbols, import graph)."""

    index: SymbolIndex
    parsed: dict[str, ParsedFile]
    symbols: dict[str, list[RawSymbol]]
    graph: ImportGraph
    loc: dict[str, int] = field(default_factory=dict)

    def raw_symbol(self, symbol_id: str) -> RawSymbol | None:
        path = symbol_id.split("::", 1)[0]
        return next((s for s in self.symbols.get(path, ()) if s.symbol_id == symbol_id), None)


def _loc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def build_from_files(
    files: Mapping[str, bytes],
    *,
    spec_endpoints: Iterable[Mapping[str, str]] = (),
    loc: Mapping[str, int] | None = None,
) -> IndexBuild:
    """Index ``{path: bytes}`` (every path with a known language is parsed; others are listed unparsed)."""
    parsed: dict[str, ParsedFile] = {}
    symbols: dict[str, list[RawSymbol]] = {}
    indexed: list[IndexedFile] = []
    texts: dict[str, str] = {}
    locs: dict[str, int] = {}
    for path in sorted(files):
        data = files[path]
        result = parse_file(path, data)
        text = result.text
        locs[path] = loc[path] if loc is not None and path in loc else _loc(text)
        indexed.append(IndexedFile(path=path, language=result.language, loc=locs[path], parsed=result.parsed))
        if not result.language:
            continue
        parsed[path] = result
        texts[path] = text
        symbols[path] = extract_symbols(result)
    graph = build_graph(texts)
    routes, inline = routes_mod.extract(parsed, symbols, graph, spec_endpoints)
    for symbol in inline:
        symbols.setdefault(symbol.path, []).append(symbol)
    handlers = {route.handler for route in routes}
    for file_symbols in symbols.values():
        for symbol in file_symbols:
            if symbol.symbol_id in handlers and symbol.kind in {"function", "method"}:
                symbol.kind = "route_handler"
        file_symbols.sort(key=lambda s: (s.span.start_byte, -s.span.end_byte, s.kind != "module"))
    components = components_mod.extract(parsed, symbols)
    entries: list[SymbolEntry] = []
    for path in sorted(symbols):
        by_qualname = {s.qualname: s.symbol_id for s in symbols[path]}
        entries.extend(
            SymbolEntry(
                id=symbol.symbol_id,
                path=symbol.path,
                language=symbol.language,
                kind=symbol.kind,
                qualname=symbol.qualname,
                span=symbol.span,
                signature=symbol.signature,
                decorators=symbol.decorators,
                content_sha=symbol.content_sha,
                parent=by_qualname.get(symbol.parent_qualname) if symbol.parent_qualname else None,
            )
            for symbol in symbols[path]
        )
    endpoints = [
        EndpointEntry(
            method=route.method,
            path=route.path,
            framework=route.framework,
            handler=route.handler,
            spec_endpoint_id=route.spec_endpoint_id,
            confidence=route.confidence,
        )
        for route in routes
    ]
    index = SymbolIndex(
        index_version=INDEX_VERSION,
        files=indexed,
        symbols=entries,
        endpoints=endpoints,
        components=components,
    )
    return IndexBuild(index=index, parsed=parsed, symbols=symbols, graph=graph, loc=locs)


def spec_endpoints_of(run: GenerationRun) -> list[dict[str, str]]:
    """``[{id, method, path}]`` of the run's TaskSpec (empty without a task)."""
    version = run.task_version
    if version is None:
        return []
    content: dict[str, Any] = version.content or {}
    return [
        {"id": endpoint["id"], "method": endpoint["method"], "path": endpoint["path"]}
        for endpoint in content.get("endpoints", [])
    ]


def build_for_run(run: GenerationRun) -> IndexBuild:
    from backend.generation.slot.tree import parent_tree

    tree = parent_tree(run)
    files = {entry.path: tree.read(entry.path) for entry in tree if entry.is_source and language_for(entry.path)}
    return build_from_files(files, spec_endpoints=spec_endpoints_of(run))


def build(run: GenerationRun) -> SymbolIndex:
    """The symbol index of a run's collected tree."""
    return build_for_run(run).index


def stats_of(index: SymbolIndex) -> SlotIndexStats:
    unparsed = sorted(f.path for f in index.files if f.language and not f.parsed)
    return SlotIndexStats(
        files=len(index.files),
        symbols=len(index.symbols),
        endpoints=len(index.endpoints),
        components=len(index.components),
        unparsed=unparsed,
    )


def ensure_index(run: GenerationRun) -> RunSymbolIndex:
    """The stored index of ``run`` for the current ``INDEX_VERSION`` (built and stored when missing)."""
    from backend.generation import blobs
    from backend.generation.models import RunSymbolIndex

    row = RunSymbolIndex.objects.filter(run=run, index_version=INDEX_VERSION).first()
    if row is not None:
        return row
    index = build(run)
    sha = blobs.put_json(index.model_dump(mode="json"))
    row, _ = RunSymbolIndex.objects.get_or_create(
        run=run,
        index_version=INDEX_VERSION,
        defaults={"blob": sha, "stats": stats_of(index).model_dump(mode="json")},
    )
    return row


def load(row: RunSymbolIndex) -> SymbolIndex:
    from backend.generation import blobs

    return SymbolIndex.model_validate(blobs.get_json(row.blob))


def symbol_by_id(index: SymbolIndex, symbol_id: str) -> SymbolEntry | None:
    return next((symbol for symbol in index.symbols if symbol.id == symbol_id), None)


def component_by_name(index: SymbolIndex, name: str) -> ComponentEntry | None:
    return next((component for component in index.components if component.name == name), None)
