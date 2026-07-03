from __future__ import annotations

import json

from backend.analysis.services import parsers


def test_bandit_parser_maps_severity_and_rule():
    raw = json.dumps(
        {
            "results": [
                {
                    "filename": "app.py",
                    "issue_severity": "HIGH",
                    "issue_confidence": "MEDIUM",
                    "issue_text": "Use of insecure MD5 hash function",
                    "test_id": "B303",
                    "test_name": "md5",
                    "line_number": 4,
                    "code": "hashlib.md5(x)",
                },
            ],
        },
    )
    parsed = parsers.parse("bandit", raw)
    assert len(parsed.findings) == 1
    assert parsed.metrics == {}
    f = parsed.findings[0]
    assert f.severity == "high"
    assert f.category == "security"
    assert f.rule_id == "B303"
    assert f.line_number == 4


def test_ruff_parser():
    raw = json.dumps(
        [
            {
                "code": "F401",
                "message": "'os' imported but unused",
                "filename": "app.py",
                "location": {"row": 1, "column": 1},
            },
        ],
    )
    findings = parsers.parse("ruff", raw).findings
    assert len(findings) == 1
    assert findings[0].rule_id == "F401"
    assert findings[0].category == "quality"


def test_eslint_parser_severity_levels():
    raw = json.dumps(
        [
            {
                "filePath": "/work/frontend.js",
                "messages": [
                    {"ruleId": "no-undef", "severity": 2, "message": "x is not defined", "line": 3, "column": 5},
                    {"ruleId": "no-unused-vars", "severity": 1, "message": "unused", "line": 1, "column": 1},
                ],
            },
        ],
    )
    findings = parsers.parse("eslint", raw).findings
    assert {f.severity for f in findings} == {"high", "low"}
    assert findings[0].file_path == "/work/frontend.js"


def test_semgrep_parser():
    raw = json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.dangerous-subprocess",
                    "path": "backend_code.py",
                    "start": {"line": 5, "col": 5},
                    "end": {"line": 5, "col": 40},
                    "extra": {
                        "severity": "ERROR",
                        "message": "Detected subprocess with shell=True",
                        "lines": "subprocess.run(cmd, shell=True)",
                        "metadata": {"confidence": "HIGH"},
                    },
                },
            ],
        },
    )
    findings = parsers.parse("semgrep", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high"
    assert f.category == "security"
    assert f.rule_id == "python.lang.security.dangerous-subprocess"
    assert f.line_number == 5


def test_pylint_parser_severity_map():
    raw = json.dumps(
        [
            {
                "type": "error",
                "message": "undefined name 'foo'",
                "symbol": "undefined-variable",
                "path": "app.py",
                "line": 3,
                "column": 4,
            },
            {
                "type": "convention",
                "message": "missing docstring",
                "symbol": "missing-docstring",
                "path": "app.py",
                "line": 1,
                "column": 0,
            },
        ],
    )
    findings = parsers.parse("pylint", raw).findings
    assert {f.severity for f in findings} == {"high", "low"}
    assert findings[0].rule_id == "undefined-variable"
    assert findings[0].category == "quality"


def test_mypy_parser_text():
    raw = (
        'backend_code.py:4:1: error: Argument 1 to "greet" has incompatible type '
        '"int"; expected "str"  [arg-type]\n'
        "backend_code.py:4:1: note: see docs\n"
    )
    findings = parsers.parse("mypy", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "medium"
    assert f.line_number == 4
    assert f.rule_id == "arg-type"


def test_gitleaks_parser():
    raw = json.dumps(
        [
            {
                "RuleID": "aws-access-token",
                "Description": "AWS Access Key",
                "File": "backend_code.py",
                "StartLine": 1,
                "Match": "AKIA...REDACTED",
            },
        ],
    )
    findings = parsers.parse("gitleaks", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high"
    assert f.category == "secrets"
    assert f.rule_id == "aws-access-token"


def test_radon_parser_findings_only_for_e_and_f_ranks():
    raw = json.dumps(
        {
            "app.py": [
                {
                    "type": "class",
                    "name": "Dispatcher",
                    "rank": "C",
                    "complexity": 12,
                    "lineno": 1,
                    "col_offset": 0,
                    "endline": 40,
                    "methods": [
                        {
                            "type": "method",
                            "name": "dispatch",
                            "rank": "F",
                            "complexity": 44,
                            "lineno": 3,
                            "col_offset": 4,
                            "endline": 39,
                        },
                    ],
                },
            ],
            "broken.py": {"error": "invalid syntax"},
        },
    )
    findings = parsers.parse("radon", raw).findings
    # The C-rank class is metric data, not a finding; only E/F blocks surface.
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "CC-F"
    assert f.severity == "critical"
    assert f.line_number == 3
    assert f.category == "performance"


def test_radon_parser_metrics():
    raw = json.dumps(
        {
            "app.py": [
                {"type": "function", "name": "simple", "rank": "A", "complexity": 2, "lineno": 1},
                {"type": "function", "name": "medium", "rank": "B", "complexity": 8, "lineno": 10},
                {
                    "type": "class",
                    "name": "Dispatcher",
                    "rank": "C",
                    "complexity": 12,
                    "lineno": 20,
                    "methods": [
                        {"type": "method", "name": "dispatch", "rank": "F", "complexity": 42, "lineno": 22},
                    ],
                },
            ],
        },
    )
    metrics = parsers.parse("radon", raw).metrics
    assert metrics["total_blocks"] == 4
    assert metrics["max_complexity"] == 42
    assert metrics["average_complexity"] == 16.0  # (2+8+12+42)/4
    assert metrics["rank_distribution"] == {"A": 1, "B": 1, "C": 1, "D": 0, "E": 0, "F": 1}
    top = metrics["top_functions"]
    assert top[0]["name"] == "dispatch"
    assert top[0]["complexity"] == 42
    assert top[0]["file"] == "app.py"


def test_vulture_parser_text():
    raw = (
        "app.py:1: unused import 'os' (90% confidence)\n"
        "app.py:4: unused function 'never_called' (60% confidence)\n"
        "vulture: some note line\n"
    )
    findings = parsers.parse("vulture", raw).findings
    assert len(findings) == 2
    assert findings[0].confidence == "high"
    assert findings[0].rule_id == "unused-import"
    assert findings[1].confidence == "medium"
    assert findings[1].line_number == 4
    assert all(f.category == "quality" for f in findings)


def test_detect_secrets_parser():
    raw = json.dumps(
        {
            "version": "1.5.0",
            "results": {
                "config.py": [
                    {
                        "type": "AWS Access Key",
                        "filename": "config.py",
                        "line_number": 2,
                        "hashed_secret": "abc123",
                        "is_verified": False,
                    },
                ],
            },
        },
    )
    findings = parsers.parse("detect_secrets", raw).findings
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high"
    assert f.category == "secrets"
    assert f.rule_id == "AWS Access Key"
    assert f.line_number == 2
    assert f.tool_specific_data == {"hashed_secret": "abc123", "is_verified": False}
    assert f.code_snippet == ""


def test_jscpd_parser_truncates_fragment():
    raw = json.dumps(
        {
            "duplicates": [
                {
                    "format": "python",
                    "lines": 30,
                    "tokens": 120,
                    "fragment": "x" * 900,
                    "firstFile": {"name": "a.py", "start": 3, "end": 32},
                    "secondFile": {"name": "b.py", "start": 10, "end": 39},
                },
            ],
            "statistics": {},
        },
    )
    parsed = parsers.parse("jscpd", raw)
    assert len(parsed.findings) == 1
    assert parsed.metrics == {}  # no statistics.total in payload
    f = parsed.findings[0]
    assert f.severity == "medium"  # >= 25 lines
    assert f.file_path == "a.py"
    assert f.line_number == 3
    assert "b.py:10" in f.title
    assert len(f.code_snippet) == 500
    assert f.rule_id == "duplicate-code"


def test_jscpd_parser_metrics_from_statistics():
    raw = json.dumps(
        {
            "duplicates": [],
            "statistics": {
                "total": {
                    "lines": 200,
                    "tokens": 1600,
                    "sources": 4,
                    "clones": 2,
                    "duplicatedLines": 24,
                    "duplicatedTokens": 190,
                    "percentage": 12.0,
                    "percentageTokens": 11.875,
                },
            },
        },
    )
    parsed = parsers.parse("jscpd", raw)
    assert parsed.findings == []
    assert parsed.metrics == {
        "clones": 2,
        "duplicated_lines": 24,
        "duplicated_tokens": 190,
        "total_lines": 200,
        "total_sources": 4,
        "duplication_percentage": 12.0,
        "token_duplication_percentage": 11.88,
    }


def test_hadolint_parser_level_map():
    raw = json.dumps(
        [
            {"file": "Dockerfile", "line": 1, "code": "DL3007", "level": "warning", "message": "latest tag"},
            {"file": "Dockerfile", "line": 2, "code": "DL3009", "level": "info", "message": "apt lists"},
            {"file": "Dockerfile", "line": 3, "code": "DL4000", "level": "style", "message": "style"},
        ],
    )
    findings = parsers.parse("hadolint", raw).findings
    assert [f.severity for f in findings] == ["medium", "low", "info"]
    assert findings[0].rule_id == "DL3007"
    # Non-list payload (e.g. an error object) yields no findings.
    assert parsers.parse("hadolint", json.dumps({"error": "x"})).findings == []


def test_codespell_parser_text():
    raw = "app.py:3:  recieve ==> receive\napp.py:7:  seperate ==> separate\n"
    findings = parsers.parse("codespell", raw).findings
    assert len(findings) == 2
    f = findings[0]
    assert f.severity == "info"
    assert f.line_number == 3
    assert f.suggestion == "Replace 'recieve' with 'receive'"
    assert f.rule_id == "typo"


def test_unknown_parser_returns_empty():
    parsed = parsers.parse("does-not-exist", "{}")
    assert parsed.findings == []
    assert parsed.metrics == {}


def test_parser_tolerates_leading_noise():
    raw = "warning: something\n" + json.dumps({"results": []})
    assert parsers.parse("bandit", raw).findings == []


def test_parser_tolerates_empty_and_non_json_output():
    # A tool that scanned nothing or printed a CLI error must yield nothing quietly.
    for key in ("eslint", "gitleaks", "radon", "vulture", "detect_secrets", "jscpd", "hadolint", "codespell"):
        for raw in ("", "No files matching the pattern were found."):
            parsed = parsers.parse(key, raw)
            assert parsed.findings == []
            assert parsed.metrics == {}
