"""Issue clusters for study tables, read from the analysis engine's deduplication.

The analysis engine deduplicates findings once, when a run finalizes
(``analysis.engine.dedup``): counted static findings that describe one
problem (same file, lines within the window, a shared CWE or
rule-equivalence class from ``data/mappings/rule_equivalence.yaml``) share an
``issue_key``, and the run's measurement counts issues from those keys.
Study tables must count the same issues, so this module never
re-deduplicates with rules of its own: it groups findings by their stored
``issue_key`` and summarises each group with the engine's
``dedup.issue_summary`` (most severe member; bandit's evidence-based
severity wins over a ruff proxy of it).

Value dicts without an ``issue_key`` (findings that never went through a
run's finalize step) are clustered with the engine's own algorithm, so the
platform has exactly one deduplication rule.

The clusters also give the tool-overlap matrix: how often two tools
reported the same issue, and their agreement on the issue classes both of
them can detect.
"""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from itertools import combinations
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine import dedup as engine_dedup
from backend.analysis.engine.normalize import equivalence_classes

if TYPE_CHECKING:
    from collections.abc import Iterable

# Higher = more severe (the engine's ``types.SEVERITY_RANK`` is the reverse: 0 = critical).
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
# The engine's default static dedup window (``assign_issue_keys(line_window=2)``).
# Only used for value dicts without an issue_key: keyed findings keep the
# grouping the engine stored when their run finalized.
DEFAULT_LINE_TOLERANCE = 2

FINDING_FIELDS = (
    "id",
    "run_id",
    "tool_slug",
    "rule_id",
    "severity",
    "category",
    "cwe",
    "file_path",
    "line_number",
    "end_line",
    "title",
    "description",
    "issue_key",
    "is_ai",
    "counted",
    "is_consensus",
    "grounding",
    "review_sample_id",
    "tool_specific_data",
)


def _cwe_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted({str(c).upper() for c in value if c})
    return [str(value).upper()] if value else []


def issue_class(tool: str, rule_id: str, *, cwe: Any = "", category: str = "") -> str:
    """A readable class label for a (tool, rule): its engine equivalence class(es), else CWE, else ``tool:rule``.

    Descriptive only (the "which tool finds which kind of issue" table and
    the review sheets): clustering is the engine's, by ``issue_key``.
    """
    classes = sorted(equivalence_classes(tool, rule_id or ""))
    if classes:
        return "+".join(classes)
    cwes = _cwe_list(cwe)
    if cwes:
        return cwes[0]
    if category == "secrets":
        return "secret"
    return f"{tool}:{rule_id or 'unknown'}"


def finding_record(row: dict[str, Any]) -> dict[str, Any]:
    """A ``Finding.objects.values(*FINDING_FIELDS)`` row in the record shape clusters hold."""
    extra = row.get("tool_specific_data") if isinstance(row.get("tool_specific_data"), dict) else {}
    tool = row.get("tool_slug") or ""
    return {
        "id": row.get("id"),
        "run_id": str(row.get("run_id")),
        "tool": tool,
        "tool_slug": tool,
        "rule_id": row.get("rule_id") or "",
        "severity": row.get("severity") or "info",
        "category": row.get("category") or "",
        "cwe": _cwe_list(row.get("cwe")),
        "file_path": row.get("file_path") or "",
        "line": row.get("line_number"),
        "end_line": row.get("end_line"),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "issue_key": row.get("issue_key") or "",
        "is_ai": bool(row.get("is_ai")),
        "counted": bool(row.get("counted")),
        "is_consensus": bool(row.get("is_consensus")),
        "grounding": row.get("grounding") or "",
        "review_sample_id": row.get("review_sample_id"),
        "tool_specific_data": extra,
    }


def load_records(run_ids: Iterable[Any], *, ai: bool = False, counted_only: bool = True) -> list[dict[str, Any]]:
    """Findings of *run_ids* as records: counted static ones (default) or counted AI ones (``ai=True``).

    Static and AI findings are never mixed: AI findings are opinions,
    reported apart from the static measurement. ``counted`` is the flag the
    measurement counted (static: in measured scope and a counted category;
    AI: consensus, grounded and in scope).
    """
    from backend.analysis.models import Finding

    ids = list(run_ids)
    if not ids:
        return []
    qs = Finding.objects.filter(run_id__in=ids, is_ai=ai)
    if counted_only:
        qs = qs.filter(counted=True)
    rows = qs.order_by("run_id", "file_path", "line_number", "id").values(*FINDING_FIELDS)
    return [finding_record(row) for row in rows]


@dataclass
class IssueCluster:
    """One deduplicated issue: every finding (from one or more tools) that reports it."""

    run_id: str
    issue_class: str
    file_path: str
    line: int | None
    members: list[dict[str, Any]] = field(default_factory=list)
    issue_key: str = ""
    ai: bool = False

    @property
    def tools(self) -> list[str]:
        return sorted({m["tool"] for m in self.members})

    @property
    def summary(self) -> dict[str, Any]:
        """The engine's issue summary (static: most severe member; AI: replicate consensus)."""
        return engine_dedup.issue_summary(self.members, mode="consensus" if self.ai else "max")

    @property
    def severity(self) -> str:
        return self.summary["severity"]

    @property
    def category(self) -> str:
        return self.summary["category"]

    def to_dict(self) -> dict[str, Any]:
        summary = self.summary
        return {
            "run_id": self.run_id,
            "issue_key": self.issue_key,
            "issue_class": self.issue_class,
            "file_path": self.file_path,
            "line": self.line,
            "end_line": summary["end_line"],
            "severity": summary["severity"],
            "category": summary["category"],
            "cwe": summary["cwe"],
            "tools": self.tools,
            "n_findings": len(self.members),
            "rule_ids": sorted({f"{m['tool']}:{m.get('rule_id') or ''}" for m in self.members}),
            "finding_ids": [str(m["id"]) for m in self.members if m.get("id") is not None],
        }


def _normalise(f: dict[str, Any]) -> dict[str, Any]:
    tool = f.get("tool") or f.get("tool_slug") or ""
    return {**f, "tool": tool, "tool_slug": tool, "cwe": _cwe_list(f.get("cwe"))}


def _cluster_class(members: list[dict[str, Any]]) -> str:
    """The class a cluster's members share (engine equivalence), else its most common member label."""
    shared = set.intersection(*(set(equivalence_classes(m["tool"], m.get("rule_id") or "")) for m in members))
    if shared:
        return "+".join(sorted(shared))
    labels = Counter(
        issue_class(m["tool"], m.get("rule_id") or "", cwe=m.get("cwe"), category=m.get("category") or "")
        for m in members
    )
    return min(labels, key=lambda label: (-labels[label], label))


def cluster_findings(
    findings: list[dict[str, Any]],
    *,
    line_tolerance: int = DEFAULT_LINE_TOLERANCE,
    ai: bool = False,
) -> list[IssueCluster]:
    """Group *findings* into deduplicated issues.

    Each finding is a record with ``id, run_id, tool (or tool_slug),
    rule_id, severity, category, file_path, line`` and optionally
    ``end_line, cwe, issue_key``. Records that carry an ``issue_key`` are
    grouped by it (the engine's stored deduplication); the rest are
    clustered with the engine's algorithm (window *line_tolerance*, the AI
    window for *ai*). Findings of different runs (different apps) never
    merge. *ai* summarises a cluster by replicate consensus.
    """
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in findings:
        rec = _normalise(f)
        by_run[str(rec.get("run_id"))].append(rec)

    clusters: list[IssueCluster] = []
    for run_id, items in sorted(by_run.items()):
        keyed: dict[str, list[dict[str, Any]]] = defaultdict(list)
        unkeyed: list[dict[str, Any]] = []
        for rec in items:
            if rec.get("issue_key"):
                keyed[rec["issue_key"]].append(rec)
            else:
                unkeyed.append(rec)
        groups: list[tuple[str, list[dict[str, Any]]]] = sorted(keyed.items())
        window = engine_dedup.AI_LINE_WINDOW if ai else line_tolerance
        groups += [("", members) for members in engine_dedup.cluster_findings(unkeyed, window=window, ai=ai)]
        for key, group in groups:
            members = sorted(group, key=lambda m: (m.get("line") is None, m.get("line") or 0, m["tool"]))
            lines = [m["line"] for m in members if isinstance(m.get("line"), int)]
            clusters.append(
                IssueCluster(
                    run_id=run_id,
                    issue_class=_cluster_class(members),
                    file_path=members[0].get("file_path") or "",
                    line=min(lines) if lines else None,
                    members=members,
                    issue_key=key,
                    ai=ai,
                ),
            )
    return clusters


def tool_overlap(clusters: list[IssueCluster]) -> dict[str, Any]:
    """Per-tool unique/exclusive counts and pairwise co-detection between tools.

    ``agreement`` for a pair is the Jaccard index of their clusters,
    restricted to issue classes *both* tools reported somewhere in the
    data: a pair that cannot detect the same things is not a disagreement.
    """
    classes_by_tool: dict[str, set[str]] = defaultdict(set)
    per_tool: dict[str, Counter] = defaultdict(Counter)
    for c in clusters:
        tools = c.tools
        for t in tools:
            classes_by_tool[t].add(c.issue_class)
            per_tool[t]["unique_issues"] += 1
            per_tool[t]["exclusive" if len(tools) == 1 else "shared"] += 1
        for m in c.members:
            per_tool[m["tool"]]["raw_findings"] += 1

    pairs: list[dict[str, Any]] = []
    for a, b in combinations(sorted(classes_by_tool), 2):
        comparable = classes_by_tool[a] & classes_by_tool[b]
        if not comparable:
            continue
        both = only_a = only_b = 0
        for c in clusters:
            if c.issue_class not in comparable:
                continue
            has_a, has_b = a in c.tools, b in c.tools
            if has_a and has_b:
                both += 1
            elif has_a:
                only_a += 1
            elif has_b:
                only_b += 1
        union = both + only_a + only_b
        pairs.append(
            {
                "tool_a": a,
                "tool_b": b,
                "comparable_classes": sorted(comparable),
                "both": both,
                "only_a": only_a,
                "only_b": only_b,
                "agreement": round(both / union, 3) if union else None,
            },
        )
    tools = [
        {
            "tool": t,
            "raw_findings": counts["raw_findings"],
            "unique_issues": counts["unique_issues"],
            "exclusive": counts["exclusive"],
            "shared": counts["shared"],
        }
        for t, counts in sorted(per_tool.items())
    ]
    multi = sum(1 for c in clusters if len(c.tools) > 1)
    return {
        "tools": tools,
        "pairs": sorted(pairs, key=lambda p: (-p["both"], p["tool_a"], p["tool_b"])),
        "issues": len(clusters),
        "multi_tool_issues": multi,
    }
