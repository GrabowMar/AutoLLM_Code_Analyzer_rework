"""Cross-tool deduplication (dedup-v1) and AI/static overlap."""

from __future__ import annotations

import pytest

from backend.analysis.engine import dedup
from backend.analysis.engine.types import NormalizedFinding
from backend.analysis.models import Finding
from backend.analysis.tests.support import measure as fx


def nf(tool, rule, line, *, path="app.py", cwe=(), category="security", severity="medium", **kwargs):
    return NormalizedFinding(
        tool_slug=tool,
        rule_id=rule,
        title=f"{tool} {rule}",
        severity=severity,
        native_severity="",
        category=category,
        cwe=list(cwe),
        confidence="medium",
        file_path=path,
        line=line,
        end_line=kwargs.pop("end_line", line),
        column=None,
        fingerprint=kwargs.pop("fingerprint", f"fp-{tool}-{rule}-{path}-{line}"),
        **kwargs,
    )


def keys(findings):
    return [f.issue_key for f in findings]


def test_cross_tool_same_issue_by_cwe_and_equivalence():
    findings = [
        nf("bandit", "B608", 10, cwe=["CWE-89"]),
        nf("semgrep", "x.flask.security.tainted-sql-string.tainted-sql-string", 11, cwe=["CWE-89"]),
        nf("ruff", "S608", 10),  # no CWE on the finding: joins through the equivalence class
    ]
    assert dedup.assign_issue_keys(findings) == 1
    assert len(set(keys(findings))) == 1
    assert all(len(k) == 64 for k in keys(findings))


def test_same_tool_never_merges_across_lines_and_chains_stop():
    findings = [
        nf("ruff", "F401", 1, category="dead_code"),
        nf("ruff", "F401", 2, category="dead_code"),
        nf("pylint", "W0611", 1, category="dead_code"),
        nf("pylint", "W0611", 2, category="dead_code"),
    ]
    assert dedup.assign_issue_keys(findings) == 2
    ruff1, ruff2, pylint1, pylint2 = findings
    assert ruff1.issue_key == pylint1.issue_key
    assert ruff2.issue_key == pylint2.issue_key
    assert ruff1.issue_key != ruff2.issue_key


def test_same_tool_same_line_merges_only_when_related():
    findings = [
        nf("semgrep", "a.subprocess-shell-true", 8, cwe=["CWE-78"]),
        nf("semgrep", "a.dangerous-subprocess-use", 8, cwe=["CWE-78"]),
        nf("semgrep", "a.unrelated-style", 8, category="maintainability"),
    ]
    assert dedup.assign_issue_keys(findings) == 2
    assert findings[0].issue_key == findings[1].issue_key != findings[2].issue_key


def test_window_ranges_and_paths():
    far = [nf("bandit", "B602", 10, cwe=["CWE-78"]), nf("semgrep", "x.y", 13, cwe=["CWE-78"])]
    assert dedup.assign_issue_keys(far) == 2
    assert dedup.assign_issue_keys(far, line_window=3) == 1
    ranged = [nf("bandit", "B602", 10, cwe=["CWE-78"]), nf("semgrep", "x.y", 5, end_line=20, cwe=["CWE-78"])]
    assert dedup.assign_issue_keys(ranged) == 1
    other_file = [nf("bandit", "B602", 10, cwe=["CWE-78"]), nf("semgrep", "x.y", 10, path="b.py", cwe=["CWE-78"])]
    assert dedup.assign_issue_keys(other_file) == 2
    unrelated = [nf("bandit", "B602", 10, cwe=["CWE-78"]), nf("semgrep", "x.y", 10, cwe=["CWE-89"])]
    assert dedup.assign_issue_keys(unrelated) == 2


def test_secret_scanners_and_password_checks_merge():
    findings = [
        nf("gitleaks", "generic-api-key", 6, cwe=["CWE-798"], category="secrets"),
        nf("detect-secrets", "Secret Keyword", 6, cwe=["CWE-798"], category="secrets"),
        nf("bandit", "B105", 6, cwe=["CWE-259"], category="secrets"),
        nf("gitleaks", "generic-api-key", 7, cwe=["CWE-798"], category="secrets"),
        nf("detect-secrets", "Secret Keyword", 7, cwe=["CWE-798"], category="secrets"),
    ]
    assert dedup.assign_issue_keys(findings) == 2
    assert len({f.issue_key for f in findings[:3]}) == 1
    assert findings[3].issue_key == findings[4].issue_key != findings[0].issue_key


def test_counted_and_uncounted_are_separate_namespaces():
    counted = nf("bandit", "B602", 8, cwe=["CWE-78"])
    uncounted = nf("semgrep", "x.y", 8, cwe=["CWE-78"], counted=False)
    style = nf("ruff", "E501", 30, category="style", counted=False)
    assert dedup.assign_issue_keys([counted, uncounted, style]) == 1
    assert counted.issue_key != uncounted.issue_key
    assert uncounted.issue_key
    assert style.issue_key


def test_file_level_findings():
    findings = [
        nf("mypy", "misc", None, category="type"),
        nf("pylint", "E0602", None, category="bug"),
        nf("ruff", "F821", 3, category="bug"),
    ]
    assert dedup.assign_issue_keys(findings) == 3


def test_ai_findings_clustered_separately_and_consensus_keys_kept():
    keyed = nf("ai_review", "security", 8, cwe=["CWE-78"], is_ai=True, sample_index=0, issue_key="consensus-key")
    a0 = nf("ai_review", "bug", 20, category="bug", is_ai=True, sample_index=0, counted=False)
    a1 = nf("ai_review", "bug", 22, category="bug", is_ai=True, sample_index=1, counted=False)
    a1_far = nf("ai_review", "bug", 26, category="bug", is_ai=True, sample_index=1, counted=False)
    static = nf("bandit", "B602", 8, cwe=["CWE-78"])
    assert dedup.assign_issue_keys([keyed, a0, a1, a1_far, static]) == 1
    assert keyed.issue_key == "consensus-key"
    assert a0.issue_key == a1.issue_key != a1_far.issue_key
    assert static.issue_key not in {a0.issue_key, keyed.issue_key}


def test_keys_are_order_independent():
    def build():
        return [
            nf("bandit", "B608", 10, cwe=["CWE-89"]),
            nf("semgrep", "x.tainted-sql-string", 11, cwe=["CWE-89"]),
            nf("ruff", "F401", 1, category="dead_code"),
            nf("pylint", "W0611", 1, category="dead_code"),
            nf("ruff", "F401", 2, category="dead_code"),
            nf("eslint", "no-eval", 4, path="a.js", cwe=["CWE-95"]),
        ]

    reference = build()
    dedup.assign_issue_keys(reference)
    expected = {(f.tool_slug, f.rule_id, f.line): f.issue_key for f in reference}
    for order in ([5, 4, 3, 2, 1, 0], [2, 0, 5, 1, 4, 3], [3, 5, 0, 4, 2, 1]):
        built = build()
        shuffled = [built[i] for i in order]
        dedup.assign_issue_keys(shuffled)
        assert {(f.tool_slug, f.rule_id, f.line): f.issue_key for f in shuffled} == expected


def test_identical_lines_in_one_file_stay_distinct_issues():
    # Same fingerprint (same line text), different locations: two issues.
    a = nf("bandit", "B110", 10, fingerprint="same", category="bug")
    b = nf("bandit", "B110", 50, fingerprint="same", category="bug")
    assert dedup.assign_issue_keys([a, b]) == 2


def test_accepts_dicts():
    rows = [
        {"tool_slug": "bandit", "rule_id": "B602", "line_number": 8, "cwe": ["CWE-78"], "counted": True, "id": 1},
        {"tool_slug": "semgrep", "rule_id": "x", "line_number": 9, "cwe": ["CWE-78"], "counted": True, "id": 2},
    ]
    for row in rows:
        row.update(file_path="app.py", is_ai=False, fingerprint="", end_line=None, issue_key="")
    assert dedup.assign_issue_keys(rows) == 1
    assert rows[0]["issue_key"] == rows[1]["issue_key"]


@pytest.mark.django_db
def test_assign_run_issue_keys_updates_rows():
    run = fx.make_run()
    bandit = fx.add_result(run, "bandit", category="security")
    semgrep = fx.add_result(run, "semgrep", category="security")
    ai = fx.add_result(run, "ai_review", kind="ai", category="ai")
    fx.add_finding(bandit, rule_id="B602", line_number=8, cwe=["CWE-78"], category="security")
    fx.add_finding(semgrep, rule_id="x.y", line_number=9, cwe=["CWE-78"], category="security")
    fx.add_finding(ai, rule_id="security", line_number=8, cwe=["CWE-78"], is_ai=True, issue_key="kept")
    assert dedup.assign_run_issue_keys(run) == 1
    static_keys = set(Finding.objects.filter(run=run, is_ai=False).values_list("issue_key", flat=True))
    assert len(static_keys) == 1
    assert "" not in static_keys
    assert Finding.objects.get(run=run, is_ai=True).issue_key == "kept"


def test_issue_summary_modes():
    members = [
        nf("ai_review", "bug", 5, severity="low", category="bug"),
        nf("ai_review", "bug", 6, severity="high", category="security"),
        nf("ai_review", "bug", 7, severity="low", category="bug"),
    ]
    assert dedup.issue_summary(members)["severity"] == "high"
    assert dedup.issue_summary(members)["category"] == "security"
    consensus = dedup.issue_summary(members, mode="consensus")
    assert (consensus["severity"], consensus["category"]) == ("low", "bug")
    assert (consensus["line"], consensus["end_line"], consensus["size"]) == (5, 7, 3)


def test_ai_static_overlap():
    static = [
        nf("bandit", "B602", 8, cwe=["CWE-78"], issue_key="s1"),
        nf("semgrep", "x", 9, cwe=["CWE-78"], issue_key="s1"),
        nf("ruff", "F401", 1, category="dead_code", issue_key="s2"),
        nf("ruff", "E501", 2, category="style", issue_key="s3", counted=False),
    ]
    ai = [
        nf("ai_review", "security", 10, cwe=["CWE-78"], is_ai=True, issue_key="a1", sample_index=0),
        nf("ai_review", "security", 10, cwe=["CWE-78"], is_ai=True, issue_key="a1", sample_index=1),
        nf("ai_review", "bug", 40, category="bug", is_ai=True, issue_key="a2"),
        nf("ai_review", "bug", 50, category="bug", is_ai=True, issue_key="a3", counted=False),
    ]
    overlap = dedup.ai_static_overlap(static, ai)
    assert (overlap["static_issues"], overlap["ai_issues"], overlap["matched"]) == (2, 2, 1)
    assert overlap["jaccard"] == pytest.approx(1 / 3, abs=1e-4)
    assert overlap["ai_confirmed_share"] == 0.5
    assert overlap["static_found_by_ai_share"] == 0.5
    assert overlap["by_category"]["security"] == {"static": 1, "ai": 1, "matched": 1}
    empty = dedup.ai_static_overlap([], [])
    assert empty["jaccard"] is None
    assert empty["ai_confirmed_share"] is None


# Rule ids semgrep 1.182 reported on the audit fixture (maps/tools.md), paired
# with the bandit test that flags the same line.
@pytest.mark.parametrize(
    ("semgrep_id", "bandit_rule", "semgrep_cwe", "bandit_cwe"),
    [
        (
            "python.django.security.injection.sql.sql-injection-using-db-cursor-execute.sql-injection-db-cursor-execute",
            "B608",
            "CWE-89",
            "CWE-89",
        ),
        ("python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host", "B104", "CWE-668", "CWE-605"),
        ("python.flask.security.audit.debug-enabled.debug-enabled", "B201", "CWE-489", "CWE-94"),
        ("python.lang.security.audit.subprocess-shell-true.subprocess-shell-true", "B602", "CWE-78", "CWE-78"),
        (
            "python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5",
            "B324",
            "CWE-327",
            "CWE-327",
        ),
        ("python.lang.security.audit.eval-detected.eval-detected", "B307", "CWE-95", "CWE-78"),
    ],
)
def test_real_semgrep_ids_dedup_with_bandit(semgrep_id, bandit_rule, semgrep_cwe, bandit_cwe):
    findings = [nf("semgrep", semgrep_id, 36, cwe=[semgrep_cwe]), nf("bandit", bandit_rule, 36, cwe=[bandit_cwe])]
    assert dedup.assign_issue_keys(findings) == 1
