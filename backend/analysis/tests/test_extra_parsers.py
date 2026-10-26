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
    assert AnalyzerTool.objects.filter(category="dependencies").count() == 4


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
    assert sev == {"F401": "medium", "E402": "low", "F821": "high"}


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
