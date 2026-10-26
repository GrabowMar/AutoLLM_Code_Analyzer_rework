"""Cross-tool deduplication into issues (``dedup-v1``; owner: sample/measure stage).

Derived and versioned: raw findings are never mutated except for their
``issue_key``. Two counted static findings join one issue when they share a
path, their lines are within the window, and they share a CWE or are
equivalent per ``data/mappings/rule_equivalence.yaml``.

Algorithm (deterministic, independent of input order):

1. Findings are clustered per namespace: counted static, uncounted static,
   and AI (AI findings already keyed by consensus keep their key). Issue
   statistics therefore never mix counted and uncounted members.
2. Candidate pairs (same path):
   * different tools: line distance <= window (overlapping ranges = 0) and a
     shared CWE or a shared equivalence class;
   * the same tool: the same line and the same rule, a shared CWE or class
     (one problem reported under several rules, e.g. three semgrep rules on
     one ``shell=True`` call). The same tool never merges across lines: two
     unused imports on adjacent lines are two issues.
3. Union-find over the pairs sorted by (distance, canonical order), with a
   cannot-link constraint: a cluster holds at most one line per tool (per
   review sample for AI), so chains across neighbouring lines stop.
4. ``issue_key`` = sha256 over the namespace and the members' fingerprints
   and lines: stable for the same findings, unique per location.

An issue's severity (``issue_summary``) is its most severe member's, with one
rule: a member whose severity is only a proxy of another tool's default
(``severity_basis == "bandit_default"``: a ruff ``S###`` rule mapped to
bandit's default severity for the test) does not decide it when the issue
also holds a bandit finding, whose severity bandit derived from the code
(e.g. LOW for ``subprocess.run("ls", shell=True)``, HIGH for a built command).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections import defaultdict
from typing import Any

from backend.analysis.engine.normalize import equivalence_classes
from backend.analysis.engine.types import DEDUP_VERSION
from backend.analysis.engine.types import SEVERITY_RANK
from backend.analysis.engine.types import NormalizedFinding

AI_LINE_WINDOW = 3


# --------------------------------------------------------------------------
# Attribute access (NormalizedFinding, Finding rows and value dicts)
# --------------------------------------------------------------------------


def _get(f: Any, name: str, default: Any = None) -> Any:
    if isinstance(f, dict):
        return f.get(name, default)
    return getattr(f, name, default)


def _line(f: Any) -> int | None:
    value = _get(f, "line")
    if value is None:
        value = _get(f, "line_number")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _end_line(f: Any) -> int | None:
    start = _line(f)
    end = _get(f, "end_line")
    if not isinstance(end, int) or isinstance(end, bool) or start is None or end < start:
        return start
    return end


def _severity_basis(f: Any) -> str:
    """``extra["severity_basis"]`` of a NormalizedFinding, Finding row or value dict."""
    for name in ("extra", "tool_specific_data"):
        extra = _get(f, name)
        if isinstance(extra, dict):
            return str(extra.get("severity_basis") or "")
    return ""


# Severity bases that only stand in for a tool's context-aware severity.
PROXY_SEVERITY_BASES = frozenset({"bandit_default"})


def _sample_index(f: Any) -> Any:
    value = _get(f, "sample_index")
    if value is None:
        value = _get(f, "review_sample_id")
    return value


def _cwes(f: Any) -> frozenset[str]:
    return frozenset(str(c) for c in (_get(f, "cwe") or []))


def _fingerprint_material(f: Any) -> str:
    fp = _get(f, "fingerprint") or ""
    if not fp:
        fp = "|".join(str(_get(f, k) or "") for k in ("tool_slug", "rule_id", "file_path", "title"))
    return f"{fp}@{_line(f)}-{_end_line(f)}"


def _canonical(f: Any) -> tuple:
    line = _line(f)
    return (
        _get(f, "file_path") or "",
        -1 if line is None else line,
        _end_line(f) or -1,
        _get(f, "tool_slug") or "",
        str(_sample_index(f) or ""),
        _get(f, "rule_id") or "",
        _get(f, "fingerprint") or "",
        SEVERITY_RANK.get(_get(f, "severity") or "info", 4),
        _get(f, "category") or "",
    )


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def _distance(a: Any, b: Any) -> int | None:
    la, lb = _line(a), _line(b)
    if la is None or lb is None:
        return 0 if la is None and lb is None else None
    ea, eb = _end_line(a) or la, _end_line(b) or lb
    if la <= eb and lb <= ea:
        return 0
    return lb - ea if lb > ea else la - eb


def _tool_identity(f: Any, ai: bool) -> tuple[str, str]:
    """The cannot-link unit: a tool, or one review sample for AI findings."""
    return (_get(f, "tool_slug") or "", str(_sample_index(f)) if ai else "")


def _linkable(a: Any, b: Any, *, window: int, ai: bool) -> int | None:
    """The pair's distance when the two findings may describe one issue, else None."""
    distance = _distance(a, b)
    if distance is None or distance > window:
        return None
    shared_cwe = bool(_cwes(a) & _cwes(b))
    if ai:
        if _tool_identity(a, ai) == _tool_identity(b, ai):
            return None
        same_category = (_get(a, "category") or "") == (_get(b, "category") or "")
        return distance if shared_cwe or same_category else None
    tool_a, tool_b = _get(a, "tool_slug") or "", _get(b, "tool_slug") or ""
    classes_a = equivalence_classes(tool_a, _get(a, "rule_id") or "")
    classes_b = equivalence_classes(tool_b, _get(b, "rule_id") or "")
    related = shared_cwe or bool(classes_a & classes_b)
    if tool_a == tool_b:
        same_rule = (_get(a, "rule_id") or "") == (_get(b, "rule_id") or "")
        return 0 if _line(a) == _line(b) and (same_rule or related) else None
    return distance if related else None


def _cluster(findings: list[Any], *, window: int, ai: bool) -> list[list[Any]]:
    """Clusters of *findings* (each sorted canonically), deterministic."""
    ordered = sorted(findings, key=_canonical)
    parent = list(range(len(ordered)))
    tool_lines: list[dict[tuple[str, str], int | None]] = [{_tool_identity(f, ai): _line(f)} for f in ordered]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs: list[tuple[int, int, int]] = []
    by_path: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(ordered):
        by_path[_get(f, "file_path") or ""].append(i)
    for indices in by_path.values():
        # Canonical order sorts by line within a path, so the sweep can stop
        # once a later finding starts beyond the window of the current one.
        for pos, i in enumerate(indices):
            a = ordered[i]
            end_a = _end_line(a)
            for j in indices[pos + 1 :]:
                b = ordered[j]
                start_b = _line(b)
                if end_a is not None and start_b is not None and start_b - end_a > window:
                    break
                distance = _linkable(a, b, window=window, ai=ai)
                if distance is not None:
                    pairs.append((distance, i, j))
    pairs.sort()
    for _distance_value, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        lines_i, lines_j = tool_lines[ri], tool_lines[rj]
        if any(tool in lines_j and lines_j[tool] != line for tool, line in lines_i.items()):
            continue  # cannot-link: one tool, two different lines
        keep, drop = (ri, rj) if ri < rj else (rj, ri)
        parent[drop] = keep
        tool_lines[keep] = {**tool_lines[drop], **tool_lines[keep]}
    groups: dict[int, list[Any]] = defaultdict(list)
    for i, f in enumerate(ordered):
        groups[find(i)].append(f)
    return [groups[root] for root in sorted(groups)]


def _issue_key(namespace: str, members: list[Any]) -> str:
    material = "\n".join(sorted(_fingerprint_material(f) for f in members))
    return hashlib.sha256(f"{DEDUP_VERSION}|{namespace}|{material}".encode()).hexdigest()


def _set_key(f: Any, key: str) -> None:
    if isinstance(f, dict):
        f["issue_key"] = key
    else:
        f.issue_key = key


def cluster_findings(findings: list[Any], *, window: int, ai: bool = False) -> list[list[Any]]:
    """Public clustering entry point (used by measurement/overlap and tests)."""
    return _cluster(findings, window=window, ai=ai)


def assign_issue_keys(findings: list[NormalizedFinding], *, line_window: int = 2) -> int:
    """Set ``issue_key`` on every finding (static and AI clustered separately);
    returns the number of distinct counted static issues.

    AI findings that already carry a key (set by ``consensus.apply_consensus``)
    keep it; unkeyed AI findings are clustered with the AI window (3 lines,
    shared CWE or category, one line per review sample). Also accepts
    ``models.Finding`` rows or value dicts (``line_number`` is read as ``line``).
    """
    static = [f for f in findings if not _get(f, "is_ai")]
    counted = [f for f in static if _get(f, "counted")]
    uncounted = [f for f in static if not _get(f, "counted")]
    for namespace, group in (("static", counted), ("static-uncounted", uncounted)):
        for members in _cluster(group, window=line_window, ai=False):
            key = _issue_key(namespace, members)
            for f in members:
                _set_key(f, key)
    unkeyed_ai = [f for f in findings if _get(f, "is_ai") and not _get(f, "issue_key")]
    for members in _cluster(unkeyed_ai, window=AI_LINE_WINDOW, ai=True):
        key = _issue_key("ai", members)
        for f in members:
            _set_key(f, key)
    return len({_get(f, "issue_key") for f in counted})


def assign_run_issue_keys(run, *, line_window: int = 2) -> int:
    """Dedup a persisted run's static findings in the DB; returns the counted issue count.

    Convenience for the executor's finalize step: AI keys (set by consensus)
    are left untouched.
    """
    from backend.analysis.models import Finding

    rows = list(
        Finding.objects.filter(run=run, is_ai=False).only(
            "id",
            "tool_slug",
            "rule_id",
            "file_path",
            "line_number",
            "end_line",
            "cwe",
            "category",
            "severity",
            "counted",
            "is_ai",
            "fingerprint",
            "issue_key",
        ),
    )
    issues = assign_issue_keys(rows, line_window=line_window)
    Finding.objects.bulk_update(rows, ["issue_key"], batch_size=500)
    return issues


# --------------------------------------------------------------------------
# Issue summaries + AI/static overlap
# --------------------------------------------------------------------------


def group_issues(findings: list[Any]) -> dict[str, list[Any]]:
    """{issue_key: members}; a finding without a key is its own issue."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for f in findings:
        key = _get(f, "issue_key") or f"single:{_get(f, 'id') or id(f)}"
        groups[key].append(f)
    return dict(groups)


def issue_summary(members: list[Any], *, mode: str = "max") -> dict[str, Any]:
    """Severity / category / CWE / location of one issue.

    ``mode="max"`` (static issues): the most severe member decides, ties by
    category name. ``mode="consensus"`` (AI clusters): the most frequent
    severity and category among the replicates, ties to the more severe.
    """
    if mode == "consensus":
        severities = Counter(_get(f, "severity") or "info" for f in members)
        severity = min(severities, key=lambda s: (-severities[s], SEVERITY_RANK.get(s, 4)))
        categories = Counter(_get(f, "category") or "other" for f in members)
        category = min(categories, key=lambda c: (-categories[c], c))
    else:
        deciding = members
        if any(_get(f, "tool_slug") == "bandit" for f in members):
            evidenced = [f for f in members if _severity_basis(f) not in PROXY_SEVERITY_BASES]
            deciding = evidenced or members
        first = min(
            deciding,
            key=lambda f: (SEVERITY_RANK.get(_get(f, "severity") or "info", 4), _get(f, "category") or ""),
        )
        severity = _get(first, "severity") or "info"
        category = _get(first, "category") or "other"
    lines = [line for f in members if (line := _line(f)) is not None]
    ends = [end for f in members if (end := _end_line(f)) is not None]
    return {
        "severity": severity,
        "category": category,
        "cwe": sorted(set().union(*(_cwes(f) for f in members))),
        "file_path": _get(members[0], "file_path") or "",
        "line": min(lines) if lines else None,
        "end_line": max(ends) if ends else None,
        "tools": sorted({_get(f, "tool_slug") or "" for f in members}),
        "size": len(members),
    }


def ai_static_overlap(static: list[NormalizedFinding], ai: list[NormalizedFinding]) -> dict[str, Any]:
    """Agreement between counted static issues and AI consensus findings.

    *static*: counted static findings (keyed by :func:`assign_issue_keys`);
    *ai*: counted AI findings (consensus clusters, keyed by consensus). Each
    side is reduced to issues; an AI issue matches a static issue on the same
    file within 3 lines sharing a CWE or the category. Matching is one-to-one,
    closest pairs first. ``jaccard`` = matched / (static + ai - matched).
    """
    static_issues = [issue_summary(m) for m in group_issues([f for f in static if _get(f, "counted")]).values()]
    ai_issues = [
        issue_summary(m, mode="consensus") for m in group_issues([f for f in ai if _get(f, "counted")]).values()
    ]
    candidates: list[tuple[int, int, int]] = []
    for i, s in enumerate(static_issues):
        for j, a in enumerate(ai_issues):
            if s["file_path"] != a["file_path"]:
                continue
            distance = _distance(s, a)
            if distance is None or distance > AI_LINE_WINDOW:
                continue
            if set(s["cwe"]) & set(a["cwe"]) or s["category"] == a["category"]:
                candidates.append((distance, i, j))
    candidates.sort()
    matched_static: set[int] = set()
    matched_ai: set[int] = set()
    for _d, i, j in candidates:
        if i in matched_static or j in matched_ai:
            continue
        matched_static.add(i)
        matched_ai.add(j)
    n_static, n_ai, matched = len(static_issues), len(ai_issues), len(matched_static)
    union = n_static + n_ai - matched
    by_category: dict[str, dict[str, int]] = {}
    for i, s in enumerate(static_issues):
        entry = by_category.setdefault(s["category"], {"static": 0, "ai": 0, "matched": 0})
        entry["static"] += 1
        entry["matched"] += int(i in matched_static)
    for a in ai_issues:
        by_category.setdefault(a["category"], {"static": 0, "ai": 0, "matched": 0})["ai"] += 1
    return {
        "window": AI_LINE_WINDOW,
        "static_issues": n_static,
        "ai_issues": n_ai,
        "matched": matched,
        "jaccard": round(matched / union, 4) if union else None,
        "ai_confirmed_share": round(matched / n_ai, 4) if n_ai else None,
        "static_found_by_ai_share": round(matched / n_static, 4) if n_static else None,
        "by_category": dict(sorted(by_category.items())),
    }
