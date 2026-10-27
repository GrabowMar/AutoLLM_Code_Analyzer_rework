"""Parsers for the dependency / web / extra lint shop tools.

Fixtures are trimmed from real tool output captured in the analyzer-base
image against a generated Flask + React app.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from backend.analysis.models import AnalyzerTool
from backend.analysis.services import parsers

NEW_TOOLS = (
    "pip-audit",
    "npm-audit",
    "deptry",
    "depcheck",
    "flake8",
    "htmlhint",
    "stylelint",
    "eslint-react",
    "eslint-security",
    "shellcheck",
    "lizard",
    "perflint",
)


@pytest.mark.django_db
def test_new_tools_seeded_with_parsers():
    call_command("seed_analysis_tools")
    for slug in NEW_TOOLS:
        tool = AnalyzerTool.objects.get(slug=slug)
        assert parsers.has_parser(tool.parser_key), slug
        assert tool.icon, slug
        assert tool.sample_code, slug
    dependency_tools = set(AnalyzerTool.objects.filter(category="dependencies").values_list("slug", flat=True))
    assert {"pip-audit", "npm-audit", "deptry", "depcheck", "osv-scanner", "retire"} <= dependency_tools


def test_pip_audit_dedupes_repeated_vulns_per_section():
    vuln = {"id": "PYSEC-1", "fix_versions": ["3.1.3"], "aliases": ["GHSA-x", "CVE-2026-1"], "description": "d"}
    report = {"dependencies": [{"name": "flask", "version": "3.0.0", "vulns": [vuln, vuln]}], "fixes": []}
    raw = f"### requirements.txt\n{json.dumps(report)}\n### backend/requirements.txt\n{json.dumps(report)}\n"
    findings = parsers.parse("pip_audit", raw).findings
    assert [(f.file_path, f.title) for f in findings] == [
        ("requirements.txt", "flask 3.0.0: CVE-2026-1"),
        ("backend/requirements.txt", "flask 3.0.0: CVE-2026-1"),
    ]
    assert findings[0].suggestion == "Upgrade flask to 3.1.3"


def test_npm_audit_reports_advisories_not_inherited_entries():
    report = {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "esbuild": {
                "severity": "moderate",
                "isDirect": False,
                "via": [
                    {
                        "source": 1102341,
                        "title": "esbuild dev server CORS",
                        "url": "https://github.com/advisories/GHSA-67mh-4wv8-2f99",
                        "severity": "moderate",
                        "cwe": ["CWE-346"],
                        "cvss": {"score": 5.3},
                        "range": "<=0.24.2",
                    },
                ],
                "fixAvailable": {"name": "vite", "version": "8.3.5", "isSemVerMajor": True},
            },
            # Only inherits esbuild's advisory: no finding of its own.
            "vite": {"severity": "moderate", "isDirect": True, "via": ["esbuild"], "fixAvailable": True},
        },
    }
    raw = f"### frontend/package.json\n{json.dumps(report, indent=2)}\n"
    [f] = parsers.parse("npm_audit", raw).findings
    assert f.severity == "medium"
    assert f.rule_id == "GHSA-67mh-4wv8-2f99"
    assert f.file_path == "frontend/package.json"
    assert f.suggestion == "Upgrade vite to 8.3.5 (major upgrade)"
    assert f.tool_specific_data["cwe"] == "CWE-346"


def test_deptry_prefixes_paths_and_ranks_missing_imports():
    items = [
        {
            "error": {"code": "DEP001", "message": "'yaml' imported but missing"},
            "module": "yaml",
            "location": {"file": "app.py", "line": 3, "column": 1},
        },
        {
            "error": {"code": "DEP002", "message": "'Werkzeug' defined but not used"},
            "module": "Werkzeug",
            "location": {"file": "requirements.txt", "line": None, "column": None},
        },
    ]
    raw = f"### backend\n{json.dumps(items)}\n### .\n[]\n"
    missing, unused = parsers.parse("deptry", raw).findings
    assert (missing.severity, missing.file_path, missing.line_number) == ("high", "backend/app.py", 3)
    assert (unused.severity, unused.file_path) == ("low", "backend/requirements.txt")


def test_depcheck_missing_and_unused():
    report = {
        "dependencies": ["uuid"],
        "devDependencies": ["jest"],
        "missing": {"axios": ["/work/frontend/src/App.jsx"]},
    }
    raw = f"### frontend/package.json\n{json.dumps(report)}\n"
    findings = {f.rule_id: f for f in parsers.parse("depcheck", raw).findings}
    assert findings["missing-dependency"].severity == "high"
    assert findings["missing-dependency"].file_path == "/work/frontend/src/App.jsx"
    assert findings["unused-dependencies"].file_path == "frontend/package.json"
    assert findings["unused-devDependencies"].severity == "info"


def test_flake8_severity_by_code():
    raw = (
        "/work/app.py:323:1: F401 'yaml' imported but unused\n"
        "/work/app.py:324:1: E402 module level import not at top of file\n"
        "/work/app.py:326:7: F821 undefined name 'undefined_name'\n"
        "garbage line\n"
    )
    sev = {f.rule_id: f.severity for f in parsers.parse("flake8", raw).findings}
    assert sev == {"F401": "low", "E402": "low", "F821": "high"}


def test_lizard_metrics_and_complex_functions():
    raw = (
        '13,5,71,1,13,"f@1-13@/work/perf.py","/work/perf.py","f","f( items )",1,13\n'
        '30,26,300,4,60,"route@10-70@/work/app.py","/work/app.py","route","route( a, b, c, d )",10,70\n'
    )
    out = parsers.parse("lizard", raw)
    assert out.metrics["functions"] == 2
    assert out.metrics["max_ccn"] == 26
    assert out.metrics["functions_over_15"] == 1
    [f] = out.findings
    assert (f.severity, f.line_number, f.rule_id) == ("high", 10, "CCN")


def test_perflint_reuses_pylint_json():
    raw = json.dumps(
        [
            {
                "type": "warning",
                "line": 3,
                "column": 4,
                "path": "perf.py",
                "symbol": "use-list-copy",
                "message": "Use a list copy instead of a for-loop",
                "message-id": "W8402",
                "obj": "f",
            },
        ],
    )
    [f] = parsers.parse("perflint", raw).findings
    assert (f.category, f.severity) == ("performance", "low")


def test_eslint_security_severity_and_cwe():
    raw = json.dumps(
        [
            {
                "filePath": "/work/x.jsx",
                "messages": [
                    {
                        "ruleId": "security/detect-eval-with-expression",
                        "severity": 1,
                        "message": "eval",
                        "line": 6,
                        "column": 3,
                    },
                    {
                        "ruleId": "security/detect-non-literal-regexp",
                        "severity": 1,
                        "message": "regexp",
                        "line": 5,
                        "column": 14,
                    },
                ],
            },
        ],
    )
    evalf, regexf = parsers.parse("eslint_security", raw).findings
    assert (evalf.severity, evalf.category, evalf.tool_specific_data["cwe"]) == ("high", "security", "CWE-95")
    assert (regexf.severity, regexf.tool_specific_data["cwe"]) == ("medium", "CWE-1333")


def test_htmlhint_and_stylelint():
    html = json.dumps(
        [
            {
                "file": "/work/index.html",
                "messages": [
                    {
                        "type": "error",
                        "message": "Doctype must be declared first.",
                        "line": 1,
                        "col": 1,
                        "evidence": "<html>",
                        "rule": {"id": "doctype-first", "description": "Doctype first"},
                    },
                ],
            },
        ],
    )
    [h] = parsers.parse("htmlhint", html).findings
    assert (h.rule_id, h.severity, h.line_number) == ("doctype-first", "medium", 1)

    css = json.dumps(
        [
            {
                "source": "/work/x.css",
                "parseErrors": [],
                "warnings": [
                    {
                        "line": 1,
                        "column": 5,
                        "rule": "property-no-unknown",
                        "severity": "error",
                        "text": 'Unexpected unknown property "colr" (property-no-unknown)',
                    },
                ],
            },
        ],
    )
    [c] = parsers.parse("stylelint", css).findings
    assert c.title == 'Unexpected unknown property "colr"'
    assert c.rule_id == "property-no-unknown"


def test_shellcheck_handles_multiple_xargs_batches():
    batch = [
        {"file": "/work/x.sh", "line": 2, "column": 8, "level": "warning", "code": 2115, "message": "Use ${var:?}"},
    ]
    raw = json.dumps(batch) + json.dumps([{**batch[0], "level": "info", "code": 2086, "message": "Double quote"}])
    findings = parsers.parse("shellcheck", raw).findings
    assert [(f.rule_id, f.severity) for f in findings] == [("SC2115", "medium"), ("SC2086", "low")]


def test_empty_output_yields_nothing():
    for key in ("pip_audit", "npm_audit", "deptry", "depcheck", "htmlhint", "stylelint", "shellcheck", "lizard"):
        out = parsers.parse(key, "")
        assert out.findings == [], key
        assert out.metrics == {}, key


def test_failed_manifest_is_a_coverage_gap_not_a_clean_result():
    ok = '{"dependencies": [{"name": "flask", "version": "3.1.0", "vulns": []}]}'
    raw = f"### requirements.txt\n{ok}\n### backend/requirements.txt\n!! ERROR: network unreachable\n"
    parsed = parsers.parse("pip_audit", raw)
    assert parsed.error is None
    assert parsed.warnings == ["Not analyzed: backend/requirements.txt: ERROR: network unreachable"]

    parsed = parsers.parse("pip_audit", "### requirements.txt\n\n")
    assert parsed.error.startswith("No manifest could be analyzed")


def test_pip_audit_reports_hallucinated_packages_and_versions():
    raw = (
        "### requirements.txt\n"
        "!! ERROR: Could not find a version that satisfies the requirement flask-magic-auth==1.0 "
        "(from versions: none)\n"
        "!! ERROR: No matching distribution found for flask-magic-auth==1.0\n"
        "### api/requirements.txt\n"
        "!! ERROR: No matching distribution found for flask==99.0.0\n"
    )
    parsed = parsers.parse("pip_audit", raw)
    by_rule = {f.rule_id: f for f in parsed.findings}
    assert by_rule["nonexistent-package"].tool_specific_data["package"] == "flask-magic-auth"
    assert by_rule["nonexistent-package"].severity == "high"
    assert by_rule["nonexistent-version"].file_path == "api/requirements.txt"
    # Neither manifest was audited for vulnerabilities: the result is failed
    # (not a measurement), but the hallucinated dependencies are kept and the
    # gap is recorded as a warning (the runner stores both on the result).
    assert parsed.error.startswith("No manifest could be analyzed")
    assert parsed.warnings[0].startswith("Not analyzed: requirements.txt")


def test_npm_audit_reports_hallucinated_packages():
    raw = (
        "### frontend/package.json\n"
        "!! npm error code E404\n"
        "!! npm error 404  'react-super-grid-zz@^1.0.0' is not in this registry.\n"
    )
    findings = parsers.parse("npm_audit", raw).findings
    assert [(f.rule_id, f.tool_specific_data["package"]) for f in findings] == [
        ("nonexistent-package", "react-super-grid-zz"),
    ]


def test_parser_crash_is_an_error(monkeypatch):
    monkeypatch.setitem(parsers._PARSERS, "boom", lambda raw: 1 / 0)
    assert "ZeroDivisionError" in parsers.parse("boom", "x").error


def test_ruff_and_flake8_rate_codes_alike():
    ruff = json.dumps(
        [
            {"code": "F401", "message": "unused", "filename": "a.py", "location": {"row": 1}},
            {"code": "F821", "message": "undefined", "filename": "a.py", "location": {"row": 2}},
        ],
    )
    assert [f.severity for f in parsers.parse("ruff", ruff).findings] == ["low", "high"]


def test_semgrep_category_comes_from_rule_metadata():
    raw = json.dumps(
        {
            "results": [
                {
                    "check_id": "a.sec",
                    "path": "a.py",
                    "start": {"line": 1},
                    "extra": {"metadata": {"category": "security"}},
                },
                {
                    "check_id": "a.bp",
                    "path": "a.py",
                    "start": {"line": 2},
                    "extra": {"metadata": {"category": "best-practice"}},
                },
            ],
        },
    )
    assert [f.category for f in parsers.parse("semgrep", raw).findings] == ["security", "best_practice"]


# --- Tool's own severity and enriched tool_specific_data ---------------------


def test_npm_audit_keeps_its_label_and_drops_unscored_cvss():
    report = {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "react-router": {
                "severity": "moderate",
                "isDirect": False,
                "via": [
                    {
                        "source": 1124268,
                        "title": "Open redirect",
                        "url": "https://github.com/advisories/GHSA-wrjc-x8rr-h8h6",
                        "severity": "moderate",
                        "cwe": ["CWE-601"],
                        "cvss": {"score": 0, "vectorString": None},
                        "range": ">=6.0.0 <7.18.0",
                    },
                    {
                        "source": 1124272,
                        "title": "Constructor injection",
                        "url": "https://github.com/advisories/GHSA-337j-9hxr-rhxg",
                        "severity": "high",
                        "cwe": ["CWE-470"],
                        "cvss": {"score": 6.1, "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"},
                        "range": ">=6.4.0 <7.18.0",
                    },
                ],
                "fixAvailable": False,
            },
        },
    }
    raw = f"### frontend/package.json\n{json.dumps(report)}\n"
    unscored, scored = parsers.parse("npm_audit", raw).findings
    assert (unscored.tool_severity, scored.tool_severity) == ("moderate", "high")
    # The advisory URL is a link, not a description.
    assert unscored.description == ""
    assert unscored.tool_specific_data == {
        "package": "react-router",
        "direct": False,
        "vuln_id": "GHSA-wrjc-x8rr-h8h6",
        "docs_url": "https://github.com/advisories/GHSA-wrjc-x8rr-h8h6",
        "manifest": "frontend/package.json",
        "cwe": "CWE-601",
    }
    assert scored.tool_specific_data["cvss_score"] == 6.1
    assert scored.tool_specific_data["cvss_vector"].startswith("CVSS:3.1/")
    assert scored.tool_specific_data["cvss"] == 6.1  # legacy key


def test_pip_audit_has_no_severity_and_standard_dependency_keys():
    vuln = {"id": "PYSEC-2026-2151", "fix_versions": ["3.1.3"], "aliases": ["CVE-2026-27205"], "description": "d"}
    nofix = {"id": "PYSEC-2024-271", "fix_versions": [], "aliases": [], "description": "e"}
    report = {"dependencies": [{"name": "flask", "version": "3.0.0", "vulns": [vuln, nofix]}]}
    fixed, unfixed = parsers.parse("pip_audit", f"### requirements.txt\n{json.dumps(report)}\n").findings
    assert fixed.tool_severity == ""  # pip-audit's JSON rates nothing
    assert fixed.tool_specific_data == {
        "package": "flask",
        "installed_version": "3.0.0",
        "fixed_versions": ["3.1.3"],
        "vuln_id": "PYSEC-2026-2151",
        # Every id of the advisory, vuln_id first (dedup clusters on any of them).
        "aliases": ["PYSEC-2026-2151", "CVE-2026-27205"],
        "manifest": "requirements.txt",
        "version": "3.0.0",
        "fix_versions": ["3.1.3"],
    }
    assert unfixed.suggestion == ""  # no invented "no fixed release" text
    assert "fixed_versions" not in unfixed.tool_specific_data


def test_lint_tools_report_their_levels_and_docs():
    shell = json.dumps(
        [
            {
                "file": "/work/x.sh",
                "line": 2,
                "endLine": 2,
                "column": 8,
                "endColumn": 12,
                "level": "style",
                "code": 2006,
                "message": "Use $(...)",
                "fix": None,
            },
        ],
    )
    [sh] = parsers.parse("shellcheck", shell).findings
    assert sh.tool_severity == "style"
    assert sh.suggestion == ""
    assert sh.tool_specific_data == {
        "docs_url": "https://www.shellcheck.net/wiki/SC2006",
        "end_line": 2,
        "end_column": 12,
    }

    css = json.dumps(
        [
            {
                "source": "/work/x.css",
                "warnings": [
                    {
                        "line": 1,
                        "column": 5,
                        "endLine": 1,
                        "endColumn": 9,
                        "rule": "property-no-unknown",
                        "severity": "error",
                        "text": 'Unexpected unknown property "colr" (property-no-unknown)',
                    },
                    {"line": 3, "column": 1, "rule": "CssSyntaxError", "severity": "error", "text": "Unclosed block"},
                ],
            },
        ],
    )
    known, syntax = parsers.parse("stylelint", css).findings
    assert known.tool_severity == "error"
    assert known.tool_specific_data["docs_url"] == "https://stylelint.io/user-guide/rules/property-no-unknown"
    assert "docs_url" not in syntax.tool_specific_data

    html = json.dumps(
        [
            {
                "file": "/work/index.html",
                "messages": [
                    {
                        "type": "warning",
                        "message": "The id value [ a ] must be unique.",
                        "line": 1,
                        "col": 52,
                        "rule": {
                            "id": "id-unique",
                            "description": "The value of id attributes must be unique.",
                            "link": "https://htmlhint.com/rules/id-unique",
                        },
                    },
                ],
            },
        ],
    )
    [h] = parsers.parse("htmlhint", html).findings
    assert h.tool_severity == "warning"
    assert h.tool_specific_data == {"docs_url": "https://htmlhint.com/rules/id-unique"}


def test_unrated_dependency_and_complexity_tools():
    deptry = [
        {"error": {"code": "DEP001", "message": "'yaml' imported but missing"}, "module": "yaml", "location": {}},
    ]
    [dep] = parsers.parse("deptry", f"### .\n{json.dumps(deptry)}\n").findings
    assert (dep.tool_severity, dep.suggestion) == ("", "")

    depcheck = {"dependencies": ["uuid"], "devDependencies": [], "missing": {"axios": ["/work/src/App.jsx"]}}
    found = parsers.parse("depcheck", f"### frontend/package.json\n{json.dumps(depcheck)}\n").findings
    assert {f.tool_severity for f in found} == {""}
    assert {f.suggestion for f in found} == {""}
    assert all(f.tool_specific_data["manifest"] == "frontend/package.json" for f in found)

    lizard = '30,26,300,4,60,"route@10-70@/work/app.py","/work/app.py","route","route( a, b, c, d )",10,70\n'
    [cx] = parsers.parse("lizard", lizard).findings
    assert (cx.tool_severity, cx.suggestion) == ("", "")
    assert cx.tool_specific_data == {"complexity": 26, "function": "route", "nloc": 30, "end_line": 70, "ccn": 26}


def test_perflint_keeps_pylint_level_without_core_docs_link():
    raw = json.dumps(
        [
            {
                "type": "warning",
                "line": 307,
                "column": 4,
                "endLine": 308,
                "endColumn": 60,
                "path": "app.py",
                "symbol": "use-list-copy",
                "message": "Use a list copy instead of a for-loop",
                "message-id": "W8402",
                "obj": "board_view",
            },
        ],
    )
    [f] = parsers.parse("perflint", raw).findings
    assert f.tool_severity == "warning"
    assert f.tool_specific_data == {"obj": "board_view", "message_id": "W8402", "end_line": 308, "end_column": 60}


def test_eslint_security_plugin_rules_keep_level_and_cwe():
    raw = json.dumps(
        [
            {
                "filePath": "/work/x.jsx",
                "messages": [
                    {"ruleId": "security/detect-child-process", "severity": 1, "message": "child", "line": 2},
                ],
            },
        ],
    )
    [f] = parsers.parse("eslint_security", raw).findings
    assert f.tool_severity == "warning"
    assert f.tool_specific_data == {"cwe": "CWE-78"}


def _lizard_row(file: str, name: str, ccn: int = 2, start: int = 1) -> str:
    return f'5,{ccn},30,1,5,"{name}@{start}-{start + 4}@{file}","{file}","{name}","{name}( )",{start},{start + 4}'


def test_lizard_without_any_function_is_an_error():
    parsed = parsers.parse("lizard", "# files: jsx 2\n")
    assert parsed.metrics == {}
    assert parsed.error == "Lizard measured no function in 2 .jsx file(s): nothing was analyzed"
    # Old run command (no preamble), empty CSV.
    assert parsers.parse("lizard", "").error is not None


def test_lizard_flags_an_extension_without_measured_functions():
    raw = "# files: jsx 3\n# files: py 1\n" + _lizard_row("/work/app.py", "index") + "\n"
    parsed = parsers.parse("lizard", raw)
    assert parsed.error is None
    assert parsed.metrics["functions"] == 1
    assert parsed.warnings == ["No function measured in 3 .jsx file(s)"]

    both = raw + _lizard_row("/work/src/App.jsx", "App") + "\n"
    assert parsers.parse("lizard", both).warnings == []


def test_pip_audit_aliases_include_its_own_id():
    vuln = {"id": "PYSEC-1", "fix_versions": [], "aliases": ["GHSA-x", "PYSEC-1", "CVE-2026-1"]}
    report = {"dependencies": [{"name": "flask", "version": "1", "vulns": [vuln]}]}
    [f] = parsers.parse("pip_audit", f"### requirements.txt\n{json.dumps(report)}\n").findings
    assert f.tool_specific_data["aliases"] == ["PYSEC-1", "GHSA-x", "CVE-2026-1"]
