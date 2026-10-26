"""Cross-tool deduplication of findings: one issue reported by several tools counts once.

Tools overlap: Ruff's ``S`` rules re-implement Bandit, Semgrep's Python
security rules cover much of Bandit, detect-secrets and Gitleaks both flag
the same hard-coded key, and Ruff, Pylint and Vulture all report the same
unused import. Summing their findings counts one defect several times.

A finding is mapped to an *issue class* (:func:`issue_class`): an explicit
rule equivalence when one is known, else its CWE, else its own
``tool:rule`` (which never merges across tools). Within one analysis run,
findings of the same class in the same file whose lines are within
``line_tolerance`` of each other form one :class:`IssueCluster`. A tool's
own findings merge only when they are exact duplicates (same rule, same
line), so two separate secrets on adjacent lines stay two issues.

The clusters also give the tool-overlap matrix: how often two tools
reported the same issue, and their agreement on the issue classes both of
them can detect.
"""

from __future__ import annotations

import re
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from itertools import combinations
from typing import Any

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
DEFAULT_LINE_TOLERANCE = 2

# Bandit test ids (and Ruff's flake8-bandit mirror, S<nnn> = B<nnn>).
_BANDIT_CLASSES: dict[str, str] = {
    "B101": "assert-used",
    "B102": "exec-used",
    "B104": "bind-all-interfaces",
    "B105": "hardcoded-secret",
    "B106": "hardcoded-secret",
    "B107": "hardcoded-secret",
    "B108": "insecure-temp-file",
    "B110": "try-except-pass",
    "B113": "request-without-timeout",
    "B201": "flask-debug",
    "B301": "unsafe-deserialization",
    "B303": "weak-hash",
    "B304": "weak-cipher",
    "B307": "eval-used",
    "B311": "insecure-random",
    "B324": "weak-hash",
    "B403": "unsafe-deserialization",
    "B501": "tls-verification-disabled",
    "B506": "unsafe-yaml-load",
    "B602": "shell-injection",
    "B605": "shell-injection",
    "B608": "sql-injection",
    "B703": "xss-markup",
}

# Lint rules several quality tools share (Ruff / Pylint / Vulture / Mypy / ESLint).
_LINT_CLASSES: dict[str, str] = {
    "ruff:F401": "unused-import",
    "pylint:unused-import": "unused-import",
    "pylint:W0611": "unused-import",
    "vulture:unused-import": "unused-import",
    "ruff:F841": "unused-variable",
    "pylint:unused-variable": "unused-variable",
    "pylint:W0612": "unused-variable",
    "vulture:unused-variable": "unused-variable",
    "ruff:F821": "undefined-name",
    "pylint:undefined-variable": "undefined-name",
    "pylint:E0602": "undefined-name",
    "mypy:name-defined": "undefined-name",
    "ruff:E722": "bare-except",
    "pylint:bare-except": "bare-except",
    "pylint:W0702": "bare-except",
    "ruff:F811": "redefined-name",
    "pylint:function-redefined": "redefined-name",
    "pylint:E0102": "redefined-name",
    "vulture:unreachable-code": "unreachable-code",
    "pylint:unreachable": "unreachable-code",
    "pylint:W0101": "unreachable-code",
    "eslint:no-unused-vars": "unused-variable",
    "eslint:no-undef": "undefined-name",
    "eslint:no-eval": "eval-used",
    "eslint:react/no-danger": "xss-dangerous-html",
}

# Semgrep check ids are dotted paths; the last segments name the rule.
_SEMGREP_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("debug-enabled", "flask-debug"),
    ("bad-host", "bind-all-interfaces"),
    ("bind-all", "bind-all-interfaces"),
    ("sql", "sql-injection"),
    ("hardcoded", "hardcoded-secret"),
    ("hard-coded", "hardcoded-secret"),
    ("secret", "hardcoded-secret"),
    ("password", "hardcoded-secret"),
    ("insecure-hash", "weak-hash"),
    ("md5", "weak-hash"),
    ("sha1", "weak-hash"),
    ("cert-validation", "tls-verification-disabled"),
    ("verify-false", "tls-verification-disabled"),
    ("subprocess-shell", "shell-injection"),
    ("shell-true", "shell-injection"),
    ("system-call", "shell-injection"),
    ("eval", "eval-used"),
    ("exec-detected", "exec-used"),
    ("pickle", "unsafe-deserialization"),
    ("pyyaml-load", "unsafe-yaml-load"),
    ("use-timeout", "request-without-timeout"),
    ("dangerouslysetinnerhtml", "xss-dangerous-html"),
    ("innerhtml", "xss-dangerous-html"),
    ("insecure-random", "insecure-random"),
)

_CWE_CLASSES: dict[str, str] = {
    "CWE-78": "shell-injection",
    "CWE-89": "sql-injection",
    "CWE-259": "hardcoded-secret",
    "CWE-295": "tls-verification-disabled",
    "CWE-327": "weak-hash",
    "CWE-328": "weak-hash",
    "CWE-330": "insecure-random",
    "CWE-338": "insecure-random",
    "CWE-377": "insecure-temp-file",
    "CWE-502": "unsafe-deserialization",
    "CWE-605": "bind-all-interfaces",
    "CWE-798": "hardcoded-secret",
}

_SECRET_TOOLS = frozenset({"detect-secrets", "detect_secrets", "gitleaks"})
_RUFF_BANDIT_RE = re.compile(r"^S(\d{3})$")


def issue_class(tool: str, rule_id: str, *, cwe: str = "", category: str = "") -> str:  # noqa: PLR0911
    """The canonical class a finding belongs to; findings of one class may be one issue."""
    rule = (rule_id or "").strip()
    if tool == "bandit" and rule in _BANDIT_CLASSES:
        return _BANDIT_CLASSES[rule]
    if tool == "ruff":
        m = _RUFF_BANDIT_RE.match(rule)
        if m and f"B{m.group(1)}" in _BANDIT_CLASSES:
            return _BANDIT_CLASSES[f"B{m.group(1)}"]
    if tool in _SECRET_TOOLS or category == "secrets":
        return "hardcoded-secret"
    mapped = _LINT_CLASSES.get(f"{tool}:{rule}")
    if mapped:
        return mapped
    if tool == "semgrep" and rule:
        tail = ".".join(rule.lower().split(".")[-2:])
        for keyword, cls in _SEMGREP_KEYWORDS:
            if keyword in tail:
                return cls
    if cwe:
        return _CWE_CLASSES.get(cwe.upper(), cwe.lower())
    return f"{tool}:{rule or 'unknown'}"


@dataclass
class IssueCluster:
    """One deduplicated issue: every finding (from one or more tools) that reports it."""

    run_id: str
    issue_class: str
    file_path: str
    line: int | None
    members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tools(self) -> list[str]:
        return sorted({m["tool"] for m in self.members})

    @property
    def severity(self) -> str:
        return max((m["severity"] for m in self.members), key=lambda s: SEVERITY_RANK.get(s, 0))

    @property
    def category(self) -> str:
        cats = {m.get("category") or "" for m in self.members}
        for preferred in ("security", "secrets"):
            if preferred in cats:
                return preferred
        return sorted(cats)[0] if cats else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "issue_class": self.issue_class,
            "file_path": self.file_path,
            "line": self.line,
            "severity": self.severity,
            "category": self.category,
            "tools": self.tools,
            "n_findings": len(self.members),
            "rule_ids": sorted({f"{m['tool']}:{m.get('rule_id') or ''}" for m in self.members}),
            "finding_ids": [str(m["id"]) for m in self.members if m.get("id") is not None],
        }


def _fits(cluster: IssueCluster, f: dict[str, Any], tolerance: int) -> bool:
    line = f.get("line")
    for m in cluster.members:
        m_line = m.get("line")
        if m["tool"] == f["tool"]:
            # Same tool: only an exact duplicate merges; any other report by
            # that tool is a distinct issue it found.
            if m_line != line or (m.get("rule_id") or "") != (f.get("rule_id") or ""):
                return False
            continue
        if line is None or m_line is None:
            # File-level findings only merge with other file-level ones.
            if (line is None) != (m_line is None):
                return False
            continue
        lo, hi = m_line, max(m_line, m.get("end_line") or m_line)
        if not (lo - tolerance <= line <= hi + tolerance):
            return False
    return True


def cluster_findings(
    findings: list[dict[str, Any]],
    *,
    line_tolerance: int = DEFAULT_LINE_TOLERANCE,
) -> list[IssueCluster]:
    """Group *findings* into deduplicated issues.

    Each finding is a dict with ``id, run_id, tool, rule_id, severity,
    category, file_path, line`` and optionally ``end_line, cwe``. Findings
    of different runs (different apps) never merge.
    """
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for f in findings:
        cls = issue_class(f["tool"], f.get("rule_id") or "", cwe=f.get("cwe") or "", category=f.get("category") or "")
        buckets[(str(f["run_id"]), f.get("file_path") or "", cls)].append(f)

    clusters: list[IssueCluster] = []
    for (run_id, path, cls), items in buckets.items():
        items.sort(key=lambda f: (f.get("line") is None, f.get("line") or 0, f["tool"]))
        open_clusters: list[IssueCluster] = []
        for f in items:
            target = next((c for c in open_clusters if _fits(c, f, line_tolerance)), None)
            if target is None:
                target = IssueCluster(run_id=run_id, issue_class=cls, file_path=path, line=f.get("line"))
                open_clusters.append(target)
            target.members.append(f)
        clusters.extend(open_clusters)
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
