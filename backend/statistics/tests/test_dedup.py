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


def test_flake8_and_ruff_unused_import_is_one_issue():
    findings = [
        {
            "id": 1,
            "run_id": "r",
            "tool": "ruff",
            "rule_id": "F401",
            "severity": "low",
            "file_path": "app.py",
            "line": 3,
        },
        {
            "id": 2,
            "run_id": "r",
            "tool": "flake8",
            "rule_id": "F401",
            "severity": "low",
            "file_path": "app.py",
            "line": 3,
        },
    ]
    assert len(cluster_findings(findings)) == 1


def test_vulnerable_package_from_several_scanners_is_one_issue():
    """pip-audit (PYSEC id), osv-scanner (GHSA) and trivy (CVE) name one advisory differently."""
    base = {"run_id": "r", "severity": "medium", "category": "dependency", "line": None, "package": "Jinja2"}
    findings = [
        {**base, "id": 1, "tool": "pip-audit", "rule_id": "PYSEC-2024-1", "file_path": "requirements.txt"},
        {
            **base,
            "id": 2,
            "tool": "osv-scanner",
            "rule_id": "GHSA-xxxx",
            "aliases": ["CVE-2024-1", "PYSEC-2024-1"],
            "file_path": "requirements.txt",
        },
        {**base, "id": 3, "tool": "trivy", "rule_id": "CVE-2024-1", "file_path": "requirements.txt"},
        {
            **base,
            "id": 4,
            "tool": "npm-audit",
            "rule_id": "GHSA-a",
            "file_path": "frontend/package.json",
            "package": "vite",
        },
        {
            **base,
            "id": 5,
            "tool": "osv-scanner",
            "rule_id": "GHSA-a",
            "file_path": "frontend/package-lock.json",
            "package": "vite",
        },
    ]
    clusters = cluster_findings(findings)
    assert sorted(len(c.members) for c in clusters) == [2, 3]


def test_different_advisories_of_one_package_stay_apart():
    """Package alone is not enough: two scanners reporting different advisories are two issues."""
    base = {"run_id": "r", "severity": "", "category": "dependency", "line": None, "package": "pyjwt"}
    findings = [
        {**base, "id": 1, "tool": "osv-scanner", "rule_id": "PYSEC-1", "aliases": ["CVE-1"], "file_path": "r.txt"},
        {**base, "id": 2, "tool": "pip-audit", "rule_id": "PYSEC-2", "aliases": ["CVE-2"], "file_path": "r.txt"},
        {**base, "id": 3, "tool": "trivy", "rule_id": "CVE-1", "file_path": "r.txt"},
    ]
    assert sorted(len(c.members) for c in cluster_findings(findings)) == [1, 2]


def test_file_level_findings_naming_different_packages_stay_apart():
    base = {"run_id": "r", "severity": "", "category": "dependency", "line": None, "file_path": "requirements.txt"}
    findings = [
        {**base, "id": i, "tool": "deptry", "rule_id": "DEP002", "title": f"'{p}' defined but not used", "package": p}
        for i, p in enumerate(["flask-cors", "gunicorn", "requests"])
    ]
    assert len(cluster_findings(findings)) == 3


def test_semgrep_classes_match_whole_tokens():
    assert (
        issue_class("semgrep", "python.lang.security.audit.md5-used-as-password.md5-used-as-password") == "weak-hash"
    )
    assert issue_class("semgrep", "x.app-run-param-config.avoid_app_run_with_bad_host") == "bind-all-interfaces"
    assert not issue_class("semgrep", "python.foo.retrieval.retrieval-step").startswith("eval")


def test_dast_scanners_reporting_one_missing_header_is_one_issue():
    base = {"run_id": "r", "severity": "low", "category": "security", "line": None, "file_path": "/"}
    findings = [
        {**base, "id": 1, "tool": "zap", "rule_id": "10038", "issue_key": "missing-header:content-security-policy"},
        {**base, "id": 2, "tool": "wapiti", "rule_id": "csp", "issue_key": "missing-header:content-security-policy"},
        {
            **base,
            "id": 3,
            "tool": "nuclei",
            "rule_id": "http-missing-security-headers",
            "issue_key": "missing-header:content-security-policy",
        },
        # Same Nuclei template, another header: a separate issue.
        {
            **base,
            "id": 4,
            "tool": "nuclei",
            "rule_id": "http-missing-security-headers",
            "issue_key": "missing-header:x-frame-options",
        },
    ]
    assert sorted(len(c.members) for c in cluster_findings(findings)) == [1, 3]


def test_eslint_plugin_rules_map_under_their_reporting_tool_slug():
    # react/no-danger is reported by the eslint-react tool, security/* by eslint-security.
    assert issue_class("eslint-react", "react/no-danger") == "xss-dangerous-html"
    assert (
        issue_class("semgrep", "javascript.react.security.audit.react-dangerouslysetinnerhtml") == "xss-dangerous-html"
    )
    assert (
        issue_class("eslint-security", "security/detect-eval-with-expression", cwe="CWE-95")
        == issue_class("semgrep", "javascript.browser.security.eval-detected.eval-detected")
        == "eval-used"
    )
    assert (
        issue_class("eslint-security", "security/detect-child-process", cwe="CWE-78")
        == issue_class("semgrep", "javascript.lang.security.detect-child-process.detect-child-process")
        == "shell-injection"
    )
    assert (
        issue_class("eslint-security", "security/detect-non-literal-regexp", cwe="CWE-1333")
        == issue_class("semgrep", "javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp")
        == "redos"
    )
    assert issue_class("eslint-security", "security/detect-pseudoRandomBytes") == "insecure-random"
    assert issue_class("eslint-security", "security/detect-non-literal-fs-filename") == "path-traversal"


def test_eslint_security_and_semgrep_eval_is_one_issue():
    clusters = cluster_findings(
        [
            _f("eslint-security", "security/detect-eval-with-expression", 4, path="server.js", cwe="CWE-95"),
            _f("semgrep", "javascript.browser.security.eval-detected.eval-detected", 4, path="server.js"),
        ],
    )
    assert len(clusters) == 1
    assert clusters[0].tools == ["eslint-security", "semgrep"]


def test_one_tool_reporting_one_advisory_under_two_ids_is_one_issue():
    """pip-audit prints CVE-2024-49767 for werkzeug as PYSEC-2026-1860 and PYSEC-2026-3417 (linked aliases)."""
    base = {"run_id": "r", "severity": "medium", "category": "dependency", "line": None, "package": "werkzeug"}
    findings = [
        {
            **base,
            "id": 1,
            "tool": "pip-audit",
            "rule_id": "PYSEC-2026-1860",
            "aliases": ["CVE-2024-49767", "PYSEC-2026-3417"],
            "file_path": "requirements.txt",
        },
        {**base, "id": 2, "tool": "pip-audit", "rule_id": "PYSEC-2026-3417", "file_path": "requirements.txt"},
        {**base, "id": 3, "tool": "osv-scanner", "rule_id": "PYSEC-2026-3417", "file_path": "requirements.txt"},
        {**base, "id": 4, "tool": "trivy", "rule_id": "CVE-2024-49767", "file_path": "requirements.txt"},
        # Another advisory of the same package stays its own issue.
        {**base, "id": 5, "tool": "pip-audit", "rule_id": "PYSEC-2099-1", "file_path": "requirements.txt"},
    ]
    clusters = cluster_findings(findings)
    assert sorted(len(c.members) for c in clusters) == [1, 4]


def test_cluster_membership_does_not_depend_on_input_order():
    from itertools import permutations

    base = {"run_id": "r", "severity": "medium", "category": "dependency", "line": None, "package": "werkzeug"}
    findings = [
        {**base, "id": i, "tool": tool, "rule_id": rule, "aliases": aliases, "file_path": "requirements.txt"}
        for i, (tool, rule, aliases) in enumerate(
            [
                ("pip-audit", "PYSEC-1", ["CVE-1"]),
                ("pip-audit", "PYSEC-2", ["CVE-2"]),
                ("osv-scanner", "GHSA-1", ["CVE-1", "CVE-2"]),
                ("trivy", "CVE-2", []),
                ("bandit", "B105", []),
            ],
        )
    ]

    def memberships(items):
        return sorted(sorted(m["id"] for m in c.members) for c in cluster_findings(items))

    expected = memberships(findings)
    assert all(memberships(list(order)) == expected for order in permutations(findings))


def test_cluster_dict_names_the_job_and_the_levels_tools_printed():
    clusters = cluster_findings(
        [
            _f("bandit", "B105", 10, run="job-1", severity="low", tool_severity="LOW"),
            _f("gitleaks", "generic-api-key", 10, run="job-1", severity="high", category="secrets"),
        ],
    )
    d = clusters[0].to_dict()
    assert d["job_id"] == "job-1"
    assert "run_id" not in d
    assert d["reported_levels"] == ["bandit:LOW", "gitleaks:unrated"]
    assert d["severity"] == "high"  # normalized maximum


def test_only_vulnerability_scanner_clusters_are_advisories():
    deptry = cluster_findings(
        [_f("deptry", "DEP001", None, path="app.py", category="dependency", title="'werkzeug' imported but missing")],
    )[0]
    osv = cluster_findings([_f("osv-scanner", "GHSA-1", None, path="requirements.txt", category="dependency",
                               package="flask")])[0]  # fmt: skip
    assert not deptry.is_advisory
    assert osv.is_advisory


def test_finding_record_carries_level_and_fixed_versions():
    from backend.statistics.services.dedup import finding_record

    row = {
        "id": 1,
        "result__tool_slug": "trivy",
        "rule_id": "CVE-1",
        "severity": "high",
        "category": "dependency",
        "file_path": "requirements.txt",
        "line_number": None,
        "tool_specific_data": {"package": "PyJWT", "fixed_version": "2.10.0, 3.0.1", "installed_version": "2.8.0"},
        "title": "PyJWT",
        "tool_severity": "CRITICAL",
    }
    record = finding_record(row, "job")
    assert record["tool_severity"] == "CRITICAL"
    assert record["fixed_versions"] == ["2.10.0", "3.0.1"]
    assert finding_record({**row, "tool_specific_data": {}}, "job")["fixed_versions"] is None


def test_advisory_split_follows_the_shared_p2_definition():
    """``IssueCluster.is_advisory`` is ``base.is_dependency_advisory``, as in the run views."""
    from backend.analysis.services.base import is_dependency_advisory

    invented = cluster_findings(
        [_f("pip-audit", "nonexistent-package", None, path="requirements.txt", category="dependency",
            package="flask-superauth")],
    )[0]  # fmt: skip
    # A package that does not exist is never merged by advisory id with vulnerability findings.
    assert invented.issue_class == "pip-audit:nonexistent-package"
    assert invented.is_advisory == is_dependency_advisory("pip-audit", "dependency", "nonexistent-package")
    secret = cluster_findings([_f("trivy", "aws-key", 3, category="secrets")])[0]
    assert not secret.is_advisory


def test_one_unparsable_file_reported_by_several_linters_is_one_issue():
    clusters = cluster_findings(
        [
            _f("ruff", "syntax-error", 12, category="quality"),
            _f("flake8", "E999", 12, category="quality"),
            _f("pylint", "E0001", 12, category="quality"),
            _f("mypy", "syntax", 12, category="quality"),
        ],
    )
    assert len(clusters) == 1
    assert clusters[0].issue_class == "syntax-error"
    assert issue_class("pylint", "syntax-error") == issue_class("ruff", "E999") == "syntax-error"


def test_finding_record_carries_the_installed_version_and_deptrys_declared_package():
    from backend.statistics.services.dedup import finding_record

    row = {
        "id": 1,
        "result__tool_slug": "pip-audit",
        "rule_id": "PYSEC-1",
        "severity": "medium",
        "category": "dependency",
        "file_path": "requirements.txt",
        "line_number": None,
        "tool_specific_data": {"package": "flask", "version": "3.0.3"},
        "title": "flask 3.0.3: PYSEC-1",
        "tool_severity": "",
    }
    assert finding_record(row, "job")["installed_version"] == "3.0.3"  # the legacy key
    deptry = {**row, "result__tool_slug": "deptry", "rule_id": "DEP002", "tool_specific_data": {"module": "uvicorn"}}
    assert finding_record(deptry, "job")["package"] == "uvicorn"  # stored before parsers recorded ``package``
    # For an import rule ``module`` is an import name, not a declared package.
    assert finding_record({**deptry, "rule_id": "DEP001"}, "job")["package"] == ""
