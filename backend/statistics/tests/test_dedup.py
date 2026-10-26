"""Cross-tool deduplication of findings."""

from __future__ import annotations

from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.dedup import issue_class
from backend.statistics.services.dedup import tool_overlap


def _f(tool, rule, line, *, run="r1", path="app.py", severity="medium", category="security", **kw):
    return {
        "id": f"{tool}-{rule}-{line}-{run}",
        "run_id": run,
        "tool": tool,
        "rule_id": rule,
        "severity": severity,
        "category": category,
        "file_path": path,
        "line": line,
        **kw,
    }


def test_issue_class_equivalences():
    assert issue_class("bandit", "B105") == "hardcoded-secret"
    assert issue_class("ruff", "S105") == "hardcoded-secret"
    assert issue_class("gitleaks", "generic-api-key") == "hardcoded-secret"
    assert issue_class("semgrep", "python.flask.security.audit.debug-enabled.debug-enabled") == "flask-debug"
    assert issue_class("bandit", "B201") == "flask-debug"
    assert issue_class("pylint", "unused-import") == issue_class("ruff", "F401") == "unused-import"
    assert issue_class("semgrep", "some.unmapped.rule", cwe="CWE-89") == "sql-injection"
    # Unknown rules never merge across tools.
    assert issue_class("eslint", "semi") == "eslint:semi"


def test_same_issue_from_three_tools_is_one_cluster():
    findings = [
        _f("bandit", "B201", 40, severity="high"),
        _f("semgrep", "python.flask.security.audit.debug-enabled.debug-enabled", 41),
        _f("ruff", "S201", 40, severity="low"),
    ]
    clusters = cluster_findings(findings)
    assert len(clusters) == 1
    assert clusters[0].tools == ["bandit", "ruff", "semgrep"]
    assert clusters[0].severity == "high"


def test_distance_file_and_run_keep_issues_apart():
    findings = [
        _f("bandit", "B105", 10),
        _f("semgrep", "python.lang.security.audit.hardcoded-password", 30),
        _f("bandit", "B105", 10, path="models.py"),
        _f("bandit", "B105", 10, run="r2"),
    ]
    assert len(cluster_findings(findings)) == 4


def test_one_tool_adjacent_findings_stay_separate_but_exact_duplicates_merge():
    findings = [_f("bandit", "B105", 10), _f("bandit", "B105", 11), _f("bandit", "B105", 10)]
    clusters = cluster_findings(findings)
    assert sorted(len(c.members) for c in clusters) == [1, 2]


def test_semgrep_range_counts_for_tolerance():
    findings = [
        _f("semgrep", "python.lang.security.audit.formatted-sql-query", 10, end_line=20),
        _f("bandit", "B608", 18),
    ]
    assert len(cluster_findings(findings, line_tolerance=0)) == 1


def test_tool_overlap_counts_and_agreement():
    findings = [
        _f("bandit", "B105", 10),
        _f("semgrep", "python.jwt.security.jwt-hardcode.jwt-python-hardcoded-secret", 10),
        _f("bandit", "B105", 50),
        _f("ruff", "F401", 1, category="quality"),
    ]
    overlap = tool_overlap(cluster_findings(findings))
    tools = {t["tool"]: t for t in overlap["tools"]}
    assert tools["bandit"] == {"tool": "bandit", "raw_findings": 2, "unique_issues": 2, "exclusive": 1, "shared": 1}
    pair = next(p for p in overlap["pairs"] if {p["tool_a"], p["tool_b"]} == {"bandit", "semgrep"})
    assert pair["both"] == 1
    assert pair["agreement"] == 0.5
    # ruff shares no issue class with bandit here: no pair row.
    assert not any("ruff" in (p["tool_a"], p["tool_b"]) for p in overlap["pairs"])
    assert overlap["multi_tool_issues"] == 1
