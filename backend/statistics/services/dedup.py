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

import posixpath
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
    "flake8:F401": "unused-import",
    "flake8:F841": "unused-variable",
    "flake8:F821": "undefined-name",
    "flake8:E722": "bare-except",
    "flake8:F811": "redefined-name",
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
    # Keys are the reporting tool's slug: react/no-danger comes from eslint-react
    # (the base eslint tool does not enable it), security/* from eslint-security.
    "eslint-react:react/no-danger": "xss-dangerous-html",
    "eslint-security:security/detect-eval-with-expression": "eval-used",
    "eslint-security:security/detect-child-process": "shell-injection",
    "eslint-security:security/detect-pseudoRandomBytes": "insecure-random",
    "eslint-security:security/detect-non-literal-regexp": "redos",
    "eslint-security:security/detect-unsafe-regex": "redos",
    "eslint-security:security/detect-non-literal-fs-filename": "path-traversal",
}

# Semgrep check ids are dotted paths; the last segments name the rule.
# Keywords match whole hyphen/underscore-separated tokens, first match wins,
# so the specific ones (md5, sha1) come before the generic ones (secret).
_SEMGREP_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("debug-enabled", "flask-debug"),
    ("bad-host", "bind-all-interfaces"),
    ("bind-all", "bind-all-interfaces"),
    ("insecure-hash", "weak-hash"),
    ("md5", "weak-hash"),
    ("sha1", "weak-hash"),
    ("sql", "sql-injection"),
    ("sqli", "sql-injection"),
    ("sqlalchemy-execute-raw-query", "sql-injection"),
    ("formatted-sql-query", "sql-injection"),
    ("hardcoded", "hardcoded-secret"),
    ("hard-coded", "hardcoded-secret"),
    ("secret", "hardcoded-secret"),
    ("cert-validation", "tls-verification-disabled"),
    ("verify-false", "tls-verification-disabled"),
    ("subprocess-shell", "shell-injection"),
    ("child-process", "shell-injection"),
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
    ("non-literal-regexp", "redos"),
    ("path-traversal", "path-traversal"),
)

_CWE_CLASSES: dict[str, str] = {
    "CWE-22": "path-traversal",
    "CWE-78": "shell-injection",
    "CWE-89": "sql-injection",
    "CWE-95": "eval-used",
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
    "CWE-1333": "redos",
}

_SECRET_TOOLS = frozenset({"detect-secrets", "detect_secrets", "gitleaks"})
# Known-vulnerability scanners. They name one advisory differently (PYSEC /
# GHSA / CVE ids) and point at different manifests of one project
# (requirements.txt, package.json vs package-lock.json), so their findings
# match on the vulnerable package within the project directory instead.
_VULN_TOOLS = frozenset({"pip-audit", "npm-audit", "osv-scanner", "trivy", "retire"})
_VULN_PREFIX = "vulnerable-dependency:"
_RUFF_BANDIT_RE = re.compile(r"^S(\d{3})$")
_TOKEN_SPLIT_RE = re.compile(r"[._\-]+")


def _has_tokens(tokens: list[str], needle: list[str]) -> bool:
    """Whether *needle* occurs as a contiguous run of whole tokens in *tokens*."""
    n = len(needle)
    return any(tokens[i : i + n] == needle for i in range(len(tokens) - n + 1))


def advisory_ids(finding: dict[str, Any]) -> set[str]:
    """The advisory ids (rule id plus CVE/GHSA/PYSEC aliases) a vulnerability finding names."""
    ids = {finding.get("rule_id") or "", finding.get("vuln_id") or ""}
    ids.update(a for a in finding.get("aliases") or [] if isinstance(a, str))
    return {i.strip().upper() for i in ids if i and i.strip()}


def issue_class(  # noqa: PLR0911
    tool: str,
    rule_id: str,
    *,
    cwe: str = "",
    category: str = "",
    package: str = "",
    issue_key: str = "",
) -> str:
    """The canonical class a finding belongs to; findings of one class may be one issue.

    *issue_key* is a class a parser already knows (``tool_specific_data``),
    e.g. ``missing-header:x-frame-options`` from the DAST scanners.
    """
    if issue_key:
        return issue_key
    rule = (rule_id or "").strip()
    if tool in _VULN_TOOLS and category == "dependency" and package and not rule.startswith("nonexistent-"):
        return _VULN_PREFIX + package.strip().lower()
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
        tokens = _TOKEN_SPLIT_RE.split(".".join(rule.lower().split(".")[-2:]))
        for keyword, cls in _SEMGREP_KEYWORDS:
            if _has_tokens(tokens, keyword.split("-")):
                return cls
    if (tool == "radon" and rule in ("CC-D", "CC-E", "CC-F")) or (tool == "lizard" and rule == "CCN"):
        # Both flag one over-complex function (radon ranks D-F, lizard's CCN threshold).
        return "high-complexity"
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


def _same_report(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two findings of one tool are the same report (an exact duplicate)."""
    if a.get("line") != b.get("line") or (a.get("rule_id") or "") != (b.get("rule_id") or ""):
        return False
    # File-level findings (no line) of one rule can still name different
    # things: three hallucinated packages, five unused dependencies.
    return (a.get("package") or "") == (b.get("package") or "") and (
        a.get("line") is not None or (a.get("title") or "") == (b.get("title") or "")
    )


def _fits(cluster: IssueCluster, f: dict[str, Any], tolerance: int) -> bool:
    line = f.get("line")
    if cluster.issue_class.startswith(_VULN_PREFIX):
        # One package can have many advisories: tools agree only when they name
        # the same one (under any of its ids, linked through the members' aliases).
        known = set().union(*(advisory_ids(m) for m in cluster.members if m["tool"] != f["tool"]))
        if known and not known & advisory_ids(f):
            return False
    for m in cluster.members:
        m_line = m.get("line")
        if m["tool"] == f["tool"]:
            # Same tool: only an exact duplicate merges; any other report by
            # that tool is a distinct issue it found.
            if not _same_report(m, f):
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


# The Finding.values() fields :func:`finding_record` reads.
RECORD_FIELDS = (
    "id",
    "result__tool_slug",
    "rule_id",
    "severity",
    "category",
    "file_path",
    "line_number",
    "tool_specific_data",
    "title",
)


def finding_record(row: dict[str, Any], run_id: str) -> dict[str, Any]:
    """A :func:`cluster_findings` record from a ``Finding.objects.values(*RECORD_FIELDS)`` row."""
    extra = row["tool_specific_data"] if isinstance(row["tool_specific_data"], dict) else {}
    return {
        "id": row["id"],
        "run_id": run_id,
        "tool": row["result__tool_slug"],
        "rule_id": row["rule_id"],
        "severity": row["severity"],
        "category": row["category"],
        "file_path": row["file_path"],
        "line": row["line_number"],
        "end_line": extra.get("end_line") or extra.get("endline"),
        "cwe": extra.get("cwe") or "",
        "package": extra.get("package") or "",
        "vuln_id": extra.get("vuln_id") or "",
        "aliases": extra.get("aliases") or [],
        "issue_key": extra.get("issue_key") or "",
        "title": row["title"],
    }


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
        cls = issue_class(
            f["tool"],
            f.get("rule_id") or "",
            cwe=f.get("cwe") or "",
            category=f.get("category") or "",
            package=f.get("package") or "",
            issue_key=f.get("issue_key") or "",
        )
        path = f.get("file_path") or ""
        if cls.startswith(_VULN_PREFIX):
            # One project, several manifests: match within the directory.
            path = posixpath.dirname(path)
        buckets[(str(f["run_id"]), path, cls)].append(f)

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


def _comparable_key(cluster: IssueCluster) -> tuple[str, str]:
    return cluster.issue_class, posixpath.splitext(cluster.file_path)[1].lower()


def tool_overlap(clusters: list[IssueCluster]) -> dict[str, Any]:
    """Per-tool unique/exclusive counts and pairwise co-detection between tools.

    ``agreement`` for a pair is the Jaccard index of their clusters,
    restricted to issue classes *both* tools reported somewhere in the
    data: a pair that cannot detect the same things is not a disagreement.
    """
    # Comparable = same class on the same kind of file: ESLint's and Vulture's
    # "unused-variable" never look at the same code, so they cannot disagree.
    classes_by_tool: dict[str, set[tuple[str, str]]] = defaultdict(set)
    per_tool: dict[str, Counter] = defaultdict(Counter)
    for c in clusters:
        tools = c.tools
        for t in tools:
            classes_by_tool[t].add(_comparable_key(c))
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
            if _comparable_key(c) not in comparable:
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
                "comparable_classes": sorted({cls for cls, _ext in comparable}),
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
