"""``resolve_context(target, context_policy) -> ResolvedContext`` (per plan, hashed; spec section 7.4).

The context is what a slot prompt (or a restricted agent's brief) shows besides the hole. It is resolved
deterministically from the frozen parent, so every variant of one condition sees byte-identical context and
its ``sha`` (which also commits to the policy, the hole mode and ``CONTEXT_VERSION``) enters
``treatment_hash`` as ``resolved_context_sha``.

Modes:

* ``slot_file_only``: the slot file with the hole applied;
* ``related_files`` (default): the slot file, then the files it imports (``related.imports_depth``), then
  model/schema files (path contains ``model``/``schema`` or the file defines ORM classes), then the
  routes/app entry file, then the other files connected to the slot file by the import graph, nearest first;
* ``full_app``: as ``related_files``, followed by every other source file (by path);
* ``interface_only``: the slot file, then the related files reduced to an outline (imports, decorators and
  signatures).

Parent-authored tests are left out unless ``include_tests``, and so are READMEs the leak scan flags. Files
are added in priority order until ``max_context_tokens`` is reached; the slot file always comes first and is
always included; files that do not fit are listed in ``truncated_paths`` (nothing is cut mid-file). Tokens
are estimated as ``ceil(chars / 3.6)`` (deterministic, no tokenizer dependency).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Final

from backend.generation import identity
from backend.generation.contracts.slot import ContextFile
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import ResolvedContext
from backend.generation.slot.index.imports import graph_distances
from backend.generation.slot.part import Part
from backend.generation.slot.part import part_of
from backend.generation.slot.tree import is_readme

if TYPE_CHECKING:
    from backend.generation.models import SlotTarget
    from backend.generation.slot.hole import HoleResult

__all__ = [
    "CONTEXT_VERSION",
    "ContextFileText",
    "MaterializedContext",
    "estimate_tokens",
    "materialize",
    "resolve_context",
]

CONTEXT_VERSION: Final = 1
CHARS_PER_TOKEN: Final = 3.6
MAX_CONTEXT_FILE_BYTES: Final = 256 * 1024
_ORM_RE: Final = re.compile(
    r"class\s+\w+\s*\([^)]*\b(?:db\.Model|models\.Model|Base|DeclarativeBase|SQLModel|BaseModel|Document)\b"
    r"|@Entity\b|mongoose\.Schema|new\s+Schema\s*\(|sequelize\.define|\bgorm\.Model\b|\bpgTable\(|\bsqliteTable\(",
)
_ENTRY_NAMES: Final = frozenset(
    {
        "app.py",
        "main.py",
        "server.py",
        "wsgi.py",
        "asgi.py",
        "urls.py",
        "server.js",
        "app.js",
        "index.js",
        "server.ts",
        "app.ts",
        "index.ts",
        "main.go",
        "main.ts",
        "main.js",
        "routes.py",
        "routes.js",
        "routes.ts",
    },
)
_ENTRY_RE: Final = re.compile(
    r"\bFlask\(|\bFastAPI\(|\bexpress\(\)|@SpringBootApplication|\bgin\.Default\(|http\.ListenAndServe",
)
_OUTLINE_KINDS: Final = frozenset({"function", "method", "class", "component", "route_handler"})


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


@dataclass(frozen=True)
class ContextFileText:
    path: str
    text: str
    sha: str
    tokens: int
    role: str  # slot | related | outline


@dataclass(frozen=True)
class MaterializedContext:
    resolved: ResolvedContext
    files: list[ContextFileText]
    hole: HoleResult


def _is_model_file(path: str, text: str) -> bool:
    lowered = path.lower()
    return "model" in lowered or "schema" in lowered or bool(_ORM_RE.search(text))


def _is_entry_file(path: str, text: str) -> bool:
    return PurePosixPath(path).name.lower() in _ENTRY_NAMES or bool(_ENTRY_RE.search(text))


def _imports_bfs(part: Part, depth: int) -> list[str]:
    graph = part.parent.graph
    found: list[str] = []
    frontier = [part.path]
    seen = {part.path}
    for _ in range(depth):
        nxt: list[str] = []
        for path in frontier:
            for target in graph.neighbours(path):
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        found += sorted(nxt)
        frontier = nxt
    return found


def _excluded_readmes(part: Part, candidates: list[str]) -> set[str]:
    """READMEs the leak scan flags (verbatim or above the high-leak threshold)."""
    from backend.generation.slot import leak

    flagged: set[str] = set()
    for path in candidates:
        if is_readme(path):
            report = leak.score_texts(part.original_text, part.language, {path: part.parent.texts[path]})
            if report.score > leak.HIGH_LEAK_SCORE or any(match.verbatim for match in report.matches):
                flagged.add(path)
    return flagged


def _priority(part: Part, policy: ContextPolicy) -> list[str]:
    """Every candidate context path except the slot file, in priority order."""
    texts = part.parent.texts
    tree = part.parent.tree
    candidates = [
        path
        for path in tree.source_paths(include_tests=policy.include_tests)
        if path != part.path and path in texts and len(tree.read(path)) <= MAX_CONTEXT_FILE_BYTES
    ]
    if not policy.include_tests:
        flagged = _excluded_readmes(part, candidates)
        candidates = [path for path in candidates if path not in flagged]
    allowed = set(candidates)
    ordered: list[str] = []

    def add(paths: list[str]) -> None:
        for path in paths:
            if path in allowed and path not in ordered:
                ordered.append(path)

    if policy.mode == "slot_file_only":
        return []
    add(_imports_bfs(part, policy.related.imports_depth))
    if policy.related.include_models:
        add(sorted(p for p in candidates if _is_model_file(p, texts[p])))
    if policy.related.include_routes_file:
        add(sorted(p for p in candidates if _is_entry_file(p, texts[p])))
    distances = graph_distances(part.parent.graph, part.path)
    add(sorted((p for p in candidates if p in distances), key=lambda p: (distances[p], p)))
    if policy.mode == "full_app":
        add(sorted(candidates))
    return ordered


def _outline(part: Part, path: str) -> str:
    """Imports, decorators and signatures of a file (the ``interface_only`` view)."""
    lines: list[str] = []
    text = part.parent.texts[path]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ", "#include", "package ", "export {")):
            lines.append(line)
    for symbol in part.parent.index.symbols:
        if symbol.path != path or symbol.kind not in _OUTLINE_KINDS:
            continue
        indent = "    " * (symbol.qualname.count("."))
        lines.extend(f"{indent}{decorator}" for decorator in symbol.decorators)
        lines.append(f"{indent}{symbol.signature}")
    return "\n".join(lines) + ("\n" if lines else "")


def materialize(
    part: Part,
    policy: ContextPolicy,
    *,
    hole_mode: str | None = None,
    keep_docstring: bool | None = None,
) -> MaterializedContext:
    """The resolved context plus the texts it lists (what the prompt renders)."""
    hole = part.hole(hole_mode, keep_docstring)
    files: list[ContextFileText] = []
    used = 0
    if not hole.removed:
        text = hole.holed_text
        tokens = estimate_tokens(text)
        files.append(ContextFileText(part.path, text, identity.text_sha(text), tokens, "slot"))
        used += tokens
    truncated: list[str] = []
    for path in _priority(part, policy):
        if truncated:
            truncated.append(path)
            continue
        if policy.mode == "interface_only":
            text, role = _outline(part, path), "outline"
            if not text:
                continue
        else:
            text, role = part.parent.texts[path], "related"
        tokens = estimate_tokens(text)
        if used + tokens > policy.max_context_tokens:
            truncated.append(path)
            continue
        files.append(ContextFileText(path, text, identity.text_sha(text), tokens, role))
        used += tokens
    sha = identity.H(
        {
            "version": CONTEXT_VERSION,
            "policy": policy.policy_hash(),
            "hole_mode": hole_mode or part.default_hole_mode,
            "keep_docstring": part.default_keep_docstring if keep_docstring is None else keep_docstring,
            "files": [[f.path, f.sha, f.tokens] for f in files],
        },
    )
    resolved = ResolvedContext(
        files=[ContextFile(path=f.path, sha=f.sha, tokens=f.tokens) for f in files],
        sha=sha,
        tokens=used,
        truncated_paths=truncated,
    )
    return MaterializedContext(resolved=resolved, files=files, hole=hole)


def resolve_context(
    target: SlotTarget | Part,
    context_policy: ContextPolicy | None = None,
    *,
    hole_mode: str | None = None,
    keep_docstring: bool | None = None,
) -> ResolvedContext:
    """The files and token counts a slot prompt shows (hole mode and docstring default to the target's)."""
    part = target if isinstance(target, Part) else part_of(target)
    policy = context_policy or part.default_context_policy
    return materialize(part, policy, hole_mode=hole_mode, keep_docstring=keep_docstring).resolved
