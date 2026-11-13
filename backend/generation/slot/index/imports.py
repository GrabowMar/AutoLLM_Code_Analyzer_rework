"""Import graph of a tree (python, js/ts incl. Vue/Svelte script blocks, go, java), resolved to tree paths.

Used by the route extractors (blueprint/router registration across files), the context resolver
(``related_files``: imports depth, import-graph distance) and the leak scan. Resolution is purely
textual and deterministic: an import that does not resolve to a file of the tree (a third-party package,
the standard library) is kept in ``external`` and never guessed.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
from typing import Final

from backend.generation.slot.index.languages import language_for

__all__ = [
    "ImportGraph",
    "Imported",
    "build_graph",
    "file_imports",
    "graph_distances",
]

_PY_FROM_RE: Final = re.compile(r"^from\s+(\.*[\w.]*)\s+import\s+(.+)$")
_PY_IMPORT_RE: Final = re.compile(r"^import\s+([\w., \t]+)$")
_JS_IMPORT_RE: Final = re.compile(
    r"""(?:\bimport\s+(?:([\w$*{}\s,]+?)\s+from\s+)?|\bexport\s+[\w$*{}\s,]*?\s*from\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)["']([^"'\n]+)["']""",
)
_JS_REQUIRE_RE: Final = re.compile(
    r"""\b(?:const|let|var)\s+([\w$]+|\{[^}]*\})\s*=\s*require\s*\(\s*["']([^"'\n]+)["']\s*\)""",
)
_GO_IMPORT_BLOCK_RE: Final = re.compile(r"^import\s*\((.*?)\)", re.S | re.M)
_GO_IMPORT_RE: Final = re.compile(r"^import\s+(?:\w+\s+)?\"([^\"]+)\"", re.M)
_GO_SPEC_RE: Final = re.compile(r"(?:\w+\s+)?\"([^\"]+)\"")
_GO_MODULE_RE: Final = re.compile(r"^module\s+(\S+)", re.M)
_JAVA_IMPORT_RE: Final = re.compile(r"^import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;", re.M)
_JS_EXTENSIONS: Final = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")


@dataclass(frozen=True)
class Imported:
    """One import statement: the module text, the names bound, and the tree path it resolves to (or None)."""

    module: str
    names: tuple[str, ...]
    path: str | None
    alias: str | None = None  # ``import x as y`` / default import name


@dataclass
class ImportGraph:
    edges: dict[str, list[str]] = field(default_factory=dict)  # path -> resolved paths (deduplicated, sorted)
    imports: dict[str, list[Imported]] = field(default_factory=dict)
    external: dict[str, list[str]] = field(default_factory=dict)  # path -> unresolved module names

    def neighbours(self, path: str) -> list[str]:
        return self.edges.get(path, [])


# ---------------------------------------------------------------------------


def _python_candidates(module: str, importer: str) -> list[str]:
    dots = len(module) - len(module.lstrip("."))
    name = module[dots:]
    parts = [part for part in name.split(".") if part]
    if dots:
        base = PurePosixPath(importer).parent
        for _ in range(dots - 1):
            base = base.parent
        prefixes = [base.as_posix()]
    else:
        # Absolute imports may be rooted at the tree root or at the importer's top-level directory.
        prefixes = ["."]
        top = PurePosixPath(importer).parts
        for depth in range(1, len(top)):
            prefixes.append(PurePosixPath(*top[:depth]).as_posix())
    candidates: list[str] = []
    for prefix in prefixes:
        stem = PurePosixPath(prefix, *parts).as_posix() if parts else prefix
        stem = stem.removeprefix("./")
        if stem and stem != ".":
            candidates += [f"{stem}.py", f"{stem}/__init__.py"]
    return candidates


def _resolve_first(candidates: Iterable[str], paths: frozenset[str]) -> str | None:
    for candidate in candidates:
        normalized = PurePosixPath(candidate).as_posix().removeprefix("./")
        if normalized in paths:
            return normalized
    return None


def _python_logical_lines(text: str) -> Iterable[str]:
    """Import statements as single logical lines (parenthesised and backslash-continued lists joined)."""
    pending = ""
    depth = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if pending:
            pending += " " + line.strip()
        elif line.lstrip().startswith(("import ", "from ")):
            pending = line.strip()
        else:
            continue
        depth = pending.count("(") - pending.count(")")
        if depth > 0 or pending.endswith("\\"):
            pending = pending.removesuffix("\\")
            continue
        yield pending.replace("(", " ").replace(")", " ")
        pending = ""


def _split_names(raw: str) -> list[tuple[str, str]]:
    """``a, b as c`` -> ``[("a", "a"), ("b", "c")]`` (original name, bound name)."""
    pairs: list[tuple[str, str]] = []
    for part in raw.split(","):
        original, _, alias = part.strip().partition(" as ")
        original = original.strip()
        if original and original != "*":
            pairs.append((original, alias.strip() or original))
    return pairs


def _python_imports(path: str, text: str, paths: frozenset[str]) -> list[Imported]:
    found: list[Imported] = []
    for line in _python_logical_lines(text):
        from_match = _PY_FROM_RE.match(line)
        if from_match:
            module = from_match.group(1)
            pairs = _split_names(from_match.group(2))
            resolved = _resolve_first(_python_candidates(module, path), paths)
            found.append(Imported(module=module, names=tuple(bound for _, bound in pairs), path=resolved))
            # ``from pkg import module``: an imported name may be a submodule.
            for original, bound in pairs:
                sub = f"{module}{original}" if module.endswith(".") else f"{module}.{original}"
                sub_path = _resolve_first(_python_candidates(sub, path), paths)
                if sub_path and sub_path != resolved:
                    found.append(Imported(module=sub, names=(bound,), path=sub_path, alias=bound))
            continue
        import_match = _PY_IMPORT_RE.match(line)
        if import_match:
            for original, bound in _split_names(import_match.group(1)):
                resolved = _resolve_first(_python_candidates(original, path), paths)
                found.append(
                    Imported(
                        module=original,
                        names=(bound,),
                        path=resolved,
                        alias=bound if bound != original else None,
                    ),
                )
    return found


def _js_candidates(module: str, importer: str) -> list[str]:
    if module.startswith(("./", "../")):
        base = PurePosixPath(importer).parent / module
    elif module.startswith(("@/", "~/")):
        top = PurePosixPath(importer).parts
        root = PurePosixPath(top[0], "src") if len(top) > 1 and top[0] != "src" else PurePosixPath("src")
        base = root / module[2:]
    else:
        return []
    parts: list[str] = []
    for part in base.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    stem = "/".join(parts)
    candidates = [stem]
    candidates += [f"{stem}{ext}" for ext in _JS_EXTENSIONS]
    candidates += [f"{stem}/index{ext}" for ext in _JS_EXTENSIONS]
    return candidates


def _js_names(bound: str) -> tuple[str, ...]:
    """``a, { b as c }`` / ``{ a: b }`` -> the bound local names."""
    cleaned = bound.replace("{", ",").replace("}", ",")
    names = []
    for part in cleaned.split(","):
        part = part.strip()  # noqa: PLW2901
        if not part or part == "*":
            continue
        names.append(part.split(" as ")[-1].split(":")[-1].strip())
    return tuple(name for name in names if name)


def _js_imports(path: str, text: str, paths: frozenset[str]) -> list[Imported]:
    found: list[Imported] = []
    bound_requires: set[int] = set()
    for match in _JS_REQUIRE_RE.finditer(text):
        names = _js_names(match.group(1))
        module = match.group(2)
        bound_requires.add(match.start(2))
        resolved = _resolve_first(_js_candidates(module, path), paths)
        found.append(Imported(module=module, names=names, path=resolved, alias=names[0] if names else None))
    for match in _JS_IMPORT_RE.finditer(text):
        if match.start(2) in bound_requires:
            continue
        names = _js_names(match.group(1) or "")
        module = match.group(2)
        resolved = _resolve_first(_js_candidates(module, path), paths)
        found.append(Imported(module=module, names=names, path=resolved, alias=names[0] if names else None))
    return found


def _go_imports(path: str, text: str, paths: frozenset[str], module_root: str) -> list[Imported]:
    specs: list[str] = list(_GO_IMPORT_RE.findall(text))
    for block in _GO_IMPORT_BLOCK_RE.findall(text):
        specs += _GO_SPEC_RE.findall(block)
    found: list[Imported] = []
    directories = sorted({PurePosixPath(p).parent.as_posix() for p in paths if p.endswith(".go")})
    for spec in specs:
        relative = spec[len(module_root) + 1 :] if module_root and spec.startswith(module_root + "/") else spec
        target_dir = next((d for d in directories if d == relative or d.endswith("/" + relative)), None)
        resolved = None
        if target_dir is not None:
            resolved = next(
                (p for p in sorted(paths) if p.endswith(".go") and PurePosixPath(p).parent.as_posix() == target_dir),
                None,
            )
        found.append(Imported(module=spec, names=(spec.rsplit("/", 1)[-1],), path=resolved))
    del path
    return found


def _java_imports(text: str, paths: frozenset[str]) -> list[Imported]:
    found: list[Imported] = []
    for module in _JAVA_IMPORT_RE.findall(text):
        suffix = module.replace(".", "/") + ".java"
        resolved = next((p for p in sorted(paths) if p == suffix or p.endswith("/" + suffix)), None)
        found.append(Imported(module=module, names=(module.rsplit(".", 1)[-1],), path=resolved))
    return found


def file_imports(path: str, text: str, paths: frozenset[str], *, go_module: str = "") -> list[Imported]:
    """The imports of one file, resolved against ``paths`` (the tree)."""
    language = language_for(path)
    if language == "python":
        return _python_imports(path, text, paths)
    if language in {"javascript", "typescript", "tsx", "vue", "svelte"}:
        return _js_imports(path, text, paths)
    if language == "go":
        return _go_imports(path, text, paths, go_module)
    if language == "java":
        return _java_imports(text, paths)
    return []


def build_graph(texts: Mapping[str, str]) -> ImportGraph:
    """The import graph of a tree (``{path: text}`` of its source files)."""
    paths = frozenset(texts)
    go_mod = next((texts[p] for p in sorted(paths) if PurePosixPath(p).name == "go.mod"), "")
    module_match = _GO_MODULE_RE.search(go_mod)
    go_module = module_match.group(1) if module_match else ""
    graph = ImportGraph()
    for path in sorted(paths):
        imports = file_imports(path, texts[path], paths, go_module=go_module)
        if not imports:
            continue
        graph.imports[path] = imports
        resolved = sorted({item.path for item in imports if item.path and item.path != path})
        if resolved:
            graph.edges[path] = resolved
        external = sorted({item.module for item in imports if item.path is None})
        if external:
            graph.external[path] = external
    return graph


def graph_distances(graph: ImportGraph, start: str, *, undirected: bool = True) -> dict[str, int]:
    """BFS distance from ``start`` over the import edges (both directions by default)."""
    adjacency: dict[str, set[str]] = {}
    for source, targets in graph.edges.items():
        for target in targets:
            adjacency.setdefault(source, set()).add(target)
            if undirected:
                adjacency.setdefault(target, set()).add(source)
    distances = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbour in sorted(adjacency.get(current, ())):
            if neighbour not in distances:
                distances[neighbour] = distances[current] + 1
                queue.append(neighbour)
    return distances
