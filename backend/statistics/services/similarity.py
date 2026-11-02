"""Run-to-run similarity: generated code, finding sets and LLM call sequences of two jobs.

Descriptive *diversity* measures for repeats of one setup, never quality:

* **Code.** Each job's *agent-written source*: ``result_data.files`` filtered
  by the LOC source rules (``reports.services.loc.is_source_file``: no
  manifests, lockfiles, docs, minified bundles or dependency/cache dirs),
  without test code (``is_test_path``) and without files the stack scaffold
  supplied unchanged (agent runs: ``agent.workspace.manifest`` origin
  ``scaffold``; template modes: files identical to the pinned stack
  skeleton; when neither is available the map is marked ``approximate``).
  Pairwise: path Jaccard, identical files (manifest sha256, else a content
  hash), normalized-line Jaccard, token 5-gram shingle Jaccard (regex
  tokens, per file) and, for Python, an ``ast`` node-type 3-gram Jaccard.
  A legacy single-blob result has no paths: its path measures are None.
  Results are cached per (job_a, job_b, :data:`ALGO_VERSION`): a finished
  job's code never changes.
* **Findings.** Per tool, the Jaccard of ``(rule_id, normalized path)``
  signatures of each job's latest result for that tool
  (``ToolResult.latest_ids_per_job``), only for tools that *measured* both
  jobs (completed, or skipped as not applicable), with code issues and
  dependency advisories (``is_dependency_advisory``) apart. Findings in test
  code or non-source dirs and runtime (DAST) findings are left out. A pair
  no common tool measured is None, never 0.
* **Calls (determinism probe).** For exact replays (same ``run_fingerprint``):
  the first call whose response (content, tool name + arguments, status)
  differs, from the stored ``agent_call_NNNN`` artifacts (content truncated
  at 8 000 chars there, so equality is judged on what was stored).

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
from backend.reports.services.loc import is_source_file
from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_from_result_data

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Bump when any pairwise measure changes: it keys the pair cache.
ALGO_VERSION = 1
SHINGLE_K = 5
AST_N = 3
_PAIR_CACHE_SECONDS = 30 * 24 * 3600
_CALLS_CACHE_SECONDS = 30 * 24 * 3600

# Identifiers (JS ``$`` included), numbers, then any single other character.
_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d+(?:\.\d+)?|[^\sA-Za-z0-9_$]")
_WS_RE = re.compile(r"\s+")
# Lines that carry no content of their own (closing brackets, separators).
_TRIVIAL_LINE_RE = re.compile(r"^[\s{}()\[\];,:<>/]*$")
_PATH_PREFIXES = ("/work/", "/app/", "/workspace/", "./")


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


def normalize_path(path: str) -> str:
    """A finding's file path relative to the app root (container prefixes and ``./`` stripped)."""
    p = (path or "").replace("\\", "/")
    for prefix in _PATH_PREFIXES:
        if p.startswith(prefix):
            p = p[len(prefix) :]
    return p.lstrip("/")


# ── agent-written source ──────────────────────────────────────────────


def _content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def agent_source_map(
    result_data: Any,
    *,
    skeleton: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """The agent-written source files of a job (``{path: text}``) and how they were selected.

    *skeleton* is the stack scaffold's ``{path: text}`` for template-mode
    jobs (no workspace manifest): files identical to it are subtracted.
    The info dict has ``origin`` (``manifest`` | ``skeleton`` | ``unfiltered``
    | ``blob`` | ``none``), ``approximate``, ``has_paths``,
    ``scaffold_excluded`` and ``sha`` (``{path: sha256}``).
    """
    data = result_data if isinstance(result_data, dict) else {}
    files = data.get("files")
    if isinstance(files, dict) and files:
        agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
        workspace = agent.get("workspace") if isinstance(agent.get("workspace"), dict) else {}
        manifest = workspace.get("manifest") if isinstance(workspace.get("manifest"), dict) else None
        out: dict[str, str] = {}
        sha: dict[str, str] = {}
        excluded = 0
        for path, text in files.items():
            if not isinstance(text, str) or not is_source_file(path) or is_test_path(path):
                continue
            entry = manifest.get(path) if manifest is not None else None
            if isinstance(entry, dict) and entry.get("origin") == "scaffold":
                excluded += 1
                continue
            if manifest is None and skeleton and isinstance(skeleton.get(path), str):
                if skeleton[path].strip() == text.strip():
                    excluded += 1
                    continue
            out[path] = text
            recorded = entry.get("sha256") if isinstance(entry, dict) else None
            sha[path] = recorded if isinstance(recorded, str) and recorded else _content_sha(text)
        if manifest is not None:
            origin = "manifest"
        elif skeleton:
            origin = "skeleton"
        else:
            origin = "unfiltered"
        return out, {
            "origin": origin,
            "approximate": origin == "unfiltered",
            "has_paths": True,
            "scaffold_excluded": excluded,
            "sha": sha,
        }
    blob: dict[str, str] = {}
    for keys, name in ((("backend_code", "backend"), "backend"), (("frontend_code", "frontend"), "frontend")):
        text = next((data.get(k) for k in keys if isinstance(data.get(k), str) and data.get(k)), None)
        if text:
            blob[name] = text
    return blob, {
        "origin": "blob" if blob else "none",
        "approximate": True,
        "has_paths": False,
        "scaffold_excluded": 0,
        "sha": {name: _content_sha(text) for name, text in blob.items()},
    }


@dataclass
class SourceFeatures:
    """What the pairwise code measures need from one job (sets of hashes, no text)."""

    paths: frozenset[str]
    sha: dict[str, str]
    has_paths: bool
    approximate: bool
    origin: str
    lines: frozenset[int]
    shingles: frozenset[int]
    ast: frozenset[tuple[str, ...]] | None
    n_files: int


def source_features(result_data: Any, *, skeleton: dict[str, Any] | None = None) -> SourceFeatures:
    files, info = agent_source_map(result_data, skeleton=skeleton)
    lines: set[int] = set()
    shingle_set: set[int] = set()
    ast_set: set[tuple[str, ...]] = set()
    parsed_python = False
    for path, text in files.items():
        lines |= normalized_lines(text)
        shingle_set |= shingles(text)
        if path.endswith(".py") or (path == "backend" and not info["has_paths"]):
            grams = ast_ngrams(text)
            if grams is not None:
                parsed_python = True
                ast_set |= grams
    return SourceFeatures(
        paths=frozenset(files) if info["has_paths"] else frozenset(),
        sha=info["sha"],
        has_paths=info["has_paths"],
        approximate=info["approximate"],
        origin=info["origin"],
        lines=frozenset(lines),
        shingles=frozenset(shingle_set),
        ast=frozenset(ast_set) if parsed_python else None,
        n_files=len(files),
    )


def compare_sources(a: SourceFeatures, b: SourceFeatures) -> dict[str, Any]:
    """The pairwise code measures of two jobs (see the module docstring)."""
    both_paths = a.has_paths and b.has_paths
    common = a.paths & b.paths if both_paths else frozenset()
    return {
        "path_jaccard": jaccard(a.paths, b.paths) if both_paths else None,
        "identical_files": sum(1 for p in common if a.sha.get(p) and a.sha.get(p) == b.sha.get(p))
        if both_paths
        else None,
        "line_jaccard": jaccard(a.lines, b.lines),
        "shingle_jaccard": jaccard(a.shingles, b.shingles),
        "ast_jaccard": jaccard(a.ast, b.ast) if a.ast is not None and b.ast is not None else None,
        "files_a": a.n_files,
        "files_b": b.n_files,
        "approximate": a.approximate or b.approximate,
    }


# ── loading (Django) ──────────────────────────────────────────────────

_SOURCE_FIELDS = (
    "id",
    "mode",
    "status",
    "stack_slug",
    "resolved_bundle__stack",
    "result_data__files",
    "result_data__agent__workspace__manifest",
    "result_data__backend_code",
    "result_data__frontend_code",
    "result_data__backend",
    "result_data__frontend",
)


def _result_data_of(row: dict[str, Any]) -> dict[str, Any]:
    """A minimal ``result_data`` from a ``values(*_SOURCE_FIELDS)`` row (what the source rules read)."""
    data: dict[str, Any] = {}
    for key in ("files", "backend_code", "frontend_code", "backend", "frontend"):
        value = row.get(f"result_data__{key}")
        if value is not None:
            data[key] = value
    manifest = row.get("result_data__agent__workspace__manifest")
    if isinstance(manifest, dict):
        data["agent"] = {"workspace": {"manifest": manifest}}
    return data


class SourceStore:
    """Loads jobs' source features (and source LOC) on demand, keeping a bounded LRU of features.

    A job's files are read from the database once per load; features (sets of
    hashes) of at most ``capacity`` jobs are kept, so a large experiment never
    holds every job's shingles at once. ``loc`` keeps every loaded job's
    ``loc_from_result_data`` (small dicts). Template-mode jobs subtract their
    pinned stack skeleton, read once per (slug, version).
    """

    def __init__(self, capacity: int = 24) -> None:
        self.capacity = capacity
        self._features: OrderedDict[str, SourceFeatures] = OrderedDict()
        self.loc: dict[str, dict[str, Any]] = {}
        self._skeletons: dict[tuple, dict[str, Any] | None] = {}

    def _skeleton(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if row.get("result_data__agent__workspace__manifest"):
            return None
        stack = row.get("resolved_bundle__stack") if isinstance(row.get("resolved_bundle__stack"), dict) else {}
        key = (stack.get("slug") or row.get("stack_slug") or "", stack.get("version"))
        if key not in self._skeletons:
            from backend.runtime.services.scaffolding import get_stack_row

            stack_row = get_stack_row(key[0], key[1]) if key[0] else None
            files = getattr(stack_row, "files", None) if stack_row is not None else None
            self._skeletons[key] = files if isinstance(files, dict) and files else None
        return self._skeletons[key]

    def load(self, job_ids: Iterable[str], *, features: bool = True) -> None:
        """Read the jobs not loaded yet (one query per chunk); compute LOC and, with *features*, features."""
        from backend.generation.models import GenerationJob

        wanted = [str(j) for j in job_ids if str(j) not in self.loc or (features and str(j) not in self._features)]
        for start in range(0, len(wanted), 20):
            chunk = wanted[start : start + 20]
            for row in GenerationJob.objects.filter(id__in=chunk).values(*_SOURCE_FIELDS):
                job_id = str(row["id"])
                data = _result_data_of(row)
                self.loc[job_id] = loc_from_result_data(data)
                if features:
                    self._put(job_id, source_features(data, skeleton=self._skeleton(row)))

    def _put(self, job_id: str, value: SourceFeatures) -> None:
        self._features[job_id] = value
        self._features.move_to_end(job_id)
        while len(self._features) > self.capacity:
            self._features.popitem(last=False)

    def features(self, job_id: str) -> SourceFeatures:
        job_id = str(job_id)
        if job_id not in self._features:
            self.load([job_id], features=True)
        value = self._features.get(job_id)
        if value is None:  # the job does not exist (deleted mid-request)
            value = source_features({})
            self._put(job_id, value)
        else:
            self._features.move_to_end(job_id)
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
    """Code similarity of each ``(job_a, job_b)`` pair, cached; the keys are the pairs as given.

    Pairs are computed in the order given, so callers that list pairs grouped
    by job (cell by cell) keep the store's working set small. Only finished
    jobs should be passed: the cache assumes the code never changes.
    """
    pairs = [(str(a), str(b)) for a, b in pairs if str(a) != str(b)]
    keys = {pair: _pair_key(*pair) for pair in pairs}
    cached = _cache_get_many(sorted(set(keys.values())))
    missing = [p for p in pairs if keys[p] not in cached]
    if missing:
        store = store or SourceStore()
        # Windows of pairs whose jobs fit the store: one query per window.
        window: list[tuple[str, str]] = []
        jobs: set[str] = set()
        for pair in [*missing, None]:
            if pair is not None and (len(jobs | set(pair)) <= max(2, store.capacity) or not window):
                window.append(pair)
                jobs |= set(pair)
                continue
            store.load(sorted(jobs))
            for a, b in window:
                key = keys[(a, b)]
                if key in cached:
                    continue
                value = compare_sources(store.features(a), store.features(b))
                cached[key] = value
                _cache_set(key, value, _PAIR_CACHE_SECONDS)
            if pair is not None:
                window, jobs = [pair], set(pair)
    return {pair: cached[keys[pair]] for pair in pairs}


# ── findings ──────────────────────────────────────────────────────────


@dataclass
class FindingSets:
    """Per job and tool: the signatures of the latest result, and which tools measured the job."""

    measured: dict[str, set[str]]
    code: dict[str, dict[str, set[tuple[str, str]]]]
    dependency: dict[str, dict[str, set[tuple[str, str]]]]


def finding_sets(job_ids: Iterable[str]) -> FindingSets:
    """Finding signatures of each job's latest result per tool (AI reviewers left out)."""
    from backend.analysis.models import AnalysisMode
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult
    from backend.analysis.services.runner import SKIP_NOT_APPLICABLE

    ids = [str(j) for j in job_ids]
    measured: dict[str, set[str]] = defaultdict(set)
    code: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    dependency: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    if not ids:
        return FindingSets(measured={}, code={}, dependency={})
    ai = AnalyzerTool.ai_slugs()
    dynamic = set(AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True))
    result_to: dict[str, tuple[str, str]] = {}
    for result_id, job_id, slug, status, summary in ToolResult.objects.filter(
        id__in=ToolResult.latest_ids_per_job(ids),
    ).values_list("id", "run__generation_job_id", "tool_slug", "status", "summary"):
        if slug in ai:
            continue
        skip_reason = summary.get("skip_reason") if isinstance(summary, dict) else None
        if status == ToolResult.Status.COMPLETED or (
            status == ToolResult.Status.SKIPPED and skip_reason == SKIP_NOT_APPLICABLE
        ):
            measured[str(job_id)].add(slug)
            result_to[str(result_id)] = (str(job_id), slug)
    for result_id, rule_id, file_path, category in Finding.objects.filter(
        result_id__in=list(result_to),
    ).values_list("result_id", "rule_id", "file_path", "category"):
        job_id, slug = result_to[str(result_id)]
        path = normalize_path(file_path)
        if is_dependency_advisory(slug, category or "", rule_id or ""):
            # One project, several manifests: an advisory is matched within its directory.
            dependency[job_id][slug].add((rule_id or "", posixpath.dirname(path)))
            continue
        if slug in dynamic or (path and (is_test_path(path) or is_non_source_path(path))):
            continue  # runtime issues and test code are counted apart, not as code issues
        code[job_id][slug].add((rule_id or "", path))
    return FindingSets(
        measured={k: set(v) for k, v in measured.items()},
        code={k: dict(v) for k, v in code.items()},
        dependency={k: dict(v) for k, v in dependency.items()},
    )


def finding_overlap(sets: FindingSets, job_a: str, job_b: str) -> dict[str, Any] | None:
    """Jaccard of two jobs' finding signatures over the tools that measured both; None when none did."""
    job_a, job_b = str(job_a), str(job_b)
    tools = sorted(sets.measured.get(job_a, set()) & sets.measured.get(job_b, set()))
    if not tools:
        return None
    per_tool = []
    pooled = {"code": (set(), set()), "dependency": (set(), set())}
    for tool in tools:
        row: dict[str, Any] = {"tool": tool}
        for kind, table in (("code", sets.code), ("dependency", sets.dependency)):
            sa = table.get(job_a, {}).get(tool, set())
            sb = table.get(job_b, {}).get(tool, set())
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


def _calls(job_id: str) -> list[dict[str, Any]]:
    from backend.generation.models import GenerationArtifact

    return list(
        GenerationArtifact.objects.filter(job_id=job_id, stage__startswith="agent_call_")
        .order_by("stage")
        .values("stage", "response_payload"),
    )


def call_divergence(job_a: str, job_b: str) -> dict[str, Any]:
    """Where two runs' LLM call sequences first differ (cached per pair).

    ``first_divergent_call`` is 1-based; None when every call matched and the
    counts are equal (the replay reproduced, as far as stored artifacts tell).
    A run without stored calls gives ``comparable: false``.
    """
    lo, hi = sorted((str(job_a), str(job_b)))
    key = f"variability:calls:v{ALGO_VERSION}:{lo}:{hi}"
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001
        hit = None
    if hit is not None:
        return hit if lo == str(job_a) else _swap_calls(hit)
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
    return value if lo == str(job_a) else _swap_calls(value)


def _swap_calls(value: dict[str, Any]) -> dict[str, Any]:
    out = dict(value)
    out["calls_a"], out["calls_b"] = value["calls_b"], value["calls_a"]
    out["providers_a"], out["providers_b"] = value["providers_b"], value["providers_a"]
    return out
