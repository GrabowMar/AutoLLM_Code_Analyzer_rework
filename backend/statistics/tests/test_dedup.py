"""Study issue clusters: the analysis engine's deduplication (issue_key), never a second rule set."""

from __future__ import annotations

import pytest

from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import FindingFactory
from backend.rankings.tests.measured import make_run
from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.dedup import issue_class
from backend.statistics.services.dedup import load_records
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


def test_issue_class_uses_the_engine_equivalence_table():
    assert issue_class("bandit", "B105") == issue_class("ruff", "S105") == "hardcoded-credentials"
    assert issue_class("bandit", "B201") == "flask-debug"
    assert issue_class("semgrep", "python.flask.security.audit.debug-enabled.debug-enabled") == "flask-debug"
    assert issue_class("pylint", "unused-import") == issue_class("ruff", "F401") == "unused-import"
    assert issue_class("semgrep", "some.unmapped.rule", cwe=["cwe-89"]) == "CWE-89"
    # Unknown rules never read as a shared class.
    assert issue_class("eslint", "semi") == "eslint:semi"


def test_keyed_findings_follow_the_stored_issue_key():
    # The engine put these three into one issue and the fourth apart: that grouping
    # stands, even where a distance rule of our own would have decided otherwise.
    findings = [
        _f("bandit", "B201", 40, severity="high", issue_key="k1"),
        _f("semgrep", "python.flask.security.audit.debug-enabled.debug-enabled", 48, issue_key="k1"),
        _f("ruff", "S201", 40, severity="low", issue_key="k1"),
        _f("bandit", "B201", 41, issue_key="k2"),
    ]
    clusters = cluster_findings(findings)
    assert sorted(len(c.members) for c in clusters) == [1, 3]
    big = next(c for c in clusters if c.issue_key == "k1")
    assert big.tools == ["bandit", "ruff", "semgrep"]
    assert big.severity == "high"
    assert big.issue_class == "flask-debug"
    assert big.to_dict()["issue_key"] == "k1"


def test_unkeyed_findings_use_the_engine_algorithm():
    findings = [
        _f("bandit", "B201", 40, severity="high"),
        _f("semgrep", "python.flask.security.audit.debug-enabled.debug-enabled", 41),
        _f("ruff", "S201", 40, severity="low"),
        # Same tool on adjacent lines: two issues; another file or run: apart.
        _f("bandit", "B105", 10),
        _f("bandit", "B105", 11),
        _f("bandit", "B105", 10, path="models.py"),
        _f("bandit", "B105", 10, run="r2"),
    ]
    clusters = cluster_findings(findings)
    assert len(clusters) == 5
    debug = next(c for c in clusters if c.issue_class == "flask-debug")
    assert debug.tools == ["bandit", "ruff", "semgrep"]


def test_bandit_evidence_decides_over_a_ruff_proxy_severity():
    findings = [
        _f("bandit", "B602", 5, severity="low", issue_key="k"),
        _f("ruff", "S602", 5, severity="high", issue_key="k", tool_specific_data={"severity_basis": "bandit_default"}),
    ]
    assert cluster_findings(findings)[0].severity == "low"


def test_tool_overlap_counts_and_agreement():
    findings = [
        _f("bandit", "B105", 10, issue_key="a"),
        _f("semgrep", "python.jwt.security.jwt-hardcode.jwt-python-hardcoded-secret", 10, issue_key="a"),
        _f("bandit", "B105", 50, issue_key="b"),
        _f("ruff", "F401", 1, category="maintainability", issue_key="c"),
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


@pytest.mark.django_db
def test_load_records_reads_counted_static_or_ai_apart():
    run = make_run(tools={"bandit": ToolResult.Status.SUCCEEDED, "ai_review": ToolResult.Status.SUCCEEDED})
    bandit = run.results.get(tool_slug="bandit")
    ai = run.results.get(tool_slug="ai_review")
    FindingFactory(result=bandit, rule_id="B105", issue_key="s1", cwe=["CWE-798"])
    FindingFactory(result=bandit, rule_id="E501", category="style", counted=False)
    FindingFactory(result=ai, is_ai=True, counted=True, is_consensus=True, issue_key="a1")

    static = load_records([run.id])
    assert [(r["tool"], r["rule_id"], r["issue_key"], r["cwe"]) for r in static] == [
        ("bandit", "B105", "s1", ["CWE-798"]),
    ]
    assert [r["tool"] for r in load_records([run.id], ai=True)] == ["ai_review"]
    assert len(load_records([run.id], counted_only=False)) == 2
    assert load_records([]) == []
