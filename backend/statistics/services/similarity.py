"""Run-to-run similarity: generated code, finding sets and LLM call sequences of two runs.

Descriptive *diversity* measures for repeats of one setup, never quality:

* **Code.** Each run's *model-written source*: its **model** scope (``RunView``: authored, modified and slot
  files; starter files, artifacts and excluded rows are not in it), filtered by the LOC source rules
  (``reports.services.loc.is_source_file``: no manifests, lockfiles, docs, minified bundles or
  dependency/cache dirs) and without test code (``is_test_path``). Read in bulk with
  ``run_views_code(runs, "model")``. Pairwise: path Jaccard, identical files (content hash),
  normalized-line Jaccard, token 5-gram shingle Jaccard (regex tokens, per file) and, for Python, an
  ``ast`` node-type 3-gram Jaccard. Results are cached per (run_a, run_b, :data:`ALGO_VERSION`): a finished
  run's code never changes.
* **Findings.** Per tool, the Jaccard of ``(rule_id, normalized path)`` signatures of each run's latest
  result for that tool (``ToolResult.latest_ids_per_job``), only for tools that *measured* both runs
  (completed; a not-applicable tool measured nothing), with code issues and dependency advisories
  (``is_dependency_advisory``) apart. Findings in test code or non-source dirs and runtime (DAST) findings
  are left out. A pair no common tool measured is None, never 0.
* **Calls (determinism probe).** For exact replays (same ``run_fingerprint``): the first call whose
  response (content, tool name + arguments, status) differs, from the stored ``agent_call_NNNN`` /
  ``slot_call_NNNN`` artifacts (content truncated at 8 000 chars there, so equality is judged on what was
  stored).

Jaccard of two empty sets is None (nothing to compare), not 1.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import posixpath
import re
from collections import OrderedDict
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from django.core.cache import cache

from backend.analysis.services.base import is_dependency_advisory
from backend.analysis.services.base import is_non_source_path
from backend.reports.services.loc import is_outside_app
from backend.reports.services.loc import is_source_file
from backend.statistics.services.runs import code_loc
from backend.statistics.services.runs import normalize_path

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Bump when any pairwise measure changes: it keys the pair cache (2: v3 model scope).
ALGO_VERSION = 2
SHINGLE_K = 5
AST_N = 3
_PAIR_CACHE_SECONDS = 30 * 24 * 3600
_CALLS_CACHE_SECONDS = 30 * 24 * 3600

# Identifiers (JS ``$`` included), numbers, then any single other character.
_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d+(?:\.\d+)?|[^\sA-Za-z0-9_$]")
_WS_RE = re.compile(r"\s+")
# Lines that carry no content of their own (closing brackets, separators).
_TRIVIAL_LINE_RE = re.compile(r"^[\s{}()\[\];,:<>/]*$")
_CALL_STAGE_RE = re.compile(r"^(agent|slot)_call_\d{4}$")


# ── pure measures ─────────────────────────────────────────────────────


def jaccard(a: set | frozenset, b: set | frozenset) -> float | None:
    """``len(a & b) / len(a | b)``; None when both are empty (nothing to compare)."""
    if not a and not b:
        return None
    union = len(a | b)
    return round(len(a & b) / union, 4) if union else None


def tokens(text: str) -> list[str]:
    """Regex tokens of source text: identifiers, numbers and single punctuation characters."""
    return _TOKEN_RE.findall(text or "")


def shingles(text: str, k: int = SHINGLE_K) -> set[int]:
    """Hashed token *k*-grams of one file (a file shorter than *k* tokens gives one shingle of all of them)."""
    toks = tokens(text)
    if not toks:
        return set()
    if len(toks) < k:
        return {hash(tuple(toks))}
    return {hash(tuple(toks[i : i + k])) for i in range(len(toks) - k + 1)}


def normalized_lines(text: str) -> set[int]:
    """Hashed lines with whitespace collapsed; blank and bracket-only lines dropped."""
    out = set()
    for line in (text or "").splitlines():
        if _TRIVIAL_LINE_RE.match(line):
            continue
        out.add(hash(_WS_RE.sub(" ", line.strip())))
    return out


def _ast_types(node: ast.AST) -> Iterable[str]:
    """Node type names in depth-first pre-order."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield type(current).__name__
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def ast_ngrams(text: str, n: int = AST_N) -> set[tuple[str, ...]] | None:
    """Python ``ast`` node-type *n*-grams of one module; None when it does not parse."""
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    names = list(_ast_types(tree))
    if len(names) < n:
        return {tuple(names)} if names else set()
    return {tuple(names[i : i + n]) for i in range(len(names) - n + 1)}


# ── model-written source ──────────────────────────────────────────────


def _content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def model_source_map(files: dict[str, str] | None) -> dict[str, str]:
    """The model-written, non-test source files (``{path: text}``) of a run's model-scope code."""
    return {
        path: text
        for path, text in (files or {}).items()
        if isinstance(text, str) and is_source_file(path) and not is_outside_app(path)
    }


@dataclass
class SourceFeatures:
    """What the pairwise code measures need from one run (sets of hashes, no text)."""

    paths: frozenset[str]
    sha: dict[str, str]
    lines: frozenset[int]
    shingles: frozenset[int]
    ast: frozenset[tuple[str, ...]] | None
    n_files: int


def source_features(files: dict[str, str] | None) -> SourceFeatures:
    """:class:`SourceFeatures` of a run's model-scope code (``{path: text}``)."""
    source = model_source_map(files)
    lines: set[int] = set()
    shingle_set: set[int] = set()
    ast_set: set[tuple[str, ...]] = set()
    parsed_python = False
    for path, text in source.items():
        lines |= normalized_lines(text)
        shingle_set |= shingles(text)
        if path.endswith(".py"):
            grams = ast_ngrams(text)
            if grams is not None:
                parsed_python = True
                ast_set |= grams
    return SourceFeatures(
        paths=frozenset(source),
        sha={path: _content_sha(text) for path, text in source.items()},
        lines=frozenset(lines),
        shingles=frozenset(shingle_set),
        ast=frozenset(ast_set) if parsed_python else None,
        n_files=len(source),
    )


def compare_sources(a: SourceFeatures, b: SourceFeatures) -> dict[str, Any]:
    """The pairwise code measures of two runs (see the module docstring)."""
    common = a.paths & b.paths
    return {
        "path_jaccard": jaccard(a.paths, b.paths),
        "identical_files": sum(1 for p in common if a.sha.get(p) and a.sha.get(p) == b.sha.get(p)),
        "line_jaccard": jaccard(a.lines, b.lines),
        "shingle_jaccard": jaccard(a.shingles, b.shingles),
        "ast_jaccard": jaccard(a.ast, b.ast) if a.ast is not None and b.ast is not None else None,
        "files_a": a.n_files,
        "files_b": b.n_files,
    }


# ── loading (Django) ──────────────────────────────────────────────────


class SourceStore:
    """Loads runs' source features (and model-scope source LOC) on demand, keeping a bounded LRU of features.

    A run's model-scope files are read once per load (``run_views_code``, one query per chunk); features (sets
    of hashes) of at most ``capacity`` runs are kept, so a large study never holds every run's shingles at
    once. ``loc`` keeps every loaded run's :func:`~backend.statistics.services.runs.code_loc` (small dicts;
    ``None`` values for a run whose tree was never collected).
    """

    def __init__(self, capacity: int = 24) -> None:
        self.capacity = capacity
        self._features: OrderedDict[str, SourceFeatures] = OrderedDict()
        self.loc: dict[str, dict[str, Any]] = {}

    def load(self, run_ids: Iterable[str], *, features: bool = True) -> None:
        """Read the runs not loaded yet (one query per chunk); compute LOC and, with *features*, features."""
        from backend.generation.models import GenerationRun
        from backend.generation.runs.view import run_views_code

        wanted = [str(r) for r in run_ids if str(r) not in self.loc or (features and str(r) not in self._features)]
        for start in range(0, len(wanted), 20):
            runs = list(
                GenerationRun.objects.filter(id__in=wanted[start : start + 20]).only(
                    "id",
                    "kind",
                    "parent_run",
                    "tree_hash",
                ),
            )
            code = run_views_code(runs, "model")
            for run in runs:
                run_id = str(run.pk)
                files = code.get(run_id) or {}
                self.loc[run_id] = code_loc(files, has_tree=bool(run.tree_hash) or bool(files))
                if features:
                    self._put(run_id, source_features(files))

    def _put(self, run_id: str, value: SourceFeatures) -> None:
        self._features[run_id] = value
        self._features.move_to_end(run_id)
        while len(self._features) > self.capacity:
            self._features.popitem(last=False)

    def features(self, run_id: str) -> SourceFeatures:
        run_id = str(run_id)
        if run_id not in self._features:
            self.load([run_id], features=True)
        value = self._features.get(run_id)
        if value is None:  # the run does not exist (deleted mid-request)
            value = source_features({})
            self._put(run_id, value)
        else:
            self._features.move_to_end(run_id)
        return value


def _pair_key(a: str, b: str) -> str:
    lo, hi = sorted((str(a), str(b)))
    return f"variability:sim:v{ALGO_VERSION}:{lo}:{hi}"


def _cache_get_many(keys: list[str]) -> dict[str, Any]:
    try:
        return cache.get_many(keys)
    except Exception:  # noqa: BLE001 — a cache outage only costs the recomputation
        logger.debug("similarity cache read failed", exc_info=True)
        return {}


def _cache_set(key: str, value: Any, seconds: int) -> None:
    try:
        cache.set(key, value, seconds)
    except Exception:  # noqa: BLE001
        logger.debug("similarity cache write failed", exc_info=True)


def pair_similarities(
    pairs: Iterable[tuple[str, str]],
    store: SourceStore | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Code similarity of each ``(run_a, run_b)`` pair, cached; the keys are the pairs as given.

    Pairs are computed in the order given, so callers that list pairs grouped
    by run (cell by cell) keep the store's working set small. Only finished
    runs should be passed: the cache assumes the code never changes.
    """
    pairs = [(str(a), str(b)) for a, b in pairs if str(a) != str(b)]
    keys = {pair: _pair_key(*pair) for pair in pairs}
    cached = _cache_get_many(sorted(set(keys.values())))
    missing = [p for p in pairs if keys[p] not in cached]
    if missing:
        store = store or SourceStore()
        # Windows of pairs whose runs fit the store: one query per window.
        window: list[tuple[str, str]] = []
        runs: set[str] = set()
        for pair in [*missing, None]:
            if pair is not None and (len(runs | set(pair)) <= max(2, store.capacity) or not window):
                window.append(pair)
                runs |= set(pair)
                continue
            store.load(sorted(runs))
            for a, b in window:
                key = keys[(a, b)]
                if key in cached:
                    continue
                value = compare_sources(store.features(a), store.features(b))
                cached[key] = value
                _cache_set(key, value, _PAIR_CACHE_SECONDS)
            if pair is not None:
                window, runs = [pair], set(pair)
    return {pair: cached[keys[pair]] for pair in pairs}


# ── findings ──────────────────────────────────────────────────────────


@dataclass
class FindingSets:
    """Per run and tool: the signatures of the latest result, and which tools measured the run."""

    measured: dict[str, set[str]]
    code: dict[str, dict[str, set[tuple[str, str]]]]
    dependency: dict[str, dict[str, set[tuple[str, str]]]]


def finding_sets(run_ids: Iterable[str]) -> FindingSets:
    """Finding signatures of each run's latest result per tool (AI reviewers left out)."""
    from backend.analysis.models import AnalysisMode
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult
    from backend.statistics.services.study import tool_measured

    ids = [str(j) for j in run_ids]
    measured: dict[str, set[str]] = defaultdict(set)
    code: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    dependency: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    if not ids:
        return FindingSets(measured={}, code={}, dependency={})
    ai = AnalyzerTool.ai_slugs()
    dynamic = set(AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True))
    result_to: dict[str, tuple[str, str]] = {}
    for result_id, run_id, slug, status, summary in ToolResult.objects.filter(
        id__in=ToolResult.latest_ids_per_job(ids),
    ).values_list("id", "run__generation_job_id", "tool_slug", "status", "summary"):
        if slug in ai:
            continue
        if tool_measured(status, summary):
            measured[str(run_id)].add(slug)
            result_to[str(result_id)] = (str(run_id), slug)
    for result_id, rule_id, file_path, category in Finding.objects.filter(
        result_id__in=list(result_to),
    ).values_list("result_id", "rule_id", "file_path", "category"):
        run_id, slug = result_to[str(result_id)]
        path = normalize_path(file_path)
        if is_dependency_advisory(slug, category or "", rule_id or ""):
            # One project, several manifests: an advisory is matched within its directory.
            dependency[run_id][slug].add((rule_id or "", posixpath.dirname(path)))
            continue
        if slug in dynamic or (path and (is_outside_app(path) or is_non_source_path(path))):
            continue  # runtime issues and test code are counted apart, not as code issues
        code[run_id][slug].add((rule_id or "", path))
    return FindingSets(
        measured={k: set(v) for k, v in measured.items()},
        code={k: dict(v) for k, v in code.items()},
        dependency={k: dict(v) for k, v in dependency.items()},
    )


def finding_overlap(sets: FindingSets, run_a: str, run_b: str) -> dict[str, Any] | None:
    """Jaccard of two runs' finding signatures over the tools that measured both; None when none did."""
    run_a, run_b = str(run_a), str(run_b)
    tools = sorted(sets.measured.get(run_a, set()) & sets.measured.get(run_b, set()))
    if not tools:
        return None
    per_tool = []
    pooled = {"code": (set(), set()), "dependency": (set(), set())}
    for tool in tools:
        row: dict[str, Any] = {"tool": tool}
        for kind, table in (("code", sets.code), ("dependency", sets.dependency)):
            sa = table.get(run_a, {}).get(tool, set())
            sb = table.get(run_b, {}).get(tool, set())
            pooled[kind][0].update((tool, *s) for s in sa)
            pooled[kind][1].update((tool, *s) for s in sb)
            row[kind] = {"jaccard": jaccard(sa, sb), "a": len(sa), "b": len(sb), "both": len(sa & sb)}
        per_tool.append(row)
    return {
        "tools": tools,
        "code": {
            "jaccard": jaccard(*pooled["code"]),
            "a": len(pooled["code"][0]),
            "b": len(pooled["code"][1]),
            "both": len(pooled["code"][0] & pooled["code"][1]),
        },
        "dependency": {
            "jaccard": jaccard(*pooled["dependency"]),
            "a": len(pooled["dependency"][0]),
            "b": len(pooled["dependency"][1]),
            "both": len(pooled["dependency"][0] & pooled["dependency"][1]),
        },
        "per_tool": per_tool,
    }


# ── determinism probe ─────────────────────────────────────────────────


def _call_signature(response: Any) -> tuple:
    """What a call's response said: status, content and tool calls (name + arguments; ids are random)."""
    r = response if isinstance(response, dict) else {}
    calls = tuple(
        (str(tc.get("name") or (tc.get("function") or {}).get("name") or ""), str(tc.get("arguments") or ""))
        for tc in r.get("tool_calls") or []
        if isinstance(tc, dict)
    )
    return (r.get("status"), (r.get("content") or "").strip(), calls)


def _calls(run_id: str) -> list[dict[str, Any]]:
    """The stored call artifacts of the run's last attempt, in call order (a retry restarts the numbering)."""
    from backend.generation.models import GenerationArtifact

    rows = [
        row
        for row in GenerationArtifact.objects.filter(run_id=run_id, stage__regex=_CALL_STAGE_RE.pattern)
        .order_by("attempt", "stage")
        .values("attempt", "stage", "response_payload")
        if _CALL_STAGE_RE.match(row["stage"])
    ]
    last = max((row["attempt"] for row in rows), default=None)
    return [row for row in rows if row["attempt"] == last]


def call_divergence(run_a: str, run_b: str) -> dict[str, Any]:
    """Where two runs' LLM call sequences first differ (cached per pair).

    ``first_divergent_call`` is 1-based; None when every call matched and the
    counts are equal (the replay reproduced, as far as stored artifacts tell).
    A run without stored calls gives ``comparable: false``.
    """
    lo, hi = sorted((str(run_a), str(run_b)))
    key = f"variability:calls:v{ALGO_VERSION}:{lo}:{hi}"
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001
        hit = None
    if hit is not None:
        return hit if lo == str(run_a) else _swap_calls(hit)
    calls_a, calls_b = _calls(lo), _calls(hi)
    providers_a = sorted({str((c["response_payload"] or {}).get("provider") or "unknown") for c in calls_a})
    providers_b = sorted({str((c["response_payload"] or {}).get("provider") or "unknown") for c in calls_b})
    first = None
    same_first = None
    if calls_a and calls_b:
        same_first = _call_signature(calls_a[0]["response_payload"]) == _call_signature(calls_b[0]["response_payload"])
        for i, (ca, cb) in enumerate(zip(calls_a, calls_b, strict=False), start=1):
            if _call_signature(ca["response_payload"]) != _call_signature(cb["response_payload"]):
                first = i
                break
        else:
            if len(calls_a) != len(calls_b):
                first = min(len(calls_a), len(calls_b)) + 1
    value = {
        "comparable": bool(calls_a and calls_b),
        "first_divergent_call": first,
        "identical_first_response": same_first,
        "reproduced": bool(calls_a and calls_b) and first is None,
        "calls_a": len(calls_a),
        "calls_b": len(calls_b),
        "providers_a": providers_a,
        "providers_b": providers_b,
        "same_providers": providers_a == providers_b,
        "basis": "stored call artifacts (content truncated at 8000 chars)",
    }
    _cache_set(key, value, _CALLS_CACHE_SECONDS)
    return value if lo == str(run_a) else _swap_calls(value)


def _swap_calls(value: dict[str, Any]) -> dict[str, Any]:
    out = dict(value)
    out["calls_a"], out["calls_b"] = value["calls_b"], value["calls_a"]
    out["providers_a"], out["providers_b"] = value["providers_b"], value["providers_a"]
    return out
