"""The discovery loop (spec section 7.9): detector drafts from a code selection, and diff vs original (step 7).

``draft_from_selection`` turns a selected line range of a run's file into a ``DetectorContent`` draft that
pre-fills the DetectorEditor (``POST /patterns/detectors/from-selection/``). Saving goes through the library
API (a new detector version); nothing here writes a library row.

- ``regex``: the selection's tokens, escaped, joined by ``\\s*`` (whitespace-insensitive, literal otherwise);
- ``ts_query``: the smallest named node covering the selection, rendered as an S-expression up to depth 3,
  with ``#eq?`` predicates pinning identifier texts and the root captured as ``@match``.

``diff_vs_original`` computes ``contracts.slot.DiffVsOriginal`` for a variant: line diff counts, the
``difflib`` ratio, an AST edit distance (Levenshtein over the pre-order named-node type sequences, an
approximation of the Zhang-Shasha tree edit distance, computed only when both trees have at most 2000
nodes) and ``normalized_equal``.
"""

from __future__ import annotations

import difflib
import re
import sys
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.contracts.library import DetectorContent
from backend.generation.patterns import normalize as norm

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

MAX_REGEX_TOKENS = 80
MAX_QUERY_DEPTH = 3
AST_NODE_LIMIT = 2000
_TOKEN_RE = re.compile(r"""[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S""")
_IDENTIFIER_TYPES = frozenset({"identifier", "property_identifier", "field_identifier", "type_identifier"})


class DraftError(ValueError):
    pass


def selection_text(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or start_line > len(lines):
        msg = f"selection {start_line}-{end_line} is outside the file (1-{len(lines)})"
        raise DraftError(msg)
    return "".join(lines[start_line - 1 : min(end_line, len(lines))])


def regex_for(selection: str) -> str:
    tokens = _TOKEN_RE.findall(selection)
    if not tokens:
        msg = "the selection has no code tokens"
        raise DraftError(msg)
    return r"\s*".join(re.escape(token) for token in tokens[:MAX_REGEX_TOKENS])


def _covering_node(root: Any, start_line: int, end_line: int) -> Any:
    """The smallest named node whose rows cover ``[start_line, end_line]`` (1-based)."""
    best = root
    stack = [root]
    while stack:
        node = stack.pop()
        if node.start_point[0] <= start_line - 1 and node.end_point[0] >= end_line - 1:
            if node.is_named and (node.end_byte - node.start_byte) <= (best.end_byte - best.start_byte):
                best = node
            stack.extend(node.children)
    # Prefer the first statement-level node fully inside the selection when the cover is the whole file.
    if best.parent is None:
        for child in root.named_children:
            if child.start_point[0] >= start_line - 1 and child.end_point[0] <= end_line - 1:
                return child
    return best


def _sexp(node: Any, depth: int, captures: list[tuple[str, str]], field: str | None = None) -> str:
    prefix = f"{field}: " if field else ""
    if node.type in _IDENTIFIER_TYPES and node.child_count == 0:
        name = f"id{len(captures)}"
        captures.append((name, node.text.decode("utf-8", errors="replace")))
        return f"{prefix}({node.type}) @{name}"
    named = [(i, c) for i, c in enumerate(node.children) if c.is_named and "comment" not in c.type]
    if depth >= MAX_QUERY_DEPTH or not named:
        return f"{prefix}({node.type})"
    parts = [_sexp(child, depth + 1, captures, node.field_name_for_child(i)) for i, child in named]
    return f"{prefix}({node.type} {' '.join(parts)})"


def ts_query_for(text: str, language: str, start_line: int, end_line: int) -> str:
    from tree_sitter import Query

    tree = norm.parse(text, language)
    node = _covering_node(tree.root_node, start_line, end_line)
    captures: list[tuple[str, str]] = []
    body = _sexp(node, 0, captures)
    predicates = " ".join(
        f'(#eq? @{name} "{value}")' for name, value in captures if '"' not in value and "\\" not in value
    )
    query = f"({body} {predicates}) @match" if predicates else f"{body} @match"
    try:
        Query(norm.language_for(norm.canonical_language(language)), query)
    except Exception:  # noqa: BLE001 - fall back to the bare node type, which always compiles
        query = f"({node.type}) @match"
    return query


def draft_from_selection(
    *,
    text: str,
    language: str,
    path: str,
    start_line: int,
    end_line: int,
    engine: str,
    feature_key: str,
    polarity: str,
    run_id: str = "",
) -> dict[str, Any]:
    """A validated ``DetectorContent`` draft (JSON dict) from the selection."""
    language = norm.canonical_language(language)
    selection = selection_text(text, start_line, end_line)
    if engine == "regex":
        rule = regex_for(selection)
        tool = {"name": "re", "version": f"py{sys.version_info.major}.{sys.version_info.minor}"}
    elif engine == "ts_query":
        import tree_sitter

        rule = ts_query_for(text, language, start_line, end_line)
        tool = {"name": "tree-sitter", "version": str(getattr(tree_sitter, "__version__", "0"))}
    else:
        msg = "engine must be regex or ts_query"
        raise DraftError(msg)
    where = f"{path}:{start_line}-{end_line}" + (f" of run {run_id[:8]}" if run_id else "")
    content = {
        "engine": engine,
        "languages": [language],
        "rule": rule,
        "tool": tool,
        "feature_key": feature_key,
        "polarity": polarity,
        "description": f"Drafted from {where}. Review the rule before saving.",
        "scopes": ["span", "file"],
    }
    return DetectorContent.model_validate(content).model_dump(mode="json", by_alias=True)


def draft_for_run(run: GenerationRun, *, path: str, start_line: int, end_line: int, **kwargs: Any) -> dict[str, Any]:
    from backend.generation.runs.view import run_view

    files = run_view(run).code_files("app")
    if path not in files:
        msg = f"{path} is not a text file of run {run.pk}"
        raise DraftError(msg)
    language = kwargs.pop("language", None) or _language_of(run, path)
    return draft_from_selection(
        text=files[path],
        language=language,
        path=path,
        start_line=start_line,
        end_line=end_line,
        run_id=str(run.pk),
        **kwargs,
    )


def _language_of(run: GenerationRun, path: str) -> str:
    from backend.generation.models import RunFile

    row = RunFile.objects.filter(run=run, path=path).exclude(language="").values_list("language", flat=True).first()
    if row is None and run.parent_run_id:
        row = (
            RunFile.objects.filter(run_id=run.parent_run_id, path=path)
            .exclude(language="")
            .values_list("language", flat=True)
            .first()
        )
    if row:
        return str(row)
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    for lang, ext in norm.EXTENSIONS.items():
        if ext == suffix:
            return lang
    return norm.canonical_language(suffix)


# ---------------------------------------------------------------------------
# Diff vs original (step 7)
# ---------------------------------------------------------------------------


def _node_types(code: str, language: str) -> list[str] | None:
    try:
        tree = norm.parse(code, language)
    except norm.NormalizationError:
        return None
    out: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.is_named and "comment" not in node.type:
            out.append(node.type)
            if len(out) > AST_NODE_LIMIT:
                return None
        stack.extend(reversed(node.children))
    return out


def _levenshtein(a: list[str], b: list[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def diff_vs_original(original: str, replacement: str, language: str) -> dict[str, Any]:
    """``{lines_added, lines_removed, similarity, ast_edit_distance, normalized_equal}``."""
    old, new = original.splitlines(), replacement.splitlines()
    added = removed = 0
    for line in difflib.ndiff(old, new):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    similarity = round(difflib.SequenceMatcher(a=original, b=replacement, autojunk=False).ratio(), 6)
    types_a, types_b = _node_types(original, language), _node_types(replacement, language)
    distance = _levenshtein(types_a, types_b) if types_a is not None and types_b is not None else None
    sha_a, sha_b = norm.normalized_sha(original, language), norm.normalized_sha(replacement, language)
    equal = (sha_a.sha == sha_b.sha) if sha_a.ok and sha_b.ok else None
    return {
        "lines_added": added,
        "lines_removed": removed,
        "similarity": similarity,
        "ast_edit_distance": distance,
        "normalized_equal": equal,
    }
